"""OpenAI-compatible client for chat, vision and embeddings. Works with Ollama, llama-server and vLLM."""
import base64
import json
import re

import httpx

from . import config

_client = httpx.Client(base_url=config.LLM_BASE_URL, timeout=httpx.Timeout(600.0, connect=5.0))
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _clean(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def chat(messages, *, model=None, temperature=0.0, max_tokens=config.GEN_MAX_TOKENS, json_mode=False,
         think=False) -> str:
    body = {
        "model": model or config.GEN_MODEL,
        "messages": messages,
        "temperature": 0.6 if think else temperature,
        "max_tokens": max_tokens + (config.THINK_MAX_TOKENS if think else 0),
        # Qwen3 reasons in a separate field when thinking is on; the answer stays in `content`.
        "reasoning_effort": "medium" if think else "none",
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = _client.post("/chat/completions", json=body)
    r.raise_for_status()
    return _clean(r.json()["choices"][0]["message"]["content"])


def chat_stream(messages, *, model=None, think=False, max_tokens=config.GEN_MAX_TOKENS):
    """Yields ("thinking", text) and ("answer", text) deltas. Handles Ollama's `reasoning`,
    llama-server's `reasoning_content`, and inline <think> tags."""
    body = {
        "model": model or config.GEN_MODEL,
        "messages": messages,
        "stream": True,
        # Qwen3 recommends sampling (not greedy) when thinking; greedy decoding loops in long reasoning.
        "temperature": 0.6 if think else 0.0,
        "top_p": 0.95 if think else 1.0,
        "max_tokens": max_tokens + (config.THINK_MAX_TOKENS if think else 0),
        "reasoning_effort": "medium" if think else "none",
    }
    in_tag = False
    with _client.stream("POST", "/chat/completions", json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            choices = json.loads(data).get("choices") or [{}]
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning") or delta.get("reasoning_content")
            if reasoning:
                yield "thinking", reasoning
            content = delta.get("content") or ""
            while content:
                tag = "</think>" if in_tag else "<think>"
                head, sep, content = content.partition(tag)
                if head:
                    yield ("thinking" if in_tag else "answer"), head
                if sep:
                    in_tag = not in_tag


def chat_json(messages, **kw):
    raw = chat(messages, json_mode=True, **kw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group(0)) if m else {}


def describe_image(png_bytes: bytes, prompt: str) -> str:
    url = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": url}},
    ]}]
    return chat(messages, model=config.VISION_MODEL, max_tokens=1200)


def embed(texts: list[str]) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), 32):
        r = _client.post("/embeddings", json={"model": config.EMBED_MODEL, "input": texts[i:i + 32]})
        r.raise_for_status()
        out.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"]))
    return out


def unload(model: str) -> None:
    """Free a model's memory now instead of after the server's idle timeout (Ollama only; no-op elsewhere)."""
    root = config.LLM_BASE_URL.rsplit("/v1", 1)[0]
    try:
        httpx.post(f"{root}/api/generate", json={"model": model, "keep_alive": 0}, timeout=10)
    except httpx.HTTPError:
        pass


def available_models() -> list[str]:
    r = _client.get("/models")
    r.raise_for_status()
    return [m["id"] for m in r.json()["data"]]
