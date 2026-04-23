#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from responses_image_api import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_IMAGE_QUALITY,
    DEFAULT_IMAGE_SIZE,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TEXT_MODEL,
    ResponsesImageClient,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Optimize a prompt and generate an image through a Responses endpoint.",
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
    parser.add_argument("--prompt", required=True, help="Original user prompt.")
    parser.add_argument("--text-model", default=DEFAULT_TEXT_MODEL)
    parser.add_argument("--image-model", default=DEFAULT_IMAGE_MODEL)
    parser.add_argument("--image-size", default=DEFAULT_IMAGE_SIZE)
    parser.add_argument("--image-quality", default=DEFAULT_IMAGE_QUALITY)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument(
        "--skip-optimize",
        action="store_true",
        help="Send the original prompt directly without running prompt optimization.",
    )
    parser.add_argument(
        "--skip-route-check",
        action="store_true",
        help="Skip the lightweight text-route probe before generation.",
    )
    parser.add_argument(
        "--output-dir",
        default="generated-images",
        help="Directory for the image and metadata files.",
    )
    parser.add_argument(
        "--file-stem",
        help="Optional base filename without extension.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Base timeout in seconds.",
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

    if not args.skip_route_check:
        text_probe = client.probe_endpoint_route(args.text_model)
        if not text_probe.get("ok"):
            failure = {
                "ok": False,
                "stage": "probe",
                "message": str(text_probe.get("message") or "Endpoint probe failed."),
                "probe": text_probe,
            }
            print(json.dumps(failure, ensure_ascii=False, indent=2))
            return 1

    original_prompt = args.prompt.strip()
    if not original_prompt:
        failure = {
            "ok": False,
            "stage": "input",
            "message": "Prompt cannot be empty.",
        }
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    try:
        optimized_prompt = ""
        final_prompt = original_prompt
        if not args.skip_optimize:
            optimized_prompt = client.optimize_prompt(
                original_prompt,
                text_model=args.text_model,
                reasoning_effort=args.reasoning_effort,
            )
            final_prompt = optimized_prompt or original_prompt

        result = client.generate_image(
            prompt=final_prompt,
            text_model=args.text_model,
            image_model=args.image_model,
            image_size=args.image_size,
            image_quality=args.image_quality,
            reasoning_effort=args.reasoning_effort,
        )
    except Exception as exc:  # noqa: BLE001
        failure = {
            "ok": False,
            "stage": "generate",
            "message": str(exc),
            "endpoint_host": urlparse(args.endpoint).netloc or args.endpoint,
            "text_model": args.text_model,
            "image_model": args.image_model,
        }
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = args.file_stem or build_default_stem(args.image_model, original_prompt)
    extension = guess_image_extension(result.image_bytes)
    image_path = output_dir / f"{stem}.{extension}"
    metadata_path = output_dir / f"{stem}.json"

    image_path.write_bytes(result.image_bytes)

    payload = {
        "ok": True,
        "endpoint_host": urlparse(args.endpoint).netloc or args.endpoint,
        "text_model": args.text_model,
        "image_model": args.image_model,
        "image_size": args.image_size,
        "image_quality": args.image_quality,
        "original_prompt": original_prompt,
        "optimized_prompt": optimized_prompt,
        "final_prompt": final_prompt,
        "revised_prompt": result.revised_prompt,
        "image_path": str(image_path),
        "metadata_path": str(metadata_path),
    }
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_default_stem(image_model: str, prompt: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    normalized_model = slugify(image_model) or "image"
    prompt_hint = slugify(prompt)[:32]
    if prompt_hint:
        return f"{stamp}-{normalized_model}-{prompt_hint}"
    return f"{stamp}-{normalized_model}"


def slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", normalized)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized


def guess_image_extension(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "webp"
    if image_bytes.startswith(b"BM"):
        return "bmp"
    return "png"


if __name__ == "__main__":
    sys.exit(main())
