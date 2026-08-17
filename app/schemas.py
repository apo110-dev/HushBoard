"""Pydantic request models; response documents are assembled from persisted state."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_BECH32_DATA = frozenset("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_text(value: str, *, field: str) -> str:
    value = value.strip()
    if _CONTROL_RE.search(value):
        raise ValueError(f"{field} contains control characters")
    return value


def validate_testnet_ua(value: str) -> str:
    value = value.strip()
    if not 50 <= len(value) <= 512:
        raise ValueError("refund_address length is invalid")
    if not value.startswith("utest1"):
        raise ValueError("refund_address must use the testnet utest1 prefix")
    data = value[6:]
    if not data or any(ch not in _BECH32_DATA for ch in data):
        raise ValueError("refund_address is not a lowercase Unified Address")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=4096)


class SubmissionCreate(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=4000)
    refund_address: str = Field(min_length=20, max_length=512)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = _clean_text(value, field="title")
        # Titles are one line in cards; avoid visually deceptive whitespace.
        value = re.sub(r"\s+", " ", value)
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        value = _clean_text(value, field="body")
        if not value:
            raise ValueError("body must not be blank")
        return value

    @field_validator("refund_address")
    @classmethod
    def clean_refund_address(cls, value: str) -> str:
        return validate_testnet_ua(value)


class ModerationRequest(StrictModel):
    decision: Literal["refund", "keep"]
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _clean_text(value, field="note")
        return cleaned or None


class SeedRequest(StrictModel):
    reset: bool = True
    count: int = Field(default=8, ge=1, le=20)


class DemoSendRequest(StrictModel):
    # Kept as a model so unknown fields cannot silently alter a payment request.
    pass
