"""Document-level HGPF 31-field coverage and GPS readiness audit.

This module audits an uploaded source as research material. It never claims
that a single document or metadata coverage completes genealogical proof.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from .database import db_session, utc_now
from .hgpf import DOMAINS, FIELD_RECORDS, GPS_COMPONENTS, LAYERS, OCR_REVIEW_THRESHOLD


UNCERTAINTY_TERMS = ["相傳", "據傳", "或云", "疑", "失考", "待考", "未詳", "約", "或作", "不符"]
SENSITIVE_TERMS = ["收養", "非婚生", "疾病", "族產", "祖墓", "經緯度", "在世", "身分證"]


def _excerpt(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else compact[:limit].rstrip() + "……"


def _gps_item(component: dict, score: int, findings: list[str], actions: list[str]) -> dict:
    if score >= 16:
        status = "文件條件較完整"
    elif score >= 8:
        status = "需補強"
    else:
        status = "尚不能判定"
    return {
        "component": component["id"],
        "name": component["name"],
        "score": score,
        "status": status,
        "findings": findings,
        "actions": actions,
        "human_required": True,
    }


def audit_document(document_id: int) -> dict[str, Any]:
    with db_session() as db:
        document = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            raise KeyError("找不到文件。")
        passages = db.execute(
            """
            SELECT id, ordinal, page_hint, text, hgpf_fields_json,
                   quality_score, quality_flags_json
            FROM passages WHERE document_id=? ORDER BY ordinal
            """,
            (document_id,),
        ).fetchall()

        metadata = json.loads(document["metadata_json"] or "{}")
        field_passages: dict[int, list[dict]] = {field["id"]: [] for field in FIELD_RECORDS}
        domain_counts: Counter[str] = Counter()
        uncertainty_counts: Counter[str] = Counter()
        sensitive_counts: Counter[str] = Counter()
        low_quality: list[dict] = []
        page_located = 0

        for passage in passages:
            field_ids = json.loads(passage["hgpf_fields_json"] or "[]")
            flags = json.loads(passage["quality_flags_json"] or "[]")
            if passage["page_hint"]:
                page_located += 1
            if float(passage["quality_score"] or 1.0) < OCR_REVIEW_THRESHOLD:
                low_quality.append(passage)
            for field_id in field_ids:
                if field_id in field_passages:
                    field_passages[field_id].append(passage)
            for term in UNCERTAINTY_TERMS:
                uncertainty_counts[term] += passage["text"].count(term)
            for term in SENSITIVE_TERMS:
                sensitive_counts[term] += passage["text"].count(term)

        field_results = []
        for field in FIELD_RECORDS:
            matches = field_passages[field["id"]]
            for domain in field["domains"]:
                if matches:
                    domain_counts[domain] += 1
            field_results.append(
                {
                    "id": field["id"],
                    "category": field["category"],
                    "original_name": field["original_name"],
                    "local_name": field["local_name"],
                    "domains": field["domains"],
                    "audit_focus": field["audit_focus"],
                    "status": "候選命中（待人工確認）" if matches else "未找到候選／可能不適用",
                    "hit_count": len(matches),
                    "samples": [
                        {
                            "passage_id": passage["id"],
                            "ordinal": passage["ordinal"],
                            "page_hint": passage["page_hint"] or "",
                            "quality_score": float(passage["quality_score"] or 1.0),
                            "excerpt": _excerpt(passage["text"]),
                        }
                        for passage in matches[:2]
                    ],
                }
            )

        candidate_fields = sum(1 for item in field_results if item["hit_count"])
        passage_count = len(passages)
        page_ratio = page_located / passage_count if passage_count else 0.0
        quality_ratio = 1 - (len(low_quality) / passage_count) if passage_count else 0.0
        uncertainty_total = sum(uncertainty_counts.values())

        domain_results = [
            {
                **domain,
                "candidate_field_count": domain_counts.get(domain["id"], 0),
                "status": "有候選資料" if domain_counts.get(domain["id"], 0) else "未找到候選／可能不適用",
            }
            for domain in DOMAINS
        ]

        gps1 = _gps_item(
            GPS_COMPONENTS[0],
            4,
            ["本次只預檢1份上傳文件；單一文件不能證明研究範圍已合理且詳盡。"],
            ["先建立研究問題與ResearchPlan，再補查其他版本、戶籍、契約、墓碑、地方志或口述資料。"],
        )
        gps2_score = min(20, 6 + round(14 * page_ratio)) if passages else 0
        gps2 = _gps_item(
            GPS_COMPONENTS[1],
            gps2_score,
            [
                f"{page_located}/{passage_count} 個段落具有原頁提示（{page_ratio:.0%}）。",
                f"已保存SHA-256、來源路徑與{len(metadata)}項文件metadata。",
            ],
            ([] if page_ratio >= 0.8 else ["補登頁碼、卷冊或影像區塊；只有段落序號時不可視為完整引用。"]),
        )
        gps3_score = min(20, 5 + round(9 * quality_ratio) + min(6, candidate_fields // 4)) if passages else 0
        gps3 = _gps_item(
            GPS_COMPONENTS[2],
            gps3_score,
            [
                f"31項HGPF欄位中有{candidate_fields}項出現候選，均須人工確認。",
                f"{len(low_quality)}/{passage_count} 個段落低於OCR人工複核門檻。",
                f"偵測到{uncertainty_total}處限定／不確定語候選。",
            ],
            ["依研究問題建立Claim與EvidenceRelation；欄位命中和OCR分數都不是史料可信度。"],
        )
        gps4 = _gps_item(
            GPS_COMPONENTS[3],
            min(8, 3 + min(5, uncertainty_total)),
            [f"文件內找到{uncertainty_total}處異說、限定或未詳詞候選；尚未進行跨版本衝突比對。"],
            ["匯入其他版本並執行反證檢索；建立支持、反駁、限制與ConflictSet後由具名研究者處置。"],
        )
        gps5 = _gps_item(
            GPS_COMPONENTS[4],
            0,
            ["尚未建立原子主張、證據關係與逐句引用，因此不能稽核證明結論。"],
            ["選定待查主張、掛接證據卡並產生Audit-flagged或Evidence-linked草稿後再執行GPS5。"],
        )
        gps_items = [gps1, gps2, gps3, gps4, gps5]
        score = sum(item["score"] for item in gps_items)

        risks = []
        if page_ratio < 0.8:
            risks.append({"code": "CIT-01", "severity": "高", "message": "多數片段無原頁定位。"})
        if low_quality:
            risks.append({"code": "OCR-01", "severity": "中", "message": f"{len(low_quality)}個片段須回看影像或人工校訂。"})
        if uncertainty_total:
            risks.append({"code": "NAR-01", "severity": "中", "message": "草稿必須保留相傳、疑、失考、未詳等限定語。"})
        if sum(sensitive_counts.values()):
            risks.append({"code": "GOV-01", "severity": "高", "message": "偵測到可能涉及墓址、族產、疾病或身分的敏感詞，發布前須人工分級。"})
        risks.extend(
            [
                {"code": "EL-01", "severity": "規則", "message": "不得由姓氏、堂號、祖籍或居住地推定客家身分或客語腔調。"},
                {"code": "FS-04", "severity": "規則", "message": "不得把風水評語改寫成已證實的自然因果。"},
                {"code": "ID-01", "severity": "規則", "message": "不得只依姓名或字輩合併人物。"},
            ]
        )

        report = {
            "document": {
                "id": document["id"],
                "title": document["title"],
                "source_path": document["source_path"],
                "source_type": document["source_type"],
                "access_level": document["access_level"],
                "checksum": document["checksum"],
                "metadata": metadata,
                "passage_count": passage_count,
            },
            "summary": {
                "score": score,
                "level": "文件預檢完成；尚未形成譜系證明",
                "candidate_fields": candidate_fields,
                "total_fields": 31,
                "page_locator_ratio": round(page_ratio, 4),
                "ocr_review_count": len(low_quality),
                "uncertainty_count": uncertainty_total,
            },
            "layers": LAYERS,
            "domains": domain_results,
            "fields": field_results,
            "gps": gps_items,
            "risks": risks,
            "disclaimer": "本報告是上傳文件的HGPF欄位候選與GPS研究就緒度預檢；未命中不等於族譜缺漏，命中不等於史料可信，單一文件不能通過GPS證明。",
            "created_at": utc_now(),
        }
        cursor = db.execute(
            """
            INSERT INTO document_audits(document_id, score, level, report_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                document_id,
                score,
                report["summary"]["level"],
                json.dumps(report, ensure_ascii=False),
                report["created_at"],
            ),
        )
        report["audit_id"] = cursor.lastrowid
        db.execute(
            """
            INSERT INTO processing_activities(
                activity_type, entity_type, entity_id, actor, tool_version,
                details_json, created_at
            ) VALUES ('文件HGPF與GPS預檢', 'document_audit', ?, 'system',
                      'document-audit-v1', ?, ?)
            """,
            (
                report["audit_id"],
                json.dumps(
                    {"document_id": document_id, "candidate_fields": candidate_fields, "score": score},
                    ensure_ascii=False,
                ),
                report["created_at"],
            ),
        )
        return report
