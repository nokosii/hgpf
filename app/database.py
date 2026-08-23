"""SQLite persistence and schema helpers."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("HGPF_DATA_DIR", APP_ROOT / "data"))
DB_PATH = Path(os.getenv("HGPF_DB_PATH", DATA_DIR / "hgpf.db"))
UPLOAD_DIR = DATA_DIR / "uploads"


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _dict_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = _dict_factory
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with db_session() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_type TEXT NOT NULL,
                checksum TEXT NOT NULL UNIQUE,
                access_level TEXT NOT NULL DEFAULT '研究使用',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                content_length INTEGER NOT NULL DEFAULT 0,
                passage_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS passages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                page_hint TEXT,
                text TEXT NOT NULL,
                char_start INTEGER NOT NULL DEFAULT 0,
                char_end INTEGER NOT NULL DEFAULT 0,
                hgpf_fields_json TEXT NOT NULL DEFAULT '[]',
                vector_json TEXT NOT NULL DEFAULT '{}',
                quality_score REAL NOT NULL DEFAULT 1.0,
                quality_flags_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(document_id, ordinal)
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                text TEXT NOT NULL,
                asserted_value TEXT,
                hgpf_field_id INTEGER,
                confidence TEXT NOT NULL DEFAULT '待查',
                status TEXT NOT NULL DEFAULT '草稿',
                resolution_note TEXT NOT NULL DEFAULT '',
                reviewer TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                passage_id INTEGER NOT NULL REFERENCES passages(id) ON DELETE CASCADE,
                relation TEXT NOT NULL CHECK(relation IN ('支持','反駁','限制','脈絡')),
                weight REAL NOT NULL DEFAULT 0.5,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(claim_id, passage_id, relation)
            );

            CREATE TABLE IF NOT EXISTS research_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER REFERENCES claims(id) ON DELETE CASCADE,
                query TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT '一般檢索',
                filters_json TEXT NOT NULL DEFAULT '{}',
                result_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                citations_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'Evidence-linked',
                review_note TEXT NOT NULL DEFAULT '',
                reviewer TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                score INTEGER NOT NULL,
                level TEXT NOT NULL,
                findings_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS document_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                score INTEGER NOT NULL,
                level TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processing_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                actor TEXT NOT NULL DEFAULT 'system',
                tool_version TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_passages_document ON passages(document_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence_links(claim_id);
            CREATE INDEX IF NOT EXISTS idx_research_claim ON research_events(claim_id);
            CREATE INDEX IF NOT EXISTS idx_drafts_claim ON drafts(claim_id);
            CREATE INDEX IF NOT EXISTS idx_document_audits_document
                ON document_audits(document_id);
            CREATE INDEX IF NOT EXISTS idx_processing_entity
                ON processing_activities(entity_type, entity_id);
            """
        )
        # Forward-only, non-destructive migrations for databases created by an
        # earlier prototype. SQLite has no IF NOT EXISTS form for ADD COLUMN.
        passage_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(passages)").fetchall()
        }
        if "quality_score" not in passage_columns:
            db.execute(
                "ALTER TABLE passages ADD COLUMN quality_score REAL NOT NULL DEFAULT 1.0"
            )
        if "quality_flags_json" not in passage_columns:
            db.execute(
                "ALTER TABLE passages ADD COLUMN quality_flags_json TEXT NOT NULL DEFAULT '[]'"
            )
        try:
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5("
                "passage_id UNINDEXED, title, text, tokenize='trigram')"
            )
        except sqlite3.OperationalError:
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5("
                "passage_id UNINDEXED, title, text, tokenize='unicode61')"
            )


def decode_json_fields(row: dict[str, Any] | None, *fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    for field in fields:
        value = row.get(field)
        if isinstance(value, str):
            try:
                row[field.removesuffix("_json")] = json.loads(value)
            except json.JSONDecodeError:
                row[field.removesuffix("_json")] = None
    return row
