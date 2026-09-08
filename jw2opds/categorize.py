"""Map a publication key to a stable category slug + display title.

Category slugs are used as filenames/ids and are stable across languages.
Display titles, wherever possible, are derived straight from jw.org's own
localized publication titles (see `display_title`) instead of a hand-
maintained translation table -- that way a category label is correct in
*any* language jw.org supports, not just the handful we could translate
ourselves. Only the two categories with no single representative title
("books", "tracts") fall back to a small hardcoded table, itself falling
back to English for anything not covered.
"""
from __future__ import annotations

import re

_PERIODICAL_CATEGORY = {
    "w": "watchtower",
    "wp": "watchtower",
    "wqu": "watchtower",
    "ws": "watchtower",
    "g": "awake",
    "mwb": "meeting-workbook",
}

# The yearly daily-text book, e.g. "es24" -> Examining/Táglich in den
# Schriften forschen 2024. Not a recurring "periodical" in jw.org's catalog
# (it has no IssueTagNumber, just one KeySymbol per year), but it grows every
# year the same way, so it gets the same treatment.
_DAILY_TEXT_RE = re.compile(r"^es\d{2}$")

# Tracts: small single-topic leaflets, always keyed "T-<code>" regardless of
# language (e.g. "T-dth", "T-30"). Not dated/recurring, so treated as a flat
# category like "books" rather than a per-year archive.
_TRACT_RE = re.compile(r"^T-")

# Yearbooks ("yb17", ...) are tagged with the "Yearbook" PublicationAttribute
# in jw.org's own catalog; convention/circuit-assembly programs ("CO-pgm18",
# "CA-brpgm23", ...) with "Convention" or "Circuit Assembly". Both recur
# yearly, so both get the same front-page-plus-year-archive treatment as the
# periodicals.
_YEARBOOK_ATTRS = {"Yearbook"}
_ASSEMBLY_ATTRS = {"Convention", "Circuit Assembly"}

_TITLES = {
    "books": {"en": "Books & Brochures", "de": "Bücher & Broschüren",
              "fr": "Livres et brochures", "es": "Libros y folletos",
              "it": "Libri e opuscoli", "pt": "Livros e brochuras",
              "nl": "Boeken en brochures"},
    "tracts": {"en": "Tracts", "de": "Traktate", "fr": "Tracts",
               "es": "Tratados", "it": "Volantini", "pt": "Tratados",
               "nl": "Traktaten"},
    # Fallback only -- used if a category somehow has no entries to derive
    # a title from (see display_title).
    "bible": {"en": "Bible", "de": "Bibel", "fr": "Bible", "es": "Biblia", "it": "Bibbia",
              "pt": "Bíblia", "nl": "Bijbel"},
    "watchtower": {"en": "The Watchtower", "de": "Der Wachtturm", "fr": "La Tour de Garde",
                   "es": "La Atalaya", "it": "La Torre di Guardia", "pt": "A Sentinela",
                   "nl": "De Wachttoren"},
    "awake": {"en": "Awake!", "de": "Erwachet!", "fr": "Réveillez-vous !", "es": "¡Despertad!",
              "it": "Svegliatevi!", "pt": "Despertai!", "nl": "Ontwaakt!"},
    "meeting-workbook": {"en": "Meeting Workbook", "de": "Zusammenkunftsleitfaden",
                          "fr": "Cahier de vie chrétienne", "es": "Guía de actividades",
                          "it": "Sussidio per le adunanze", "pt": "Apostila das Reuniões",
                          "nl": "Werkboek voor de vergadering"},
    "daily-text": {"en": "Daily Text", "de": "Tagestext", "fr": "Texte du jour",
                   "es": "Texto del día", "it": "Testo del giorno", "pt": "Texto do dia",
                   "nl": "Dagtekst"},
    "yearbook": {"en": "Yearbooks", "de": "Jahrbücher", "fr": "Annuaires",
                 "es": "Anuarios", "it": "Annuari", "pt": "Anuários",
                 "nl": "Jaarboeken"},
    "assembly-programs": {"en": "Convention & Assembly Programs",
                           "de": "Kongress- und Kreisprogramme",
                           "fr": "Programmes d'assemblées",
                           "es": "Programas de asambleas",
                           "it": "Programmi delle assemblee",
                           "pt": "Programas de assembleias",
                           "nl": "Congres- en kringprogramma's"},
}

CATEGORY_ORDER = [
    "bible", "watchtower", "awake", "meeting-workbook", "daily-text",
    "yearbook", "assembly-programs", "tracts", "books",
]

# Categories with enough recurring, dated issues to warrant per-year archives.
PERIODICAL_CATEGORIES = {
    "watchtower", "awake", "meeting-workbook", "daily-text",
    "yearbook", "assembly-programs",
}

_ARCHIVE_LABELS = {
    "en": "Older issues", "de": "Ältere Ausgaben", "fr": "Anciens numéros",
    "es": "Números anteriores", "it": "Numeri precedenti", "pt": "Edições anteriores",
    "nl": "Oudere nummers",
}


def categorize(pub_key: str, is_periodical: bool, attribute_tags: frozenset = frozenset()) -> str:
    if pub_key == "nwt" or pub_key.startswith("nwt"):
        return "bible"
    if _DAILY_TEXT_RE.match(pub_key):
        return "daily-text"
    if is_periodical:
        return _PERIODICAL_CATEGORY.get(pub_key, "books")
    if attribute_tags & _ASSEMBLY_ATTRS:
        return "assembly-programs"
    if attribute_tags & _YEARBOOK_ATTRS:
        return "yearbook"
    if _TRACT_RE.match(pub_key):
        return "tracts"
    return "books"


def category_title(category: str, locale: str) -> str:
    titles = _TITLES.get(category, {})
    return titles.get(locale.lower(), titles.get("en", category))


# Matches "1900"-"2099" anywhere in a string, e.g. the year jw.org bakes into
# a yearbook/assembly-program/daily-text title -- at the start ("2019
# Convention Program"), middle ("Annuaire 2017 des Témoins de Jéhovah"), or
# end ("Kongressprogramm 2019"), depending on the language's own grammar.
_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}")
_EDGE_JUNK_RE = re.compile(r"^[\s\-‐-―,.:;()\[\]]+|[\s\-‐-―,.:;()\[\]]+$")


def _strip_year(title: str) -> str:
    stripped = _YEAR_TOKEN_RE.sub("", title)
    stripped = re.sub(r"\s{2,}", " ", stripped)
    return _EDGE_JUNK_RE.sub("", stripped).strip()


def display_title(category: str, locale: str, rows: list[dict]) -> str:
    """The category label shown in the OPDS feeds. Derived from jw.org's own
    localized publication titles when possible (see module docstring);
    falls back to the static, English-default table otherwise."""
    if rows:
        newest = max(rows, key=lambda r: r.get("modified_datetime") or "")
        title = (newest.get("title") or "").strip()
        if title:
            if category in ("watchtower", "awake", "meeting-workbook"):
                # We build these ourselves as "<pubName> — <date>" (pubmedia.py).
                base = title.split(" — ")[0].strip()
                if base:
                    return base
            elif category in ("yearbook", "assembly-programs", "daily-text"):
                stripped = _strip_year(title)
                if stripped:
                    return stripped
            elif category == "bible":
                return title
    return category_title(category, locale)


def archive_label(locale: str) -> str:
    return _ARCHIVE_LABELS.get(locale.lower(), _ARCHIVE_LABELS["en"])
