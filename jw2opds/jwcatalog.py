"""Download and query jw.org's publication catalog database.

jw.org publishes a versioned SQLite database listing every known publication
(across all languages) at:

    https://app.jw-cdn.org/catalogs/publications/v4/manifest.json
      -> {"version": 1, "current": "<uuid>"}
    https://app.jw-cdn.org/catalogs/publications/v4/<uuid>/catalog.db.gz

This database does NOT contain download links (those come from the
GETPUBMEDIALINKS API, see pubmedia.py) and its language ids are internal
MEPS ids with no public mapping to the "langwritten" codes used elsewhere.
Its value here is purely as an authoritative, deduplicated list of every
publication KeySymbol (and, for periodicals, every IssueTagNumber) that
exists in *any* language -- the candidate list we then probe per-language
via GETPUBMEDIALINKS.
"""
from __future__ import annotations

import datetime
import gzip
import json
import logging
import sqlite3
from pathlib import Path

import requests

MANIFEST_URL = "https://app.jw-cdn.org/catalogs/publications/v4/manifest.json"
CATALOG_URL_TMPL = "https://app.jw-cdn.org/catalogs/publications/v4/{uuid}/catalog.db.gz"

log = logging.getLogger(__name__)


def ensure_catalog_db(
    session: requests.Session,
    manifest_path: Path,
    db_path: Path,
    timeout: int = 15,
) -> Path:
    """Download the jw.org catalog database if it's new or missing. Returns db_path."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    resp = session.get(MANIFEST_URL, timeout=timeout)
    resp.raise_for_status()
    manifest = resp.json()
    current_uuid = manifest["current"]

    previous_uuid = None
    if manifest_path.exists():
        try:
            previous_uuid = json.loads(manifest_path.read_text())["current"]
        except (json.JSONDecodeError, KeyError):
            previous_uuid = None

    if db_path.exists() and previous_uuid == current_uuid:
        log.info("jw.org publication catalog unchanged (uuid=%s)", current_uuid)
        return db_path

    log.info("Downloading jw.org publication catalog (uuid=%s)...", current_uuid)
    url = CATALOG_URL_TMPL.format(uuid=current_uuid)
    resp = session.get(url, timeout=max(timeout, 120), stream=True)
    resp.raise_for_status()
    gz_path = db_path.with_suffix(db_path.suffix + ".gz")
    with open(gz_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)

    with gzip.open(gz_path, "rb") as src, open(db_path, "wb") as dst:
        dst.write(src.read())
    gz_path.unlink(missing_ok=True)

    manifest_path.write_text(json.dumps(manifest))
    log.info("Catalog database updated: %s", db_path)
    return db_path


def list_book_keys(db_path: Path) -> list[str]:
    """Every non-periodical (IssueTagNumber=0) publication KeySymbol, e.g. Bible
    translations, books, brochures. Deduplicated across all languages."""
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT DISTINCT KeySymbol FROM Publication "
            "WHERE IssueTagNumber = 0 AND Reserved = 0 "
            "ORDER BY KeySymbol"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def list_periodical_issues(
    db_path: Path, key_symbol: str, lookback_months: int = 0
) -> list[str]:
    """Every known IssueTagNumber for a periodical KeySymbol, newest first.

    IssueTagNumber for periodicals is a date-like integer, e.g. 20240100.
    lookback_months=0 means no limit (full known history).
    """
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT DISTINCT IssueTagNumber FROM Publication "
            "WHERE KeySymbol = ? AND IssueTagNumber != 0 AND Reserved = 0 "
            "ORDER BY IssueTagNumber DESC",
            (key_symbol,),
        ).fetchall()
        issues = [str(r[0]) for r in rows]
    finally:
        con.close()

    if lookback_months and lookback_months > 0 and issues:
        cutoff = _months_ago_tag(lookback_months)
        issues = [i for i in issues if i >= cutoff]
    return issues


def _months_ago_tag(months: int) -> str:
    today = datetime.date.today()
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}{month:02d}00"
