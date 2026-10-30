from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PersistenceResult(BaseModel):
    """记录 Skill 是否真正完成持久化，避免模型虚构数据库写入。"""

    model_config = ConfigDict(extra="forbid")

    saved: bool
    note: str


class FieldSource(BaseModel):
    """记录每个上下文字段的可信来源状态。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "user_provided",
        "retrieved_product_spec",
        "retrieved_marketing_material",
        "user_confirmed",
        "agent_suggested",
        "unresolved",
        "conflict",
    ]
    source: str | None = None


class MarketingContextResult(BaseModel):
    """FastAPI 接受的 Skill 输出白名单；未声明字段会被拒绝。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    status: Literal["BLOCKED", "READY_WITH_WARNINGS", "READY"]
    generation_mode: Literal["COLD_START", "DATA_DRIVEN"] | None = None
    warnings_accepted: bool = False
    original_request: str
    goal: dict[str, Any]
    product_profile: dict[str, Any]
    enterprise_profile: dict[str, Any]
    marketing_evidence: dict[str, Any]
    missing_required: list[str]
    missing_optional: list[str]
    conflicts: list[dict[str, Any]]
    field_sources: dict[str, FieldSource]
    evidence: list[dict[str, Any]]
    next_action: str = Field(min_length=1)
    next_question: str | None = None
    persistence: PersistenceResult

    @field_validator("field_sources", mode="before")
    @classmethod
    def normalize_field_sources(cls, value: Any) -> Any:
        """兼容 Skill 的简写状态，并统一为带来源信息的标准对象。"""

        if not isinstance(value, dict):
            return value
        return {
            field_name: {"status": source, "source": None}
            if isinstance(source, str)
            else source
            for field_name, source in value.items()
        }

