#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from responses_image_api import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_IMAGE_QUALITY,
    DEFAULT_IMAGE_SIZE,
    DEFAULT_PROBE_PROMPT,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TEXT_MODEL,
    ResponsesImageClient,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe a Responses endpoint before attempting image generation.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("RESPONSES_IMAGE_ENDPOINT", "").strip(),
        help="Responses endpoint URL. Defaults to RESPONSES_IMAGE_ENDPOINT.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("RESPONSES_IMAGE_API_KEY", "").strip(),
        help="API key. Defaults to RESPONSES_IMAGE_API_KEY.",
    )
    parser.add_argument("--text-model", default=DEFAULT_TEXT_MODEL)
    parser.add_argument("--image-model", default=DEFAULT_IMAGE_MODEL)
    parser.add_argument("--image-size", default=DEFAULT_IMAGE_SIZE)
    parser.add_argument("--image-quality", default=DEFAULT_IMAGE_QUALITY)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--probe-prompt", default=DEFAULT_PROBE_PROMPT)
    parser.add_argument(
        "--skip-image-check",
        action="store_true",
        help="Only verify the text route and auth, skipping the image_generation probe.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Base timeout in seconds.",
    )
    parser.add_argument(
        "--save-json",
        help="Optional path for writing the JSON result to disk.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.endpoint:
        parser.error("missing --endpoint or RESPONSES_IMAGE_ENDPOINT")
    if not args.api_key:
        parser.error("missing --api-key or RESPONSES_IMAGE_API_KEY")

    client = ResponsesImageClient(
        args.endpoint,
        args.api_key,
        timeout=args.timeout,
    )

    text_probe = client.probe_endpoint_route(args.text_model)
    image_probe: dict[str, object] | None = None
    verdict = classify_text_probe(text_probe)
    exit_code = 1

    if text_probe.get("ok"):
        if args.skip_image_check:
            verdict = "text_only_ready"
            exit_code = 0
        else:
            image_probe = client.probe_image_generation_capability(
                text_model=args.text_model,
                image_model=args.image_model,
                image_size=args.image_size,
                image_quality=args.image_quality,
                reasoning_effort=args.reasoning_effort,
                prompt=args.probe_prompt,
            )
            verdict, exit_code = classify_image_probe(image_probe)

    result = {
        "overall_ok": exit_code == 0,
        "verdict": verdict,
        "endpoint": args.endpoint,
        "host": extract_host(args.endpoint),
        "text_model": args.text_model,
        "image_model": args.image_model,
        "text_probe": text_probe,
        "image_probe": image_probe,
    }

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)

    if args.save_json:
        save_path = Path(args.save_json).expanduser().resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(rendered + "\n", encoding="utf-8")

    return exit_code


def classify_text_probe(text_probe: dict[str, object]) -> str:
    if text_probe.get("ok"):
        return "text_route_ready"

    kind = str(text_probe.get("kind") or "")
    if kind in {"html_response", "non_json_response"}:
        return "bad_route"
    if kind == "auth_error":
        return "auth_error"
    if kind == "network_error":
        return "network_error"
    return "text_probe_failed"


def classify_image_probe(image_probe: dict[str, object]) -> tuple[str, int]:
    if image_probe.get("ok"):
        return "image_generation_ready", 0

    message = str(image_probe.get("message") or "").lower()
    if "non-standard sse payload" in message or "response.output_item.done" in message:
        return "image_generation_uncertain", 2
    return "image_generation_unavailable", 2


def extract_host(endpoint: str) -> str:
    from urllib.parse import urlparse

    return urlparse(endpoint).netloc or endpoint


if __name__ == "__main__":
    sys.exit(main())
