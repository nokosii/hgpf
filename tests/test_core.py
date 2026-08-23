from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class HGPFCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        os.environ["HGPF_DATA_DIR"] = cls.tempdir.name
        os.environ["HGPF_DB_PATH"] = str(Path(cls.tempdir.name) / "test.db")
        from app.database import init_db

        init_db()

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_framework_has_31_fields_and_four_layers(self):
        from app.hgpf import FIELD_RECORDS, LAYERS

        self.assertEqual(31, len(FIELD_RECORDS))
        self.assertEqual(["M1", "M2", "M3", "M4"], [layer["id"] for layer in LAYERS])

    def test_ingest_search_claim_audit_and_write(self):
        from app.audit import audit_claim
        from app.database import db_session, utc_now
        from app.document_audit import audit_document
        from app.ingest import import_document
        from app.retrieval import search
        from app.writer import generate_draft

        source = Path(self.tempdir.name) / "sample.md"
        source.write_text(
            "# 張氏族譜\n\n先祖原居廣東大埔，後遷臺灣苗栗。\n\n另譜對開基年代未詳，疑有傳抄之誤。",
            encoding="utf-8",
        )
        document = import_document(source)
        self.assertGreater(document["passage_count"], 0)
        document_report = audit_document(document["id"])
        self.assertEqual(31, len(document_report["fields"]))
        self.assertEqual(5, len(document_report["gps"]))
        self.assertIn("尚未形成譜系證明", document_report["summary"]["level"])
        self.assertIn("單一文件不能通過GPS證明", document_report["disclaimer"])
        with db_session() as db:
            now = utc_now()
            cursor = db.execute(
                """
                INSERT INTO claims(claim_type, subject, text, hgpf_field_id, confidence, status, created_at, updated_at)
                VALUES ('遷徙主張','張氏','張氏先祖由廣東大埔遷臺',18,'可能','草稿',?,?)
                """,
                (now, now),
            )
            claim_id = cursor.lastrowid
        results = search("廣東 大埔 遷臺", claim_id=claim_id)
        self.assertTrue(results)
        with db_session() as db:
            db.execute(
                "INSERT INTO evidence_links(claim_id,passage_id,relation,weight,note,created_at) VALUES (?,?,?,?,?,?)",
                (claim_id, results[0]["passage_id"], "支持", 0.8, "測試", utc_now()),
            )
        draft = generate_draft(claim_id)
        self.assertIn("不構成GPS認證", draft["content"])
        self.assertEqual("條件支持", draft["evidence_state"])
        audit = audit_claim(claim_id)
        self.assertEqual(5, len(audit["items"]))
        self.assertLessEqual(audit["score"], 100)
        self.assertLess(audit["items"][1]["score"], 20)  # no original-page locator

    def test_chunking_removes_pipeline_wrapper_and_flags_bad_ocr(self):
        from app.ingest import assess_text_quality, chunk_text, strip_processing_wrapper

        wrapped = (
            "# sample\n\n> **來源檔案**：sample.pdf\n> **OCR 引擎**：test\n\n---\n\n"
            "## 譜序\n\n江氏祖祠位於坑河村。"
        )
        body = strip_processing_wrapper(wrapped)
        self.assertNotIn("OCR 引擎", body)
        chunks = chunk_text(body, target_size=40, overlap=5)
        self.assertTrue(chunks)
        self.assertTrue(all(len(item["text"]) <= 40 for item in chunks))
        score, flags = assess_text_quality("V○LUME WH○LE BLURRED D○CUMENT 1234")
        self.assertLess(score, 0.65)
        self.assertIn("ocr_pipeline_warning", flags)

    def test_search_only_proposes_candidates(self):
        from app.database import db_session, utc_now
        from app.retrieval import search

        with db_session() as db:
            now = utc_now()
            cursor = db.execute(
                """
                INSERT INTO claims(claim_type, subject, text, confidence, status, created_at, updated_at)
                VALUES ('遷徙主張','候選測試','某祖由廣東遷臺','已證','草稿',?,?)
                """,
                (now, now),
            )
            claim_id = cursor.lastrowid
        self.assertTrue(search("廣東 遷臺", claim_id=claim_id))
        with db_session() as db:
            count = db.execute(
                "SELECT COUNT(*) AS n FROM evidence_links WHERE claim_id=?", (claim_id,)
            ).fetchone()["n"]
        self.assertEqual(0, count)

    def test_unresolved_conflict_cannot_be_published(self):
        from fastapi import HTTPException

        from app.database import db_session, utc_now
        from app.main import review_draft
        from app.schemas import DraftReview
        from app.writer import generate_draft

        with db_session() as db:
            now = utc_now()
            passage_id = db.execute("SELECT id FROM passages ORDER BY id LIMIT 1").fetchone()["id"]
            cursor = db.execute(
                """
                INSERT INTO claims(claim_type, subject, text, confidence, status, created_at, updated_at)
                VALUES ('譜系主張','衝突閘門','A為B之子','已證','草稿',?,?)
                """,
                (now, now),
            )
            claim_id = cursor.lastrowid
            for relation in ("支持", "限制"):
                db.execute(
                    """
                    INSERT INTO evidence_links(claim_id,passage_id,relation,weight,note,created_at)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (claim_id, passage_id, relation, 0.5, "測試", now),
                )
        draft = generate_draft(claim_id)
        self.assertEqual("衝突", draft["evidence_state"])
        with self.assertRaises(HTTPException) as caught:
            review_draft(
                draft["id"],
                DraftReview(
                    status="Approved-for-publication",
                    reviewer="測試者",
                    review_note="嘗試發布",
                ),
            )
        self.assertEqual(409, caught.exception.status_code)


if __name__ == "__main__":
    unittest.main()
