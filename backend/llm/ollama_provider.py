"""Ollama provider implementation.

Uses direct HTTP calls via httpx for full control over timeouts,
mimicking the ollama API format.
"""

import logging
import time
import httpx

import config
from .base import ChatClient, EmbedClient

logger = logging.getLogger(__name__)


class OllamaChatClient(ChatClient):
    def __init__(self):
        self._base_url = config.OLLAMA_BASE_URL.rstrip("/")

    def chat(self, model: str | None, messages: list,
             options: dict | None = None, think: bool = False,
             response_format: dict | None = None) -> dict:
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

        logger.debug(f"OllamaChatClient.chat: POST {self._base_url}/api/chat (timeout={config.LLM_CHAT_TIMEOUT}s)")
        t0 = time.time()
        resp = httpx.post(
            f"{self._base_url}/api/chat",
            json=payload,
            timeout=config.LLM_CHAT_TIMEOUT,
        )
        elapsed = time.time() - t0
        logger.debug(f"OllamaChatClient.chat: HTTP {resp.status_code} in {elapsed:.1f}s")
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})
        content = message.get("content") or ""
        logger.debug(f"OllamaChatClient.chat: done, content_len={len(content)}")
        return {
            "model": data.get("model", chat_model),
            "message": {
                "role": message.get("role", "assistant"),
                "content": content,
            },
        }


class OllamaEmbedClient(EmbedClient):
    def __init__(self):
        self._base_url = config.OLLAMA_BASE_URL.rstrip("/")

    def embed(self, model: str | None, input: str) -> dict:
        embed_model = model or config.OLLAMA_EMBED_MODEL
        payload = {
            "model": embed_model,
            "input": input,
        }

        resp = httpx.post(
            f"{self._base_url}/api/embed",
            json=payload,
            timeout=config.LLM_EMBED_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if not embeddings and "embedding" in data:
            embeddings = [data["embedding"]]
        return {"embeddings": embeddings}
