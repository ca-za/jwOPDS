"""Resolve human-friendly language identifiers to jw.org's internal
"langwritten" codes, via the public language list API used by jw.org itself.
"""
from __future__ import annotations

import logging

import requests

LANGUAGES_URL = "https://b.jw-cdn.org/apis/mediator/v1/languages/E/all"

log = logging.getLogger(__name__)


class Language:
    def __init__(self, code: str, locale: str, name: str, vernacular: str, direction: str):
        self.code = code          # jw "langwritten" symbol, e.g. "X" for German
        self.locale = locale      # ISO-ish locale, e.g. "de"
        self.name = name          # English name, e.g. "German"
        self.vernacular = vernacular  # native name, e.g. "Deutsch"
        self.direction = direction

    def __repr__(self):
        return f"Language(code={self.code!r}, locale={self.locale!r}, name={self.name!r})"


def fetch_languages(session: requests.Session, timeout: int = 15) -> list[Language]:
    resp = session.get(LANGUAGES_URL, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    result = []
    for entry in data.get("languages", []):
        result.append(
            Language(
                code=entry["code"],
                locale=entry.get("locale", ""),
                name=entry.get("name", ""),
                vernacular=entry.get("vernacular", entry.get("name", "")),
                direction="rtl" if entry.get("isRTL") else "ltr",
            )
        )
    return result


def resolve_language(identifier: str, languages: list[Language]) -> Language:
    """Resolve a config value (ISO locale like 'de', or jw code like 'X') to a Language."""
    ident = identifier.strip()
    # Exact jw code match (case-sensitive, codes are short uppercase-ish symbols)
    for lang in languages:
        if lang.code == ident:
            return lang
    # Locale match (case-insensitive)
    for lang in languages:
        if lang.locale.lower() == ident.lower():
            return lang
    raise ValueError(
        f"Could not resolve language '{identifier}' against jw.org's language list. "
        "Use an ISO locale (e.g. 'de') or a jw.org language code (e.g. 'X')."
    )
