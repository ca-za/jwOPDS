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
