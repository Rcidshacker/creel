"""One authoritative Pydantic schema per extraction job, validated post-hoc
against whatever an Extractor returns — SGAI's schema-guided output,
llm_direct's raw JSON, or (Phase 3) Firecrawl's JSON schema. None of them
are trusted blindly. On validation failure, extract/pipeline.py runs ONE
guided retry feeding the validator's errors back as corrective context,
then gives up. Unvalidated LLM JSON is `data: dict` fiction; this module is
what makes `data` trustworthy.
"""
from __future__ import annotations

from typing import Optional, Type

from pydantic import BaseModel, ValidationError


def validate(data: Optional[dict], schema: Optional[Type[BaseModel]]) -> tuple[Optional[dict], Optional[str]]:
    """Returns (validated_dict, error). error is None on success. With no
    schema, any non-None dict passes through unchanged — there is nothing
    to validate against."""
    if schema is None:
        return data, None
    if data is None:
        return None, "no data to validate"
    try:
        instance = schema.model_validate(data)
    except ValidationError as e:
        return None, str(e)
    return instance.model_dump(), None


def retry_prompt(original_prompt: str, error: str) -> str:
    return (
        f"{original_prompt}\n\n"
        f"Your previous answer did not match the required schema. Validation error:\n{error}\n"
        f"Please respond again, correcting these issues."
    )
