from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import coverart, jwcatalog, mediator, opds
from .categorize import categorize
from .config import Config
from .pubmedia import fetch_epub_media
from .state import StateDB

log = logging.getLogger(__name__)


def _candidates(cfg: Config, db_path):
    """Yield (pub_key, issue, is_periodical) for every candidate to probe."""
    for key in jwcatalog.list_book_keys(db_path):
        yield key, "", False
    for key in cfg.periodical_keys:
        issues = jwcatalog.list_periodical_issues(
            db_path, key, cfg.periodical_lookback_months
        )
        for issue in issues:
            yield key, issue, True


def run_sync(cfg: Config, only_languages: list[str] | None = None, force: bool = False) -> None:
    session = requests.Session()
    session.headers["User-Agent"] = cfg.user_agent

    db_path = jwcatalog.ensure_catalog_db(
        session, cfg.jw_catalog_manifest_path, cfg.jw_catalog_db_path, cfg.request_timeout
    )
    attribute_tags = jwcatalog.load_attribute_tags(db_path)

    all_languages = mediator.fetch_languages(session, cfg.request_timeout)
    wanted = only_languages or cfg.languages
    resolved = [mediator.resolve_language(ident, all_languages) for ident in wanted]

    state = StateDB(cfg.state_db_path)
    candidates = list(_candidates(cfg, db_path))
    log.info("%d candidate publications to consider per language", len(candidates))

    for lang in resolved:
        locale = lang.locale or lang.code
        log.info("=== %s (%s / %s) ===", lang.name, lang.code, locale)
        _sync_language(cfg, session, state, lang, candidates, force, attribute_tags)

        rows = state.available_entries(lang.code)
        by_category: dict[str, list] = {}
        for row in rows:
            by_category.setdefault(row["category"], []).append(row)

        for category, cat_rows in by_category.items():
            opds.write_category_feed(cfg.output_dir, cfg.base_url, lang, category, cat_rows)
        opds.write_language_feed(cfg.output_dir, cfg.base_url, lang, by_category)
        opds.write_new_feed(cfg.output_dir, cfg.base_url, lang, rows)
        log.info("%s: %d publications available", lang.name, len(rows))

    opds.write_root_feed(cfg.output_dir, cfg.base_url, resolved)
    state.close()
    log.info("Done. OPDS root feed: %s", cfg.output_dir / "index.xml")


def _sync_language(cfg, session, state: StateDB, lang, candidates, force: bool, attribute_tags: dict) -> None:
    to_check = []
    for pub_key, issue, is_periodical in candidates:
        if force or state.needs_check(lang.code, pub_key, issue, cfg.recheck_days):
            to_check.append((pub_key, issue, is_periodical))

    log.info("%s: checking %d/%d candidates (rest are recently cached)",
              lang.name, len(to_check), len(candidates))

    def worker(item):
        pub_key, issue, is_periodical = item
        media = fetch_epub_media(session, lang.code, pub_key, issue, cfg.request_timeout)
        if media is None:
            return pub_key, issue, is_periodical, media, None, None
        cover_url, thumbnail_url = coverart.find_cover(
            session, pub_key, lang.code, issue, cfg.request_timeout
        )
        return pub_key, issue, is_periodical, media, cover_url, thumbnail_url

    with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
        futures = [pool.submit(worker, item) for item in to_check]
        for future in as_completed(futures):
            pub_key, issue, is_periodical, media, cover_url, thumbnail_url = future.result()
            if media is None:
                state.mark_unavailable(lang.code, pub_key, issue)
                continue

            category = categorize(pub_key, is_periodical, attribute_tags.get(pub_key, frozenset()))
            state.upsert_available(
                lang_code=lang.code,
                pub_key=pub_key,
                issue=issue,
                category=category,
                title=media.title,
                epub_url=media.url,
                checksum=media.checksum,
                filesize=media.filesize,
                modified_datetime=media.modified_datetime,
                cover_url=cover_url or "",
                thumbnail_url=thumbnail_url or "",
                pdf_url=media.pdf_url,
                pdf_checksum=media.pdf_checksum,
                pdf_filesize=media.pdf_filesize,
            )
