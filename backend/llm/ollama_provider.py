"""Ollama provider implementation.

Uses direct HTTP calls via httpx for full control over timeouts,
mimicking the ollama API format.
"""

import json
import logging
import time
import httpx

import config
from .base import ChatClient, EmbedClient, ChatResponse, ToolCall, retry_http_call

logger = logging.getLogger(__name__)


class OllamaChatClient(ChatClient):
    def __init__(self):
        self._base_url = config.OLLAMA_BASE_URL.rstrip("/")

    def chat(self, model: str | None, messages: list,
             options: dict | None = None, think: bool = False,
             response_format: dict | None = None,
             tools: list[dict] | None = None,
             timeout: float | None = None) -> ChatResponse:
        chat_model = model or config.OLLAMA_CHAT_MODEL
        logger.debug(f"OllamaChatClient.chat: model={chat_model}, msg_count={len(messages)}")
        payload: dict = {
            "model": chat_model,
            "messages": messages,
        }
        if options:
            payload["options"] = options
        payload["stream"] = False
        if think:
            payload["think"] = True
        if tools:
            payload["tools"] = tools

        logger.debug(f"OllamaChatClient.chat: POST {self._base_url}/api/chat (timeout={config.LLM_CHAT_TIMEOUT}s)")
        t0 = time.time()
        resp = retry_http_call(
            lambda: httpx.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=timeout or config.LLM_CHAT_TIMEOUT,
            )
        )
        elapsed = time.time() - t0
        logger.debug(f"OllamaChatClient.chat: HTTP {resp.status_code} in {elapsed:.1f}s")
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})
        content = message.get("content") or ""

        tool_calls = []
        raw_tool_calls = message.get("tool_calls", [])
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                function_name=fn.get("name", ""),
                arguments=args,
            ))

        logger.debug(f"OllamaChatClient.chat: done, content_len={len(content)}, tool_calls={len(tool_calls)}")
        return ChatResponse(content=content, tool_calls=tool_calls)


class OllamaEmbedClient(EmbedClient):
    def __init__(self):
        self._base_url = config.OLLAMA_BASE_URL.rstrip("/")

    def embed(self, model: str | None, input: str,
              timeout: float | None = None) -> dict:
        embed_model = model or config.OLLAMA_EMBED_MODEL
        payload = {
            "model": embed_model,
            "input": input,
        }

        resp = retry_http_call(
            lambda: httpx.post(
                f"{self._base_url}/api/embed",
                json=payload,
                timeout=timeout or config.LLM_EMBED_TIMEOUT,
            )
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if not embeddings and "embedding" in data:
            embeddings = [data["embedding"]]
        return {"embeddings": embeddings}
