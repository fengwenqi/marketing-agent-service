import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.hermes.contracts import (
    MarketingContextResult,
    MarketingStrategyResult,
    StageMediaPlanResult,
    VideoScriptResult,
)
from app.hermes.ports import (
    ContextIntakeRequest,
    ContextIntakeResponse,
    HermesOutputValidationError,
    HermesRequestConflictError,
    HermesRequestPreviouslyFailedError,
    HermesTurnTimeoutError,
    MediaPlanGenerationRequest,
    MediaPlanGenerationResponse,
    StrategyGenerationRequest,
    StrategyGenerationResponse,
    VideoScriptGenerationRequest,
    VideoScriptGenerationResponse,
)


class HermesGatewayError(RuntimeError):
    pass


class HermesGatewayAdapter:
    """把 Hermes 的内部客户端封装在边界内，业务层只依赖 HermesPort。"""

    def __init__(
        self,
        hermes_source: Path,
        workspace: Path,
        turn_timeout_seconds: int = 600,
    ) -> None:
        self.hermes_source = hermes_source
        self.workspace = workspace
        self.turn_timeout_seconds = turn_timeout_seconds

    async def run_context_intake(self, request: ContextIntakeRequest) -> ContextIntakeResponse:
        # Hermes 目前没有稳定的独立 Python SDK，因此只在此处延迟导入其客户端。
        source = str(self.hermes_source)
        if source not in sys.path:
            sys.path.insert(0, source)
        from hermes_cli.gateway_client import connect_gateway

        async with connect_gateway() as client:
            session_id, stored_session_id, recovering = await self._open_session(client, request)
            text = await self._submit_and_wait(
                client,
                session_id=session_id,
                request_id=request.request_id,
                prompt=self._prompt(request, recovering=recovering),
            )
            try:
                result = MarketingContextResult.model_validate(self._decode_json(text))
            except ValidationError as error:
                raise HermesOutputValidationError(
                    "Hermes returned JSON that does not match MarketingContextResult"
                ) from error
            return ContextIntakeResponse(result=result, stored_session_id=stored_session_id)

    async def run_strategy_generation(
        self, request: StrategyGenerationRequest
    ) -> StrategyGenerationResponse:
        source = str(self.hermes_source)
        if source not in sys.path:
            sys.path.insert(0, source)
        from hermes_cli.gateway_client import connect_gateway

        async with connect_gateway() as client:
            session_id, stored_session_id, _ = await self._open_strategy_session(client, request)
            text = await self._submit_and_wait(
                client,
                session_id=session_id,
                request_id=request.request_id,
                prompt=self._strategy_prompt(request),
            )
            return StrategyGenerationResponse(
                result=self._validate_strategy_result(text),
                stored_session_id=stored_session_id,
            )

    async def run_media_plan_generation(
        self, request: MediaPlanGenerationRequest
    ) -> MediaPlanGenerationResponse:
        source = str(self.hermes_source)
        if source not in sys.path:
            sys.path.insert(0, source)
        from hermes_cli.gateway_client import connect_gateway

        async with connect_gateway() as client:
            session_id, stored_session_id, _ = await self._open_media_plan_session(client, request)
            text = await self._submit_and_wait(
                client,
                session_id=session_id,
                request_id=request.request_id,
                prompt=self._media_plan_prompt(request),
            )
            return MediaPlanGenerationResponse(
                result=self._validate_media_plan_result(text),
                stored_session_id=stored_session_id,
            )

    async def run_video_script_generation(
        self, request: VideoScriptGenerationRequest
    ) -> VideoScriptGenerationResponse:
        source = str(self.hermes_source)
        if source not in sys.path:
            sys.path.insert(0, source)
        from hermes_cli.gateway_client import connect_gateway

        async with connect_gateway() as client:
            session_id, stored_session_id, _ = await self._open_video_script_session(
                client, request
            )
            text = await self._submit_and_wait(
                client,
                session_id=session_id,
                request_id=request.request_id,
                prompt=self._video_script_prompt(request),
            )
            return VideoScriptGenerationResponse(
                result=self._validate_video_script_result(text),
                stored_session_id=stored_session_id,
            )

    async def _submit_and_wait(
        self,
        client: Any,
        *,
        session_id: str,
        request_id: str,
        prompt: str,
    ) -> str:
        from hermes_cli.gateway_client import GatewayRPCError

        try:
            receipt = await client.rpc(
                "prompt.submit",
                session_id=session_id,
                input_id=request_id,
                text=prompt,
                finite=True,
            )
        except GatewayRPCError as error:
            if str(error) == "admission_conflict":
                raise HermesRequestConflictError(
                    "Hermes request ID was already used with different parameters"
                ) from error
            raise
        admission_id = receipt.get("admission_id")
        if not admission_id:
            raise HermesGatewayError("Gateway prompt.submit returned no admission_id")
        durable_receipt = await self._get_receipt(client, session_id, admission_id)
        terminal_text = self._terminal_receipt_text(durable_receipt)
        if terminal_text is not None:
            return terminal_text
        return await self._wait_for_completion(client, session_id, admission_id)

    async def _open_session(
        self, client: Any, request: ContextIntakeRequest
    ) -> tuple[str, str, bool]:
        if request.stored_session_id:
            try:
                snapshot = await client.rpc(
                    "session.resume",
                    session_id=request.stored_session_id,
                    source="tool",
                )
                runtime_id = snapshot["session_id"]
                stored_id = snapshot.get("stored_session_id") or request.stored_session_id
                return runtime_id, stored_id, False
            except Exception as error:
                if str(error) not in {"not_found", "session_not_found"}:
                    raise
                # state.db 中的会话丢失时创建替代会话，随后用 PostgreSQL 内容补水。
                runtime_id, stored_id = await self._create_session(client, request)
                return runtime_id, stored_id, True

        runtime_id, stored_id = await self._create_session(client, request)
        return runtime_id, stored_id, False

    async def _create_session(self, client: Any, request: ContextIntakeRequest) -> tuple[str, str]:
        description = await client.rpc("runtime.describe")
        parameters = description.get("session_create", {}).get("parameters", [])
        if "skills" not in parameters:
            raise HermesGatewayError("Gateway session.create does not support skills")
        snapshot = await client.rpc(
            "session.create",
            request_id=request.request_id,
            source="tool",
            cwd=str(self.workspace),
            skills=["marketing-context-intake"],
        )
        runtime_id = snapshot["session_id"]
        return runtime_id, snapshot.get("stored_session_id") or runtime_id

    async def _open_strategy_session(
        self, client: Any, request: StrategyGenerationRequest
    ) -> tuple[str, str, bool]:
        if request.stored_session_id:
            try:
                snapshot = await client.rpc(
                    "session.resume",
                    session_id=request.stored_session_id,
                    source="tool",
                )
                return (
                    snapshot["session_id"],
                    snapshot.get("stored_session_id") or request.stored_session_id,
                    False,
                )
            except Exception as error:
                if str(error) not in {"not_found", "session_not_found"}:
                    raise
        description = await client.rpc("runtime.describe")
        parameters = description.get("session_create", {}).get("parameters", [])
        if "skills" not in parameters:
            raise HermesGatewayError("Gateway session.create does not support skills")
        snapshot = await client.rpc(
            "session.create",
            request_id=request.request_id,
            source="tool",
            cwd=str(self.workspace),
            skills=["marketing-strategy"],
        )
        return (
            snapshot["session_id"],
            snapshot.get("stored_session_id") or snapshot["session_id"],
            bool(request.stored_session_id),
        )

    async def _open_media_plan_session(
        self, client: Any, request: MediaPlanGenerationRequest
    ) -> tuple[str, str, bool]:
        if request.stored_session_id:
            try:
                snapshot = await client.rpc(
                    "session.resume",
                    session_id=request.stored_session_id,
                    source="tool",
                )
                return (
                    snapshot["session_id"],
                    snapshot.get("stored_session_id") or request.stored_session_id,
                    False,
                )
            except Exception as error:
                if str(error) not in {"not_found", "session_not_found"}:
                    raise
        description = await client.rpc("runtime.describe")
        parameters = description.get("session_create", {}).get("parameters", [])
        if "skills" not in parameters:
            raise HermesGatewayError("Gateway session.create does not support skills")
        snapshot = await client.rpc(
            "session.create",
            request_id=request.request_id,
            source="tool",
            cwd=str(self.workspace),
            skills=["marketing-media-plan"],
        )
        return (
            snapshot["session_id"],
            snapshot.get("stored_session_id") or snapshot["session_id"],
            bool(request.stored_session_id),
        )

    async def _open_video_script_session(
        self, client: Any, request: VideoScriptGenerationRequest
    ) -> tuple[str, str, bool]:
        if request.stored_session_id:
            try:
                snapshot = await client.rpc(
                    "session.resume",
                    session_id=request.stored_session_id,
                    source="tool",
                )
                return (
                    snapshot["session_id"],
                    snapshot.get("stored_session_id") or request.stored_session_id,
                    False,
                )
            except Exception as error:
                if str(error) not in {"not_found", "session_not_found"}:
                    raise
        description = await client.rpc("runtime.describe")
        parameters = description.get("session_create", {}).get("parameters", [])
        if "skills" not in parameters:
            raise HermesGatewayError("Gateway session.create does not support skills")
        snapshot = await client.rpc(
            "session.create",
            request_id=request.request_id,
            source="tool",
            cwd=str(self.workspace),
            skills=["marketing-video-script"],
        )
        return (
            snapshot["session_id"],
            snapshot.get("stored_session_id") or snapshot["session_id"],
            bool(request.stored_session_id),
        )

    @staticmethod
    def _prompt(request: ContextIntakeRequest, *, recovering: bool) -> str:
        base = (
            "请执行 marketing-context-intake。识别并唯一映射产品后，直接调用 "
            "search_product_spec 和 search_marketing_material；每次调用必须传入非空的 "
            "product_key 和 question，禁止通过 tool_call 桥接。最终响应只能包含符合该 Skill "
            "模板的 JSON 对象，"
            "不要输出分析、摘要、前导说明或 Markdown 代码块；"
            "响应的第一个非空字符必须是 {，最后一个非空字符必须是 }。"
        )
        if not recovering:
            return base + "用户输入：\n" + request.message
        recovery = {
            "structured_context": request.context or {},
            "recent_messages": request.recent_messages or [],
        }
        return (
            base
            + "Hermes 原会话不可用，请根据 PostgreSQL 中保存的上下文继续，不要重复已确认信息。"
            + "\n恢复数据：\n"
            + json.dumps(recovery, ensure_ascii=False)
            + "\n本次用户输入：\n"
            + request.message
        )

    @staticmethod
    def _strategy_prompt(request: StrategyGenerationRequest) -> str:
        payload = {
            "marketing_context": request.marketing_context,
            "current_strategy": request.current_strategy,
            "revision_instruction": request.revision_instruction,
        }
        return (
            "请执行 marketing-strategy。严格根据输入生成完整 A/B/C 三阶段框架；"
            "修改模式也必须返回完整框架。最终只能输出符合 Skill 模板的 JSON 对象，"
            "不要输出分析、说明或 Markdown 代码块。\n输入：\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    @classmethod
    def _validate_strategy_result(cls, text: str) -> MarketingStrategyResult:
        try:
            return MarketingStrategyResult.model_validate(cls._decode_json(text))
        except ValidationError as error:
            raise HermesOutputValidationError(
                "Hermes returned JSON that does not match MarketingStrategyResult"
            ) from error

    @staticmethod
    def _media_plan_prompt(request: MediaPlanGenerationRequest) -> str:
        payload = {
            "stage_code": request.stage_code,
            "marketing_context": request.marketing_context,
            "confirmed_strategy": request.confirmed_strategy,
            "confirmation_inputs": request.confirmation_inputs,
            "current_plan": request.current_plan,
            "revision_instruction": request.revision_instruction,
        }
        return (
            "请执行 marketing-media-plan。只生成输入阶段的完整千川投放计划；"
            "修改或补参模式也必须返回完整计划。最终只能输出符合 Skill 模板的 JSON 对象，"
            "target_roi、allocation_ratio、stage_budget、amount 的非空值"
            "必须是带引号的十进制字符串，"
            "allocation_ratio 必须使用百分点：24 表示 24%，禁止输出 0.24；"
            "全部预算单元的 allocation_ratio 合计必须等于 100，"
            "amount 合计必须等于 stage_budget；"
            "不要输出分析、说明或 Markdown 代码块。\n输入：\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    @classmethod
    def _validate_media_plan_result(cls, text: str) -> StageMediaPlanResult:
        try:
            return StageMediaPlanResult.model_validate(cls._decode_json(text))
        except ValidationError as error:
            raise HermesOutputValidationError(
                "Hermes returned JSON that does not match StageMediaPlanResult"
            ) from error

    @staticmethod
    def _video_script_prompt(request: VideoScriptGenerationRequest) -> str:
        payload = {
            "stage_code": request.stage_code,
            "marketing_context": request.marketing_context,
            "confirmed_strategy": request.confirmed_strategy,
            "confirmed_media_plan": request.confirmed_media_plan,
            "creative_slot": request.creative_slot,
            "current_script": request.current_script,
            "revision_instruction": request.revision_instruction,
        }
        return (
            "请执行 marketing-video-script。只为输入的一个创意槽位生成完整视频脚本；"
            "修改模式也必须返回完整脚本。最终只能输出符合 Skill 模板的 JSON 对象，"
            "creative_slot_id 和 creative_type 必须与输入槽位完全一致，"
            "场景时间轴必须从 0 开始、连续且最终时长一致；"
            "不要输出分析、说明或 Markdown 代码块。\n输入：\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    @classmethod
    def _validate_video_script_result(cls, text: str) -> VideoScriptResult:
        try:
            return VideoScriptResult.model_validate(cls._decode_json(text))
        except ValidationError as error:
            raise HermesOutputValidationError(
                "Hermes returned JSON that does not match VideoScriptResult"
            ) from error

    async def _wait_for_completion(self, client: Any, session_id: str, admission_id: str) -> str:
        # Gateway 会通过 message.delta 流式发送正文。message.complete 主要表示本轮结束，
        # 其中的 text 在部分响应路径下可能为空，因此必须保留本轮的增量文本作为回退。
        streamed_chunks: list[str] = []
        try:
            async with asyncio.timeout(self.turn_timeout_seconds):
                while True:
                    try:
                        # WebSocket 是实时快路径；定时轮询 receipt 是持久化回退路径，
                        # 可处理重复提交以及完成事件恰好在监听前到达的情况。
                        event = await asyncio.wait_for(client.events.get(), timeout=2)
                    except TimeoutError:
                        receipt = await self._get_receipt(client, session_id, admission_id)
                        terminal_text = self._terminal_receipt_text(receipt)
                        if terminal_text is not None:
                            return terminal_text
                        continue
                    if isinstance(event, Exception):
                        raise HermesGatewayError(str(event)) from event
                    params = event.get("params", {})
                    if params.get("session_id") != session_id:
                        continue
                    event_admission = params.get("admission_id") or params.get("payload", {}).get(
                        "admission_id"
                    )
                    if admission_id and event_admission != admission_id:
                        continue
                    event_type = params.get("type")
                    payload = params.get("payload", {})
                    completed_text = HermesGatewayAdapter._consume_turn_event(
                        event_type, payload, streamed_chunks
                    )
                    if completed_text is not None:
                        return completed_text
        except TimeoutError as error:
            raise HermesTurnTimeoutError(
                f"Hermes did not finish within {self.turn_timeout_seconds} seconds"
            ) from error

    @staticmethod
    async def _get_receipt(client: Any, session_id: str, admission_id: str) -> dict[str, Any]:
        return await client.rpc(
            "prompt.receipt",
            session_id=session_id,
            admission_id=admission_id,
            include_result=True,
        )

    @staticmethod
    def _terminal_receipt_text(receipt: dict[str, Any]) -> str | None:
        if receipt.get("status") != "terminal":
            return None

        outcome = receipt.get("outcome")
        result = receipt.get("result")
        if outcome == "completed":
            text = result.get("final_response") if isinstance(result, dict) else None
            if isinstance(text, str) and text:
                return text
            raise HermesGatewayError("Hermes completed without a persisted response")

        detail = result.get("error") if isinstance(result, dict) else None
        suffix = f": {detail}" if detail else ""
        raise HermesRequestPreviouslyFailedError(
            f"Hermes request already ended with outcome {outcome or 'failed'}{suffix}"
        )

    @staticmethod
    def _consume_turn_event(
        event_type: str | None, payload: dict[str, Any], streamed_chunks: list[str]
    ) -> str | None:
        if event_type == "message.delta":
            chunk = payload.get("text") or payload.get("delta") or payload.get("content")
            if isinstance(chunk, str) and chunk:
                streamed_chunks.append(chunk)
            return None
        if event_type != "message.complete":
            return None
        if payload.get("status") == "error":
            detail = payload.get("error") or payload.get("text") or "unknown error"
            raise HermesGatewayError(f"Hermes turn failed: {detail}")

        # 完成事件有正文时，它是本轮权威结果；否则使用之前收到的流式正文。
        completed_text = payload.get("text") or payload.get("content")
        if isinstance(completed_text, str) and completed_text:
            return completed_text
        streamed_text = "".join(streamed_chunks)
        if streamed_text:
            return streamed_text
        raise HermesGatewayError("Hermes completed without response text")

    @staticmethod
    def _decode_json(text: str) -> dict[str, Any]:
        stripped = text.strip()
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as strict_error:
            # 模型偶尔会在合法 JSON 前后附加解释文字或 Markdown 围栏。
            # 从每个 “{” 起点尝试 raw_decode；首个可完整解码的对象再由 Pydantic
            # 执行业务字段白名单校验，不能因为容错而绕过结构契约。
            decoder = json.JSONDecoder()
            search_from = 0
            while True:
                object_start = stripped.find("{", search_from)
                if object_start < 0:
                    break
                try:
                    candidate, _ = decoder.raw_decode(stripped, object_start)
                except json.JSONDecodeError:
                    search_from = object_start + 1
                    continue
                if isinstance(candidate, dict):
                    return candidate
                search_from = object_start + 1

            preview = stripped.replace("\n", " ")[:200]
            raise HermesOutputValidationError(
                f"Hermes returned invalid JSON (response preview: {preview!r})"
            ) from strict_error

        if not isinstance(value, dict):
            raise HermesOutputValidationError("Hermes result must be a JSON object")
        return value
