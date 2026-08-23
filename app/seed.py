"""Seed the prototype with selected OCR-complete Hakka genealogies already in the workspace."""

from __future__ import annotations

from pathlib import Path

from .database import APP_ROOT, DB_PATH, db_session, init_db, utc_now
from .ingest import import_document
from .retrieval import search
from .writer import generate_draft
from .audit import audit_claim
from .hgpf import OCR_REVIEW_THRESHOLD


CORPUS_CANDIDATES = [
    ("台灣_苗栗_張姓近代族譜_1冊(37頁)_1968.md", "張姓近代族譜（苗栗，1968）"),
    ("台灣_苗栗_徐氏大族譜_1冊(118頁)_1978-compressed.md", "徐氏大族譜（苗栗，1978）"),
    ("客09_詔安江氏志.md", "詔安江氏志"),
]


def workspace_root() -> Path:
    return APP_ROOT.parent


def seed_demo(reset: bool = False) -> dict:
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    init_db()
    corpus_dir = workspace_root() / "所有電子檔"
    imported = []
    errors = []
    for filename, title in CORPUS_CANDIDATES:
        path = corpus_dir / filename
        if not path.exists():
            errors.append(f"找不到：{path}")
            continue
        try:
            imported.append(import_document(path, title=title, source_type="客家族譜OCR"))
        except Exception as exc:
            errors.append(f"{filename}: {exc}")

    with db_session() as db:
        claim = db.execute("SELECT * FROM claims ORDER BY id LIMIT 1").fetchone()
        if claim:
            return {"documents": imported, "claim_id": claim["id"], "errors": errors, "already_seeded": True}
        now = utc_now()
        cursor = db.execute(
            """
            INSERT INTO claims(claim_type, subject, text, asserted_value, hgpf_field_id,
                               confidence, status, created_at, updated_at)
            VALUES ('祖先地景主張', '心展公祠位置與坐向', ?, ?, 30, '待查', '稽核中', ?, ?)
            """,
            (
                "《詔安江氏志》記載心展公祠位於坑河村下坑頭，祖祠坐申兼庚；此為特定版本中的祖先地景主張。",
                "坑河村下坑頭；坐申兼庚",
                now,
                now,
            ),
        )
        claim_id = cursor.lastrowid

    support_results = search(
        "衍慶堂 心展公祠 坑河村 下坑頭 坐申兼庚",
        limit=12,
        claim_id=claim_id,
    )
    explicit_support = next(
        (
            result
            for result in support_results
            if all(term in result["text"] for term in ("心展", "坑河村", "坐申兼庚"))
            and result.get("quality_score", 1.0) >= OCR_REVIEW_THRESHOLD
        ),
        None,
    )
    # Similarity alone never establishes a supporting relation. The seed only
    # links a passage after deterministic anchor checks; everything remains
    # marked for human comparison with the page image.
    with db_session() as db:
        if explicit_support:
            db.execute(
                """
                INSERT OR IGNORE INTO evidence_links(claim_id, passage_id, relation, weight, note, created_at)
                VALUES (?, ?, '支持', ?, '示範資料以實體、地點及坐向三項錨點核對；仍須人工回看原頁。', ?)
                """,
                (
                    claim_id,
                    explicit_support["passage_id"],
                    0.5,
                    utc_now(),
                ),
            )
    try:
        generate_draft(claim_id)
        audit_claim(claim_id)
    except Exception as exc:
        errors.append(f"建立示範主張：{exc}")
    return {"documents": imported, "claim_id": claim_id, "errors": errors, "already_seeded": False}


if __name__ == "__main__":
    print(seed_demo(reset=False))
