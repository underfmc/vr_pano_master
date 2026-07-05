# -*- coding: utf-8 -*-
"""Optional OpenAI-compatible AITunnel helper.

Recommended model roles for this project:
- AITUNNEL_MODEL=claude-sonnet-5              # main agent / planning / prompts
- AITUNNEL_VISION_MODEL=gemini-3-5-flash     # image QA / satellite & street pano analysis
- AITUNNEL_CHEAP_MODEL=qwen3-7-plus          # POI / JSON / cheap mass tasks
- AITUNNEL_CODE_MODEL=claude-sonnet-5        # code tasks
- AITUNNEL_REVIEW_MODEL=gpt-5-5              # expensive audit, rare

Do not commit real API keys.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

from openai import OpenAI


ROLE_ENV = {
    "main": "AITUNNEL_MODEL",
    "vision": "AITUNNEL_VISION_MODEL",
    "cheap": "AITUNNEL_CHEAP_MODEL",
    "code": "AITUNNEL_CODE_MODEL",
    "review": "AITUNNEL_REVIEW_MODEL",
}

ROLE_DEFAULT = {
    "main": "claude-sonnet-5",
    "vision": "gemini-3-5-flash",
    "cheap": "qwen3-7-plus",
    "code": "claude-sonnet-5",
    "review": "gpt-5-5",
}


def get_client() -> OpenAI:
    key = os.getenv("AITUNNEL_API_KEY")
    base_url = os.getenv("AITUNNEL_BASE_URL", "https://api.aitunnel.ru/v1")
    if not key:
        raise RuntimeError("AITUNNEL_API_KEY is not set")
    return OpenAI(api_key=key, base_url=base_url)


def model_for_role(role: str = "main", explicit_model: str | None = None) -> str:
    if explicit_model:
        return explicit_model
    env_name = ROLE_ENV.get(role, "AITUNNEL_MODEL")
    return os.getenv(env_name) or ROLE_DEFAULT.get(role) or os.getenv("AITUNNEL_MODEL", "claude-sonnet-5")


def ask_ai(system: str, user: str, model: str | None = None, role: str = "main", temperature: float = 0.2) -> str:
    client = get_client()
    resp = client.chat.completions.create(
        model=model_for_role(role, model),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def image_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def ask_vision(system: str, prompt: str, image_paths: Iterable[Path], model: str | None = None) -> str:
    client = get_client()
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(Path(p))}})
    resp = client.chat.completions.create(
        model=model_for_role("vision", model),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        temperature=0.1,
    )
    return resp.choices[0].message.content or ""


def extract_json_object(text: str) -> Dict[str, Any]:
    """Best-effort JSON object extraction from an LLM response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)
