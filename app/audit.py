"""GPS-aligned audit engine. It produces findings, never certification."""

from __future__ import annotations

import json
from typing import Any

from .database import db_session, utc_now
from .hgpf import OCR_REVIEW_THRESHOLD


def _item(component: str, name: str, score: int, findings: list[str], actions: list[str], human_required: bool = True) -> dict:
    if score >= 16:
        status = "通過初檢"
    elif score >= 9:
        status = "需補強"
    else:
        status = "未通過"
    return {
        "component": component,
        "name": name,
        "score": score,
        "status": status,
        "findings": findings,
        "actions": actions,
        "human_required": human_required,
    }


def audit_claim(claim_id: int) -> dict[str, Any]:
    with db_session() as db:
        claim = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if not claim:
            raise KeyError("找不到主張。")
        evidence = db.execute(
            """
            SELECT e.*, p.ordinal, p.page_hint, p.text, p.quality_score,
                   p.quality_flags_json, d.id AS document_id,
                   d.title AS document_title, d.source_path, d.source_type
            FROM evidence_links e
            JOIN passages p ON p.id = e.passage_id
            JOIN documents d ON d.id = p.document_id
            WHERE e.claim_id = ? ORDER BY e.id
            """,
            (claim_id,),
        ).fetchall()
        events = db.execute(
            "SELECT * FROM research_events WHERE claim_id = ? ORDER BY id", (claim_id,)
        ).fetchall()
        drafts = db.execute(
            "SELECT * FROM drafts WHERE claim_id = ? ORDER BY id DESC", (claim_id,)
        ).fetchall()

        document_ids = {row["document_id"] for row in evidence}
        source_types = {row["source_type"] for row in evidence}
        counter_events = [row for row in events if row["mode"] == "反證檢索"]
        gps1_score = min(20, len(events) * 3 + len(document_ids) * 4 + len(source_types) * 2 + (5 if counter_events else 0))
        gps1_findings = [f"已記錄 {len(events)} 次檢索、{len(document_ids)} 份文獻、{len(source_types)} 種來源類型。"]
        gps1_actions = []
        if len(document_ids) < 2:
            gps1_actions.append("至少加入另一份獨立來源，避免以單一族譜定論。")
        if not counter_events:
            gps1_actions.append("執行反證導向檢索並保存查詢式、範圍與結果。")
        gps1_actions.append("由研究者界定檔案館、資料庫與田野範圍；系統不能宣告研究已合理窮盡。")

        citable = [row for row in evidence if row["document_title"] and row["ordinal"]]
        page_located = [row for row in evidence if row["page_hint"]]
        gps2_score = 0 if not evidence else min(
            20,
            6
            + round(6 * len(citable) / len(evidence))
            + round(8 * len(page_located) / len(evidence)),
        )
        gps2_findings = [
            f"{len(citable)}/{len(evidence)} 筆具文件名與段落定位；"
            f"{len(page_located)}/{len(evidence)} 筆可定位至原頁。"
        ]
        gps2_actions = [] if evidence else ["為主張掛接至少一筆可回到原文的證據。"]
        if any(not row["page_hint"] for row in evidence):
            gps2_actions.append("補登缺少的頁碼／影像區塊；段落序號僅是雛型定位。")

        relations = {row["relation"] for row in evidence}
        low_quality = [
            row
            for row in evidence
            if float(row["quality_score"] or 1.0) < OCR_REVIEW_THRESHOLD
        ]
        gps3_score = min(20, len(evidence) * 4 + len(relations) * 3 + (3 if claim["hgpf_field_id"] else 0))
        gps3_score = max(0, gps3_score - min(6, len(low_quality) * 2))
        gps3_findings = [f"已建立 {len(evidence)} 筆證據關係：{'、'.join(sorted(relations)) or '尚無'}。"]
        if low_quality:
            gps3_findings.append(
                f"其中 {len(low_quality)} 筆OCR文字可用性偏低；品質分數不代表史料可信度。"
            )
        gps3_actions = []
        if len(evidence) < 2:
            gps3_actions.append("增加可相互關聯的證據，並區分支持、限制、反駁與脈絡。")
        if not claim["hgpf_field_id"]:
            gps3_actions.append("指定HGPF欄位，套用相應的在地化稽核規則。")

        contradictions = [row for row in evidence if row["relation"] in {"反駁", "限制"}]
        resolved = bool(claim["resolution_note"].strip() and claim["reviewer"].strip())
        if not contradictions:
            gps4_score = 12 if counter_events else 6
            gps4_findings = ["目前未掛接反駁／限制證據；這不等於不存在衝突。"]
            gps4_actions = ["完成反證檢索後，由研究者確認是否存在未處理的異說。"]
        elif resolved:
            gps4_score = 20
            gps4_findings = [f"已揭露 {len(contradictions)} 筆反駁／限制證據，並留有人工處置說明。"]
            gps4_actions = ["發表前再次核對處置說明是否逐一回應關鍵衝突。"]
        else:
            gps4_score = 8
            gps4_findings = [f"發現 {len(contradictions)} 筆反駁／限制證據，但尚無具名人工處置。"]
            gps4_actions = ["填寫衝突處置說明與覆核者；AI不得自行宣告衝突已解決。"]

        latest_draft = drafts[0] if drafts else None
        citation_count = len(json.loads(latest_draft["citations_json"])) if latest_draft else 0
        gps5_score = min(20, (8 if latest_draft else 0) + citation_count * 2 + (4 if latest_draft and latest_draft["status"] in {"Human-reviewed", "Approved-for-publication"} else 0))
        if latest_draft and latest_draft["status"] == "Audit-flagged":
            gps5_score = max(0, gps5_score - 4)
        gps5_findings = [f"已有 {len(drafts)} 版證明草稿，最近一版含 {citation_count} 筆證據引用。"]
        gps5_actions = [] if latest_draft else ["產生受證據約束的證明摘要，並逐句人工覆核。"]
        if latest_draft and latest_draft["status"] not in {"Human-reviewed", "Approved-for-publication"}:
            gps5_actions.append("草稿尚未經具名人工覆核，不可作為發布結論。")

        items = [
            _item("GPS1", "合理且詳盡的研究", gps1_score, gps1_findings, gps1_actions),
            _item("GPS2", "完整且準確的來源引用", gps2_score, gps2_findings, gps2_actions),
            _item("GPS3", "可靠的證據關聯與詮釋", gps3_score, gps3_findings, gps3_actions),
            _item("GPS4", "解決相互矛盾的證據", gps4_score, gps4_findings, gps4_actions),
            _item("GPS5", "嚴密推理且條理分明的結論", gps5_score, gps5_findings, gps5_actions),
        ]
        total = sum(item["score"] for item in items)
        if total >= 80 and resolved and latest_draft and latest_draft["status"] in {"Human-reviewed", "Approved-for-publication"}:
            level = "具HGPF內部發布條件（非GPS認證）"
        elif total >= 55:
            level = "可進入人工複核"
        else:
            level = "證據與研究紀錄仍待補強"
        result = {
            "claim_id": claim_id,
            "score": total,
            "level": level,
            "items": items,
            "disclaimer": "本結果是HGPF雛型的GPS導向稽核提示，不是BCG認證，也不能證明研究已合理窮盡。",
            "created_at": utc_now(),
        }
        cursor = db.execute(
            "INSERT INTO audit_runs(claim_id, score, level, findings_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (claim_id, total, level, json.dumps(result, ensure_ascii=False), result["created_at"]),
        )
        result["audit_id"] = cursor.lastrowid
        return result
