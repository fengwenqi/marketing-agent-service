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
        "plugin_mapping",
        "retrieved_product_spec",
        "retrieved_marketing_material",
        "user_confirmed",
        "agent_suggested",
        "unresolved",
        "conflict",
    ]
    source: str | None = None
    # Hermes 会同时返回该字段当前采用的值，方便前端解释结构化上下文的来源。
    # 值可能是字符串、数字、列表或对象，因此这里保留 JSON 兼容的开放类型。
    value: Any | None = None
    # 对缺失、冲突或来源选择的简短说明；与 source 的来源标识职责不同。
    note: str | None = None


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

    @field_validator("missing_required", "missing_optional", mode="before")
    @classmethod
    def normalize_missing_fields(cls, value: Any) -> Any:
        """兼容模型偶尔返回的 {field, reason}，对外仍保持字符串数组契约。"""

        if not isinstance(value, list):
            return value
        normalized: list[Any] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("field"), str):
                normalized.append(item["field"])
            else:
                normalized.append(item)
        return normalized

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

