"""Evidence-constrained proof-summary writer."""

from __future__ import annotations

import json
import re

from .database import db_session, utc_now
from .hgpf import OCR_REVIEW_THRESHOLD


WRITING_MODES = {"一般證明摘要", "客家證據書寫"}
HAKKA_TERM_FORMS = (
    ("客家", "客家"),
    ("客族", "客族"),
    ("客語", "客語"),
    ("客話", "客話"),
    ("客话", "客話"),
    ("客籍", "客籍"),
)


def _excerpt(text: str, limit: int = 170) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else compact[:limit].rstrip() + "……"


def _hakka_terms(text: str) -> list[str]:
    terms: list[str] = []
    for raw, normalized in HAKKA_TERM_FORMS:
        if raw in text and normalized not in terms:
            terms.append(normalized)
    return terms


def generate_draft(
    claim_id: int,
    title: str | None = None,
    writing_mode: str = "一般證明摘要",
) -> dict:
    if writing_mode not in WRITING_MODES:
        raise ValueError("不支援的書寫模式。")
    with db_session() as db:
        claim = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if not claim:
            raise KeyError("找不到主張。")
        evidence = db.execute(
            """
            SELECT e.*, p.ordinal, p.page_hint, p.text, d.title AS document_title,
                   p.quality_score, p.quality_flags_json, d.source_path,
                   d.source_type, d.access_level
            FROM evidence_links e
            JOIN passages p ON p.id = e.passage_id
            JOIN documents d ON d.id = p.document_id
            WHERE e.claim_id = ? ORDER BY CASE e.relation
                WHEN '支持' THEN 1 WHEN '反駁' THEN 2 WHEN '限制' THEN 3 ELSE 4 END, e.id
            """,
            (claim_id,),
        ).fetchall()
        if not evidence:
            raise ValueError("至少掛接一筆證據後才能產生草稿。")

        support = [row for row in evidence if row["relation"] == "支持"]
        contradictions = [row for row in evidence if row["relation"] in {"反駁", "限制"}]
        conflict_resolved = bool(claim["resolution_note"].strip() and claim["reviewer"].strip())
        low_quality = [
            row
            for row in evidence
            if float(row["quality_score"] or 1.0) < OCR_REVIEW_THRESHOLD
        ]
        restricted = [row for row in evidence if row["access_level"] not in {"公開", "研究使用"}]
        support_documents = {row["document_title"] for row in support}

        if contradictions and not conflict_resolved:
            evidence_state = "衝突"
        elif not support:
            evidence_state = "證據不足"
        elif len(support_documents) < 2:
            evidence_state = "條件支持"
        else:
            evidence_state = "支持"

        draft_status = "Evidence-linked"
        if evidence_state in {"衝突", "證據不足"} or low_quality or restricted:
            draft_status = "Audit-flagged"

        citations = []
        grouped: dict[str, list[str]] = {"支持": [], "反駁": [], "限制": [], "脈絡": []}
        for index, row in enumerate(evidence, start=1):
            label = f"E{index}"
            locator = f"頁{row['page_hint']}" if row["page_hint"] else f"段落{row['ordinal']}"
            citations.append(
                {
                    "label": label,
                    "relation": row["relation"],
                    "document_title": row["document_title"],
                    "source_path": row["source_path"],
                    "locator": locator,
                    "excerpt": _excerpt(row["text"], 220),
                    "quality_score": float(row["quality_score"] or 1.0),
                    "quality_flags": json.loads(row["quality_flags_json"] or "[]"),
                    "access_level": row["access_level"],
                    "hakka_terms": _hakka_terms(row["text"]),
                }
            )
            grouped[row["relation"]].append(
                f"《{row['document_title']}》{locator}記載：「{_excerpt(row['text'])}」〔{label}〕"
            )

        confidence_word = {
            "支持": "在目前已揭露的證據範圍內獲得支持",
            "條件支持": "僅在目前版本與來源範圍內獲得條件支持",
            "衝突": "仍屬相互衝突的主張，須並陳異說",
            "證據不足": "尚無足夠支持證據，仍屬待查假設",
        }[evidence_state]
        detected_terms = list(
            dict.fromkeys(term for citation in citations for term in citation["hakka_terms"])
        )
        if writing_mode == "客家證據書寫" and not any(
            citation["relation"] == "支持" and citation["hakka_terms"]
            for citation in citations
        ):
            evidence_state = "客家證據不足"
            draft_status = "Audit-flagged"

        labels = "、".join(
            citation["label"]
            for citation in citations
            if citation["quality_score"] < OCR_REVIEW_THRESHOLD
        )
        if writing_mode == "客家證據書寫":
            source_sections = []
            for relation in ("支持", "反駁", "限制", "脈絡"):
                if grouped[relation]:
                    source_sections.append(f"{relation}：" + "；".join(grouped[relation]))
            if evidence_state == "客家證據不足":
                support_scope = (
                    "目前支持證據未明示客家、客族、客語、客話或客籍，不能形成客家身分、"
                    "語言使用或籍屬結論；現有材料最多只能作為後續檢索線索。"
                )
            else:
                support_scope = (
                    f"本主張{confidence_word}；結論只適用於來源明載的人物、房派、時間、地點與語境，"
                    "不得擴及未記載的世代或宗族成員。"
                )
            limitation_parts = []
            if grouped["反駁"] or grouped["限制"]:
                limitation_parts.append("存在已掛接的反駁或限制材料，必須與支持證據並陳。")
            if low_quality:
                limitation_parts.append(f"證據卡{labels}的OCR文字可用性偏低，須回看原頁。")
            if restricted:
                limitation_parts.append("草稿含宗族限定或敏感證據，不得直接公開。")
            if not limitation_parts:
                limitation_parts.append("目前未掛接反駁或限制材料，不代表相反證據不存在。")
            resolution = (
                f"具名研究者的處置說明：{claim['resolution_note']}。"
                if claim["resolution_note"].strip()
                else "尚須由具名研究者核對原頁、補查異本並說明剩餘不確定性。"
            )
            paragraphs = [
                f"客家證據主張：{claim['text']}",
                f"HGPF證據狀態：{evidence_state}。",
                "一、來源記載。" + (" ".join(source_sections) if source_sections else "尚無可引用來源。"),
                "來源明示詞：" + ("、".join(detected_terms) if detected_terms else "未檢出") +
                "。詞彙命中只用於定位原文，不等於族群身分或語言能力已獲證明。",
                "二、可支持範圍。" + support_scope,
                "三、禁止外推。不得以姓氏、堂號、祖籍、居住地或單一文化實作推定客家身分；"
                "不得把客籍等同客語能力，也不得把客語或客話使用直接等同族群自我認同。",
                "四、反證與限制。" + " ".join(limitation_parts),
                "五、人工責任。" + resolution +
                "本草稿只反映目前已掛接證據，不代表完成合理且詳盡的研究，也不構成GPS認證。",
            ]
        else:
            paragraphs = [
                f"考證主張：{claim['text']}",
                f"HGPF證據狀態：{evidence_state}。",
                "本段依目前已掛接的數位證據形成可審核草稿；未出現在證據卡中的細節不予補寫。",
            ]
            if grouped["支持"]:
                paragraphs.append("支持證據方面，" + "；".join(grouped["支持"]) + "。")
            if grouped["反駁"]:
                paragraphs.append("相反或衝突證據方面，" + "；".join(grouped["反駁"]) + "。")
            if grouped["限制"]:
                paragraphs.append("證據限制方面，" + "；".join(grouped["限制"]) + "。")
            if grouped["脈絡"]:
                paragraphs.append("可供理解但不直接證明本主張的脈絡材料包括：" + "；".join(grouped["脈絡"]) + "。")
            if low_quality:
                paragraphs.append(
                    f"OCR品質警示：證據卡{labels}的文字可用性偏低，須回看頁面影像或人工校訂後再判讀。"
                )
            if restricted:
                paragraphs.append("存取限制：草稿含宗族限定或敏感證據，不得直接轉為公開發布版本。")
            if grouped["反駁"] or grouped["限制"]:
                if claim["resolution_note"].strip():
                    paragraphs.append(f"研究者的衝突處置說明為：{claim['resolution_note']}。")
                else:
                    paragraphs.append("目前仍有反駁或限制證據尚待具名研究者處置，因此不得將本主張寫成確定事實。")
            paragraphs.append(
                f"綜合而言，本主張{confidence_word}。此判斷僅反映目前匯入語料與研究紀錄，"
                "不代表已完成合理且詳盡的研究，也不構成GPS認證。人工設定的信心標籤不得凌駕此證據狀態。"
            )
        content = "\n\n".join(paragraphs)
        draft_title = title or (
            f"{claim['subject']}客家證據書寫"
            if writing_mode == "客家證據書寫"
            else f"{claim['subject']}考證摘要"
        )
        now = utc_now()
        cursor = db.execute(
            """
            INSERT INTO drafts(claim_id, title, writing_mode, content, citations_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_id,
                draft_title,
                writing_mode,
                content,
                json.dumps(citations, ensure_ascii=False),
                draft_status,
                now,
                now,
            ),
        )
        draft_id = cursor.lastrowid
        db.execute(
            """
            INSERT INTO processing_activities(
                activity_type, entity_type, entity_id, actor, tool_version,
                details_json, created_at
            ) VALUES (?, 'draft', ?, 'system', 'writer-rules-v3', ?, ?)
            """,
            (
                writing_mode,
                draft_id,
                json.dumps(
                    {
                        "claim_id": claim_id,
                        "evidence_state": evidence_state,
                        "citation_count": len(citations),
                        "low_quality_count": len(low_quality),
                        "writing_mode": writing_mode,
                        "hakka_terms": detected_terms,
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        return {
            "id": draft_id,
            "claim_id": claim_id,
            "title": draft_title,
            "writing_mode": writing_mode,
            "content": content,
            "citations": citations,
            "detected_terms": detected_terms,
            "status": draft_status,
            "evidence_state": evidence_state,
            "created_at": now,
        }
