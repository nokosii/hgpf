"""Document extraction, chunking and indexing."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from .database import db_session, utc_now
from .hgpf import infer_fields


SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}


def extract_text(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF匯入需要安裝 pypdf。") from exc
        reader = PdfReader(str(path))
        parts = []
        for page_number, page in enumerate(reader.pages, start=1):
            parts.append(f"\n\n<!-- HGPF_PAGE:{page_number} -->\n{page.extract_text() or ''}")
        text = "".join(parts)
    elif suffix == ".docx":
        try:
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
        except ImportError as exc:
            raise RuntimeError("DOCX匯入需要安裝 python-docx。") from exc
        document = Document(str(path))
        blocks: list[str] = []
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                value = Paragraph(child, document).text.strip()
                if value:
                    blocks.append(value)
            elif child.tag.endswith("}tbl"):
                table = Table(child, document)
                rows = [
                    "\t".join(cell.text.strip() for cell in row.cells)
                    for row in table.rows
                ]
                table_text = "\n".join(row for row in rows if row.strip())
                if table_text:
                    blocks.append(table_text)
        text = "\n\n".join(blocks)
    else:
        raise ValueError(f"不支援的檔案格式：{suffix}")

    metadata = parse_front_matter(text)
    metadata.update({"extension": suffix, "filename": path.name})
    return clean_text(strip_processing_wrapper(text)), metadata


def parse_front_matter(text: str) -> dict:
    metadata: dict[str, str] = {}
    for line in text[:1800].splitlines():
        match = re.match(r"^>\s*\*\*(.+?)\*\*[：:]\s*(.+)$", line.strip())
        if match:
            metadata[match.group(1).strip()] = match.group(2).strip()
    return metadata


def strip_processing_wrapper(text: str) -> str:
    """Remove OCR pipeline metadata from the evidence body.

    The metadata is retained on the document record. Keeping it inside the
    first passage makes terms such as the filename, OCR engine or page count
    look like genealogical evidence and can distort retrieval.
    """
    lines = text.splitlines()
    cleaned: list[str] = []
    in_leading_wrapper = True
    for line in lines:
        stripped = line.strip()
        if in_leading_wrapper and (
            not stripped
            or stripped.startswith("> **")
            or stripped == "---"
            or (stripped.startswith("# ") and not cleaned)
        ):
            continue
        in_leading_wrapper = False
        cleaned.append(line)
    return "\n".join(cleaned)


def clean_text(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\x00", "")
    text = re.sub(r"\[OCR ERROR:[^\]]+\]", "", text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _is_structural_boundary(line: str) -> bool:
    stripped = line.strip()
    return bool(
        re.match(r"^#{1,6}\s+", stripped)
        or re.match(r"^第[一二三四五六七八九十百千0-9]+[章節卷篇]", stripped)
        or re.match(r"^[一二三四五六七八九十]+、", stripped)
    )


def _source_units(text: str) -> list[tuple[str, str]]:
    """Create page-aware, structure-aware units before size chunking."""
    units: list[tuple[str, str]] = []
    page_hint = ""
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        content = "\n".join(buffer).strip()
        if content:
            units.append((page_hint, content))
        buffer = []

    for line in text.splitlines():
        marker = re.search(r"<!--\s*HGPF_PAGE:([^>]+?)\s*-->", line)
        printed_page = re.search(
            r"第\s*([一二三四五六七八九十百千〇零0-9]+)\s*[頁页]", line
        )
        if marker:
            flush()
            page_hint = marker.group(1).strip()
            line = re.sub(r"<!--\s*HGPF_PAGE:[^>]+?\s*-->", "", line)
        elif printed_page:
            flush()
            page_hint = printed_page.group(1).strip()
        if _is_structural_boundary(line) and buffer:
            flush()
        if not line.strip():
            flush()
            continue
        buffer.append(line)
    flush()
    return units


def chunk_text(text: str, target_size: int = 760, overlap: int = 80) -> list[dict]:
    """Split into auditable passages without detaching headings from content."""
    chunks: list[dict] = []
    char_cursor = 0
    for page_hint, unit in _source_units(text):
        step = max(1, target_size - overlap)
        segments = (
            [unit]
            if len(unit) <= target_size
            else [unit[index : index + target_size] for index in range(0, len(unit), step)]
        )
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            start = text.find(segment[:80], char_cursor)
            if start < 0:
                start = char_cursor
            end = start + len(segment)
            score, flags = assess_text_quality(segment)
            chunks.append(
                {
                    "text": segment,
                    "page_hint": page_hint,
                    "char_start": start,
                    "char_end": end,
                    "quality_score": score,
                    "quality_flags": flags,
                }
            )
            char_cursor = max(char_cursor, end - overlap)
    return chunks


def assess_text_quality(text: str) -> tuple[float, list[str]]:
    """Estimate OCR usability, never historical truth or source credibility."""
    compact = re.sub(r"\s+", "", text)
    flags: list[str] = []
    penalty = 0.0
    if re.search(r"BLURRED|V[O○]LUME|WH[O○]LE|OCR\s*ERROR", text, re.I):
        flags.append("ocr_pipeline_warning")
        penalty += 0.28
    if compact:
        han = len(re.findall(r"[\u3400-\u9fff]", compact)) / len(compact)
        if han < 0.42:
            flags.append("low_han_ratio")
            penalty += 0.18
    if len(compact) < 35:
        flags.append("very_short")
        penalty += 0.12
    midpoint = len(compact) // 2
    if midpoint > 80 and compact[:midpoint] == compact[midpoint : midpoint * 2]:
        flags.append("repeated_ocr_block")
        penalty += 0.25
    return round(max(0.2, 1.0 - penalty), 3), flags


def _features(text: str, dimensions: int = 384) -> dict[str, float]:
    normalized = re.sub(r"\s+", "", text.lower())
    grams = [normalized[i : i + 2] for i in range(max(0, len(normalized) - 1))]
    counts: Counter[int] = Counter()
    for gram in grams:
        index = int(hashlib.blake2b(gram.encode("utf-8"), digest_size=4).hexdigest(), 16) % dimensions
        counts[index] += 1
    norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
    return {str(index): round(value / norm, 6) for index, value in counts.items()}


def import_document(
    path: Path,
    title: str | None = None,
    source_type: str = "族譜OCR",
    access_level: str = "研究使用",
) -> dict:
    path = path.resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"找不到檔案：{path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("僅支援 MD、TXT、PDF、DOCX。")
    raw_bytes = path.read_bytes()
    checksum = hashlib.sha256(raw_bytes).hexdigest()
    text, metadata = extract_text(path)
    if len(text) < 20:
        raise ValueError("文件沒有足夠的可索引文字；請先完成OCR或檢查檔案內容。")
    chunks = chunk_text(text)
    resolved_title = title or metadata.get("來源檔案") or path.stem
    with db_session() as db:
        existing = db.execute("SELECT * FROM documents WHERE checksum = ?", (checksum,)).fetchone()
        if existing:
            return {**existing, "duplicate": True}
        cursor = db.execute(
            """
            INSERT INTO documents(title, source_path, source_type, checksum, access_level,
                                  metadata_json, content_length, passage_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_title,
                str(path),
                source_type,
                checksum,
                access_level,
                json.dumps(metadata, ensure_ascii=False),
                len(text),
                len(chunks),
                utc_now(),
            ),
        )
        document_id = cursor.lastrowid
        for ordinal, chunk in enumerate(chunks, start=1):
            fields = infer_fields(chunk["text"])
            passage_cursor = db.execute(
                """
                INSERT INTO passages(document_id, ordinal, page_hint, text, char_start, char_end,
                                     hgpf_fields_json, vector_json, quality_score,
                                     quality_flags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    ordinal,
                    chunk["page_hint"],
                    chunk["text"],
                    chunk["char_start"],
                    chunk["char_end"],
                    json.dumps(fields),
                    json.dumps(_features(chunk["text"])),
                    chunk["quality_score"],
                    json.dumps(chunk["quality_flags"], ensure_ascii=False),
                ),
            )
            passage_id = passage_cursor.lastrowid
            db.execute(
                "INSERT INTO passages_fts(passage_id, title, text) VALUES (?, ?, ?)",
                (passage_id, resolved_title, chunk["text"]),
            )
        db.execute(
            """
            INSERT INTO processing_activities(
                activity_type, entity_type, entity_id, actor, tool_version,
                details_json, created_at
            ) VALUES ('文件匯入與索引', 'document', ?, 'system', 'hgpf-local-baseline-0.2', ?, ?)
            """,
            (
                document_id,
                json.dumps(
                    {
                        "checksum": checksum,
                        "source_path": str(path),
                        "passage_count": len(chunks),
                        "chunking": "structure-aware-v2",
                        "quality_signal": "ocr-usability-only",
                    },
                    ensure_ascii=False,
                ),
                utc_now(),
            ),
        )
    return {
        "id": document_id,
        "title": resolved_title,
        "source_path": str(path),
        "source_type": source_type,
        "access_level": access_level,
        "content_length": len(text),
        "passage_count": len(chunks),
        "metadata": metadata,
        "duplicate": False,
    }


def feature_vector(text: str) -> dict[str, float]:
    return _features(text)


def backfill_passage_quality() -> int:
    """Populate OCR usability signals for passages created before schema v2."""
    updated = 0
    with db_session() as db:
        rows = db.execute(
            """
            SELECT id, text FROM passages
            WHERE quality_flags_json = '[]' AND quality_score = 1.0
            """
        ).fetchall()
        for row in rows:
            score, flags = assess_text_quality(row["text"])
            if score != 1.0 or flags:
                db.execute(
                    "UPDATE passages SET quality_score=?, quality_flags_json=? WHERE id=?",
                    (score, json.dumps(flags, ensure_ascii=False), row["id"]),
                )
                updated += 1
    return updated


def backfill_page_hints() -> int:
    """Recover printed page labels from legacy OCR passages when available."""
    updated = 0
    with db_session() as db:
        rows = db.execute(
            "SELECT id, text FROM passages WHERE page_hint IS NULL OR page_hint = ''"
        ).fetchall()
        for row in rows:
            match = re.search(
                r"第\s*([一二三四五六七八九十百千〇零0-9]+)\s*[頁页]", row["text"]
            )
            if match:
                db.execute(
                    "UPDATE passages SET page_hint=? WHERE id=?",
                    (match.group(1).strip(), row["id"]),
                )
                updated += 1
    return updated
