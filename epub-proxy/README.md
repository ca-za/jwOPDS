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
from hosts on `ALLOWED_SOURCE_HOSTS` (default: `jw-cdn.org`).

## Endpoint

```
GET /optimize/<device>.epub?src=<url-encoded source EPUB URL>
```

- `<device>`: `x4` (480x800) or `x3` (528x792) -- CrossPoint's own device
  profiles.
- `src`: the original EPUB URL. Must be on an allowlisted host or the
  request is rejected with 403.

First request for a given `(src, device)` pair fetches + transforms +
caches (may take a few seconds depending on the book's size/image count);
every subsequent request for the same pair is served straight from cache.

## Running it

```
docker compose up -d
```

Cache persists in the `epub-cache` Docker volume. Config via environment
variables (see `docker-compose.yml`): `ALLOWED_SOURCE_HOSTS` (comma-separated
host suffixes), `JPEG_QUALITY` (default 85), `SOURCE_FETCH_TIMEOUT` (seconds,
default 60).

Put this behind your own reverse proxy / TLS termination (Caddy, nginx,
Traefik, a Cloudflare Tunnel, whatever you already use) to get it a public
HTTPS URL -- this container itself only speaks plain HTTP on :8080.

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
