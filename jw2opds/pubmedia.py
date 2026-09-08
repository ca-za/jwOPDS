"""Client for jw.org's public GETPUBMEDIALINKS API, used to resolve a
(publication key, issue, language) triple to an actual EPUB download link,
if one exists.

Note: this API's "pubImage" field is always empty for EPUB renditions, so
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


def fetch_epub_media(
    session: requests.Session,
    lang_code: str,
    pub_key: str,
    issue: str = "",
    timeout: int = 15,
) -> EpubMedia | None:
    """Return EpubMedia if this publication has an EPUB rendition in this
    language, otherwise None. Never raises for "not available" responses
    (jw.org returns HTTP 400/404 for those)."""
    params = {
        "langwritten": lang_code,
        "pub": pub_key,
        "fileformat": "EPUB",
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
    files = data.get("files", {}).get(lang_code, {}).get("EPUB")
    if not files:
        return None
    entry = files[0]
    file_info = entry.get("file", {})

    title = html.unescape(data.get("pubName") or entry.get("title") or pub_key)
    formatted_date = html.unescape(data.get("formattedDate") or "")
    if formatted_date:
        title = f"{title} — {formatted_date}"

    return EpubMedia(
        pub_key=pub_key,
        issue=issue,
        lang_code=lang_code,
        title=title,
        url=file_info.get("url", ""),
        checksum=file_info.get("checksum", "") or "",
        filesize=int(entry.get("filesize") or 0),
        modified_datetime=file_info.get("modifiedDatetime", "") or "",
    )
