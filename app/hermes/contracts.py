from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    content: str


class MarketingContextResult(BaseModel):
    """FastAPI 接受的 Skill 输出白名单；未声明字段会被拒绝。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["BLOCKED", "READY_WITH_WARNINGS", "READY"]
    generation_mode: Literal["COLD_START", "DATA_DRIVEN"] | None = None
    goal: dict[str, Any]
    product: dict[str, Any] | None = None
    missing_required: list[str]
    missing_optional: list[str]
    conflicts: list[dict[str, Any]]
    evidence: list[Evidence]
    warnings_accepted: bool = False
    next_action: str = Field(min_length=1)

