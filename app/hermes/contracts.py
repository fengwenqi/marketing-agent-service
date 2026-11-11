from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class StrategyMaterialPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_count_min: int = Field(ge=1)
    target_count_max: int = Field(ge=1)
    oral_video_count: int = Field(ge=0)
    short_video_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "StrategyMaterialPlan":
        if self.target_count_min > self.target_count_max:
            raise ValueError("material count minimum cannot exceed maximum")
        return self


class StrategyDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget_ratio: int = Field(ge=0, le=100)
    channel: Literal["douyin"]
    launch_form: Literal["qianchuan_universal_live_room"]
    bidding: str = Field(min_length=1)
    targeting: str = Field(min_length=1)


class StrategyContentBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["A", "B", "C"]
    communication_theme: str = Field(min_length=1)
    target_audience: list[str] = Field(min_length=1)
    core_selling_points: list[str] = Field(min_length=1)
    content_focus: str = Field(min_length=1)
    content_directions: list[str] = Field(min_length=1)
    channel: Literal["douyin"]
    launch_object: Literal["live_room"]
    bidding: str = Field(min_length=1)
    material_package: str = Field(min_length=1)
    stage_inputs: list[str] = Field(min_length=1)
    execution_progression: str = Field(min_length=1)
    conversion_path: list[str] = Field(min_length=1)
    rhythm: str = Field(min_length=1)
    core_metrics: list[str] = Field(min_length=1)


class MarketingStageStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["A", "B", "C"]
    name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    lifecycle: str = Field(min_length=1)
    tagline: str = Field(min_length=1)
    communication_theme: str = Field(min_length=1)
    target_audience: list[str] = Field(min_length=1)
    core_selling_points: list[str] = Field(min_length=1)
    content_directions: list[str] = Field(min_length=1)
    material_plan: StrategyMaterialPlan
    delivery: StrategyDelivery
    success_criteria: list[str] = Field(min_length=1)
    next_stage_inputs: list[str]
    content_brief: StrategyContentBrief


class MarketingStrategyResult(BaseModel):
    """Hermes 策略 Skill 的严格白名单输出。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    framework_summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    stages: list[MarketingStageStrategy] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_abc_framework(self) -> "MarketingStrategyResult":
        if [stage.code for stage in self.stages] != ["A", "B", "C"]:
            raise ValueError("strategy stages must be ordered A, B, C")

        expected = {
            "A": ("起量赛马", 50, 15, 15, 9, 6),
            "B": ("收敛优化", 30, 5, 5, 3, 2),
            "C": ("集中收口", 20, 1, 2, 1, 1),
        }
        for stage in self.stages:
            name, ratio, count_min, count_max, oral, short = expected[stage.code]
            actual = (
                stage.name,
                stage.delivery.budget_ratio,
                stage.material_plan.target_count_min,
                stage.material_plan.target_count_max,
                stage.material_plan.oral_video_count,
                stage.material_plan.short_video_count,
            )
            if actual != (name, ratio, count_min, count_max, oral, short):
                raise ValueError(f"stage {stage.code} does not match the fixed framework")
            if stage.content_brief.stage != stage.code:
                raise ValueError("content brief stage must match strategy stage")
        return self


DecimalString = str


def normalize_decimal_string(value: Any) -> Any:
    """兼容 LLM 把十进制字符串输出为 JSON 数字，同时避免浮点参与业务计算。"""

    if value is None or isinstance(value, str) or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation:
            return value
        if decimal_value.is_finite():
            return format(decimal_value, "f")
    return value


class MediaPlanDeliveryObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["douyin"]
    platform: Literal["qianchuan"]
    launch_target: Literal["live_room"]
    objective: str = Field(min_length=1)
    target_roi: DecimalString | None = Field(
        default=None, pattern=r"^\d+(?:\.\d+)?$"
    )

    _normalize_target_roi = field_validator("target_roi", mode="before")(
        normalize_decimal_string
    )


class MediaPlanAudiencePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    targeting: list[str] = Field(min_length=1)


class MediaPlanBudgetUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    allocation_ratio: DecimalString = Field(pattern=r"^\d+(?:\.\d+)?$")
    amount: DecimalString | None = Field(default=None, pattern=r"^\d+(?:\.\d+)?$")
    audience_package_ids: list[str] = Field(min_length=1)

    _normalize_decimal_fields = field_validator(
        "allocation_ratio", "amount", mode="before"
    )(normalize_decimal_string)


class MediaPlanBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_budget: DecimalString | None = Field(
        default=None, pattern=r"^\d+(?:\.\d+)?$"
    )
    units: list[MediaPlanBudgetUnit] = Field(min_length=1)

    _normalize_stage_budget = field_validator("stage_budget", mode="before")(
        normalize_decimal_string
    )


class MediaPlanScheduleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    budget_unit_ids: list[str] = Field(min_length=1)
    description: str = Field(min_length=1)


class MediaPlanBiddingStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(min_length=1)
    optimization_goal: str = Field(min_length=1)
    bid_guidance: str = Field(min_length=1)


class MediaPlanTestCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    audience_package_id: str = Field(min_length=1)
    creative_slot_ids: list[str] = Field(min_length=1)
    budget_unit_id: str = Field(min_length=1)


class MediaPlanCreativeSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    format: Literal["oral_video", "short_video"]
    direction: str = Field(min_length=1)
    test_variable: str = Field(min_length=1)
    priority: int = Field(ge=1)


class MediaPlanMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    target: str = Field(min_length=1)
    window: str = Field(min_length=1)


class MediaPlanOptimizationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1)
    action: str = Field(min_length=1)


class MediaPlanRiskControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk: str = Field(min_length=1)
    mitigation: str = Field(min_length=1)


class StageMediaPlanResult(BaseModel):
    """A 阶段投放计划 Skill 的严格白名单输出。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    stage_code: Literal["A"]
    plan_summary: str = Field(min_length=1)
    planning_assumptions: list[str]
    delivery_objective: MediaPlanDeliveryObjective
    audience_packages: list[MediaPlanAudiencePackage] = Field(min_length=1)
    budget_plan: MediaPlanBudget
    schedule: list[MediaPlanScheduleEntry] = Field(min_length=1)
    bidding_strategy: MediaPlanBiddingStrategy
    testing_matrix: list[MediaPlanTestCell] = Field(min_length=1)
    creative_slots: list[MediaPlanCreativeSlot] = Field(min_length=15, max_length=15)
    monitoring_metrics: list[MediaPlanMetric] = Field(min_length=1)
    optimization_rules: list[MediaPlanOptimizationRule] = Field(min_length=1)
    risk_controls: list[MediaPlanRiskControl] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_A_creative_slots(self) -> "StageMediaPlanResult":
        collections = {
            "audience package": [item.id for item in self.audience_packages],
            "budget unit": [item.id for item in self.budget_plan.units],
            "schedule": [item.id for item in self.schedule],
            "test cell": [item.id for item in self.testing_matrix],
            "creative slot": [item.id for item in self.creative_slots],
        }
        for label, ids in collections.items():
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label} ids must be unique")
        oral_count = sum(slot.format == "oral_video" for slot in self.creative_slots)
        short_count = sum(slot.format == "short_video" for slot in self.creative_slots)
        if (oral_count, short_count) != (9, 6):
            raise ValueError("A stage requires 9 oral and 6 short video slots")
        return self


class VideoScriptScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_no: int = Field(ge=1)
    start_second: int = Field(ge=0)
    end_second: int = Field(gt=0)
    visual_description: str = Field(min_length=1)
    shot_type: str = Field(min_length=1)
    voiceover: str = Field(min_length=1)
    subtitle: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "VideoScriptScene":
        if self.end_second <= self.start_second:
            raise ValueError("scene end_second must be greater than start_second")
        return self


class VideoScriptResult(BaseModel):
    """Hermes 单个创意槽位视频脚本的严格白名单输出。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    creative_slot_id: str = Field(min_length=1)
    creative_type: Literal["oral_video", "short_video"]
    script_title: str = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    target_audience: str = Field(min_length=1)
    core_message: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    scenes: list[VideoScriptScene] = Field(min_length=1)
    cta: str = Field(min_length=1)
    compliance_notes: list[str]
    production_notes: list[str]

