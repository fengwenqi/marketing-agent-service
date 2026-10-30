import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.hermes.contracts import MarketingContextResult
from app.hermes.ports import ContextIntakeRequest


class HermesGatewayError(RuntimeError):
    pass


class HermesGatewayAdapter:
    """把 Hermes 的内部客户端封装在边界内，业务层只依赖 HermesPort。"""

    def __init__(self, hermes_source: Path, workspace: Path) -> None:
        self.hermes_source = hermes_source
        self.workspace = workspace

    async def run_context_intake(self, request: ContextIntakeRequest) -> MarketingContextResult:
        # Hermes 目前没有稳定的独立 Python SDK，因此只在此处延迟导入其客户端。
        source = str(self.hermes_source)
        if source not in sys.path:
            sys.path.insert(0, source)
        from hermes_cli.gateway_client import connect_gateway

        async with connect_gateway() as client:
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
            session_id = snapshot["stored_session_id"]
            receipt = await client.rpc(
                "prompt.submit",
                session_id=session_id,
                input_id=request.request_id,
                text=self._prompt(request.message),
                finite=True,
                unattended=True,
            )
            admission_id = receipt.get("admission_id")
            text = await self._wait_for_completion(client, session_id, admission_id)
            return MarketingContextResult.model_validate(self._decode_json(text))

    @staticmethod
    def _prompt(message: str) -> str:
        return (
            "请执行 marketing-context-intake。最终只输出符合该 Skill 模板的 JSON，"
            "不要使用 Markdown 代码块。用户输入：\n" + message
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

