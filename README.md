# jw2opds

> **Unofficial, third-party project.** Not created by, affiliated with,
> endorsed by, or connected in any way to jw.org, Jehovah's Witnesses, or the
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

## Automated hosting on GitHub Pages

`.github/workflows/sync.yml` runs the sync on a daily schedule (and on
manual trigger), commits the updated `cache/state.sqlite3` index back to the
repo, and deploys the freshly generated `output/` directory to GitHub Pages.
The jw.org catalog database itself (`cache/jw_catalog.db`, ~200 MB) is
intentionally *not* committed — it's re-downloaded fresh on every run
instead, which is quick.

One-time setup after pushing this repo to GitHub:

1. Repo Settings → **Pages** → Source: **GitHub Actions**.
2. Run the "Sync catalog and deploy to GitHub Pages" workflow once
   (Actions tab → Run workflow), or just wait for the daily schedule.
3. Your OPDS root feed will be live at
   `https://<username>.github.io/<repo>/index.xml`.

This works on the free GitHub plan as long as the repository is **public**:
GitHub Actions minutes are unmetered for public repos, and GitHub Pages
requires a paid plan only for *private* repos. Repo content here is just
generated link-index XML plus this project's own code — no publication
content is stored in the repo.

Note: GitHub automatically disables a public repo's scheduled workflows
after 60 days with no repository activity. Since this workflow commits an
update on every run, it keeps itself alive as long as it's actually running;
it would only need manual re-enabling (Actions tab) if the schedule had
somehow stopped firing for over two months.

## Output layout

```
output/
  index.xml                        root navigation feed (one entry per language)
  <locale>/index.xml                per-language navigation feed (one entry per category)
  <locale>/new.xml                  recently updated publications, any category
  <locale>/bible.xml                flat acquisition feed
  <locale>/books.xml                flat acquisition feed (everything not otherwise categorized)
  <locale>/tracts.xml               flat acquisition feed
  <locale>/watchtower.xml           latest year's issues + link into the archive
  <locale>/watchtower/index.xml     archive: one entry per year
  <locale>/watchtower/<year>.xml    all issues from that year
  <locale>/awake.xml                (same per-year archive pattern)
  <locale>/awake/...
  <locale>/meeting-workbook.xml     (same per-year archive pattern)
  <locale>/meeting-workbook/...
  <locale>/daily-text.xml           (same per-year archive pattern)
  <locale>/daily-text/...
  <locale>/yearbook.xml             (same per-year archive pattern)
  <locale>/yearbook/...
  <locale>/assembly-programs.xml    (same per-year archive pattern; convention +
  <locale>/assembly-programs/...     circuit assembly programs)
```

Categories: Bible, Watchtower, Awake!, Meeting Workbook, Daily Text,
Yearbooks, and Convention & Assembly Programs are split off automatically
(the latter two via jw.org's own `PublicationAttribute` tags -- "Yearbook",
"Convention", "Circuit Assembly" -- rather than guessing from publication
codes). Tracts and everything else land in Books & Brochures.

Bible, Books & Brochures, and Tracts are flat lists. The rest accumulate
dated issues/editions over time, so each gets its own front page (latest
year only) plus a year-by-year archive, rather than one ever-growing feed.
Any acquisition feed that still grows past 50 entries (the flat ones, or a
single year with unusually many editions) is automatically split into
`-2.xml`, `-3.xml`, ... pages linked with `rel="next"`/`"previous"`, so no
single feed file risks exceeding a constrained OPDS client's entry limit.

Category *labels* are derived straight from jw.org's own localized
publication titles wherever one exists to derive them from (e.g. stripping
the year off "Kongressprogramm 2019" gives "Kongressprogramm", regardless of
where in the title jw.org's own translators placed the year) -- so they're
correct in any language jw.org supports, not just the handful of languages
this project could itself translate "Books & Brochures"/"Tracts" into
(falls back to English there).

Every `<entry>` acquisition link (`rel="http://opds-spec.org/acquisition"`,
plus a matching `rel="alternate"` per RFC 4287 4.1.2) points directly at the
EPUB file on `jw-cdn.org`; cover/thumbnail links
(`rel="http://opds-spec.org/image"` / `.../image/thumbnail`) do the same for
jw.org's image CDN, when cover art was found for that publication.

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
