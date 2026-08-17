"""LLM client protocol. OpenAI is the default; Gemini is an env-var swap."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field


class AgentAction(BaseModel):
    thought: str = Field(..., description="Short reason for this action.")
    type: str = Field(..., description="click | type | navigate | extract | done | fail")
    index: int | None = Field(default=None, description="Control index from the snapshot.")
    text: str | None = Field(default=None, description="Text to type for type actions.")
    url: str | None = None
    field: str | None = Field(default=None, description="Output field name for extract.")
    outputs: dict[str, Any] | None = None
    reason: str | None = None


class LLMClient(Protocol):
    async def complete(self, messages: list[dict[str, str]]) -> AgentAction: ...


def _openai_api_key() -> str:
    """Accept OPENAI_API_KEY, plus the common mistype openapi_key."""
    key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("openapi_key")
        or os.getenv("OPENAPI_KEY")
        or ""
    ).strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return key


def build_llm() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    if provider in {"openai", "openapi"}:
        return OpenAIClient()
    if provider == "scripted":
        return ScriptedClient()
    if provider == "gemini":
        return GeminiClient()
    return OpenAIClient()


class ScriptedClient:
    """Deterministic stand-in for tests. Not a substitute for a live discovery evidence run."""

    def __init__(self) -> None:
        self.phase = 0

    def _index(self, blob: str, predicate) -> int | None:
        for line in blob.splitlines():
            line = line.strip()
            if not line.startswith("["):
                continue
            try:
                idx = int(line[1 : line.index("]")])
            except ValueError:
                continue
            if predicate(line):
                return idx
        return None

    async def complete(self, messages: list[dict[str, str]]) -> AgentAction:
        blob = "\n".join(m["content"] for m in messages)
        if self.phase == 0:
            idx = self._index(blob, lambda line: "name=username" in line)
            self.phase = 1
            return AgentAction(thought="Type username", type="type", index=idx or 1, text="john")
        if self.phase == 1:
            idx = self._index(blob, lambda line: "name=password" in line)
            self.phase = 2
            return AgentAction(thought="Type password", type="type", index=idx or 2, text="demo")
        if self.phase == 2:
            idx = self._index(blob, lambda line: "Log In" in line)
            self.phase = 3
            return AgentAction(thought="Submit login", type="click", index=idx or 3)
        if self.phase == 3:
            idx = self._index(blob, lambda line: " link " in line and any(ch.isdigit() for ch in line.split()))
            if idx:
                self.phase = 4
                return AgentAction(thought="Open first account", type="click", index=idx)
            self.phase = 4
            return AgentAction(
                thought="On overview",
                type="done",
                outputs={"account_id": "unknown", "available_balance": "unknown"},
            )
        return AgentAction(
            thought="Finished",
            type="done",
            outputs={"account_id": "seen", "available_balance": "seen"},
        )


class GeminiClient:
    def __init__(self) -> None:
        from google import genai
        from google.genai import types

        key = os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_API_KEY is not set")
        self._types = types
        self._client = genai.Client(api_key=key)
        self._model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._fallbacks = [
            self._model,
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-flash-latest",
        ]

    async def complete(self, messages: list[dict[str, str]]) -> AgentAction:
        import asyncio

        types = self._types
        contents = []
        system = ""
        for msg in messages:
            if msg["role"] == "system":
                system += msg["content"] + "\n"
            elif msg["role"] == "user":
                contents.append(msg["content"])
            else:
                contents.append(f"Assistant: {msg['content']}")
        prompt = (system + "\n\n" + "\n\n".join(contents)).strip()
        schema = {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "type": {"type": "string"},
                "index": {"type": "integer"},
                "text": {"type": "string"},
                "url": {"type": "string"},
                "field": {"type": "string"},
                "outputs": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string"},
                        "available_balance": {"type": "string"},
                        "error": {"type": "string"},
                    },
                },
                "reason": {"type": "string"},
            },
            "required": ["thought", "type"],
        }
        last_error: Exception | None = None
        seen: set[str] = set()
        for model in self._fallbacks:
            if model in seen:
                continue
            seen.add(model)
            for attempt in range(3):
                try:
                    config_kwargs: dict[str, Any] = {
                        "response_mime_type": "application/json",
                        "response_schema": schema,
                        "temperature": 0.1,
                    }
                    response = await self._client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(**config_kwargs),
                    )
                    text = (response.text or "").strip()
                    if text.startswith("```"):
                        text = text.strip("`")
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()
                    self._model = model
                    return AgentAction.model_validate_json(text)
                except Exception as exc:
                    last_error = exc
                    message = str(exc)
                    if "503" in message or "UNAVAILABLE" in message or "high demand" in message.lower():
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    break
        raise RuntimeError(f"Gemini failed: {last_error}") from last_error


class OpenAIClient:
    def __init__(self) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=_openai_api_key())
        self._model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self._fallbacks = [
            self._model,
            "gpt-4.1-mini",
            "gpt-4o-mini",
        ]

    async def complete(self, messages: list[dict[str, str]]) -> AgentAction:
        import asyncio

        last_error: Exception | None = None
        seen: set[str] = set()
        for model in self._fallbacks:
            if model in seen:
                continue
            seen.add(model)
            for attempt in range(3):
                try:
                    response = await self._client.chat.completions.create(
                        model=model,
                        messages=messages,  # type: ignore[arg-type]
                        temperature=0.1,
                        response_format={"type": "json_object"},
                    )
                    text = response.choices[0].message.content or "{}"
                    if text.startswith("```"):
                        text = text.strip("`")
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()
                    data = json.loads(text)
                    self._model = model
                    return AgentAction.model_validate(data)
                except Exception as exc:
                    last_error = exc
                    message = str(exc).lower()
                    if any(token in message for token in ("rate", "429", "timeout", "503", "overloaded")):
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    if any(token in message for token in ("model", "404", "does not exist", "not found")):
                        break
                    if attempt < 2:
                        await asyncio.sleep(1)
                        continue
                    break
        raise RuntimeError(f"OpenAI failed: {last_error}") from last_error
