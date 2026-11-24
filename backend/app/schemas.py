from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints, field_validator

TrimmedMessage = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: TrimmedMessage
    system_prompt: Optional[str] = Field(default=None, max_length=8000)

    @field_validator("session_id")
    @classmethod
    def _session_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("session_id must not be blank")
        return v

    @field_validator("system_prompt")
    @classmethod
    def _blank_prompt_is_none(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v and v.strip() else None
