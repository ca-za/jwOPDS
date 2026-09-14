"""On-demand EPUB optimizer proxy.

GET /optimize/<device>.epub?src=<url>

Fetches the EPUB at `src` (must be on an allowlisted host), runs it through
CrossPoint Reader's own optimizer (see optimizer/, vendored from
crosspoint-reader/calibre-plugins), caches the result on disk keyed by
(src, device), and serves it. Never stores or serves anything from a host
not on ALLOWED_SOURCE_HOSTS -- this is a transformer for a specific known
source, not a general-purpose open proxy.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, abort, request, send_file

from optimizer.optimizer import DEVICE_PROFILES, Options, optimize_epub

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = app.logger

# This is a public-facing, unauthenticated endpoint that does real work per
# request (network fetch + image/XML processing), so it should sit behind a
# reverse proxy that rate-limits it -- see nginx.conf.example. (An in-app
# limiter was tried first and dropped: with gunicorn's multiple worker
# processes, an in-process/in-memory limiter's state isn't shared across
# workers, so the effective limit silently multiplies by worker count.
# nginx sits in front of all of them and doesn't have that problem.)

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data/cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SOURCE_HOSTS = {
    h.strip().lower()
    for h in os.environ.get("ALLOWED_SOURCE_HOSTS", "jw-cdn.org").split(",")
    if h.strip()
}

FETCH_TIMEOUT = int(os.environ.get("SOURCE_FETCH_TIMEOUT", "60"))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "85"))

# Defensive limits against a malicious/compromised source: real jw.org EPUBs
# (checked against the full Bible, the largest one we've seen) are well
# under these; a "book" claiming to need more is treated as hostile, not a
# legitimate edge case.
MAX_DOWNLOAD_BYTES = int(os.environ.get("MAX_DOWNLOAD_BYTES", str(150 * 1024 * 1024)))
MAX_UNCOMPRESSED_BYTES = int(os.environ.get("MAX_UNCOMPRESSED_BYTES", str(500 * 1024 * 1024)))


class SourceError(Exception):
    """A problem with the fetched source that should map to a 4xx, not a
    generic 500 -- the source was reachable but unusable/unsafe."""


def _host_allowed(host: str) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_SOURCE_HOSTS)


def _cache_key(src: str, device: str) -> str:
    return hashlib.sha256(f"{device}|{src}".encode("utf-8")).hexdigest()


def _download(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=FETCH_TIMEOUT) as resp:
        # Re-validate the *final* host after redirects: the initial `src`
        # passing the allowlist doesn't mean a 3xx hop couldn't land
        # somewhere else -- requests follows redirects by default without
        # re-checking our allowlist itself.
        final_host = urlparse(resp.url).hostname
        if not _host_allowed(final_host):
            raise SourceError(f"redirected to a disallowed host: {final_host}")
        resp.raise_for_status()

        content_length = resp.headers.get("Content-Length")
        if content_length is not None and int(content_length) > MAX_DOWNLOAD_BYTES:
            raise SourceError(
                f"source declares {content_length} bytes, over the {MAX_DOWNLOAD_BYTES}-byte limit"
            )

        total = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise SourceError(f"source exceeded the {MAX_DOWNLOAD_BYTES}-byte download limit")
                f.write(chunk)


def _check_zip_safety(path: Path) -> None:
    """Reject zip bombs -- a small download that would decompress to an
    enormous amount of data -- before handing the file to the optimizer."""
    try:
        with zipfile.ZipFile(path) as z:
            total_uncompressed = sum(info.file_size for info in z.infolist())
    except zipfile.BadZipFile as e:
        raise SourceError(f"source is not a valid EPUB/zip file: {e}") from e
    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise SourceError(
            f"source decompresses to {total_uncompressed} bytes, "
            f"over the {MAX_UNCOMPRESSED_BYTES}-byte limit"
        )


@app.get("/optimize/<device>.epub")
def optimize(device: str):
    device = device.upper()
    if device not in DEVICE_PROFILES:
        abort(400, f"unknown device {device!r}, expected one of {sorted(DEVICE_PROFILES)}")

    src = request.args.get("src")
    if not src:
        abort(400, "missing 'src' query parameter")

    parsed = urlparse(src)
    if parsed.scheme not in ("http", "https") or not _host_allowed(parsed.hostname):
        abort(403, "source host not allowed")

    cache_path = CACHE_DIR / f"{_cache_key(src, device)}.epub"
    if not cache_path.is_file():
        log.info("Cache miss for %s (%s), fetching + optimizing", src, device)
        # Must be on the same filesystem as CACHE_DIR (typically a separate
        # mounted volume) so the final os.replace() below is an atomic
        # same-device rename, not a cross-device move (which os.replace
        # cannot do -- it errors with "Invalid cross-device link").
        try:
            with tempfile.TemporaryDirectory(dir=CACHE_DIR) as tmp:
                in_path = Path(tmp) / "in.epub"
                out_path = Path(tmp) / "out.epub"
                _download(src, in_path)
                _check_zip_safety(in_path)
                profile = DEVICE_PROFILES[device]
                opts = Options(quality=JPEG_QUALITY)
                optimize_epub(str(in_path), str(out_path), profile, opts, log_fn=log.info)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(out_path, cache_path)
        except SourceError as e:
            log.warning("Rejected source %s (%s): %s", src, device, e)
            abort(400, str(e))
        except requests.RequestException as e:
            log.warning("Fetch failed for %s: %s", src, e)
            abort(502, f"failed to fetch source: {e}")
        except Exception:
            log.exception("Optimization failed for %s (%s)", src, device)
            abort(500, "optimization failed")
    else:
        log.info("Cache hit for %s (%s)", src, device)

    download_name = Path(parsed.path).stem + f".{device.lower()}.epub"
    return send_file(
        cache_path,
        mimetype="application/epub+zip",
        as_attachment=True,
        download_name=download_name,
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
