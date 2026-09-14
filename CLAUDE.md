# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`jw2opds` generates a static OPDS (Atom-based) catalog of jw.org's EPUB/PDF
publications. It does **not** download, host, or mirror any content — every
generated acquisition link points directly at jw.org's own CDN
(`jw-cdn.org` / `cms-imgp.jw-cdn.org`). Only the small XML feed files need to
be served (currently via GitHub Pages, see below). See README.md's
Disclaimer section — it must stay intact and prominent in any future README
edits: this project is not affiliated with jw.org / the Watch Tower Bible
and Tract Society.

The one deliberate exception is `epub-proxy/` (see its own README) — an
optional, off-by-default, separately self-hosted Docker service that DOES
fetch/transform/cache content, for readers whose EPUB rendering needs
device-specific optimization. It's a distinct deployable from the main
`jw2opds` package and not run by this repo's own GitHub Actions workflow.

## Commands

```
pip install -r requirements.txt
cp config.example.yaml config.yaml          # then edit `languages`
python -m jw2opds --config config.yaml [--lang de] [--force] [-v]
python -m http.server 8080 --directory output   # serve locally for testing
```

There is no test suite and no linter configured. Validate changes by
running a sync (ideally with `--lang <one-language>` to keep it fast) and
checking the output, e.g.:

```
for f in $(find output -name "*.xml"); do python3 -c "import xml.etree.ElementTree as ET; ET.parse('$f')" || echo "BROKEN: $f"; done
```

For anything touching feed structure, also spot-check against the W3C Feed
Validator (`https://validator.w3.org/feed/check.cgi?url=<live-feed-url>`) —
it caught a real RFC 4287 violation once (see "Hard-won lessons" below) and
generic XML well-formedness checks won't catch spec violations like that.

## Architecture

### Two independent jw.org data sources feed the pipeline

1. **`jwcatalog.py`** downloads jw.org's own versioned SQLite database
   (`catalog.db`, ~200MB, via `manifest.json` + `catalog.db.gz`). This is
   the *candidate enumeration* source: every publication `KeySymbol` that
   exists in **any** language, and every periodical issue number
   (`IssueTagNumber`) ever published. It is intentionally gitignored and
   re-downloaded fresh on every run (cheap; only a `catalog.db` uuid
   comparison, not the whole 200MB, unless it actually changed).

   **Important gotcha**: `catalog.db`'s `MepsLanguageId` has no public
   mapping to jw.org's `langwritten` codes (the codes used everywhere else,
   e.g. "X" for German). Don't try to filter this DB by language — instead,
   candidates are gathered globally (deduplicated across all languages) and
   each one is individually probed per configured language via
   `GETPUBMEDIALINKS` (see below); a 400/404 there just means "not in this
   language" and is cached as such (`state.py`'s `available=0`).

   `catalog.db` also holds `PublicationAttribute`/`PublicationAttributeMap`
   (tags like "Convention", "Circuit Assembly", "Yearbook") — this is how
   `categorize.py` detects those categories, not by guessing at publication
   codes. `jwcatalog.load_attribute_tags()` loads this once per run.

2. **`pubmedia.py`** (`GETPUBMEDIALINKS` API) resolves one
   `(langwritten code, pub_key, issue)` triple to actual file links. It's
   called with `fileformat=EPUB,PDF` — both formats come back in a single
   request at no extra cost, so PDF is attached whenever jw.org offers it,
   but EPUB alone determines whether a publication counts as "available".
   Its `pubImage` field is always empty; cover art comes from a third,
   undocumented source instead: **`coverart.py`** probes (HEAD request) a
   predictable but unofficial image-CDN URL pattern and just treats a
   non-200 as "no cover" — some older/legacy publications genuinely have
   none.

   **`mediator.py`** resolves a config value like `de` or `X` to a
   `Language` (code/locale/name/vernacular) via jw.org's own public language
   list API — this is also how the codes in `config.yaml`'s `languages:`
   list get validated.

### Categorization is derived from real jw.org data, not hardcoded per language

`categorize.py` assigns each publication to one of: `bible`, `watchtower`,
`awake`, `meeting-workbook`, `daily-text`, `yearbook`, `assembly-programs`,
`tracts`, `books` (`CATEGORY_ORDER`). Recurring ones (`PERIODICAL_CATEGORIES`)
get a "latest year on the front page + full year-by-year archive" treatment
in `opds.py`; the rest (`bible`, `books`, `tracts`) are flat feeds.

The category **display title** (`categorize.display_title`) is deliberately
*not* a hand-maintained translation table for most categories — it's derived
from jw.org's own already-localized publication titles (e.g. splitting our
own `"<pubName> — <date>"` construction for magazines, or stripping the
embedded year out of a yearbook/assembly-program/daily-text title). This
was verified to work across English/German/Spanish/French even though the
year appears in a different position in each language's grammar (start/
middle/end) — see `_strip_year`'s tests if you touch that regex. Only
`books` and `tracts` (no single representative title to derive from) fall
back to a small static translation table with an English default, so it
degrades gracefully for languages we haven't hand-translated rather than
breaking.

### `opds.py` output tree

```
index.xml                          root nav (one entry per language)
<locale>/index.xml                 language nav (one entry per category + "New")
<locale>/new.xml                   recently-updated, any category
<locale>/bible.xml, books.xml, tracts.xml     flat acquisition feeds
<locale>/<periodical>.xml          front page: latest year + archive link
<locale>/<periodical>/index.xml    archive nav (one entry per year)
<locale>/<periodical>/<year>.xml   that year's issues
```

Any acquisition feed exceeding `PAGE_SIZE` (50) entries is automatically
split into `-2.xml`, `-3.xml`, ... with `rel="next"`/`"previous"` — this
exists because of a real bug (see below), not speculative future-proofing.

### `sync.py` / `state.py`

`sync.py` is the orchestrator: enumerate candidates → probe each via a
`ThreadPoolExecutor` (size = `concurrency`) → write results to
`cache/state.sqlite3` → regenerate the whole `opds.py` feed tree from that
DB. `state.sqlite3` **is** committed to git (small, holds per-entry
`last_checked` timestamps so `recheck_days` can skip re-probing everything
on every run) — unlike `catalog.db`, which never is.

### `epub-proxy/` (optional, separate deployable)

Not part of the main `jw2opds` Python package or its GitHub Actions
workflow -- a standalone Flask service (own `Dockerfile`/`requirements.txt`)
you self-host if you want it. `GET /optimize/<device>.epub?src=<url>` fetches
`src` (must be on `ALLOWED_SOURCE_HOSTS`, default `jw-cdn.org`, so it can't be
used as a general open proxy), runs it through `optimizer/` -- vendored
unmodified from `crosspoint-reader/calibre-plugins` (MIT, see
`THIRD_PARTY_LICENSES.md`) -- caches the result keyed by
`sha256(device|src)`, and serves it. `jw2opds`'s own `opds.py` links to it
(an *additional* acquisition link per entry, alongside the original) only
when `config.yaml`'s `epub_proxy.base_url` is set; empty (the default)
means no proxy links are generated at all.

`app.py` treats the fetched source as untrusted input (it's fetched from
jw.org's own CDN, but the endpoint itself is public and unauthenticated, so
harden at the boundary anyway): re-validates the allowlist against the
*final* URL after redirects (not just the requested one -- `requests`
follows redirects by default without re-checking), rejects sources over
`MAX_DOWNLOAD_BYTES`/`MAX_UNCOMPRESSED_BYTES` before ever handing them to
the optimizer (zip-bomb protection), and maps failures to proper 4xx/502
instead of leaking a generic 500. Rate limiting is deliberately NOT in the
app (see `nginx.conf.example`) -- an in-process limiter's state doesn't
carry across gunicorn's multiple worker processes (confirmed by testing:
35 rapid requests split across 2 workers, each individually under a
30/minute cap, produced zero 429s), so it silently under-enforces exactly
when it matters. Don't re-add an in-app limiter without a shared backing
store (Redis, etc.) that's actually synchronized across workers.

## Hard-won lessons (don't rediscover these)

- **CrossPoint Reader (and likely other minimal/embedded OPDS clients) caps
  entries per feed at 62** (confirmed by reading its source). This is *why*
  pagination (`PAGE_SIZE=50`) exists in `opds.py` — it's not defensive
  overengineering, a real feed (`books.xml`) actually broke a real client.
- **RFC 4287 4.1.2**: an `<entry>` with no `<content>` MUST have a
  `rel="alternate"` link, or strict Atom parsers reject it. Caught by the
  W3C Feed Validator, not by XML well-formedness checks. `_entry_element`
  in `opds.py` adds this alongside the acquisition link.
- **The "New" feed must also be a plain `<entry>`**, not just a
  `rel="http://opds-spec.org/sort/new"` link on the nav feed — minimal
  clients that only render entries never surfaced it otherwise.
- **GitHub Pages always serves `.xml` as generic `application/xml`**,
  ignoring whatever `type=` we declare inside the feed (no custom-header
  support on Pages). Don't assume the declared type reaches the client.
- **`config.yaml`'s `base_url` must stay set (absolute links), not empty.**
  CrossPoint Reader's `UrlUtils::buildUrl()` never strips the trailing
  filename off its current URL before appending a relative reference, so
  every navigation hop beyond the first accumulates a bogus path segment
  (`.../de/index.xml/bible.xml` instead of `.../de/bible.xml`) and the
  device reports "Failed to fetch feed". This was confirmed by reading
  CrossPoint's actual source, not guessed. Relative links are perfectly
  valid per RFC 3986 and were tried first (see git history) specifically
  for local-testing convenience -- don't revert to them without fixing this
  client-side bug upstream first, or you'll reintroduce this breakage.
- **CrossPoint Reader's EPUB renderer is severely constrained** (~380KB RAM):
  JPEG/PNG only (no GIF/WebP/SVG, no embedded fonts at all), a hand-rolled
  CSS subset (~19 properties, no descendant selectors/media queries), and a
  single `<p>` over ~6KB or a spine file over ~9.5KB can crash the device.
  `epub-proxy/`'s vendored optimizer handles all of this -- don't
  reimplement any of it from scratch, and don't assume a "valid EPUB" is
  automatically a "renders fine on CrossPoint" EPUB.
- **In `epub-proxy/app.py`, the temp working directory for a request MUST be
  created inside `CACHE_DIR`** (`tempfile.TemporaryDirectory(dir=CACHE_DIR)`),
  not the default `/tmp`. Docker mounts `CACHE_DIR` as a separate volume, and
  the final `os.replace()` from a scratch file into the cache is an atomic
  rename that only works within the same filesystem -- across the volume
  boundary it raises `OSError: Invalid cross-device link`, discovered by
  actually running the container, not by reading the code.
- **This repo has its own git identity** (`git config user.name/email`,
  repo-local, not global) — `ca-za <carlo.speranza@gmail.com>` — set
  deliberately after an earlier mistake where commits picked up the
  machine's global (work) identity and GitHub attributed them to an
  unrelated account. Don't remove this local config.
- GitHub's **Contributors sidebar is a separately cached index** that does
  not reliably refresh after a force-push/history rewrite, even after the
  documented ~24h. The community-verified fix is renaming the default
  branch twice (forces a reindex), not waiting longer or re-pushing.

## Deployment

`.github/workflows/sync.yml`: runs on a daily `schedule` + manual
`workflow_dispatch`, commits the updated `cache/state.sqlite3` back to the
repo, and deploys `output/` to GitHub Pages via
`actions/upload-pages-artifact` + `actions/deploy-pages` (no git-history
bloat from the generated feeds themselves). Requires the repo to be
**public** (GitHub Pages needs a paid plan for private repos) and
Settings → Pages → Source set to **GitHub Actions** (one-time manual step,
not automatable from here). Live at `config.yaml`'s `base_url`.

`config.yaml` currently covers the top 20 languages by total speakers
(`languages:` list) with `periodical_lookback_months: 0` (jw.org's entire
known history per periodical, not just recent years — safe because
pre-EPUB-era issues just get a quick cached "not available", it's a
one-time cost, not a per-run one) and `concurrency: 16`.
