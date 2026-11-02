import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.hermes.contracts import MarketingContextResult
from app.hermes.ports import ContextIntakeRequest, ContextIntakeResponse


class HermesGatewayError(RuntimeError):
    pass


class HermesGatewayAdapter:
    """把 Hermes 的内部客户端封装在边界内，业务层只依赖 HermesPort。"""

    def __init__(self, hermes_source: Path, workspace: Path) -> None:
        self.hermes_source = hermes_source
        self.workspace = workspace

    async def run_context_intake(self, request: ContextIntakeRequest) -> ContextIntakeResponse:
        # Hermes 目前没有稳定的独立 Python SDK，因此只在此处延迟导入其客户端。
        source = str(self.hermes_source)
        if source not in sys.path:
            sys.path.insert(0, source)
        from hermes_cli.gateway_client import connect_gateway

        async with connect_gateway() as client:
            session_id, stored_session_id, recovering = await self._open_session(client, request)
            receipt = await client.rpc(
                "prompt.submit",
                session_id=session_id,
                input_id=request.request_id,
                text=self._prompt(request, recovering=recovering),
                finite=True,
                unattended=True,
            )
            admission_id = receipt.get("admission_id")
            text = await self._wait_for_completion(client, session_id, admission_id)
            result = MarketingContextResult.model_validate(self._decode_json(text))
            return ContextIntakeResponse(result=result, stored_session_id=stored_session_id)

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

    async def _create_session(
        self, client: Any, request: ContextIntakeRequest
    ) -> tuple[str, str]:
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

    @staticmethod
    def _prompt(request: ContextIntakeRequest, *, recovering: bool) -> str:
        base = (
            "请执行 marketing-context-intake。最终只输出符合该 Skill 模板的 JSON，"
            "不要使用 Markdown 代码块。"
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
    async def _wait_for_completion(client: Any, session_id: str, admission_id: str | None) -> str:
        # Gateway 会通过 message.delta 流式发送正文。message.complete 主要表示本轮结束，
        # 其中的 text 在部分响应路径下可能为空，因此必须保留本轮的增量文本作为回退。
        streamed_chunks: list[str] = []
        async with asyncio.timeout(300):
            while True:
                event = await client.events.get()
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
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            preview = text.strip().replace("\n", " ")[:200]
            raise HermesGatewayError(
                f"Hermes returned invalid JSON (response preview: {preview!r})"
            ) from error
        if not isinstance(value, dict):
            raise HermesGatewayError("Hermes result must be a JSON object")
        return value

