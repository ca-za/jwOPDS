"""Generate a static OPDS 1.2 (Atom-based) catalog tree.

Layout under output_dir:
    index.xml                          root navigation feed (one entry per language)
    <locale>/index.xml                 per-language navigation feed (one entry per category)
    <locale>/new.xml                   per-language acquisition feed, recently updated items
    <locale>/bible.xml                 flat acquisition feed
    <locale>/books.xml                 flat acquisition feed
    <locale>/<periodical>.xml          front page: latest year's issues + archive link
    <locale>/<periodical>/index.xml    archive navigation feed (one entry per year)
    <locale>/<periodical>/<year>.xml   acquisition feed for that year's issues

All acquisition links point directly at jw.org's own CDN -- nothing is
downloaded or re-hosted.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from .categorize import CATEGORY_ORDER, PERIODICAL_CATEGORIES, archive_label, display_title

ATOM_NS = "http://www.w3.org/2005/Atom"
NAV_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
EPUB_TYPE = "application/epub+zip"
PDF_TYPE = "application/pdf"

# Some OPDS clients (e.g. CrossPoint Reader's fixed-size entry buffer) cap
# how many <entry> elements they'll parse from a single feed. Keep every
# acquisition feed comfortably under such limits by paginating with
# rel="next"/"previous" (RFC 5005) instead of growing one file forever.
PAGE_SIZE = 50

ET.register_namespace("", ATOM_NS)


def _el(tag, **attrs):
    e = ET.Element(f"{{{ATOM_NS}}}{tag}")
    for k, v in attrs.items():
        e.set(k, v)
    return e


def _sub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, f"{{{ATOM_NS}}}{tag}")
    for k, v in attrs.items():
        e.set(k, v)
    if text is not None:
        e.text = text
    return e


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso(modified_datetime: str) -> str:
    if not modified_datetime:
        return _now_iso()
    try:
        dt = datetime.datetime.strptime(modified_datetime, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return _now_iso()


def _href(base_url: str, relative: str) -> str:
    if not base_url:
        return relative
    return base_url.rstrip("/") + "/" + relative.lstrip("/")


def _write(feed: ET.Element, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(feed)
    ET.indent(tree, space="  ")
    tree.write(dest, encoding="utf-8", xml_declaration=True)


def _base_feed(title: str, feed_id: str, self_href: str, self_type: str, base_url: str) -> ET.Element:
    feed = _el("feed")
    _sub(feed, "id", feed_id)
    _sub(feed, "title", title)
    _sub(feed, "updated", _now_iso())
    _sub(_sub(feed, "author"), "name", "jw2opds")
    # link rel=self
    _sub(feed, "link", rel="self", href=_href(base_url, self_href), type=self_type)
    return feed


def write_root_feed(output_dir: Path, base_url: str, languages) -> None:
    feed = _base_feed(
        "JW.ORG Library", "urn:jw2opds:root", "index.xml", NAV_TYPE, base_url
    )
    _sub(feed, "link", rel="start", href=_href(base_url, "index.xml"), type=NAV_TYPE)
    for lang in languages:
        entry = _sub(feed, "entry")
        _sub(entry, "title", lang.vernacular or lang.name)
        _sub(entry, "id", f"urn:jw2opds:lang:{lang.locale or lang.code}")
        _sub(entry, "updated", _now_iso())
        _sub(
            entry,
            "link",
            rel="subsection",
            href=_href(base_url, f"{lang.locale or lang.code}/index.xml"),
            type=NAV_TYPE,
        )
    _write(feed, output_dir / "index.xml")


def write_language_feed(output_dir: Path, base_url: str, lang, by_category: dict) -> None:
    locale = lang.locale or lang.code
    feed = _base_feed(
        lang.vernacular or lang.name,
        f"urn:jw2opds:lang:{locale}",
        f"{locale}/index.xml",
        NAV_TYPE,
        base_url,
    )
    _sub(feed, "link", rel="start", href=_href(base_url, "index.xml"), type=NAV_TYPE)
    _sub(
        feed,
        "link",
        rel="http://opds-spec.org/sort/new",
        href=_href(base_url, f"{locale}/new.xml"),
        type=ACQ_TYPE,
    )
    for category in CATEGORY_ORDER:
        if category not in by_category:
            continue
        entry = _sub(feed, "entry")
        title = display_title(category, locale, by_category[category])
        _sub(entry, "title", title)
        _sub(entry, "id", f"urn:jw2opds:cat:{locale}:{category}")
        _sub(entry, "updated", _now_iso())
        _sub(
            entry,
            "link",
            rel="subsection",
            href=_href(base_url, f"{locale}/{category}.xml"),
            type=ACQ_TYPE,
        )
    _write(feed, output_dir / locale / "index.xml")


def _entry_element(feed: ET.Element, locale: str, base_url: str, row: dict) -> None:
    entry = _sub(feed, "entry")
    _sub(entry, "title", row["title"] or row["pub_key"])
    _sub(entry, "id", f"urn:jw2opds:{locale}:{row['pub_key']}:{row['issue']}")
    _sub(entry, "updated", _to_iso(row["modified_datetime"]))
    author = _sub(entry, "author")
    _sub(author, "name", "Watch Tower Bible and Tract Society")
    if row.get("epub_url"):
        _sub(
            entry,
            "link",
            rel="http://opds-spec.org/acquisition",
            href=row["epub_url"],
            type=EPUB_TYPE,
            length=str(row.get("filesize") or 0),
        )
        # RFC 4287 4.1.2: an entry with no atom:content MUST have a
        # rel="alternate" link, or strict Atom parsers reject the entry.
        _sub(entry, "link", rel="alternate", href=row["epub_url"], type=EPUB_TYPE)
    if row.get("pdf_url"):
        _sub(
            entry,
            "link",
            rel="http://opds-spec.org/acquisition",
            href=row["pdf_url"],
            type=PDF_TYPE,
            length=str(row.get("pdf_filesize") or 0),
        )
    if row.get("cover_url"):
        cover_type = _image_type(row["cover_url"])
        _sub(
            entry,
            "link",
            rel="http://opds-spec.org/image",
            href=row["cover_url"],
            type=cover_type,
        )
    if row.get("thumbnail_url"):
        _sub(
            entry,
            "link",
            rel="http://opds-spec.org/image/thumbnail",
            href=row["thumbnail_url"],
            type=_image_type(row["thumbnail_url"]),
        )


def _image_type(url: str) -> str:
    return "image/png" if Path(url).suffix.lower() == ".png" else "image/jpeg"


def write_category_feed(output_dir: Path, base_url: str, lang, category: str, rows: list[dict]) -> None:
    if category in PERIODICAL_CATEGORIES:
        _write_periodical_category_feed(output_dir, base_url, lang, category, rows)
    else:
        _write_flat_category_feed(output_dir, base_url, lang, category, rows)


def _page_path(path_stem: str, page_index: int) -> str:
    return f"{path_stem}.xml" if page_index == 0 else f"{path_stem}-{page_index + 1}.xml"


def _write_paginated_acquisition(
    output_dir: Path,
    base_url: str,
    locale: str,
    feed_id_base: str,
    title: str,
    path_stem: str,
    up_href: str,
    rows: list[dict],
) -> None:
    """Write `rows` as one or more acquisition feed pages of at most
    PAGE_SIZE entries each, chained with rel="next"/"previous". Page 1 keeps
    the original, stable filename (path_stem + ".xml") so existing links
    into it never break as a category grows past one page."""
    pages = [rows[i:i + PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)] or [[]]
    total = len(pages)
    for index, page_rows in enumerate(pages):
        self_path = _page_path(path_stem, index)
        feed_id = feed_id_base if total == 1 else f"{feed_id_base}:page{index + 1}"
        feed = _base_feed(title, feed_id, self_path, ACQ_TYPE, base_url)
        _sub(feed, "link", rel="start", href=_href(base_url, "index.xml"), type=NAV_TYPE)
        _sub(feed, "link", rel="up", href=_href(base_url, up_href), type=NAV_TYPE)
        if index > 0:
            _sub(
                feed, "link", rel="previous",
                href=_href(base_url, _page_path(path_stem, index - 1)), type=ACQ_TYPE,
            )
        if index < total - 1:
            _sub(
                feed, "link", rel="next",
                href=_href(base_url, _page_path(path_stem, index + 1)), type=ACQ_TYPE,
            )
        for row in page_rows:
            _entry_element(feed, locale, base_url, row)
        _write(feed, output_dir / self_path)


def _write_flat_category_feed(output_dir: Path, base_url: str, lang, category: str, rows: list[dict]) -> None:
    locale = lang.locale or lang.code
    title = display_title(category, locale, rows)
    _write_paginated_acquisition(
        output_dir, base_url, locale,
        feed_id_base=f"urn:jw2opds:cat:{locale}:{category}",
        title=title,
        path_stem=f"{locale}/{category}",
        up_href=f"{locale}/index.xml",
        rows=rows,
    )


_TRAILING_YEAR_RE = re.compile(r"(\d{2})$")


def _year_of(row: dict) -> str:
    """jw.org periodicals carry the date in `issue` (e.g. "20260900" -> 2026).
    Non-periodical yearly publications like the daily text ("es24") don't
    have an issue at all, so fall back to the year encoded in the pub key."""
    issue = row.get("issue") or ""
    if len(issue) >= 4:
        return issue[:4]
    m = _TRAILING_YEAR_RE.search(row.get("pub_key") or "")
    return f"20{m.group(1)}" if m else "0000"


def _write_periodical_category_feed(
    output_dir: Path, base_url: str, lang, category: str, rows: list[dict]
) -> None:
    """Recurring publications (Watchtower, Awake!, meeting workbook) get one
    acquisition feed per year under <locale>/<category>/<year>.xml, reachable
    via a per-category archive navigation feed. The category's own front page
    (<locale>/<category>.xml) shows only the latest year's issues directly,
    plus a link into the archive for everything older."""
    locale = lang.locale or lang.code
    title = display_title(category, locale, rows)

    by_year: dict[str, list[dict]] = {}
    for row in rows:
        by_year.setdefault(_year_of(row), []).append(row)
    for year_rows in by_year.values():
        year_rows.sort(key=lambda r: r.get("issue") or "", reverse=True)
    years = sorted(by_year.keys(), reverse=True)

    for year in years:
        _write_periodical_year_feed(output_dir, base_url, lang, category, title, year, by_year[year])

    if years:
        _write_periodical_archive_index(output_dir, base_url, lang, category, title, years)

    # Front page: latest year's issues directly, archive link if there's more.
    feed = _base_feed(
        title,
        f"urn:jw2opds:cat:{locale}:{category}",
        f"{locale}/{category}.xml",
        ACQ_TYPE,
        base_url,
    )
    _sub(feed, "link", rel="start", href=_href(base_url, "index.xml"), type=NAV_TYPE)
    _sub(feed, "link", rel="up", href=_href(base_url, f"{locale}/index.xml"), type=NAV_TYPE)
    if years:
        # Leave room for the archive-link entry below; the full year is
        # always available via <category>/<year>.xml regardless.
        for row in by_year[years[0]][:PAGE_SIZE - 1]:
            _entry_element(feed, locale, base_url, row)
        if len(years) > 1:
            entry = _sub(feed, "entry")
            _sub(entry, "title", archive_label(locale))
            _sub(entry, "id", f"urn:jw2opds:archive:{locale}:{category}")
            _sub(entry, "updated", _now_iso())
            _sub(
                entry,
                "link",
                rel="subsection",
                href=_href(base_url, f"{locale}/{category}/index.xml"),
                type=NAV_TYPE,
            )
    _write(feed, output_dir / locale / f"{category}.xml")


def _write_periodical_archive_index(
    output_dir: Path, base_url: str, lang, category: str, base_title: str, years: list[str]
) -> None:
    locale = lang.locale or lang.code
    title = f"{base_title} — {archive_label(locale)}"
    feed = _base_feed(
        title,
        f"urn:jw2opds:archive:{locale}:{category}",
        f"{locale}/{category}/index.xml",
        NAV_TYPE,
        base_url,
    )
    _sub(feed, "link", rel="start", href=_href(base_url, "index.xml"), type=NAV_TYPE)
    _sub(feed, "link", rel="up", href=_href(base_url, f"{locale}/{category}.xml"), type=ACQ_TYPE)
    for year in years:
        entry = _sub(feed, "entry")
        _sub(entry, "title", year)
        _sub(entry, "id", f"urn:jw2opds:cat:{locale}:{category}:{year}")
        _sub(entry, "updated", _now_iso())
        _sub(
            entry,
            "link",
            rel="subsection",
            href=_href(base_url, f"{locale}/{category}/{year}.xml"),
            type=ACQ_TYPE,
        )
    _write(feed, output_dir / locale / category / "index.xml")


def _write_periodical_year_feed(
    output_dir: Path, base_url: str, lang, category: str, base_title: str, year: str, rows: list[dict]
) -> None:
    locale = lang.locale or lang.code
    title = f"{base_title} {year}"
    _write_paginated_acquisition(
        output_dir, base_url, locale,
        feed_id_base=f"urn:jw2opds:cat:{locale}:{category}:{year}",
        title=title,
        path_stem=f"{locale}/{category}/{year}",
        up_href=f"{locale}/{category}/index.xml",
        rows=rows,
    )


def write_new_feed(output_dir: Path, base_url: str, lang, rows: list[dict], limit: int = 50) -> None:
    locale = lang.locale or lang.code
    feed = _base_feed(
        f"New — {lang.vernacular or lang.name}",
        f"urn:jw2opds:new:{locale}",
        f"{locale}/new.xml",
        ACQ_TYPE,
        base_url,
    )
    _sub(feed, "link", rel="start", href=_href(base_url, "index.xml"), type=NAV_TYPE)
    _sub(feed, "link", rel="up", href=_href(base_url, f"{locale}/index.xml"), type=NAV_TYPE)
    sorted_rows = sorted(rows, key=lambda r: r["modified_datetime"] or "", reverse=True)
    for row in sorted_rows[:limit]:
        _entry_element(feed, locale, base_url, row)
    _write(feed, output_dir / locale / "new.xml")
