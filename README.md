# jw2opds

> **Unofficial, third-party project.** Not created by, affiliated with,
> endorsed by, or connected in any way to jw.org or the
> Watch Tower Bible and Tract Society. See [Disclaimer](#disclaimer) below.

Generates a static [OPDS](https://en.wikipedia.org/wiki/Open_Publication_Distribution_System)
catalog for jw.org's EPUB publications (Bible, books, brochures, Watchtower,
Awake!, meeting workbook, daily text), so you can browse and download them
straight from an e-reader that supports OPDS (KOReader, PocketBook, Moon+
Reader, CrossPoint Reader etc.).

**Live catalog (this repo's own deployment):**
`https://ca-za.github.io/jwOPDS/` — add this URL as an OPDS catalog
in your e-reader without user credentials

## Disclaimer

**This project was not created by, and is not affiliated with, endorsed by,
or connected in any way to jw.org or the Watch Tower
Bible and Tract Society.** It is an independent, unofficial tool built by a
third party.

`jw2opds` does not host, store, mirror, or redistribute any content. It does
not provide EPUB files, cover images, or any other publication data itself.
All it does is generate small XML index files (OPDS feeds) whose links point
directly to publications that jw.org already makes freely and publicly
available on its own servers (`jw-cdn.org`). In other words: this is a link
list, not a content host. Every file an e-reader downloads through this
catalog comes straight from jw.org, under whatever terms jw.org itself
publishes it — this project has no control over, and makes no claims about,
that content or its availability.

## How it works

1. jw.org publishes a versioned SQLite database of every known publication
   (across all languages): `catalog.db`. This gives us the full list of
   publication codes and, for periodicals (Watchtower, Awake!, meeting
   workbook), every known issue number — but not download links or a usable
   language mapping.
2. For each configured language and each candidate publication (by default,
   the catalog's *entire* known history per periodical, not just recent
   years — see `periodical_lookback_months`), jw.org's public
   `GETPUBMEDIALINKS` API is queried for an EPUB rendition. Most
   publications don't exist in every language, as EPUB, or that far back;
   those are simply skipped and cached as "not available" so they aren't
   re-probed on every run.
3. Cover art is resolved separately (`GETPUBMEDIALINKS` never returns any)
   by probing jw.org's image CDN for the publication's cover, best-effort.
4. Confirmed publications (title, EPUB URL, checksum, file size, last
   modified date, cover image if any) are recorded in a local SQLite index
   (`cache/state.sqlite3`) so future runs only re-check what's actually due
   for a re-check (see `recheck_days`).
5. An OPDS feed tree is written to `output_dir`. Every acquisition link in it
   points directly at jw.org's own CDN — nothing is downloaded or re-hosted
   by this project, only the small XML feeds need to be served somewhere.

## Setup (local / manual)

```
pip install -r requirements.txt
cp config.example.yaml config.yaml
# edit config.yaml: set your language(s), e.g. languages: [de]
python -m jw2opds --config config.yaml
```

Then serve `output_dir` with any static web server, e.g.:

```
python -m http.server 8080 --directory output
```

...and point your e-reader's OPDS catalog to `http://<host>:8080/index.xml`.

For regular updates (new magazine issues etc.), run the sync periodically,
e.g. via cron:

```
0 6 * * * cd /path/to/jw2opds && python3 -m jw2opds --config config.yaml >> sync.log 2>&1
```

## CLI

```
python -m jw2opds --config config.yaml [--lang de] [--lang en] [--force] [-v]
```

- `--lang` — sync only these languages (locale like `de` or jw.org code like
  `X`), overriding `languages` in the config. Repeatable.
- `--force` — ignore the `recheck_days` cache and re-probe every candidate.
- `-v` — verbose/debug logging.

## Configuration

See `config.example.yaml` for every option with comments. Key ones:

- `languages` — ISO locales (`de`, `en`, ...) or jw.org codes (`X`, `E`, ...).
- `periodical_keys` — which recurring periodicals to check for issues.
- `periodical_lookback_months` — how far back to check for periodical issues
  (`0` = full known history; older, pre-EPUB-era issues are just quickly
  marked "not available" and cached, so this is a one-time cost).
- `recheck_days` — minimum days between re-checking an already-confirmed,
  non-periodical publication (books, Bible translations).
- `base_url` — set only if you want absolute links in the feeds; otherwise
  links are relative and resolve fine against whatever URL the reader used
  to fetch the feed (including a GitHub Pages URL).
