# epub-proxy

An on-demand EPUB optimizer, self-hosted as a Docker container. Some
e-readers (confirmed: [CrossPoint Reader](https://crosspointreader.com), an
ESP32-based e-ink device) render plain EPUBs poorly or not at all --
oversized images, embedded fonts it can't use, single paragraphs/chapter
files too large for its ~380KB of RAM. This service fetches a source EPUB,
runs it through CrossPoint's own device-optimization pipeline (vendored
from their [calibre-plugins](https://github.com/crosspoint-reader/calibre-plugins)
repo -- see `THIRD_PARTY_LICENSES.md`), caches the result, and serves it.

It is a narrow transformer, not a general-purpose proxy: it only fetches
from hosts on `ALLOWED_SOURCE_HOSTS` (default: `jw-cdn.org`), re-checks that
allowlist against the *final* URL after redirects (not just the requested
one), and rejects sources over `MAX_DOWNLOAD_BYTES`/`MAX_UNCOMPRESSED_BYTES`
(zip-bomb protection) before handing them to the optimizer.

This is a public, unauthenticated endpoint that does real work (network
fetch + image/XML processing) per request, so **rate limiting is expected
to happen in front of it** -- see `nginx.conf.example` -- not in the app
itself (an in-process limiter's state doesn't carry across gunicorn's
multiple worker processes, so it under-enforces exactly when it matters).

## Endpoint

```
GET /optimize/<device>.epub?src=<url-encoded source EPUB URL>&checksum=<md5, optional>
```

- `<device>`: `x4` (480x800) or `x3` (528x792) -- CrossPoint's own device
  profiles.
- `src`: the original EPUB URL. Must be on an allowlisted host or the
  request is rejected with 403.
- `checksum`: jw.org's own reported MD5 for the file (jw2opds already has
  this from GETPUBMEDIALINKS and includes it automatically). Verified
  against the downloaded bytes (400 on mismatch) and folded into the cache
  key, so a content change at jw.org -- a new checksum on jw2opds's next
  sync -- invalidates the cached optimized copy instead of serving a stale
  one forever. Optional only for manually-constructed URLs; omitting it
  falls back to caching on `(src, device)` alone.

First request for a given `(src, device, checksum)` triple fetches +
transforms + caches (may take a few seconds depending on the book's size/image count);
every subsequent request for the same pair is served straight from cache.

## Running it

```
docker compose up -d
```

Cache persists in the `epub-cache` Docker volume. Config via environment
variables (see `docker-compose.yml`): `ALLOWED_SOURCE_HOSTS` (comma-separated
host suffixes), `JPEG_QUALITY` (default 85), `SOURCE_FETCH_TIMEOUT` (seconds,
default 60), `MAX_DOWNLOAD_BYTES` (default 150 MB), `MAX_UNCOMPRESSED_BYTES`
(default 500 MB).

Put this behind your own reverse proxy / TLS termination -- `nginx.conf.example`
is a ready-to-adapt starting point (TLS termination, rate limiting via
`limit_req_zone`, sane timeouts for the slow first-fetch-and-transform path).
This container itself only speaks plain HTTP on :8080 and has no rate
limiting of its own; don't expose :8080 directly to the internet.

## Wiring it into jw2opds

In the main `jw2opds` project's `config.yaml`:

```yaml
epub_proxy:
  base_url: "https://your-proxy.example.com"   # empty disables this feature
  devices: ["X4"]                               # which profiles to also link
```

When set, every generated OPDS entry gets an *additional*
`rel="http://opds-spec.org/acquisition"` link per configured device, next to
the existing direct-from-jw.org link -- so unaffected readers still get the
original file, while CrossPoint (or anything else that benefits from the
optimized version) has one available too.
