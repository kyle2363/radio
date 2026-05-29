"""Provider selection and caching.

Resolution order:
  1. Read chat_provider / embed_provider from DB settings (runtime override).
  2. Fall back to config.py defaults (CHAT_PROVIDER / EMBED_PROVIDER).
  3. If 'auto', try providers in order (ollama first, then openai).
  4. Cache the first working provider on first successful call.

Callers use llm_chat() and llm_embed() - no direct ollama imports needed.
"""

from __future__ import annotations

import logging
import config
from .base import ChatClient, EmbedClient

logger = logging.getLogger(__name__)
from .ollama_provider import OllamaChatClient, OllamaEmbedClient
from .openai_provider import OpenAIChatClient, OpenAIEmbedClient

_chat_instance: ChatClient | None = None
_embed_instance: EmbedClient | None = None


def _get_setting(key: str) -> str | None:
    """Read a setting from the database, falling back to config.py."""
    try:
        from db import database
        val = database.get_setting(key)
        if val is not None:
            return val
    except Exception as e:
        logger.debug("Could not read setting %s from DB: %s", key, e)
    # Fall back to config
    if key == "chat_provider":
        return config.CHAT_PROVIDER
    if key == "embed_provider":
        return config.EMBED_PROVIDER
    return None


def _resolve_chat_order() -> list[type[ChatClient]]:
    provider = _get_setting("chat_provider") or "auto"
    if provider == "openai":
        if config.OPENAI_BASE_URL_CHAT:
            return [OpenAIChatClient]
        return []
    if provider == "ollama":
        return [OllamaChatClient]
    # auto (default): ollama first, then openai
    order = [OllamaChatClient]
    if config.OPENAI_BASE_URL_CHAT:
        order.append(OpenAIChatClient)
    return order


def _resolve_embed_order() -> list[type[EmbedClient]]:
    provider = _get_setting("embed_provider") or "auto"
    if provider == "openai":
        if config.OPENAI_BASE_URL_EMBED:
            return [OpenAIEmbedClient]
        return []
    if provider == "ollama":
        return [OllamaEmbedClient]
    # auto (default): ollama first, then openai
    order = [OllamaEmbedClient]
    if config.OPENAI_BASE_URL_EMBED:
        order.append(OpenAIEmbedClient)
    return order


def llm_chat() -> ChatClient:
    """Get a working chat client. Tries providers in order, caches first success."""
    global _chat_instance
    if _chat_instance is not None:
        return _chat_instance
    for cls in _resolve_chat_order():
        try:
            client = cls()
            client.chat(None, [{"role": "user", "content": "hi"}])
            _chat_instance = client
            return client
        except Exception as e:
            print(f"[llm] {cls.__name__} failed: {e}")
    raise RuntimeError("No chat provider available. Check chat_provider setting and OPENAI_BASE_URL_CHAT in config.")


def llm_embed() -> EmbedClient:
    """Get a working embed client. Tries providers in order, caches first success."""
    global _embed_instance
    if _embed_instance is not None:
        return _embed_instance
    for cls in _resolve_embed_order():
        try:
            client = cls()
            client.embed(None, "test")
            _embed_instance = client
            return client
        except Exception as e:
            print(f"[llm] {cls.__name__} failed: {e}")
    raise RuntimeError("No embed provider available. Check embed_provider setting and OPENAI_BASE_URL_EMBED in config.")


def reset_providers() -> None:
    """Force re-resolution. Call after changing provider settings at runtime."""
    global _chat_instance, _embed_instance
    _chat_instance = None
    _embed_instance = None


def get_active_provider_name() -> str:
    """Return name of currently active chat provider, or 'unknown'."""
    if _chat_instance is None:
        return "uninitialized"
    return _chat_instance.__class__.__name__.replace("Client", "")
