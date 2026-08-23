"""FastAPI application for the HGPF AI-RAG prototype."""

from __future__ import annotations

import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audit import audit_claim
from .database import APP_ROOT, UPLOAD_DIR, db_session, decode_json_fields, init_db, utc_now
from .document_audit import audit_document
from .hgpf import DOMAINS, FIELD_RECORDS, GPS_COMPONENTS, LAYERS
from .ingest import (
    SUPPORTED_EXTENSIONS,
    backfill_page_hints,
    backfill_passage_quality,
    import_document,
)
from .retrieval import search
from .schemas import (
    ClaimCreate,
    ClaimUpdate,
    DraftCreate,
    DraftReview,
    EvidenceCreate,
    ImportDocumentRequest,
    SearchRequest,
    SeedRequest,
)
from .seed import seed_demo
from .writer import generate_draft


STATIC_DIR = APP_ROOT / "app" / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    backfill_passage_quality()
    backfill_page_hints()
    yield


app = FastAPI(
    title="HGPF AI-RAG 臺灣客家族譜證據數位治理系統",
    version=__version__,
    description="GPS導向、HGPF在地化的研究與書寫雛型；不提供GPS認證。",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "principle": "evidence-before-narrative"}


@app.get("/api/dashboard")
def dashboard() -> dict:
    with db_session() as db:
        stats = {
            "documents": db.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"],
            "passages": db.execute("SELECT COUNT(*) AS n FROM passages").fetchone()["n"],
            "claims": db.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"],
            "evidence_links": db.execute("SELECT COUNT(*) AS n FROM evidence_links").fetchone()["n"],
            "drafts": db.execute("SELECT COUNT(*) AS n FROM drafts").fetchone()["n"],
        }
        latest = db.execute(
            """
            SELECT a.id, a.claim_id, a.score, a.level, a.created_at, c.subject, c.text
            FROM audit_runs a JOIN claims c ON c.id = a.claim_id
            ORDER BY a.id DESC LIMIT 5
            """
        ).fetchall()
        relations = db.execute(
            "SELECT relation, COUNT(*) AS n FROM evidence_links GROUP BY relation"
        ).fetchall()
    return {
        "stats": stats,
        "latest_audits": latest,
        "relations": {row["relation"]: row["n"] for row in relations},
        "notice": "分數是雛型稽核提示，不是GPS認證或合理窮盡之證明。",
    }


@app.get("/api/framework")
def framework() -> dict:
    return {"layers": LAYERS, "domains": DOMAINS, "fields": FIELD_RECORDS, "gps": GPS_COMPONENTS}


@app.get("/api/documents")
def list_documents() -> list[dict]:
    with db_session() as db:
        rows = db.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
    for row in rows:
        decode_json_fields(row, "metadata_json")
    return rows


@app.post("/api/documents/import")
def import_path(request: ImportDocumentRequest) -> dict:
    try:
        return import_document(Path(request.path), request.title, request.source_type, request.access_level)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/documents/upload")
def upload_document(file: UploadFile = File(...), access_level: str = "研究使用") -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="僅支援 MD、TXT、PDF、DOCX。")
    destination = UPLOAD_DIR / Path(file.filename or f"upload{suffix}").name
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    try:
        return import_document(destination, source_type="使用者上傳", access_level=access_level)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/documents/{document_id}/audit")
def run_document_audit(document_id: int) -> dict:
    try:
        return audit_document(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/documents/{document_id}/audits/latest")
def latest_document_audit(document_id: int) -> dict:
    with db_session() as db:
        row = db.execute(
            """
            SELECT * FROM document_audits
            WHERE document_id=? ORDER BY id DESC LIMIT 1
            """,
            (document_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="此文件尚無預檢報告。")
    report = json.loads(row["report_json"])
    report["audit_id"] = row["id"]
    return report


@app.post("/api/search")
def hybrid_search(request: SearchRequest) -> dict:
    results = search(
        request.query,
        request.limit,
        request.counterevidence,
        request.document_ids or None,
        request.hgpf_field_id,
        request.claim_id,
    )
    return {"query": request.query, "mode": "反證檢索" if request.counterevidence else "混合檢索", "results": results}


@app.get("/api/claims")
def list_claims() -> list[dict]:
    with db_session() as db:
        return db.execute(
            """
            SELECT c.*,
                   (SELECT COUNT(*) FROM evidence_links e WHERE e.claim_id = c.id) AS evidence_count,
                   (SELECT COUNT(*) FROM drafts d WHERE d.claim_id = c.id) AS draft_count,
                   (SELECT score FROM audit_runs a WHERE a.claim_id = c.id ORDER BY a.id DESC LIMIT 1) AS audit_score
            FROM claims c ORDER BY c.id DESC
            """
        ).fetchall()


@app.post("/api/claims")
def create_claim(request: ClaimCreate) -> dict:
    now = utc_now()
    with db_session() as db:
        cursor = db.execute(
            """
            INSERT INTO claims(claim_type, subject, text, asserted_value, hgpf_field_id,
                               confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, '草稿', ?, ?)
            """,
            (
                request.claim_type,
                request.subject,
                request.text,
                request.asserted_value,
                request.hgpf_field_id,
                request.confidence,
                now,
                now,
            ),
        )
        claim_id = cursor.lastrowid
        return db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()


@app.patch("/api/claims/{claim_id}")
def update_claim(claim_id: int, request: ClaimUpdate) -> dict:
    updates = {key: value for key, value in request.model_dump().items() if value is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="沒有可更新的欄位。")
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with db_session() as db:
        cursor = db.execute(
            f"UPDATE claims SET {assignments} WHERE id = ?", [*updates.values(), claim_id]
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="找不到主張。")
        return db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: int) -> dict:
    with db_session() as db:
        claim = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if not claim:
            raise HTTPException(status_code=404, detail="找不到主張。")
        evidence = db.execute(
            """
            SELECT e.*, p.text, p.ordinal, p.page_hint, p.hgpf_fields_json,
                   p.quality_score, p.quality_flags_json,
                   d.title AS document_title, d.source_path, d.source_type,
                   d.access_level
            FROM evidence_links e JOIN passages p ON p.id=e.passage_id
            JOIN documents d ON d.id=p.document_id WHERE e.claim_id=? ORDER BY e.id
            """,
            (claim_id,),
        ).fetchall()
        drafts = db.execute("SELECT * FROM drafts WHERE claim_id=? ORDER BY id DESC", (claim_id,)).fetchall()
        audits = db.execute("SELECT * FROM audit_runs WHERE claim_id=? ORDER BY id DESC LIMIT 5", (claim_id,)).fetchall()
        events = db.execute("SELECT * FROM research_events WHERE claim_id=? ORDER BY id DESC", (claim_id,)).fetchall()
    for item in evidence:
        decode_json_fields(item, "hgpf_fields_json")
        decode_json_fields(item, "quality_flags_json")
    for item in drafts:
        decode_json_fields(item, "citations_json")
    for item in audits:
        decode_json_fields(item, "findings_json")
    return {"claim": claim, "evidence": evidence, "drafts": drafts, "audits": audits, "research_events": events}


@app.post("/api/claims/{claim_id}/evidence")
def add_evidence(claim_id: int, request: EvidenceCreate) -> dict:
    with db_session() as db:
        if not db.execute("SELECT id FROM claims WHERE id=?", (claim_id,)).fetchone():
            raise HTTPException(status_code=404, detail="找不到主張。")
        if not db.execute("SELECT id FROM passages WHERE id=?", (request.passage_id,)).fetchone():
            raise HTTPException(status_code=404, detail="找不到證據段落。")
        try:
            cursor = db.execute(
                """
                INSERT INTO evidence_links(claim_id, passage_id, relation, weight, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (claim_id, request.passage_id, request.relation, request.weight, request.note, utc_now()),
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail="此證據關係已存在。") from exc
        db.execute(
            """
            INSERT INTO processing_activities(
                activity_type, entity_type, entity_id, actor, tool_version,
                details_json, created_at
            ) VALUES ('建立證據關係', 'evidence_link', ?, 'researcher-pending',
                      'ui-manual-link-v1', ?, ?)
            """,
            (
                cursor.lastrowid,
                json.dumps(
                    {
                        "claim_id": claim_id,
                        "passage_id": request.passage_id,
                        "relation": request.relation,
                        "note": request.note,
                    },
                    ensure_ascii=False,
                ),
                utc_now(),
            ),
        )
        return {"id": cursor.lastrowid, **request.model_dump(), "claim_id": claim_id}


@app.delete("/api/evidence/{evidence_id}")
def remove_evidence(evidence_id: int) -> dict:
    with db_session() as db:
        cursor = db.execute("DELETE FROM evidence_links WHERE id=?", (evidence_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="找不到證據關係。")
    return {"deleted": evidence_id}


@app.post("/api/audit/{claim_id}")
def run_audit(claim_id: int) -> dict:
    try:
        return audit_claim(claim_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/drafts")
def create_draft(request: DraftCreate) -> dict:
    try:
        return generate_draft(request.claim_id, request.title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/drafts/{draft_id}")
def review_draft(draft_id: int, request: DraftReview) -> dict:
    if request.status in {"Human-reviewed", "Approved-for-publication"} and not request.reviewer.strip():
        raise HTTPException(status_code=400, detail="人工複核或發布必須填寫覆核者。")
    if request.status == "Approved-for-publication" and not request.review_note.strip():
        raise HTTPException(status_code=400, detail="發布核定必須填寫適用範圍、判斷理由與剩餘限制。")
    with db_session() as db:
        draft = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if not draft:
            raise HTTPException(status_code=404, detail="找不到草稿。")
        if request.status == "Approved-for-publication":
            claim = db.execute("SELECT * FROM claims WHERE id=?", (draft["claim_id"],)).fetchone()
            unresolved = db.execute(
                """
                SELECT COUNT(*) AS n FROM evidence_links
                WHERE claim_id=? AND relation IN ('反駁','限制')
                """,
                (draft["claim_id"],),
            ).fetchone()["n"]
            if unresolved and not (claim["resolution_note"].strip() and claim["reviewer"].strip()):
                raise HTTPException(status_code=409, detail="尚有反駁／限制證據未完成具名人工處置。")
            citations = json.loads(draft["citations_json"] or "[]")
            if any(item.get("access_level") not in {"公開", "研究使用"} for item in citations):
                raise HTTPException(status_code=409, detail="草稿含宗族限定或敏感證據，不能直接核定為公開版本。")
        cursor = db.execute(
            """
            UPDATE drafts SET status=?, reviewer=?, review_note=?, updated_at=? WHERE id=?
            """,
            (request.status, request.reviewer, request.review_note, utc_now(), draft_id),
        )
        db.execute(
            """
            INSERT INTO processing_activities(
                activity_type, entity_type, entity_id, actor, tool_version,
                details_json, created_at
            ) VALUES ('人工覆核草稿', 'draft', ?, ?, 'human-review-v1', ?, ?)
            """,
            (
                draft_id,
                request.reviewer or "未具名",
                json.dumps(
                    {"status": request.status, "review_note": request.review_note},
                    ensure_ascii=False,
                ),
                utc_now(),
            ),
        )
        return db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()


@app.post("/api/seed")
def seed(request: SeedRequest) -> dict:
    return seed_demo(reset=request.reset)
