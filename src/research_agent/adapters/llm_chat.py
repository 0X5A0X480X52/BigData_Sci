"""OpenAI-compatible chat adapter for optional real-agent planning.

This adapter can talk to DeepSeek, OpenAI, GLM, Ollama, or any
OpenAI-compatible Chat Completions endpoint.

Design:
- Prefer OpenAI Python SDK if installed.
- Fall back to urllib for dependency-free non-streaming calls.
- Keep complete_json() compatible with the previous implementation.
- Add chat_completion(), chat(), _chat_once(), _chat_stream() layered APIs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


try:
    from openai import OpenAI, OpenAIError
except ImportError:  # keep offline fixture demo dependency-free
    OpenAI = None  # type: ignore[assignment]

    class OpenAIError(Exception):  # type: ignore[no-redef]
        pass


@dataclass
class LLMChatConfig:
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"
    timeout: float = 30.0
    temperature: float = 0.1
    max_tokens: int = 1200
    stream: bool = False
    debug_mode: bool = False
    demo_mode: bool = False
    prefer_sdk: bool = True

    @classmethod
    def from_env(cls) -> "LLMChatConfig":
        debug_mode = os.getenv("RA_LLM_DEBUG", "0") == "1"
        
        print()
        print("=" * 20 + " LLMChatConfig.from_env " + "=" * 20)
        print(f"LLMChatConfig.from_env: debug_mode={debug_mode}")

        if debug_mode:
            print("LLMChatConfig.from_env: loading config from environment variables")
            print(f"RA_LLM_BASE_URL: {os.getenv('RA_LLM_BASE_URL')}")
            print(f"RA_LLM_API_KEY: {'set' if os.getenv('RA_LLM_API_KEY') else 'not set'}")
            print(f"DEEPSEEK_API_KEY: {'set' if os.getenv('DEEPSEEK_API_KEY') else 'not set'}")
            print(f"OPENAI_API_KEY: {'set' if os.getenv('OPENAI_API_KEY') else 'not set'}")
            print(f"RA_LLM_MODEL: {os.getenv('RA_LLM_MODEL')}")
            print(f"RA_LLM_TIMEOUT: {os.getenv('RA_LLM_TIMEOUT')}")
            print(f"RA_LLM_TEMPERATURE: {os.getenv('RA_LLM_TEMPERATURE')}")
            print(f"RA_LLM_MAX_TOKENS: {os.getenv('RA_LLM_MAX_TOKENS')}")
            print(f"RA_LLM_STREAM: {os.getenv('RA_LLM_STREAM')}")
            print(f"RA_LLM_DEMO_MODE: {os.getenv('RA_LLM_DEMO_MODE')}")
            print(f"RA_LLM_PREFER_SDK: {os.getenv('RA_LLM_PREFER_SDK')}")

        return cls(
            base_url=os.getenv("RA_LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=(
                os.getenv("RA_LLM_API_KEY", "")
                or os.getenv("DEEPSEEK_API_KEY", "")
                or os.getenv("OPENAI_API_KEY", "")
            ),
            model=os.getenv("RA_LLM_MODEL", "deepseek-chat"),
            timeout=float(os.getenv("RA_LLM_TIMEOUT", "30")),
            temperature=float(os.getenv("RA_LLM_TEMPERATURE", "0.1")),
            max_tokens=int(os.getenv("RA_LLM_MAX_TOKENS", "1200")),
            stream=os.getenv("RA_LLM_STREAM", "0") == "1",
            debug_mode=debug_mode,
            demo_mode=os.getenv("RA_LLM_DEMO_MODE", "0") == "1",
            prefer_sdk=os.getenv("RA_LLM_PREFER_SDK", "1") != "0",
        )


class LLMUnavailableError(RuntimeError):
    """Raised when LLM mode is requested but no usable endpoint is configured."""


class OpenAICompatibleChatClient:
    """Small client for OpenAI-compatible chat completions."""

    def __init__(self, config: Optional[LLMChatConfig] = None) -> None:
        self.config = config or LLMChatConfig.from_env()
        self.client: Optional[Any] = None

        if (
            not self.config.demo_mode
            and self.config.prefer_sdk
            and OpenAI is not None
            and self.config.api_key
        ):
            self.client = OpenAI(
                api_key=self.config.api_key,
                base_url=self._normalize_base_url_for_sdk(self.config.base_url),
                timeout=self.config.timeout,
            )

    @property
    def available(self) -> bool:
        return bool(self.config.api_key) or self.config.demo_mode
    
    @staticmethod
    def _normalize_base_url_for_sdk(base_url: str) -> str:
        """
        OpenAI SDK needs the API root.

        For Ollama, the OpenAI-compatible root is:
            http://localhost:11434/v1

        Then SDK will request:
            http://localhost:11434/v1/chat/completions
        """
        base = base_url.rstrip("/")

        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]

        # 如果用户直接填了 Ollama 根地址，自动补 /v1
        if base in {"http://localhost:11434", "http://127.0.0.1:11434"}:
            return f"{base}/v1"

        # 如果已经是 /v1，就直接返回
        if base.endswith("/v1"):
            return base

        return base

    @staticmethod
    def _normalize_url_for_urllib(base_url: str) -> str:
        """
        urllib calls the concrete Chat Completions endpoint directly.

        If the user passes:
        - https://api.deepseek.com
        it becomes:
        - https://api.deepseek.com/v1/chat/completions

        If the user already passes:
        - .../chat/completions
        it is used as-is.
        """
        base = base_url.rstrip("/")

        if base.endswith("/chat/completions"):
            return base

        if base.endswith("/v1"):
            return f"{base}/chat/completions"

        return f"{base}/v1/chat/completions"

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Return the raw OpenAI-compatible Chat Completions response.

        If OpenAI SDK is available, return SDK response object or stream iterator.
        Otherwise, fall back to urllib for non-streaming calls and return dict.
        """
        if self.config.demo_mode:
            raise RuntimeError(
                "DEMO_MODE 下不调用真实 Chat Completions；请使用 chat() 或设置 RA_LLM_DEMO_MODE=0。"
            )

        if not self.config.api_key:
            raise LLMUnavailableError(
                "RA_LLM_API_KEY, DEEPSEEK_API_KEY, or OPENAI_API_KEY is required"
            )

        request: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens,
            "stream": stream,
        }

        if tools is not None:
            request["tools"] = tools

        if tool_choice is not None:
            request["tool_choice"] = tool_choice

        request.update(kwargs)

        if self.config.debug_mode:
            print()
            print("=" * 20 + " DEBUG MODE " + "=" * 20)
            print(f"LLM base_url: {self.config.base_url.rstrip('/')}")
            print(f"LLM model: {self.config.model}")
            print(f"LLM stream: {stream}")
            print(f"LLM messages: {messages}")
            if tools is not None:
                print(f"LLM tools count: {len(tools)}")
            if tool_choice is not None:
                print(f"LLM tool_choice: {tool_choice}")
            extra_keys = sorted(set(request.keys()) - {"model", "messages", "temperature", "max_tokens", "stream"})
            if extra_keys:
                print(f"LLM extra request keys: {extra_keys}")

        if self.client is not None:
            if self.config.debug_mode:
                print("Using OpenAI SDK for chat completion.")
            try:
                reponse = self.client.chat.completions.create(**request)
                if self.config.debug_mode:
                    print(f"LLM SDK response type: {type(reponse).__name__}")
                    print(f"LLM SDK response: {reponse}")
                return reponse
            except OpenAIError as exc:
                if self.config.debug_mode:
                    print(f"LLM SDK call failed: {exc}")
                raise LLMUnavailableError(str(exc)) from exc

        if stream:
            raise LLMUnavailableError(
                "stream=True requires openai package. Install with: pip install openai"
            )

        if self.config.debug_mode:
            print("LLM SDK not available or failed to initialize; falling back to urllib for non-streaming call.")
        return self._post_chat_urllib(request)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        stream: Optional[bool] = None,
    ) -> str:
        """Return the final text content.

        Non-streaming is recommended for auto-grading and JSON extraction.
        Streaming is useful for CLI observation, but this method still returns
        the full accumulated string.
        """
        if self.config.demo_mode:
            return self._demo_chat(messages)

        use_stream = self.config.stream if stream is None else stream

        if use_stream:
            return self._chat_stream(messages, temperature)

        return self._chat_once(messages, temperature)

    def _chat_once(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
    ) -> str:
        """Non-streaming call: suitable for auto-grading and JSON extraction."""
        response = self.chat_completion(
            messages=messages,
            temperature=temperature,
            stream=False,
        )

        response_dict = self._response_to_dict(response)
        choice = (response_dict.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""

        if self.config.debug_mode:
            print("=" * 20 + " LLM RESPONSE " + "=" * 20)
            print(content)

        return content

    def _chat_stream(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
    ) -> str:
        """Streaming call: print chunks and return the final accumulated string."""
        stream = self.chat_completion(
            messages=messages,
            temperature=temperature,
            stream=True,
        )

        parts: List[str] = []

        try:
            for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue

                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None) or ""

                if content:
                    print(content, end="", flush=True)
                    parts.append(content)

        except OpenAIError as exc:
            raise LLMUnavailableError(str(exc)) from exc

        print()

        result = "".join(parts)

        if self.config.debug_mode:
            print("=" * 20 + " LLM STREAM DONE " + "=" * 20)
            print(result)

        return result

    def complete_json(
        self,
        system: str,
        user: str,
        schema_hint: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Ask the model for a JSON object and parse it.

        The caller provides a compact schema hint; malformed or fenced JSON is
        cleaned up before parsing. Network/configuration errors surface as
        LLMUnavailableError so runtime nodes can fall back deterministically.
        """
        if not self.available:
            raise LLMUnavailableError(
                "RA_LLM_API_KEY, DEEPSEEK_API_KEY, or OPENAI_API_KEY is required"
            )

        if self.config.demo_mode:
            return self._parse_json_object(
                self._demo_chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ]
                )
            )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"{user}\n\nReturn only JSON matching this shape:\n{schema_hint}",
            },
        ]

        response = self.chat_completion(
            messages=messages,
            temperature=self.config.temperature,
            tools=tools,
            tool_choice="auto" if tools else None,
            stream=False,
        )
        
        if self.config.debug_mode:
            print("=" * 20 + " complete_json RESPONSE " + "=" * 20)
            print(response)

        response_dict = self._response_to_dict(response)
        choice = (response_dict.get("choices") or [{}])[0]
        message = choice.get("message") or {}

        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            call = tool_calls[0]
            fn = call.get("function") or {}
            name = fn.get("name", "")
            args_text = fn.get("arguments") or "{}"

            try:
                args = json.loads(args_text)
            except json.JSONDecodeError:
                args = {}

            return {
                "tool_name": name,
                "args": args,
            }

        content = message.get("content") or "{}"
        return self._parse_json_object(content)

    def _post_chat_urllib(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dependency-free fallback for non-streaming Chat Completions."""
        url = self._normalize_url_for_urllib(self.config.base_url)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMUnavailableError(str(exc)) from exc

    def _post_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compatible wrapper for previous callers.

        Old code expected:
            _post_chat(payload) -> dict

        New code should prefer:
            chat_completion(...)
            chat(...)
        """
        payload = dict(payload)

        stream = bool(payload.get("stream", False))
        if stream:
            raise LLMUnavailableError(
                "_post_chat() compatibility wrapper does not support stream=True; use chat(..., stream=True)."
            )

        messages = payload.pop("messages")
        temperature = payload.pop("temperature", self.config.temperature)
        tools = payload.pop("tools", None)
        tool_choice = payload.pop("tool_choice", None)

        response = self.chat_completion(
            messages=messages,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            stream=False,
            **payload,
        )

        return self._response_to_dict(response)

    @staticmethod
    def _response_to_dict(response: Any) -> Dict[str, Any]:
        """Convert SDK response object or urllib dict into a plain dict."""
        if isinstance(response, dict):
            return response

        if hasattr(response, "model_dump"):
            return response.model_dump()

        if hasattr(response, "dict"):
            return response.dict()

        raise LLMUnavailableError(
            f"Unsupported LLM response type: {type(response).__name__}"
        )

    @staticmethod
    def _parse_json_object(text: str) -> Dict[str, Any]:
        stripped = text.strip()

        if stripped.startswith("```"):
            stripped = stripped.strip("`").strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()

        start = stripped.find("{")
        end = stripped.rfind("}")

        if start >= 0 and end >= start:
            stripped = stripped[start : end + 1]

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError(
                f"LLM did not return parseable JSON: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            raise LLMUnavailableError("LLM JSON response must be an object")

        return parsed

    @staticmethod
    def _demo_chat(messages: List[Dict[str, Any]]) -> str:
        return json.dumps(
            {
                "demo": True,
                "message_count": len(messages),
                "note": "RA_LLM_DEMO_MODE=1, no real LLM call was made.",
            },
            ensure_ascii=False,
        )