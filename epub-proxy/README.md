# epub-proxy

An on-demand EPUB transformer, self-hosted as a Docker container. Different
e-readers choke on jw.org's raw EPUBs for entirely different reasons, so
this fetches a source EPUB, runs it through a device-specific fix, caches
the result, and serves it:

- **`x4`/`x3`** -- [CrossPoint Reader](https://crosspointreader.com), an
  ESP32 e-ink device with ~380KB of RAM: chokes on oversized images,
  embedded fonts, and single paragraphs/chapter files that are too large.
  Runs CrossPoint's own device-optimization pipeline (vendored from their
  [calibre-plugins](https://github.com/crosspoint-reader/calibre-plugins)
  repo -- see `THIRD_PARTY_LICENSES.md`): resize/grayscale images to the
  device's screen, split oversized paragraphs/chapters, strip fonts.
- **`koreader`** -- [KOReader](https://koreader.rocks/) (runs on real
  hardware: Kobo, Kindle, Android, desktop) doesn't have CrossPoint's RAM
  problem, but jw.org bundles their *entire site-wide CSS framework* --
  including a complete icon-font glyph map -- into every single EPUB. A
  real Daily Text book carries an 18,784-rule, 1.67MB stylesheet of which
  only ~8% is ever used. This is directly confirmed as the cause of
  KOReader hanging/taking "dozens of minutes" to open jw.org books: an open
  GitHub issue ([koreader/koreader#14021](https://github.com/koreader/koreader/issues/14021))
  was filed against this exact publisher's Daily Text EPUB, and crengine's
  own source (`lvstsheet.cpp`) confirms why -- its CSS matcher buckets
  selectors by tag name, but the ~17,000 unused *tag-less* class selectors
  (exactly what jw.org's icon rules are) all fall into one shared bucket
  walked in full *for every node in the document*. `koreader_css.py`
  strips every CSS rule that's provably unreachable given the book's own
  markup (verified safe: it never removes a rule that could actually
  apply, including handling `:not()`/`:is()` correctly -- see the module's
  own docstring for two real bugs found and fixed while building this
  against an actual jw.org file). This does *not* touch images or split
  anything: KOReader isn't RAM-constrained the way CrossPoint is, and
  splitting would make its actual bottleneck (per-node CSS matching cost)
  worse by increasing the node count, not better.

It is a narrow transformer, not a general-purpose proxy: it only fetches
from hosts on `ALLOWED_SOURCE_HOSTS` (default: `jw-cdn.org`), re-checks that
allowlist against the *final* URL after redirects (not just the requested
one), and rejects sources over `MAX_DOWNLOAD_BYTES`/`MAX_UNCOMPRESSED_BYTES`
(zip-bomb protection) before handing them to either transformation.

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
  profiles -- or `koreader` (CSS-only, no fixed resolution).
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

### Cache size limits

`MAX_CACHE_BYTES` and `MAX_CACHE_FILES` (both unset/`0` = unlimited, the
default) bound the cache. Once either is exceeded, the least-recently-*served*
entries are deleted first -- a cache hit refreshes an entry's "last served"
time just as much as creating it does, so an old-but-still-frequently-read
book won't get evicted just because something else was cached more recently.
Pruning runs after every newly-cached file, is safe across gunicorn's
multiple worker processes (a plain flock -- if a worker is already pruning,
others skip that round rather than double up), and never deletes the file
the current request is about to serve, even if a misconfigured limit is
smaller than that one file (the budget is left slightly exceeded in that
case rather than corrupting the in-flight response).

Put this behind your own reverse proxy / TLS termination -- `nginx.conf.example`
is a ready-to-adapt starting point (TLS termination, rate limiting via
`limit_req_zone`, sane timeouts for the slow first-fetch-and-transform path).
This container itself only speaks plain HTTP on :8080 and has no rate
limiting of its own; don't expose :8080 directly to the internet.

## Wiring it into jw2opds

In the main `jw2opds` project's `config.yaml`:

```yaml
epub_proxy:
  base_url: "https://your-proxy.example.com"     # empty disables this feature
  devices: ["X4", "KOREADER"]                     # which profiles to also link
```

When set, every generated OPDS entry gets an *additional*
`rel="http://opds-spec.org/acquisition"` link per configured device, next to
the existing direct-from-jw.org link -- so unaffected readers still get the
original file, while CrossPoint (or anything else that benefits from the
optimized version) has one available too.
