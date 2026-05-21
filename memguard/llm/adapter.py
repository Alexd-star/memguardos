from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMAdapterError(RuntimeError):
    pass


@dataclass
class GenerationResult:
    provider: str
    generated: bool
    prompt: str
    answer: str
    model: str = ""
    endpoint: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "generated": self.generated,
            "model": self.model,
            "endpoint": self.endpoint,
            "answer": self.answer,
            "prompt": self.prompt,
            "error": self.error,
        }


def generate_answer(
    prompt: str,
    *,
    provider: str = "dry-run",
    model: str = "",
    endpoint: str = "",
    api_key: str = "",
    command: str = "",
    timeout: float = 30.0,
) -> GenerationResult:
    """Generate an answer from an already-redacted MemGuardOS prompt.

    The adapter is deliberately optional: dry-run is the default and performs
    no network or paid API call. Real providers are explicit so benchmark and
    openKylin verification remain reproducible without external services.
    """

    if provider == "dry-run":
        return GenerationResult(
            provider=provider,
            generated=False,
            model=model,
            endpoint=endpoint,
            prompt=prompt,
            answer="",
        )
    if provider == "local-command":
        return _generate_with_local_command(prompt, command=command, timeout=timeout)
    if provider == "openai-compatible":
        return _generate_with_openai_compatible(prompt, model=model, endpoint=endpoint, api_key=api_key, timeout=timeout)
    raise LLMAdapterError(f"unsupported provider: {provider}")


def _generate_with_local_command(prompt: str, *, command: str, timeout: float) -> GenerationResult:
    if not command.strip():
        raise LLMAdapterError("local-command provider requires --command")
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            shell=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMAdapterError(f"local command timed out after {timeout}s") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise LLMAdapterError(f"local command failed with exit code {completed.returncode}: {stderr}")
    return GenerationResult(
        provider="local-command",
        generated=True,
        prompt=prompt,
        answer=completed.stdout.strip(),
        endpoint=command,
    )


def _generate_with_openai_compatible(
    prompt: str,
    *,
    model: str,
    endpoint: str,
    api_key: str,
    timeout: float,
) -> GenerationResult:
    if not endpoint.strip():
        raise LLMAdapterError("openai-compatible provider requires --endpoint")
    if not model.strip():
        raise LLMAdapterError("openai-compatible provider requires --model")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是 Linux/openKylin OS Agent。只能使用用户消息里已经脱敏的记忆上下文，不得复原敏感信息。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise LLMAdapterError(f"openai-compatible request failed: {exc}") from exc

    try:
        data = json.loads(raw)
        answer = str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMAdapterError("openai-compatible response does not contain choices[0].message.content") from exc
    return GenerationResult(
        provider="openai-compatible",
        generated=True,
        model=model,
        endpoint=endpoint,
        prompt=prompt,
        answer=answer,
    )
