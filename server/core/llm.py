"""LLM adapters used by voice-agent games."""

from __future__ import annotations

import os

import aiohttp
from openai import AsyncOpenAI

from core.audio import clean_text, now_ms
from core.types import AgentConfig, StackName


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def normalize_stack(stack: StackName) -> StackName:
    if stack == "gpt":
        return "gemini"
    return stack


def client_for_stack(stack: StackName) -> AsyncOpenAI:
    normalized = normalize_stack(stack)
    if normalized == "nemotron":
        return AsyncOpenAI(
            api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
            base_url=os.getenv(
                "NEMOTRON_LLM_URL",
                "http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1",
            ),
        )
    raise ValueError(f"Stack {stack!r} does not use the OpenAI-compatible client")


def model_for_stack(stack: StackName) -> str:
    normalized = normalize_stack(stack)
    if normalized == "nemotron":
        return os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")
    return os.getenv("GEMINI_MODEL", "gemini-flash-latest")


async def generate_gemini_reply(
    agent: AgentConfig, heard_text: str, max_tokens: int
) -> tuple[str, int]:
    agent.messages.append({"role": "user", "content": heard_text})
    start = now_ms()

    model = model_for_stack(agent.stack)
    url = os.getenv(
        "GEMINI_API_URL",
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    )
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": os.environ["GEMINI_API_KEY"],
    }
    contents = []
    for message in agent.messages:
        role = "model" if message["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message["content"]}]})

    body = {
        "systemInstruction": {"parts": [{"text": agent.system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {
                "thinkingBudget": _env_int("GEMINI_THINKING_BUDGET", 0),
            },
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"Gemini error {response.status}: {payload}")

    parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = clean_text(" ".join(str(part.get("text") or "") for part in parts))
    agent.messages.append({"role": "assistant", "content": text})
    return text, now_ms() - start


async def generate_reply(agent: AgentConfig, heard_text: str, max_tokens: int) -> tuple[str, int]:
    if normalize_stack(agent.stack) == "gemini":
        return await generate_gemini_reply(agent, heard_text, max_tokens)

    agent.messages.append({"role": "user", "content": heard_text})
    start = now_ms()
    client = client_for_stack(agent.stack)

    extra_body = None
    if agent.stack == "nemotron":
        enable_thinking = os.getenv("NEMOTRON_ENABLE_THINKING", "false").lower() == "true"
        extra_body = {"chat_template_kwargs": {"enable_thinking": enable_thinking}}

    response = await client.chat.completions.create(
        model=model_for_stack(agent.stack),
        messages=[{"role": "system", "content": agent.system_prompt}, *agent.messages],
        temperature=0.7,
        max_tokens=max_tokens,
        extra_body=extra_body,
    )
    text = clean_text(response.choices[0].message.content or "")
    agent.messages.append({"role": "assistant", "content": text})
    return text, now_ms() - start
