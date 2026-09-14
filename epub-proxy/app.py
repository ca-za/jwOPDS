"""On-demand EPUB optimizer proxy.

GET /optimize/<device>.epub?src=<url>&checksum=<md5, optional>

Fetches the EPUB at `src` (must be on an allowlisted host), runs it through
CrossPoint Reader's own optimizer (see optimizer/, vendored from
crosspoint-reader/calibre-plugins), caches the result on disk keyed by
(src, device, checksum), and serves it. Never stores or serves anything
from a host not on ALLOWED_SOURCE_HOSTS -- this is a transformer for a
specific known source, not a general-purpose open proxy.

`checksum` is jw.org's own reported MD5 for the file (jw2opds already has
it from GETPUBMEDIALINKS and passes it along when it links here). Folding
it into the cache key means a content change at jw.org -- picked up as a
new checksum on jw2opds's next sync -- naturally invalidates the cached
optimized copy instead of it going stale forever; it's also verified
against the actual downloaded bytes before processing, as an integrity
check. Without it (a manually-constructed URL, say), caching falls back to
being keyed on (src, device) alone.
"""
from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import re
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

# Cache eviction: 0 = unlimited (the default -- opt in explicitly). Whichever
# limit is set, the least-recently-*served* entries (not least-recently-
# created -- a cache hit counts as "used" too) are pruned first.
MAX_CACHE_BYTES = int(os.environ.get("MAX_CACHE_BYTES", "0"))
MAX_CACHE_FILES = int(os.environ.get("MAX_CACHE_FILES", "0"))
_PRUNE_LOCK_PATH = CACHE_DIR / ".prune.lock"


class SourceError(Exception):
    """A problem with the fetched source that should map to a 4xx, not a
    generic 500 -- the source was reachable but unusable/unsafe."""


def _host_allowed(host: str) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_SOURCE_HOSTS)


def _cache_key(src: str, device: str, checksum: str = "") -> str:
    # Folding jw.org's own reported checksum in means a content change (a
    # new checksum picked up on jw2opds's next sync) invalidates the cached
    # optimized copy automatically -- without it, the cache is keyed purely
    # on the URL and would serve a stale transformation forever if jw.org
    # ever updated a file's content in place at the same URL.
    return hashlib.sha256(f"{device}|{checksum}|{src}".encode("utf-8")).hexdigest()


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _safe_log_value(value: str) -> str:
    """`src` is attacker-controlled (a GET parameter); Werkzeug happily
    URL-decodes embedded control characters into it. Strip them before
    logging so a crafted request can't forge fake-looking extra log lines
    (CWE-117) -- this is about keeping logs trustworthy, not about the
    request handling itself, which never uses the raw value unsanitized."""
    return _CONTROL_CHARS_RE.sub("", value)


_UNSAFE_FILENAME_RE = re.compile(r'[^A-Za-z0-9._-]')


def _safe_download_name(name: str, fallback: str = "book") -> str:
    """Restrict a filename derived from attacker-controlled input (the `src`
    URL's path) to a safe, boring charset before it reaches a Content-
    Disposition header. Werkzeug's send_file already rejects raw control
    characters in download_name outright (raises ValueError) and correctly
    escapes quotes rather than allowing them to break out of the header's
    filename="..." parameter -- confirmed by testing -- so this isn't
    closing a real hole, just making the failure mode "sanitized name"
    instead of "500 from an uncaught ValueError" for unusual input."""
    cleaned = _UNSAFE_FILENAME_RE.sub("_", name).strip("._")
    return cleaned or fallback


def _download(url: str, dest: Path, expected_checksum: str = "") -> None:
    """Fetch `url` to `dest`. If `expected_checksum` (an MD5 hex digest, as
    reported by jw.org's own GETPUBMEDIALINKS) is given, verify the
    downloaded bytes match it -- a defense-in-depth integrity check, since
    this proxy processes and re-serves the content rather than passing it
    through untouched."""
    md5 = hashlib.md5() if expected_checksum else None
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
                if md5 is not None:
                    md5.update(chunk)

    if md5 is not None and md5.hexdigest().lower() != expected_checksum.lower():
        raise SourceError(
            f"downloaded content checksum {md5.hexdigest()} does not match "
            f"expected {expected_checksum} -- source may have changed or been tampered with"
        )


def _touch(path: Path) -> None:
    """Mark a cache entry as just-used. mtime, not atime: many setups mount
    volumes noatime (or just don't reliably update it on every read), so we
    can't trust the filesystem to track "last served" for us -- we update it
    explicitly on every hit, and it's already "now" on creation."""
    try:
        os.utime(path, None)
    except FileNotFoundError:
        pass


def _prune_cache(exclude: Path | None = None) -> None:
    """Evict least-recently-served cache entries until both MAX_CACHE_BYTES
    and MAX_CACHE_FILES (whichever are non-zero) are satisfied. `exclude`
    (the entry this request is about to serve) is never deleted -- with a
    misconfigured limit smaller than a single file, pruning should leave the
    budget slightly exceeded rather than delete the file out from under the
    response that's about to serve it. It still counts toward the total,
    though: excluding it there too would under-count and let the *other*
    files sit one-over-limit forever without ever triggering a prune."""
    if not MAX_CACHE_BYTES and not MAX_CACHE_FILES:
        return

    prunable = []
    total_size = 0
    count = 0
    for entry in CACHE_DIR.iterdir():
        if not entry.name.endswith(".epub") or not entry.is_file():
            continue
        try:
            st = entry.stat()
        except FileNotFoundError:
            continue
        total_size += st.st_size
        count += 1
        if entry != exclude:
            prunable.append((st.st_mtime, st.st_size, entry))

    prunable.sort(key=lambda e: e[0])  # oldest last-served first

    i = 0
    while i < len(prunable) and (
        (MAX_CACHE_BYTES and total_size > MAX_CACHE_BYTES)
        or (MAX_CACHE_FILES and count > MAX_CACHE_FILES)
    ):
        _, size, path = prunable[i]
        i += 1
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        total_size -= size
        count -= 1
        log.info(
            "Pruned cache entry %s (%.1f KB, least recently served) -- now %d files, %.1f MB",
            path.name, size / 1024, count, total_size / (1024 * 1024),
        )


def _prune_cache_locked(exclude: Path | None = None) -> None:
    """Serialize pruning across gunicorn's worker processes with a plain
    flock -- if another worker is already pruning, skip this round rather
    than block or double up; the next cache-writing request tries again."""
    if not MAX_CACHE_BYTES and not MAX_CACHE_FILES:
        return
    with open(_PRUNE_LOCK_PATH, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            _prune_cache(exclude=exclude)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


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
    checksum = request.args.get("checksum", "")

    parsed = urlparse(src)
    if parsed.scheme not in ("http", "https") or not _host_allowed(parsed.hostname):
        abort(403, "source host not allowed")

    safe_src = _safe_log_value(src)
    cache_path = CACHE_DIR / f"{_cache_key(src, device, checksum)}.epub"
    if not cache_path.is_file():
        log.info("Cache miss for %s (%s), fetching + optimizing", safe_src, device)
        # Must be on the same filesystem as CACHE_DIR (typically a separate
        # mounted volume) so the final os.replace() below is an atomic
        # same-device rename, not a cross-device move (which os.replace
        # cannot do -- it errors with "Invalid cross-device link").
        try:
            with tempfile.TemporaryDirectory(dir=CACHE_DIR) as tmp:
                in_path = Path(tmp) / "in.epub"
                out_path = Path(tmp) / "out.epub"
                _download(src, in_path, checksum)
                _check_zip_safety(in_path)
                profile = DEVICE_PROFILES[device]
                opts = Options(quality=JPEG_QUALITY)
                optimize_epub(str(in_path), str(out_path), profile, opts, log_fn=log.info)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(out_path, cache_path)
        except SourceError as e:
            log.warning("Rejected source %s (%s): %s", safe_src, device, e)
            abort(400, str(e))
        except requests.RequestException as e:
            log.warning("Fetch failed for %s: %s", safe_src, e)
            abort(502, f"failed to fetch source: {e}")
        except Exception:
            log.exception("Optimization failed for %s (%s)", safe_src, device)
            abort(500, "optimization failed")

        # Best-effort maintenance: never let a pruning problem fail an
        # otherwise-successful request -- the file is already cached fine.
        try:
            _prune_cache_locked(exclude=cache_path)
        except Exception:
            log.exception("Cache pruning failed (non-fatal)")
    else:
        log.info("Cache hit for %s (%s)", safe_src, device)
        _touch(cache_path)

    download_name = _safe_download_name(Path(parsed.path).stem) + f".{device.lower()}.epub"
    try:
        return send_file(
            cache_path,
            mimetype="application/epub+zip",
            as_attachment=True,
            download_name=download_name,
        )
    except ValueError:
        # Should be unreachable now that download_name is sanitized, but
        # fail cleanly rather than as an uncaught 500 if it ever isn't.
        log.exception("send_file rejected download_name %r", download_name)
        abort(500, "failed to serve file")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
