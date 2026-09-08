"""Local index of every publication we've confirmed (or ruled out) for a
given language, so re-runs don't need to re-probe every candidate every time.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    lang_code TEXT NOT NULL,
    pub_key TEXT NOT NULL,
    issue TEXT NOT NULL DEFAULT '',
    available INTEGER NOT NULL,      -- 0 = confirmed not available, 1 = available
    category TEXT,
    title TEXT,
    epub_url TEXT,
    checksum TEXT,
    filesize INTEGER,
    modified_datetime TEXT,
    cover_url TEXT,
    thumbnail_url TEXT,
    last_checked REAL NOT NULL,
    PRIMARY KEY (lang_code, pub_key, issue)
);
"""


class StateDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path))
        self.con.execute(SCHEMA)
        existing = {row[1] for row in self.con.execute("PRAGMA table_info(entries)")}
        if "thumbnail_url" not in existing:
            self.con.execute("ALTER TABLE entries ADD COLUMN thumbnail_url TEXT")
        self.con.commit()

    def close(self):
        self.con.close()

    def get(self, lang_code: str, pub_key: str, issue: str = ""):
        cur = self.con.execute(
            "SELECT * FROM entries WHERE lang_code=? AND pub_key=? AND issue=?",
            (lang_code, pub_key, issue),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def mark_unavailable(self, lang_code: str, pub_key: str, issue: str = ""):
        self.con.execute(
            "INSERT INTO entries (lang_code, pub_key, issue, available, last_checked) "
            "VALUES (?, ?, ?, 0, ?) "
            "ON CONFLICT(lang_code, pub_key, issue) DO UPDATE SET "
            "available=0, last_checked=excluded.last_checked",
            (lang_code, pub_key, issue, time.time()),
        )
        self.con.commit()

    def upsert_available(
        self,
        lang_code: str,
        pub_key: str,
        issue: str,
        category: str,
        title: str,
        epub_url: str,
        checksum: str,
        filesize: int,
        modified_datetime: str,
        cover_url: str,
        thumbnail_url: str,
    ):
        self.con.execute(
            """
            INSERT INTO entries (
                lang_code, pub_key, issue, available, category, title, epub_url,
                checksum, filesize, modified_datetime, cover_url, thumbnail_url,
                last_checked
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lang_code, pub_key, issue) DO UPDATE SET
                available=1, category=excluded.category, title=excluded.title,
                epub_url=excluded.epub_url, checksum=excluded.checksum,
                filesize=excluded.filesize, modified_datetime=excluded.modified_datetime,
                cover_url=excluded.cover_url, thumbnail_url=excluded.thumbnail_url,
                last_checked=excluded.last_checked
            """,
            (
                lang_code, pub_key, issue, category, title, epub_url, checksum,
                filesize, modified_datetime, cover_url, thumbnail_url,
                time.time(),
            ),
        )
        self.con.commit()

    def available_entries(self, lang_code: str):
        cur = self.con.execute(
            "SELECT * FROM entries WHERE lang_code=? AND available=1 "
            "ORDER BY category, pub_key, issue DESC",
            (lang_code,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def needs_check(self, lang_code: str, pub_key: str, issue: str, recheck_days: int) -> bool:
        row = self.get(lang_code, pub_key, issue)
        if row is None:
            return True
        age_days = (time.time() - row["last_checked"]) / 86400
        return age_days >= recheck_days
