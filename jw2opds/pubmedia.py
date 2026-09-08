"""Client for jw.org's public GETPUBMEDIALINKS API, used to resolve a
(publication key, issue, language) triple to its actual EPUB (and, if jw.org
offers one, PDF) download link.

Note: this API's "pubImage" field is always empty for these renditions, so
cover art is resolved separately -- see coverart.py.
"""
from __future__ import annotations

import dataclasses
import html
import logging

import requests

PUB_MEDIA_URL = "https://app.jw-cdn.org/apis/pub-media/GETPUBMEDIALINKS"

log = logging.getLogger(__name__)


@dataclasses.dataclass
class EpubMedia:
    pub_key: str
    issue: str  # "" for non-periodicals
    lang_code: str
    title: str
    url: str
    checksum: str
    filesize: int
    modified_datetime: str  # as reported by jw.org, "YYYY-MM-DD HH:MM:SS"
    pdf_url: str = ""
    pdf_checksum: str = ""
    pdf_filesize: int = 0


def _file_entry(data: dict, lang_code: str, fileformat: str):
    files = data.get("files", {}).get(lang_code, {}).get(fileformat)
    return files[0] if files else None


def fetch_epub_media(
    session: requests.Session,
    lang_code: str,
    pub_key: str,
    issue: str = "",
    timeout: int = 15,
) -> EpubMedia | None:
    """Return EpubMedia if this publication has an EPUB rendition in this
    language, otherwise None. Never raises for "not available" responses
    (jw.org returns HTTP 400/404 for those). A PDF rendition is included in
    the same request/response and attached if jw.org happens to offer one;
    it's never required for the publication to count as "available"."""
    params = {
        "langwritten": lang_code,
        "pub": pub_key,
        "fileformat": "EPUB,PDF",
        "output": "json",
    }
    if issue:
        params["issue"] = issue

    try:
        resp = session.get(PUB_MEDIA_URL, params=params, timeout=timeout)
    except requests.RequestException as e:
        log.warning("Request failed for pub=%s issue=%s lang=%s: %s", pub_key, issue, lang_code, e)
        return None

    if resp.status_code in (400, 404):
        return None
    resp.raise_for_status()

    data = resp.json()
    epub_entry = _file_entry(data, lang_code, "EPUB")
    if epub_entry is None:
        return None
    epub_file = epub_entry.get("file", {})

    title = html.unescape(data.get("pubName") or epub_entry.get("title") or pub_key)
    formatted_date = html.unescape(data.get("formattedDate") or "")
    if formatted_date:
        title = f"{title} — {formatted_date}"

    media = EpubMedia(
        pub_key=pub_key,
        issue=issue,
        lang_code=lang_code,
        title=title,
        url=epub_file.get("url", ""),
        checksum=epub_file.get("checksum", "") or "",
        filesize=int(epub_entry.get("filesize") or 0),
        modified_datetime=epub_file.get("modifiedDatetime", "") or "",
    )

    pdf_entry = _file_entry(data, lang_code, "PDF")
    if pdf_entry is not None:
        pdf_file = pdf_entry.get("file", {})
        media.pdf_url = pdf_file.get("url", "")
        media.pdf_checksum = pdf_file.get("checksum", "") or ""
        media.pdf_filesize = int(pdf_entry.get("filesize") or 0)

    return media
