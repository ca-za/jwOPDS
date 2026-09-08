"""Map a publication key to a stable category slug + display titles.

Category slugs are used as filenames/ids and are stable across languages.
Display titles are best-effort translations for a handful of major
languages; anything else falls back to the English label.
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

_TITLES = {
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
    "books": {"en": "Books & Brochures", "de": "Bücher & Broschüren",
              "fr": "Livres et brochures", "es": "Libros y folletos",
              "it": "Libri e opuscoli", "pt": "Livros e brochuras",
              "nl": "Boeken en brochures"},
}

CATEGORY_ORDER = ["bible", "watchtower", "awake", "meeting-workbook", "daily-text", "books"]

# Categories with enough recurring, dated issues to warrant per-year archives.
PERIODICAL_CATEGORIES = {"watchtower", "awake", "meeting-workbook", "daily-text"}

_ARCHIVE_LABELS = {
    "en": "Older issues", "de": "Ältere Ausgaben", "fr": "Anciens numéros",
    "es": "Números anteriores", "it": "Numeri precedenti", "pt": "Edições anteriores",
    "nl": "Oudere nummers",
}


def categorize(pub_key: str, is_periodical: bool) -> str:
    if pub_key == "nwt" or pub_key.startswith("nwt"):
        return "bible"
    if _DAILY_TEXT_RE.match(pub_key):
        return "daily-text"
    if is_periodical:
        return _PERIODICAL_CATEGORY.get(pub_key, "books")
    return "books"


def category_title(category: str, locale: str) -> str:
    titles = _TITLES.get(category, {})
    return titles.get(locale.lower(), titles.get("en", category))


def archive_label(locale: str) -> str:
    return _ARCHIVE_LABELS.get(locale.lower(), _ARCHIVE_LABELS["en"])
