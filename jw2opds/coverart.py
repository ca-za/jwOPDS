"""Best-effort cover art discovery.

GETPUBMEDIALINKS never returns usable cover art (see pubmedia.py). jw.org's
own web pages, however, load publication covers from a predictable path on
its image CDN:

    books/Bible:  https://cms-imgp.jw-cdn.org/img/p/<pub>/<lang>/pt/<pub>_<lang>_<size>.jpg
    periodicals:  https://cms-imgp.jw-cdn.org/img/p/<pub>/<issue>/<lang>/pt/<pub>_<lang>_<issue>_<size>.jpg

There's no API to confirm a given publication actually has cover art, so we
just probe for the "lg" size with a HEAD request and treat a non-200 as "no
cover" (some specialty/reserved publications genuinely have none).
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

IMAGE_HOST = "https://cms-imgp.jw-cdn.org/img/p"


def _base_url(pub_key: str, lang_code: str, issue: str) -> str:
    if issue:
        return f"{IMAGE_HOST}/{pub_key}/{issue}/{lang_code}/pt/{pub_key}_{lang_code}_{issue}"
    return f"{IMAGE_HOST}/{pub_key}/{lang_code}/pt/{pub_key}_{lang_code}"


def find_cover(
    session: requests.Session,
    pub_key: str,
    lang_code: str,
    issue: str = "",
    timeout: int = 10,
):
    """Return (cover_url, thumbnail_url) if cover art exists, else (None, None)."""
    base = _base_url(pub_key, lang_code, issue)
    cover_url = f"{base}_lg.jpg"
    try:
        resp = session.head(cover_url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        log.debug("Cover probe failed for %s/%s: %s", pub_key, issue, e)
        return None, None
    if resp.status_code != 200:
        return None, None
    return cover_url, f"{base}_xs.jpg"
