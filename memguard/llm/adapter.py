from __future__ import annotations

import json
import ipaddress
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMAdapterError(RuntimeError):
    pass


def _endpoint_origin(parsed: urllib.parse.SplitResult) -> tuple[str, str, int]:
    try:
        port = parsed.port
    except ValueError as exc:
        raise LLMAdapterError("openai-compatible endpoint contains an invalid port") from exc
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _validate_openai_endpoint(endpoint: str) -> tuple[str, urllib.parse.SplitResult]:
    normalized = endpoint.strip()
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise LLMAdapterError("openai-compatible provider requires an http(s) endpoint URL")
    if parsed.username is not None or parsed.password is not None:
        raise LLMAdapterError("openai-compatible endpoint must not contain URL credentials")
    if parsed.fragment:
        raise LLMAdapterError("openai-compatible endpoint must not contain a URL fragment")
    _endpoint_origin(parsed)
    if parsed.scheme == "http":
        hostname = parsed.hostname.lower()
        is_loopback = hostname == "localhost"
        try:
            is_loopback = is_loopback or ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            pass
        if not is_loopback:
            raise LLMAdapterError(
                "openai-compatible HTTP endpoints are limited to the local machine; use HTTPS for remote providers"
            )
    return normalized, parsed


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str, int]):
        super().__init__()
        self._origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        normalized, parsed = _validate_openai_endpoint(newurl)
        if _endpoint_origin(parsed) != self._origin:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "openai-compatible endpoint refused a cross-origin redirect",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, normalized)


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
        command_args = shlex.split(command)
    except ValueError as exc:
        raise LLMAdapterError(f"local-command provider received an invalid command: {exc}") from exc
    if not command_args:
        raise LLMAdapterError("local-command provider requires a non-empty executable")
    try:
        completed = subprocess.run(
            command_args,
            input=prompt,
            text=True,
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
        endpoint=command_args[0],
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
    normalized_endpoint, parsed_endpoint = _validate_openai_endpoint(endpoint)
    display_endpoint = urllib.parse.urlunsplit(
        (
            parsed_endpoint.scheme,
            parsed_endpoint.netloc,
            parsed_endpoint.path,
            "",
            "",
        )
    )

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
    request = urllib.request.Request(
        normalized_endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    opener = urllib.request.build_opener(
        _SameOriginRedirectHandler(_endpoint_origin(parsed_endpoint))
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            response_bytes = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.URLError as exc:
        raise LLMAdapterError(f"openai-compatible request failed: {exc}") from exc
    if len(response_bytes) > 2 * 1024 * 1024:
        raise LLMAdapterError("openai-compatible response exceeded 2 MiB limit")
    raw = response_bytes.decode("utf-8")

    try:
        data = json.loads(raw)
        answer = str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMAdapterError("openai-compatible response does not contain choices[0].message.content") from exc
    return GenerationResult(
        provider="openai-compatible",
        generated=True,
        model=model,
        endpoint=display_endpoint,
        prompt=prompt,
        answer=answer,
    )
