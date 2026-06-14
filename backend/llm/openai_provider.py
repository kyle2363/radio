"""OpenAI-compatible provider (llama.cpp, LMStudio, vLLM, etc.).

Uses direct HTTP calls via httpx for full control over request payloads,
including llama.cpp-specific params like chat_template_kwargs and response_format.

Does NOT use the openai SDK — raw httpx POST calls ensure all params reach the server.
"""

import json
import httpx

import config
from .base import ChatClient, EmbedClient, ChatResponse, ToolCall, retry_http_call


class OpenAIChatClient(ChatClient):
    def __init__(self):
        self._base_url = config.OPENAI_BASE_URL_CHAT.rstrip("/")
        self._api_key = config.OPENAI_API_KEY or "not-needed"

    def chat(self, model: str | None, messages: list,
             options: dict | None = None, think: bool = False,
             response_format: dict | None = None,
             tools: list[dict] | None = None,
             timeout: float | None = None) -> ChatResponse:
        chat_model = model or config.OPENAI_CHAT_MODEL
        payload: dict = {
            "model": chat_model,
            "messages": messages,
        }
        if options:
            payload["temperature"] = options.get("temperature", 0.3)
            payload["top_p"] = options.get("top_p", 0.9)
            if options.get("num_predict", -1) and options["num_predict"] > 0:
                payload["max_tokens"] = int(options["num_predict"])
            if options.get("top_k"):
                payload["top_k"] = options["top_k"]
        # llama.cpp uses chat_template_kwargs for thinking control
        payload["chat_template_kwargs"] = {"enable_thinking": think}
        if response_format:
            payload["response_format"] = response_format
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = retry_http_call(
            lambda: httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout or config.LLM_CHAT_TIMEOUT,
            )
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"OpenAI chat returned no choices: {data}")
        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        tool_calls_raw = message.get("tool_calls")

        tool_calls = []
        if tool_calls_raw:
            for tc in tool_calls_raw:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    function_name=tc["function"]["name"],
                    arguments=args,
                ))

        return ChatResponse(content=content, tool_calls=tool_calls)



class OpenAIEmbedClient(EmbedClient):
    def __init__(self):
        self._base_url = config.OPENAI_BASE_URL_EMBED.rstrip("/")
        self._api_key = config.OPENAI_API_KEY or "not-needed"

    def embed(self, model: str | None, input: str,
              timeout: float | None = None) -> dict:
        embed_model = model or config.OPENAI_EMBED_MODEL
        payload = {
            "model": embed_model,
            "input": input,
        }
        resp = retry_http_call(
            lambda: httpx.post(
                f"{self._base_url}/embeddings",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout or config.LLM_EMBED_TIMEOUT,
            )
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            raise RuntimeError(f"OpenAI embed returned no data: {data}")
        return {"embeddings": [item["embedding"] for item in items]}
