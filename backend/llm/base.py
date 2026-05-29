"""Abstract base for LLM provider implementations."""

from abc import ABC, abstractmethod


class ChatClient(ABC):
    """Interface for chat/completion providers."""

    @abstractmethod
    def chat(self, model: str | None, messages: list,
             options: dict | None = None, think: bool = False,
             response_format: dict | None = None) -> dict:
        """
        Return dict shaped like:
        {
            "model": "...",
            "message": {"role": "assistant", "content": "..."}
        }
        """


class EmbedClient(ABC):
    """Interface for embedding providers."""

    @abstractmethod
    def embed(self, model: str | None, input: str) -> dict:
        """
        Return dict shaped like:
        {"embeddings": [[0.1, 0.2, ...]]}
        """
