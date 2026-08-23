"""API request models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ImportDocumentRequest(BaseModel):
    path: str
    title: str | None = None
    source_type: str = "族譜OCR"
    access_level: str = "研究使用"


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=12, ge=1, le=40)
    counterevidence: bool = False
    document_ids: list[int] = Field(default_factory=list)
    hgpf_field_id: int | None = Field(default=None, ge=1, le=31)
    claim_id: int | None = None


class ClaimCreate(BaseModel):
    claim_type: str = "譜系主張"
    subject: str = Field(min_length=1)
    text: str = Field(min_length=3)
    asserted_value: str = ""
    hgpf_field_id: int | None = Field(default=None, ge=1, le=31)
    confidence: Literal["已證", "很可能", "可能", "待查"] = "待查"


class ClaimUpdate(BaseModel):
    confidence: Literal["已證", "很可能", "可能", "待查"] | None = None
    status: Literal["草稿", "稽核中", "人工複核", "可發布", "待補證"] | None = None
    resolution_note: str | None = None
    reviewer: str | None = None


class EvidenceCreate(BaseModel):
    passage_id: int
    relation: Literal["支持", "反駁", "限制", "脈絡"]
    weight: float = Field(default=0.5, ge=0, le=1)
    note: str = ""


class DraftCreate(BaseModel):
    claim_id: int
    title: str | None = None


class DraftReview(BaseModel):
    status: Literal["Evidence-linked", "Audit-flagged", "Human-reviewed", "Approved-for-publication", "Needs-further-research"]
    reviewer: str = ""
    review_note: str = ""


class SeedRequest(BaseModel):
    reset: bool = False
