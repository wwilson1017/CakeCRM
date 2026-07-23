"""
CakeCRM — Google Gemini provider.

Streaming via google.generativeai with function calling, using a Gemini API key.
The SDK is imported lazily inside methods (with try/except ImportError) so a
missing package or absent key never breaks import or startup.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from providers.base import AIProvider

logger = logging.getLogger(__name__)

# google-generativeai 0.8.6 has no per-instance client — genai.configure() sets
# PROCESS-GLOBAL state. Serialize configure()+call on the live paths so two
# concurrent Google operations with different keys can't redirect each other's
# in-flight request to the wrong key.
_configure_lock = asyncio.Lock()

# Fields not supported by Gemini's Schema protobuf
_UNSUPPORTED_SCHEMA_FIELDS = {"default", "examples", "additionalProperties"}


def _clean_schema(schema: dict) -> dict:
    """Recursively strip fields that Gemini's FunctionDeclaration doesn't support."""
    if not isinstance(schema, dict):
        return schema
    result = {k: v for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_FIELDS}
    if "properties" in result:
        result["properties"] = {
            k: _clean_schema(v) for k, v in result["properties"].items()
        }
    if "items" in result and isinstance(result["items"], dict):
        result["items"] = _clean_schema(result["items"])
    return result

# Fallback list only — list_models() fetches live from the Gemini API and this
# is used when that call fails. Kept current against the official Gemini model
# list (see PRICING.md).
GOOGLE_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

# Non-text-chat model ids to keep OUT of the catalog: many support
# generateContent (so pass the method filter) but are TTS/image/audio/preview
# variants that must never be picked as a tier default or active model.
_GOOGLE_NON_CHAT = ("tts", "image", "audio", "embedding", "aqa", "-exp", "preview", "thinking", "-live")


class GoogleProvider(AIProvider):
    def __init__(self, api_key: str = "", model: str = "gemini-2.5-flash"):
        super().__init__(model=model)
        self.api_key = api_key

    @property
    def provider_name(self) -> str:
        return "google"

    def _format_tools(self, tools: list[dict]) -> list:
        """Convert internal tool format to Gemini function declarations."""
        try:
            from google.generativeai.types import FunctionDeclaration, Tool
        except ImportError:
            return []

        declarations = []
        for t in tools:
            schema = _clean_schema(t.get("input_schema", {}))
            declarations.append(FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=schema,
            ))

        if declarations:
            return [Tool(function_declarations=declarations)]
        return []

    async def stream_turn(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str | tuple[str, str],
    ) -> AsyncGenerator[dict, None]:
        if isinstance(system_prompt, tuple):
            system_prompt = "\n".join(system_prompt)
        try:
            import google.generativeai as genai
            from google.generativeai import protos
        except ImportError:
            yield {"type": "error", "error": "google-generativeai package not installed"}
            yield {"type": "_turn_complete", "tool_calls": [], "stop_reason": "error"}
            return

        genai.configure(api_key=self.api_key)

        gemini_tools = self._format_tools(tools)

        # Build Gemini history (all but last user message)
        history = []
        for m in messages[:-1]:
            role = m.get("role", "user")
            content = m.get("content", "")

            if isinstance(content, list):
                # Structured parts: function calls/responses, plus any text blocks
                # the provider-neutral assembler coalesced in (e.g. a compaction
                # gist folded onto a tool_result user turn). Text blocks carry no
                # `_type` and would otherwise be silently dropped here.
                parts = []
                for part_data in content:
                    if not isinstance(part_data, dict):
                        continue
                    if part_data.get("_type") == "function_call":
                        parts.append(protos.Part(
                            function_call=protos.FunctionCall(
                                name=part_data["name"],
                                args=part_data.get("args", {}),
                            )
                        ))
                    elif part_data.get("_type") == "function_response":
                        parts.append(protos.Part(
                            function_response=protos.FunctionResponse(
                                name=part_data["name"],
                                response=part_data.get("response", {}),
                            )
                        ))
                    elif part_data.get("type") == "text" and part_data.get("text"):
                        parts.append(protos.Part(text=part_data["text"]))
                if parts:
                    gemini_role = "model" if role == "assistant" else "user"
                    history.append(protos.Content(role=gemini_role, parts=parts))
            elif isinstance(content, str) and content:
                gemini_role = "model" if role != "user" else "user"
                history.append(protos.Content(
                    role=gemini_role,
                    parts=[protos.Part(text=content)],
                ))

        model_kwargs = {
            "model_name": self.model,
            "system_instruction": system_prompt,
        }
        if gemini_tools:
            model_kwargs["tools"] = gemini_tools

        model = genai.GenerativeModel(**model_kwargs)
        chat = model.start_chat(history=history)

        # Last user message — may be plain text or structured function responses
        last_content = messages[-1].get("content", "") if messages else ""
        if isinstance(last_content, list):
            # Structured parts (function responses after tool execution), plus any
            # coalesced text blocks (e.g. an approval ack or gist folded onto a
            # tool_result turn) so they aren't dropped from the sent message.
            parts = []
            for part_data in last_content:
                if not isinstance(part_data, dict):
                    continue
                if part_data.get("_type") == "function_response":
                    parts.append(protos.Part(
                        function_response=protos.FunctionResponse(
                            name=part_data["name"],
                            response=part_data.get("response", {}),
                        )
                    ))
                elif part_data.get("type") == "text" and part_data.get("text"):
                    parts.append(protos.Part(text=part_data["text"]))
            send_msg = protos.Content(role="user", parts=parts) if parts else ""
        else:
            send_msg = last_content

        full_text = ""
        tool_calls = []

        try:
            response = await chat.send_message_async(send_msg, stream=True)

            async for chunk in response:
                # Text chunks
                if hasattr(chunk, "text") and chunk.text:
                    full_text += chunk.text
                    yield {"type": "text", "text": chunk.text}

                # Function calls
                if chunk.candidates:
                    for candidate in chunk.candidates:
                        if not candidate.content.parts:
                            continue
                        for part in candidate.content.parts:
                            if hasattr(part, "function_call") and part.function_call:
                                fc = part.function_call
                                call_id = f"call_{len(tool_calls)}"
                                args = dict(fc.args) if fc.args else {}
                                tool_calls.append({
                                    "id": call_id,
                                    "name": fc.name,
                                    "args": args,
                                    "input_json": json.dumps(args),
                                })
                                yield {"type": "tool_start", "tool": fc.name, "tool_use_id": call_id}
                                yield {"type": "tool_args", "tool": fc.name, "tool_use_id": call_id, "args": args}

            stop_reason = "tool_use" if tool_calls else "stop"

        except Exception as e:
            logger.error("Gemini API error: %s", e)
            yield {"type": "error", "error": f"AI service error: {e!s}"}
            yield {"type": "_turn_complete", "tool_calls": [], "stop_reason": "error"}
            return

        yield {
            "type": "_turn_complete",
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
        }

    def add_tool_results(
        self,
        messages: list[dict],
        tool_calls: list[dict],
        results: list[dict],
    ) -> list[dict]:
        """Append model function calls + user function responses to messages.

        Gemini requires the conversation history to contain:
        1. A model message with FunctionCall parts
        2. A user message with FunctionResponse parts
        These are stored as structured dicts and converted to protos in stream_turn().
        """
        # Model's function calls
        fc_parts = []
        for tc in tool_calls:
            fc_parts.append({
                "_type": "function_call",
                "name": tc["name"],
                "args": tc.get("args", {}),
            })
        assistant_msg = {"role": "assistant", "content": fc_parts}

        # User's function responses
        fr_parts = []
        for r in results:
            fr_parts.append({
                "_type": "function_response",
                "name": r.get("tool_name", ""),
                "response": {"result": str(r["content"])},
            })
        user_msg = {"role": "user", "content": fr_parts}

        return messages + [assistant_msg, user_msg]

    async def _fetch_models(self) -> list[str]:
        import google.generativeai as genai

        def _list() -> list[str]:
            out = []
            for m in genai.list_models():
                methods = getattr(m, "supported_generation_methods", []) or []
                if "generateContent" not in methods:
                    continue
                name = m.name.removeprefix("models/")
                if any(token in name.lower() for token in _GOOGLE_NON_CHAT):
                    continue  # skip TTS/image/audio/preview variants
                out.append(name)
            return out

        async with _configure_lock:
            genai.configure(api_key=self.api_key)
            return await asyncio.to_thread(_list)

    async def list_models(self) -> list[str]:
        from providers.model_listing import (
            cache_key,
            cached_models,
            materialize_inference,
        )
        key = cache_key("google", self.api_key)
        models, is_live = await cached_models(key, self._fetch_models, GOOGLE_MODELS)
        materialize_inference("google", models, is_live)
        return models

    async def validate(self) -> bool:
        """Listing models needs only a valid key — auth-oriented validation."""
        try:
            import google.generativeai as genai
            async with _configure_lock:
                genai.configure(api_key=self.api_key)
                await asyncio.to_thread(lambda: list(genai.list_models()))
            return True
        except Exception as e:
            logger.error("Google validation failed: %s", e)
            return False
