"""OpenAI/DeepSeek 兼容模型工厂：统一处理流式截断拦截、厂商参数适配与网络退避重试。"""
from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from agentscope.agent import ReActAgent
from agentscope.model import ChatModelBase, OpenAIChatModel
from openai import APIConnectionError, APIError, LengthFinishReasonError

DEEPSEEK_V4_MAX_OUTPUT = 65536


class ModelOutputTruncatedError(RuntimeError):
    """当单次模型生成触及 max_tokens 被截断时抛出，防止残缺内容写入工作区或污染记忆。"""


class IterationBudgetExceededError(RuntimeError):
    """当 ReAct 推理轮次达到上限时抛出，提示用户继续推进剩余步骤而非冒充已完成。"""


class BoundedReActAgent(ReActAgent):
    """受控轮次 ReAct 智能体：轮次耗尽时显式抛出异常而非静默生成伪完成总结。"""

    async def _summarizing(self):
        raise IterationBudgetExceededError("已达到单次推理轮次预算上限，工作区已落盘成果均完整保留，可发送“继续”推进后续步骤。")


DEEPSEEK_V4_MODELS = {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4.1-flash", "deepseek-v4-pro"}


def build_generation_kwargs(model_name: str, base_url: str, max_output_tokens: int | None = None) -> dict[str, Any]:
    """根据目标服务端与模型名称组装生成参数，兼容 DeepSeek V4 大输出预算并关闭冗余思考链以保障工具调用稳定提速。"""
    kwargs: dict[str, Any] = {}
    host = (urlparse(base_url).hostname or "").lower()
    short_name = model_name.lower().rsplit("/", 1)[-1]

    if max_output_tokens is not None and int(max_output_tokens) > 0:
        kwargs["max_tokens"] = int(max_output_tokens)
    elif short_name in DEEPSEEK_V4_MODELS:
        kwargs["max_tokens"] = 32768
    elif "deepseek" in host or short_name.startswith("deepseek"):
        kwargs["max_tokens"] = 8192

    if host == "api.deepseek.com" or short_name.startswith("deepseek"):
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    return kwargs


def _check_finish_length(chunk_or_resp: Any) -> None:
    """检查响应帧是否因输出长度受限截断，一旦发现立即中断避免半截工具参数执行。"""
    choices = getattr(chunk_or_resp, "choices", None) or []
    if any(getattr(choice, "finish_reason", None) == "length" for choice in choices):
        raise ModelOutputTruncatedError("模型单次输出达到最大 Token 限制被截断，请拆分子任务或分章编写后重试。")


class _LengthCheckedStream:
    """包装 OpenAI 异步流，在 SDK 拼接块之前拦截 finish_reason=length。"""

    def __init__(self, response: Any):
        self._response = response
        self._stream: Any = None

    async def __aenter__(self):
        self._stream = await self._response.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._response.__aexit__(*args)

    async def __aiter__(self):
        async for item in self._stream:
            raw = item.chunk if getattr(item, "type", None) == "chunk" else item
            _check_finish_length(raw)
            yield item


class GuardedOpenAIChatModel(OpenAIChatModel):
    """扩展 AgentScope OpenAIChatModel，补齐流式与非流式输出截断硬拦截。"""

    async def _parse_openai_stream_response(self, start_datetime, response, structured_model=None):
        async for result in super()._parse_openai_stream_response(
            start_datetime, _LengthCheckedStream(response), structured_model
        ):
            yield result

    def _parse_openai_completion_response(self, start_datetime, response, structured_model=None):
        _check_finish_length(response)
        return super()._parse_openai_completion_response(start_datetime, response, structured_model)


def is_transient_error(error: BaseException) -> bool:
    """判断异常是否属于网络抖动、限流 (429) 或上游临时过载 (5xx)，仅对这类错误自动重试。"""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 425, 429) or 500 <= status < 600
    if isinstance(error, (ConnectionError, TimeoutError, httpx.TransportError, APIConnectionError)):
        return True
    if isinstance(error, APIError):
        code = str(getattr(error, "code", "") or "").lower()
        msg = str(getattr(error, "message", "") or "")
        if "unavailable" in code or "overloaded" in code or re.search(r"temporarily unavailable", msg, re.I):
            return True
    return False


class ResilientChatModel(ChatModelBase):
    """
    为底层对话模型提供最多 3 次指数退避传输重试，并支持在流式接收过程中实时回调增量文本。
    重试期间若中途断流，自动丢弃残缺块重新拉取，绝不向上层暴露半截工具调用。
    """

    def __init__(
        self,
        inner_model: ChatModelBase,
        on_log: Callable[[str, dict[str, Any]], None] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
    ):
        super().__init__(model_name=inner_model.model_name, stream=False)
        self.inner_model = inner_model
        self.on_log = on_log
        self.on_text_delta = on_text_delta

    async def __call__(self, *args, **kwargs):
        for attempt in range(1, 4):
            emitted_len = 0
            try:
                async with asyncio.timeout(240):
                    resp = await self.inner_model(*args, **kwargs)
                    if inspect.isasyncgen(resp):
                        final_chunk = None
                        try:
                            async for chunk in resp:
                                final_chunk = chunk
                                # 仅在首次尝试时向前端实时推送文本流，保证用户体验丝滑
                                if self.on_text_delta is not None and attempt == 1:
                                    full_text = "".join(
                                        b.get("text", "")
                                        for b in (getattr(chunk, "content", None) or [])
                                        if isinstance(b, dict) and b.get("type") == "text"
                                    )
                                    if len(full_text) > emitted_len:
                                        self.on_text_delta(full_text[emitted_len:])
                                        emitted_len = len(full_text)
                        finally:
                            await resp.aclose()
                        if final_chunk is None:
                            raise ConnectionError("模型流式连接提前关闭，未返回有效响应")
                        return final_chunk
                    return resp
            except LengthFinishReasonError as exc:
                raise ModelOutputTruncatedError("模型输出达到长度上限被截断") from exc
            except (OSError, httpx.HTTPError, APIError, TimeoutError) as exc:
                if not is_transient_error(exc) or attempt == 3:
                    raise
                delay = min(12.0, float(2 ** (attempt - 1)))
                if self.on_log:
                    self.on_log("model.retry", {"attempt": attempt, "delay_sec": delay, "error": type(exc).__name__})
                await asyncio.sleep(delay)


def create_chat_model(
    api_key: str,
    base_url: str,
    model_name: str,
    max_output_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
    on_log: Callable[[str, dict[str, Any]], None] | None = None,
    on_text_delta: Callable[[str], None] | None = None,
) -> ResilientChatModel:
    """构建带流式推送、长度保护与自动重试能力的标准 AgentScope 模型实例。"""
    gen_kwargs = build_generation_kwargs(model_name, base_url, max_output_tokens)
    raw_model = GuardedOpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=True,
        client_kwargs={
            "base_url": base_url,
            "timeout": 180,
            "max_retries": 0,
            **({"http_client": http_client} if http_client is not None else {}),
        },
        generate_kwargs=gen_kwargs,
    )
    return ResilientChatModel(raw_model, on_log=on_log, on_text_delta=on_text_delta)
