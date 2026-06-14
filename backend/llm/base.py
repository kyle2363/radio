"""Abstract base for LLM provider implementations."""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


def retry_http_call(fn, max_retries=2, base_delay=1.0):
    """Call fn() with retry on transient failures (connection, timeout, 5xx).

    Does NOT retry 4xx errors (client errors) or on success.
    Returns fn() result, or raises the last exception on final failure.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            status = getattr(e, "response", None) and e.response.status_code
            if status and 400 <= status < 500:
                raise  # Don't retry 4xx client errors
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.debug("Retry %d/%d after %.1fs: %s", attempt + 1, max_retries, delay, e)
                time.sleep(delay)
    raise last_exc


class ToolCall:
    """Represents a tool call response from the LLM."""
    def __init__(self, id: str, function_name: str, arguments: dict):
        self.id = id
        self.function_name = function_name
        self.arguments = arguments

    def __repr__(self):
        return f"ToolCall({self.function_name}({self.arguments}))"


class ChatResponse:
    """Wrapper around a chat response that may contain text and/or tool_calls."""
    def __init__(self, content: str = "", tool_calls: list[ToolCall] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class ChatClient(ABC):
    """Interface for chat/completion providers."""

    @abstractmethod
    def chat(self, model: str | None, messages: list,
             options: dict | None = None, think: bool = False,
             response_format: dict | None = None,
             tools: list[dict] | None = None,
             timeout: float | None = None) -> ChatResponse:
        """
        Return a ChatResponse with content and/or tool_calls.

        The tools param follows the OpenAI tool schema:
        [{
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {...}
            }
        }]
        """


class EmbedClient(ABC):
    """Interface for embedding providers."""

    @abstractmethod
    def embed(self, model: str | None, input: str,
              timeout: float | None = None) -> dict:
        """
        Return dict shaped like:
        {"embeddings": [[0.1, 0.2, ...]]}
        """
