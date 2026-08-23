"""Hybrid local retrieval with lexical, character-vector and counterevidence signals."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .database import db_session, utc_now
from .hgpf import infer_fields
from .ingest import feature_vector


COUNTEREVIDENCE_TERMS = ["異說", "另載", "不符", "矛盾", "誤", "疑", "失考", "未詳", "傳抄", "但", "然", "或云"]
QUERY_LEXICON = [
    "廣東", "大埔", "梅縣", "蕉嶺", "鎮平", "嘉應", "福建", "詔安", "臺灣", "台灣",
    "遷臺", "遷台", "渡臺", "渡台", "祖籍", "開基", "始祖", "源流", "世系", "字派",
    "祖源", "來臺祖", "來台祖", "開基祖", "房派", "世次", "承嗣", "過房", "兼祧",
    "客家", "客語", "腔調", "四縣", "海陸", "墓葬", "祖墳", "風水", "坐向", "祠堂",
    "異說", "失考", "不符", "傳抄", "族譜", "戶籍", "契約", "碑文",
]


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def _fts_query(query: str) -> str:
    cleaned = re.sub(r'["*():^{}\[\]]', " ", query)
    tokens = [token for token in re.split(r"\s+", cleaned) if token]
    if not tokens:
        return '"族譜"'
    return " OR ".join(f'"{token}"' for token in tokens[:12])


def _surface_terms(query: str) -> list[str]:
    """Extract human-readable Chinese search concepts for exact surface matching."""
    terms = [token for token in re.split(r"[\s,，。；;、/]+", query) if len(token) >= 2]
    terms.extend(term for term in QUERY_LEXICON if term in query)
    deduplicated: list[str] = []
    for term in terms:
        if term not in deduplicated:
            deduplicated.append(term)
    return deduplicated[:18]


def search(
    query: str,
    limit: int = 12,
    counterevidence: bool = False,
    document_ids: list[int] | None = None,
    hgpf_field_id: int | None = None,
    claim_id: int | None = None,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    q_vector = feature_vector(query)
    surface_terms = _surface_terms(query)
    likely_fields = set(infer_fields(query))
    if hgpf_field_id:
        likely_fields.add(hgpf_field_id)

    with db_session() as db:
        lexical_rows: list[dict] = []
        try:
            lexical_rows = db.execute(
                """
                SELECT p.id, p.document_id, p.ordinal, p.page_hint, p.text,
                       p.hgpf_fields_json, p.vector_json, p.quality_score,
                       p.quality_flags_json, d.title, d.source_path,
                       d.source_type, d.access_level, bm25(passages_fts) AS lexical_rank
                FROM passages_fts
                JOIN passages p ON p.id = passages_fts.passage_id
                JOIN documents d ON d.id = p.document_id
                WHERE passages_fts MATCH ?
                ORDER BY lexical_rank
                LIMIT ?
                """,
                (_fts_query(query), max(40, limit * 5)),
            ).fetchall()
        except Exception:
            lexical_rows = []

        where = []
        params: list[Any] = []
        if document_ids:
            placeholders = ",".join("?" for _ in document_ids)
            where.append(f"p.document_id IN ({placeholders})")
            params.extend(document_ids)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        semantic_rows = db.execute(
            f"""
            SELECT p.id, p.document_id, p.ordinal, p.page_hint, p.text,
                   p.hgpf_fields_json, p.vector_json, p.quality_score,
                   p.quality_flags_json, d.title, d.source_path,
                   d.source_type, d.access_level, 0 AS lexical_rank
            FROM passages p JOIN documents d ON d.id = p.document_id
            {where_sql}
            LIMIT 12000
            """,
            params,
        ).fetchall()

        candidates: dict[int, dict] = {row["id"]: row for row in semantic_rows}
        lexical_position = {row["id"]: index for index, row in enumerate(lexical_rows)}
        for row in lexical_rows:
            candidates[row["id"]] = row

        results = []
        for row in candidates.values():
            if document_ids and row["document_id"] not in document_ids:
                continue
            fields = set(json.loads(row["hgpf_fields_json"] or "[]"))
            if hgpf_field_id and hgpf_field_id not in fields:
                continue
            vector = json.loads(row["vector_json"] or "{}")
            semantic_score = max(0.0, min(1.0, cosine(q_vector, vector)))
            lex_index = lexical_position.get(row["id"])
            reciprocal_score = 1 / (1 + lex_index) if lex_index is not None else 0.0
            surface_hits = [term for term in surface_terms if term in row["text"]]
            surface_score = len(surface_hits) / len(surface_terms) if surface_terms else 0.0
            lexical_score = max(reciprocal_score, surface_score)
            field_score = 1.0 if fields & likely_fields else 0.0
            text = row["text"]
            counter_hits = [term for term in COUNTEREVIDENCE_TERMS if term in text]
            counter_score = min(1.0, len(counter_hits) / 2) if counterevidence else 0.0
            exact_score = 1.0 if query in text else 0.0
            raw_score = (
                lexical_score * 0.35
                + semantic_score * 0.35
                + field_score * 0.12
                + exact_score * 0.08
                + counter_score * 0.10
            )
            # OCR usability is a retrieval signal, not a statement about the
            # historical credibility of the source. Low-quality text remains
            # retrievable but cannot outrank an otherwise equivalent clean hit.
            quality_score = float(row.get("quality_score") or 1.0)
            score = raw_score * (0.55 + 0.45 * quality_score)
            if score < 0.08 and len(results) > limit * 5:
                continue
            results.append(
                {
                    "passage_id": row["id"],
                    "document_id": row["document_id"],
                    "document_title": row["title"],
                    "source_path": row["source_path"],
                    "source_type": row["source_type"],
                    "access_level": row["access_level"],
                    "ordinal": row["ordinal"],
                    "page_hint": row["page_hint"] or "",
                    "text": text,
                    "hgpf_fields": sorted(fields),
                    "quality_score": round(quality_score, 3),
                    "quality_flags": json.loads(row.get("quality_flags_json") or "[]"),
                    "score": round(score, 4),
                    "signals": {
                        "lexical": round(lexical_score, 4),
                        "surface_terms": surface_hits,
                        "semantic": round(semantic_score, 4),
                        "metadata": round(field_score, 4),
                        "counterevidence": counter_hits,
                    },
                }
            )
        results.sort(key=lambda item: (-item["score"], item["passage_id"]))
        selected = results[:limit]
        db.execute(
            """
            INSERT INTO research_events(claim_id, query, mode, filters_json, result_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                claim_id,
                query,
                "反證檢索" if counterevidence else "混合檢索",
                json.dumps(
                    {
                        "document_ids": document_ids or [],
                        "hgpf_field_id": hgpf_field_id,
                        "retrieval_version": "hybrid-local-v2",
                        "quality_signal": "ocr-usability-only",
                    },
                    ensure_ascii=False,
                ),
                len(selected),
                utc_now(),
            ),
        )
        return selected
