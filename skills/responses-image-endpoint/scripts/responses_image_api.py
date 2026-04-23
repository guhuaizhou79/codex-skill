#!/usr/bin/env python3
from __future__ import annotations

import base64
import http.client
import json
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_TEXT_MODEL = "gpt-5.4"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1024"
DEFAULT_IMAGE_QUALITY = "auto"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_PROBE_PROMPT = "A small white cat, plain background, soft natural light, realistic photography."

PROMPT_OPTIMIZER_INSTRUCTIONS = (
    "You rewrite image prompts for image generation models. Preserve the user's intent, "
    "subject, style, constraints, and language. Improve clarity, composition, lighting, "
    "materials, and camera details only when helpful. Output only the final optimized prompt "
    "with no markdown, no title, and no explanation."
)


@dataclass(slots=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass(slots=True)
class ImageGenerationResult:
    image_bytes: bytes
    revised_prompt: str


class ResponsesImageClient:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        timeout: float = 45.0,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.endpoint = endpoint.strip()
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.log_callback = log_callback
        self.retry_delays = (2.0, 4.0)
        self.text_responses_verified = False
        self.last_raw_sse_text = ""

    def probe_endpoint_route(self, text_model: str) -> dict[str, Any]:
        payload = {
            "model": text_model,
            "input": "ping",
            "max_output_tokens": 1,
        }

        try:
            response = self._send_request(
                payload,
                accept="application/json",
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "kind": "network_error",
                "message": f"Network request failed: {exc}",
            }

        preview_text = response.text[:800]
        if self._looks_like_html_response(preview_text, response.headers.get("Content-Type", "")):
            return {
                "ok": False,
                "kind": "html_response",
                "status_code": response.status_code,
                "message": (
                    "Endpoint returned HTML instead of API JSON. The URL may point to a login page, "
                    "management console, or the wrong route rather than /v1/responses."
                ),
            }

        try:
            response_json = response.json()
        except ValueError:
            return {
                "ok": False,
                "kind": "non_json_response",
                "status_code": response.status_code,
                "content_type": str(response.headers.get("Content-Type") or ""),
                "message": self._build_non_json_response_error(response),
            }

        if response.status_code >= 400:
            kind = "auth_error" if response.status_code in {401, 403} else "api_json_error"
            return {
                "ok": False,
                "kind": kind,
                "status_code": response.status_code,
                "message": self._extract_error_message(response_json)
                or f"HTTP {response.status_code}",
            }

        self.text_responses_verified = True
        return {
            "ok": True,
            "kind": "api_json_ok",
            "status_code": response.status_code,
            "preview": self._extract_text_output(response_json),
        }

    def probe_image_generation_capability(
        self,
        *,
        text_model: str,
        image_model: str,
        image_size: str,
        image_quality: str,
        reasoning_effort: str,
        prompt: str = DEFAULT_PROBE_PROMPT,
    ) -> dict[str, Any]:
        try:
            result = self.generate_image(
                prompt=prompt,
                text_model=text_model,
                image_model=image_model,
                image_size=image_size,
                image_quality=image_quality,
                reasoning_effort=reasoning_effort,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "kind": "image_generation_error",
                "message": str(exc),
            }

        return {
            "ok": True,
            "kind": "image_generation_ok",
            "bytes_length": len(result.image_bytes),
            "revised_prompt": result.revised_prompt,
        }

    def optimize_prompt(
        self,
        original_prompt: str,
        *,
        text_model: str,
        reasoning_effort: str,
    ) -> str:
        payload: dict[str, Any] = {
            "model": text_model,
            "instructions": PROMPT_OPTIMIZER_INSTRUCTIONS,
            "input": original_prompt,
            "max_output_tokens": 600,
        }
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}

        response_json = self._post_responses_request(payload, timeout=max(self.timeout, 90.0))
        self.text_responses_verified = True
        optimized_prompt = self._extract_text_output(response_json).strip()
        if not optimized_prompt:
            raise RuntimeError("Prompt optimization succeeded but returned no text output.")
        return optimized_prompt

    def generate_image(
        self,
        *,
        prompt: str,
        text_model: str,
        image_model: str,
        image_size: str,
        image_quality: str,
        reasoning_effort: str,
    ) -> ImageGenerationResult:
        image_tool: dict[str, Any] = {
            "type": "image_generation",
            "model": image_model,
        }
        if image_size:
            image_tool["size"] = image_size
        if image_quality:
            image_tool["quality"] = image_quality

        payload: dict[str, Any] = {
            "model": text_model,
            "input": prompt,
            "tools": [image_tool],
            "tool_choice": {"type": "image_generation"},
            "stream": True,
        }
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}

        revised_prompt = ""
        try:
            for event_name, event_data in self._post_responses_request_sse(
                payload,
                timeout=max(self.timeout, 180.0),
            ):
                normalized_event_name = str(event_data.get("type") or event_name).strip()
                if normalized_event_name == "response.output_item.done":
                    item = event_data.get("item")
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "image_generation_call":
                        continue
                    image_base64 = str(item.get("result") or "").strip()
                    if not image_base64:
                        continue
                    revised_prompt = str(item.get("revised_prompt") or revised_prompt)
                    if image_base64.startswith("data:") and "," in image_base64:
                        image_base64 = image_base64.split(",", 1)[1]
                    return ImageGenerationResult(
                        image_bytes=base64.b64decode(image_base64),
                        revised_prompt=revised_prompt,
                    )

                if normalized_event_name in {"error", "response.error"}:
                    raise RuntimeError(self._build_event_error(event_data))
        except RuntimeError as exc:
            raise RuntimeError(self._decorate_image_generation_error(str(exc), image_model)) from exc

        raise RuntimeError(
            self._decorate_image_generation_error(
                "The image stream ended without an image_generation result in response.output_item.done.",
                image_model,
            )
        )

    def _post_responses_request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        for attempt in range(1, len(self.retry_delays) + 2):
            try:
                response = self._send_request(
                    payload,
                    accept="application/json",
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001
                if self._should_retry_exception(exc) and self._retry_wait(attempt):
                    continue
                raise RuntimeError(f"Network request failed: {exc}") from exc

            if response.status_code >= 400:
                error_message = self._build_http_error(response)
                if self._should_retry_status_code(response.status_code) and self._retry_wait(attempt):
                    continue
                raise RuntimeError(error_message)

            try:
                return response.json()
            except ValueError as exc:
                raise RuntimeError(self._build_non_json_response_error(response)) from exc

        raise RuntimeError("Request failed after retries.")

    def _post_responses_request_sse(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> list[tuple[str, dict[str, Any]]]:
        for attempt in range(1, len(self.retry_delays) + 2):
            try:
                response = self._send_request(
                    payload,
                    accept="text/event-stream",
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001
                if self._should_retry_exception(exc) and self._retry_wait(attempt):
                    continue
                raise RuntimeError(f"Streaming request failed: {exc}") from exc

            if response.status_code >= 400:
                error_message = self._build_http_error(response)
                if self._should_retry_status_code(response.status_code) and self._retry_wait(attempt):
                    continue
                raise RuntimeError(error_message)

            self.last_raw_sse_text = response.text
            return self._parse_sse_events_from_raw_text(response.text)

        raise RuntimeError("Streaming request failed after retries.")

    def _send_request(
        self,
        payload: dict[str, Any],
        *,
        accept: str,
        timeout: float,
    ) -> HttpResponse:
        headers = self._headers(accept=accept)
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                try:
                    body = response.read()
                except http.client.IncompleteRead as exc:
                    body = exc.partial
                return HttpResponse(
                    status_code=response.getcode(),
                    headers=dict(response.headers.items()),
                    body=body,
                )
        except HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            headers_dict = dict(exc.headers.items()) if exc.headers is not None else {}
            return HttpResponse(
                status_code=exc.code,
                headers=headers_dict,
                body=body,
            )

    def _headers(self, *, accept: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": accept,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_http_error(self, response: HttpResponse) -> str:
        preview_text = response.text[:800]
        if self._looks_like_html_response(preview_text, response.headers.get("Content-Type", "")):
            return (
                f"HTTP {response.status_code}: endpoint returned HTML instead of JSON. "
                "The route may be incorrect or may point to a console/login page."
            )

        try:
            response_json = response.json()
        except ValueError:
            message = preview_text.strip() or "Unexpected non-JSON response."
            return f"HTTP {response.status_code}: {message}"

        return f"HTTP {response.status_code}: {self._extract_error_message(response_json) or 'Unknown error.'}"

    def _build_non_json_response_error(self, response: HttpResponse) -> str:
        preview_text = response.text[:800]
        if self._looks_like_html_response(preview_text, response.headers.get("Content-Type", "")):
            return (
                "Endpoint returned HTML instead of API JSON. The URL may point to a login page, "
                "management console, or wrong route instead of /v1/responses."
            )

        content_type = str(response.headers.get("Content-Type") or "")
        preview = preview_text.strip().replace("\r", " ").replace("\n", " ")
        if len(preview) > 240:
            preview = preview[:240] + "..."
        return (
            f"Endpoint returned non-JSON content. content_type={content_type or 'unknown'}; "
            f"preview={preview or '[empty response]'}"
        )

    def _extract_error_message(self, response_json: Any) -> str:
        if isinstance(response_json, dict):
            error = response_json.get("error")
            if isinstance(error, dict):
                for key in ("message", "code", "type"):
                    value = error.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            if isinstance(error, str) and error.strip():
                return error.strip()

            for key in ("message", "detail"):
                value = response_json.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        if response_json is None:
            return ""
        try:
            compact = json.dumps(response_json, ensure_ascii=False)
        except TypeError:
            return str(response_json)
        return compact[:400]

    def _parse_sse_events_from_raw_text(self, raw_sse_text: str) -> list[tuple[str, dict[str, Any]]]:
        normalized_text = raw_sse_text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized_text.strip():
            return []

        parsed_events: list[tuple[str, dict[str, Any]]] = []
        parse_errors: list[str] = []
        for event_block in re.split(r"\n{2,}", normalized_text):
            stripped_block = event_block.strip()
            if not stripped_block:
                continue
            try:
                parsed_event = self._parse_sse_event_block(stripped_block)
            except RuntimeError as exc:
                parse_errors.append(str(exc))
                continue
            if parsed_event is not None:
                parsed_events.append(parsed_event)

        if self._contains_terminal_sse_event(parsed_events):
            return parsed_events

        fallback_image_event = self._extract_image_event_from_raw_sse(normalized_text)
        if fallback_image_event is not None:
            return parsed_events + [fallback_image_event]

        if parsed_events:
            return parsed_events
        if parse_errors:
            raise RuntimeError(parse_errors[0])

        preview = normalized_text[:600]
        raise RuntimeError(f"Unable to parse SSE response. Preview: {preview}")

    def _parse_sse_event_block(self, event_block: str) -> tuple[str, dict[str, Any]] | None:
        current_event_name = ""
        data_lines: list[str] = []
        for line in event_block.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                current_event_name = line[6:].strip()
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
                continue
            if data_lines:
                data_lines.append(line)
        return self._flush_sse_event(current_event_name, data_lines)

    def _flush_sse_event(self, event_name: str, data_lines: list[str]) -> tuple[str, dict[str, Any]] | None:
        if not data_lines:
            return None

        data_text = "\n".join(data_lines).strip()
        if not data_text or data_text == "[DONE]":
            return None

        payload = self._parse_sse_json_payload(data_lines)
        if not isinstance(payload, dict):
            raise RuntimeError("SSE event payload is not a JSON object.")

        normalized_event_name = str(payload.get("type") or event_name or "").strip()
        return normalized_event_name, payload

    def _parse_sse_json_payload(self, data_lines: list[str]) -> Any:
        candidates = [
            "\n".join(data_lines).strip(),
            "".join(data_lines).strip(),
        ]
        unique_candidates: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in unique_candidates:
                unique_candidates.append(candidate)

        last_error: json.JSONDecodeError | None = None
        for candidate in unique_candidates:
            if candidate == "[DONE]":
                return None
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue

        preview = unique_candidates[0][:300] if unique_candidates else ""
        if last_error is None:
            raise RuntimeError(f"Invalid SSE event payload: {preview}")
        raise RuntimeError(f"Invalid SSE event payload: {last_error}; preview={preview}")

    def _contains_terminal_sse_event(self, parsed_events: list[tuple[str, dict[str, Any]]]) -> bool:
        for event_name, event_data in parsed_events:
            normalized_event_name = str(event_data.get("type") or event_name or "").strip()
            if normalized_event_name in {"response.output_item.done", "error", "response.error"}:
                return True
        return False

    def _extract_image_event_from_raw_sse(self, raw_sse_text: str) -> tuple[str, dict[str, Any]] | None:
        for json_like_text in self._extract_json_like_objects(raw_sse_text):
            if "response.output_item.done" not in json_like_text:
                continue
            if "image_generation_call" not in json_like_text:
                continue

            sanitized_text = self._sanitize_json_like_text(json_like_text)
            try:
                payload = json.loads(sanitized_text)
            except json.JSONDecodeError:
                payload = self._build_image_payload_from_json_like_text(json_like_text)

            if not isinstance(payload, dict):
                continue

            normalized_event_name = str(payload.get("type") or "response.output_item.done").strip()
            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") != "image_generation_call":
                continue
            if not str(item.get("result") or "").strip():
                continue
            return normalized_event_name, payload
        return None

    def _extract_json_like_objects(self, raw_text: str) -> list[str]:
        objects: list[str] = []
        start_index: int | None = None
        depth = 0
        in_string = False
        is_escaped = False

        for index, char in enumerate(raw_text):
            if start_index is None:
                if char == "{":
                    start_index = index
                    depth = 1
                    in_string = False
                    is_escaped = False
                continue

            if in_string:
                if is_escaped:
                    is_escaped = False
                    continue
                if char == "\\":
                    is_escaped = True
                    continue
                if char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
                continue
            if char != "}":
                continue

            depth -= 1
            if depth == 0:
                objects.append(raw_text[start_index:index + 1])
                start_index = None

        return objects

    def _sanitize_json_like_text(self, json_like_text: str) -> str:
        sanitized_chars: list[str] = []
        in_string = False
        is_escaped = False

        for char in json_like_text:
            if in_string and char in {"\r", "\n"}:
                continue

            sanitized_chars.append(char)
            if is_escaped:
                is_escaped = False
                continue
            if char == "\\":
                is_escaped = True
                continue
            if char == '"':
                in_string = not in_string

        return "".join(sanitized_chars)

    def _build_image_payload_from_json_like_text(self, json_like_text: str) -> dict[str, Any] | None:
        result_match = re.search(r'"result"\s*:\s*"([^"]+)"', json_like_text, re.DOTALL)
        if not result_match:
            return None

        image_base64 = re.sub(r"\s+", "", result_match.group(1))
        if not image_base64:
            return None

        revised_prompt_match = re.search(r'"revised_prompt"\s*:\s*"([^"]*)"', json_like_text, re.DOTALL)
        revised_prompt = ""
        if revised_prompt_match:
            revised_prompt = re.sub(r"[\r\n]+", "", revised_prompt_match.group(1))

        return {
            "type": "response.output_item.done",
            "item": {
                "type": "image_generation_call",
                "result": image_base64,
                "revised_prompt": revised_prompt,
            },
        }

    def _build_event_error(self, event_data: dict[str, Any]) -> str:
        nested_error = event_data.get("error")
        if isinstance(nested_error, dict):
            message = nested_error.get("message") or nested_error.get("code") or nested_error.get("type")
            if isinstance(message, str) and message.strip():
                return message.strip()

        for key in ("message", "detail", "type"):
            value = event_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return self._extract_error_message(event_data) or "Unknown streaming error."

    def _decorate_image_generation_error(self, message: str, image_model: str) -> str:
        normalized_message = str(message).strip()
        host = urlparse(self.endpoint).netloc or self.endpoint

        if self._looks_like_sse_payload_compat_issue(normalized_message):
            return (
                f"{normalized_message}\n\n"
                f"Compatibility note: text Responses may still work, but {host} appears to return "
                "a non-standard SSE payload for image generation. The relay may need a client-side "
                "compatibility adjustment before it can be declared unsupported."
            )

        if self._looks_like_image_generation_compat_issue(normalized_message):
            if self.text_responses_verified:
                return (
                    f"{normalized_message}\n\n"
                    f"Compatibility note: text Responses already worked on {host}, so auth and base "
                    f"routing look healthy. The failure happened during image generation, which usually "
                    f"means the route, relay, or upstream account pool does not support `{image_model}` "
                    "or the `image_generation` tool."
                )

            return (
                f"{normalized_message}\n\n"
                f"Compatibility note: the failure occurred in the image-generation path. {host} may not "
                f"support `{image_model}` or the `image_generation` tool on this route."
            )

        return normalized_message

    def _looks_like_sse_payload_compat_issue(self, message: str) -> bool:
        normalized_message = message.lower()
        markers = (
            "sse event payload",
            "response.output_item.done",
            "unable to parse sse response",
        )
        return any(marker in normalized_message for marker in markers)

    def _looks_like_image_generation_compat_issue(self, message: str) -> bool:
        normalized_message = message.lower()
        markers = (
            "http 404",
            "http 502",
            "http 503",
            "bad gateway",
            "not found",
            "returned html",
            "non-json",
        )
        return any(marker in normalized_message for marker in markers)

    def _extract_text_output(self, response_json: dict[str, Any]) -> str:
        direct_output = response_json.get("output_text")
        if isinstance(direct_output, str) and direct_output.strip():
            return direct_output.strip()

        fragments: list[str] = []
        for item in response_json.get("output", []):
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type in {"output_text", "text"}:
                text_value = self._normalize_text_fragment(item.get("text"))
                if text_value:
                    fragments.append(text_value)

            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                content_type = content.get("type")
                if content_type in {"output_text", "text"}:
                    text_value = self._normalize_text_fragment(content.get("text"))
                    if text_value:
                        fragments.append(text_value)

        return "\n".join(fragment for fragment in fragments if fragment).strip()

    def _normalize_text_fragment(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            candidate = value.get("value") or value.get("text") or ""
            if isinstance(candidate, str):
                return candidate.strip()
        return ""

    def _looks_like_html_response(self, text: str, content_type: str) -> bool:
        normalized_type = content_type.lower()
        if "text/html" in normalized_type:
            return True
        snippet = text.lstrip().lower()
        return snippet.startswith("<!doctype html") or snippet.startswith("<html")

    def _should_retry_exception(self, exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, socket.timeout, URLError, OSError)):
            return True
        return False

    def _should_retry_status_code(self, status_code: int) -> bool:
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}

    def _retry_wait(self, attempt: int) -> bool:
        if attempt > len(self.retry_delays):
            return False
        delay = self.retry_delays[attempt - 1]
        time.sleep(delay)
        return True
