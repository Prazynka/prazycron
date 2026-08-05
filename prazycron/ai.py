from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .analyzer import analyze
from .model import CronEntry


class AIError(RuntimeError):
    pass


SESSION_API_KEY = ""


def set_session_api_key(value: str) -> None:
    global SESSION_API_KEY
    SESSION_API_KEY = value.strip()


def get_api_key() -> str:
    return SESSION_API_KEY or os.environ.get("OPENAI_API_KEY", "")


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise AIError(f"HTTP {exc.code}: {detail[:600]}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AIError(str(exc)) from exc


def build_prompt(entry: CronEntry, language: str = "en") -> str:
    local = analyze(entry, language=language).as_text()
    output_language = "Polish" if language.lower().startswith("pl") else "English"
    return (
        f"Respond in {output_language}. Explain this Linux cron job clearly, precisely, and safely. "
        "Use these explicit sections: Schedule meaning; What the task does; Frequency; System impact; "
        "Risks / notes; Recommendation. Explain shell operators such as && and || when present, distinguish "
        "facts from inferences, mention cron's restricted environment and timezone where relevant, and do not "
        "claim certainty about unknown scripts. Include a safer or more observable version only when useful.\n\n"
        f"Schedule: {entry.schedule}\nUser: {entry.user}\nSource: {entry.source}\nCommand: {entry.command}\n\n"
        f"Local static analysis:\n{local}"
    )


def explain(entry: CronEntry, cfg: dict, provider: str | None = None) -> str:
    provider = provider or str(cfg.get("provider", "builtin"))
    language = str(cfg.get("language", "en"))
    if provider == "builtin":
        return analyze(entry, language=language).as_text()
    prompt = build_prompt(entry, language=language)
    if provider == "ollama":
        result = _post_json(
            str(cfg.get("ollama_endpoint", "http://127.0.0.1:11434/api/generate")),
            {"model": str(cfg.get("ollama_model", "qwen2.5:3b")), "prompt": prompt, "stream": False},
        )
        text = result.get("response")
        if not isinstance(text, str):
            raise AIError("Ollama returned no text response")
        return text.strip()
    if provider == "openai":
        key = get_api_key()
        if not key:
            raise AIError("API_KEY_REQUIRED")
        result = _post_json(
            str(cfg.get("openai_endpoint", "https://api.openai.com/v1/responses")),
            {"model": str(cfg.get("openai_model", "gpt-5-mini")), "input": prompt},
            {"Authorization": f"Bearer {key}"},
        )
        direct = result.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        fragments: list[str] = []
        for item in result.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    fragments.append(content["text"])
        if fragments:
            return "\n".join(fragments).strip()
        raise AIError("Online provider returned no text response")
    raise AIError(f"Unknown provider: {provider}")
