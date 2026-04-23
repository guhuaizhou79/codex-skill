#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from responses_image_api import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_IMAGE_QUALITY,
    DEFAULT_IMAGE_SIZE,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TEXT_MODEL,
    ResponsesImageClient,
)


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "image": (
        "image",
        "image_path",
        "file",
        "path",
        "filename",
        "图片",
        "图片路径",
        "配图",
        "图像",
    ),
    "text": (
        "text",
        "script",
        "narration",
        "subtitle",
        "dialogue",
        "line",
        "台词",
        "文案",
        "字幕",
        "旁白",
        "内容",
    ),
    "prompt": (
        "image_prompt",
        "prompt",
        "visual_prompt",
        "scene_prompt",
        "画面提示词",
        "提示词",
        "画面",
        "配图提示词",
    ),
    "speaker": (
        "speaker",
        "role",
        "character",
        "发言人",
        "角色",
        "人物",
    ),
    "duration": (
        "duration_seconds",
        "duration",
        "seconds",
        "时长",
        "时长秒",
        "秒数",
    ),
    "voice": ("voice", "配音", "音色", "声线"),
    "rate": ("rate", "语速"),
    "volume": ("volume", "音量"),
    "pitch": ("pitch", "音高"),
    "scene_title": ("scene_title", "sceneTitle", "title", "label", "标题", "镜头标题"),
}

ROOT_SEGMENT_KEYS = ("segments", "items", "scenes", "rows", "clips", "slides", "data")


@dataclass(slots=True)
class SegmentRecord:
    index: int
    image: str
    text: str
    prompt: str | None = None
    speaker: str | None = None
    duration_seconds: float | None = None
    voice: str | None = None
    rate: str | None = None
    volume: str | None = None
    pitch: str | None = None
    scene_title: str | None = None


class BuildError(RuntimeError):
    """Raised when the pipeline build cannot continue."""


def emit_console_text(text: str) -> None:
    if not text:
        return
    payload = text if text.endswith("\n") else f"{text}\n"
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(payload.encode(encoding, errors="replace"))
        buffer.flush()
        return
    sys.stdout.write(payload.encode(encoding, errors="replace").decode(encoding, errors="replace"))
    sys.stdout.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a narrated story video from a flexible JSON/CSV manifest."
    )
    parser.add_argument("--manifest", required=True, help="Source JSON or CSV manifest.")
    parser.add_argument(
        "--field-map",
        action="append",
        default=[],
        help="Map a normalized field to a source column, for example text=台词 or prompt=画面提示词.",
    )
    parser.add_argument("--endpoint", default=os.environ.get("RESPONSES_IMAGE_ENDPOINT", "").strip())
    parser.add_argument("--api-key", default=os.environ.get("RESPONSES_IMAGE_API_KEY", "").strip())
    parser.add_argument("--text-model", default=DEFAULT_TEXT_MODEL)
    parser.add_argument("--image-model", default=DEFAULT_IMAGE_MODEL)
    parser.add_argument("--image-size", default=DEFAULT_IMAGE_SIZE)
    parser.add_argument("--image-quality", default=DEFAULT_IMAGE_QUALITY)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--skip-optimize-prompts", action="store_true")
    parser.add_argument("--skip-image-generation", action="store_true")
    parser.add_argument("--overwrite-images", action="store_true")
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--work-dir", help="Output working directory.")
    parser.add_argument("--output", help="Final video path.")
    parser.add_argument("--voice", help="Default TTS voice.")
    parser.add_argument("--rate", help="Default TTS rate.")
    parser.add_argument("--volume", help="Default TTS volume.")
    parser.add_argument("--pitch", help="Default TTS pitch.")
    parser.add_argument("--boundary", choices=("SentenceBoundary", "WordBoundary"))
    parser.add_argument("--size", help="Video size such as 1080x1920.")
    parser.add_argument("--fps", type=int)
    parser.add_argument("--font-name", help="Subtitle font name.")
    parser.add_argument("--font-size", type=int)
    parser.add_argument("--margin-v", type=int)
    parser.add_argument("--no-burn-subtitles", action="store_true")
    parser.add_argument("--opening-subtitle-text", help="Optional subtitle block shown at the beginning.")
    parser.add_argument("--opening-subtitle-duration", type=float, help="Opening subtitle duration in seconds.")
    return parser.parse_args()


def parse_field_map(items: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise BuildError(f"Invalid --field-map value: {item}. Expected target=source.")
        target, source = item.split("=", 1)
        target = target.strip()
        source = source.strip()
        if target not in FIELD_ALIASES:
            allowed = ", ".join(sorted(FIELD_ALIASES))
            raise BuildError(f"Unknown field-map target '{target}'. Allowed: {allowed}")
        if not source:
            raise BuildError(f"Field-map source cannot be empty: {item}")
        mapping[target] = source
    return mapping


def load_source_manifest(manifest_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    suffix = manifest_path.suffix.lower()
    if suffix == ".csv":
        with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(row) for row in reader]
        if not rows:
            raise BuildError("CSV manifest is empty.")
        return rows, {}
    if suffix != ".json":
        raise BuildError("Manifest must be a JSON or CSV file.")

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)], {}
    if not isinstance(raw, dict):
        raise BuildError("JSON manifest must be a list or an object.")
    for key in ROOT_SEGMENT_KEYS:
        items = raw.get(key)
        if isinstance(items, list):
            return [dict(item) for item in items if isinstance(item, dict)], raw
    raise BuildError("JSON manifest must contain a segments/items/scenes/rows/clips/slides list.")


def pick_value(row: dict[str, Any], target: str, field_map: dict[str, str]) -> Any:
    explicit = field_map.get(target)
    if explicit is not None:
        return row.get(explicit)
    for alias in FIELD_ALIASES[target]:
        if alias in row and row.get(alias) not in (None, ""):
            return row.get(alias)
    return None


def resolve_output_paths(manifest_path: Path, row_index: int, image_value: Any) -> str:
    if image_value not in (None, ""):
        candidate = Path(str(image_value))
        if candidate.is_absolute():
            return str(candidate.resolve())
        return str((manifest_path.parent / candidate).resolve())
    default_name = f"{row_index:02d}.png"
    return str((manifest_path.parent / "images" / default_name).resolve())


def normalize_row(
    row: dict[str, Any],
    *,
    index: int,
    manifest_path: Path,
    field_map: dict[str, str],
) -> SegmentRecord:
    text = str(pick_value(row, "text", field_map) or "").strip()
    prompt = str(pick_value(row, "prompt", field_map) or "").strip()
    image = resolve_output_paths(manifest_path, index, pick_value(row, "image", field_map))

    if not text and not pick_value(row, "duration", field_map):
        raise BuildError(f"Row {index} is missing text/duration.")
    if not Path(image).exists() and not prompt:
        raise BuildError(
            f"Row {index} has no existing image and no prompt field for generation."
        )

    duration_seconds = pick_value(row, "duration", field_map)
    parsed_duration: float | None = None
    if duration_seconds not in (None, ""):
        try:
            parsed_duration = float(duration_seconds)
        except (TypeError, ValueError) as exc:
            raise BuildError(f"Row {index} has an invalid duration value.") from exc
        if parsed_duration <= 0:
            raise BuildError(f"Row {index} duration must be positive.")

    def clean(target: str) -> str | None:
        value = pick_value(row, target, field_map)
        if value in (None, ""):
            return None
        return str(value).strip()

    return SegmentRecord(
        index=index,
        image=image,
        text=text,
        prompt=prompt or None,
        speaker=clean("speaker"),
        duration_seconds=parsed_duration,
        voice=clean("voice"),
        rate=clean("rate"),
        volume=clean("volume"),
        pitch=clean("pitch"),
        scene_title=clean("scene_title"),
    )


def normalize_manifest(
    *,
    manifest_path: Path,
    rows: list[dict[str, Any]],
    root_config: dict[str, Any],
    field_map: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    segments = [
        normalize_row(
            row,
            index=index,
            manifest_path=manifest_path,
            field_map=field_map,
        )
        for index, row in enumerate(rows, start=1)
    ]

    work_dir = Path(args.work_dir).resolve() if args.work_dir else (manifest_path.parent / "build" / "story-video").resolve()
    output_path = Path(args.output).resolve() if args.output else (work_dir / "final_video.mp4").resolve()

    opening_subtitle_text = (
        args.opening_subtitle_text
        or str(root_config.get("opening_subtitle_text") or "").strip()
    )
    if not opening_subtitle_text:
        title = str(root_config.get("title") or "").strip()
        author = str(root_config.get("author") or "").strip()
        major_class = str(
            root_config.get("major_class")
            or root_config.get("class_name")
            or root_config.get("professional_class")
            or ""
        ).strip()
        lines: list[str] = []
        if title:
            lines.append(f"视频主题：{title}")
        if author:
            lines.append(f"作者：{author}")
        if major_class:
            lines.append(f"专业班级：{major_class}")
        opening_subtitle_text = "\n".join(lines)

    normalized = {
        "title": str(root_config.get("title") or manifest_path.stem).strip(),
        "work_dir": str(work_dir),
        "output": str(output_path),
        "voice": args.voice or root_config.get("voice"),
        "rate": args.rate or root_config.get("rate"),
        "volume": args.volume or root_config.get("volume"),
        "pitch": args.pitch or root_config.get("pitch"),
        "boundary": args.boundary or root_config.get("boundary"),
        "size": args.size or root_config.get("size") or "1080x1920",
        "fps": args.fps or root_config.get("fps") or 24,
        "opening_subtitle_text": opening_subtitle_text or None,
        "opening_subtitle_duration": (
            args.opening_subtitle_duration
            or root_config.get("opening_subtitle_duration")
            or None
        ),
        "segments": [],
    }
    for segment in segments:
        normalized["segments"].append(
            {
                "image": segment.image,
                "text": segment.text,
                "speaker": segment.speaker,
                "voice": segment.voice,
                "rate": segment.rate,
                "volume": segment.volume,
                "pitch": segment.pitch,
                "duration_seconds": segment.duration_seconds,
                "scene_title": segment.scene_title,
                "image_prompt": segment.prompt,
            }
        )
    return normalized


def choose_setting(primary: str | None, fallback: str | None, default: str) -> str:
    if primary:
        return primary
    if fallback:
        return fallback
    return default


def ensure_images(
    *,
    normalized_manifest: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    if args.skip_image_generation:
        return

    missing_segments = [
        segment for segment in normalized_manifest["segments"] if not Path(segment["image"]).exists()
    ]
    if not missing_segments:
        return

    endpoint = choose_setting(args.endpoint, os.environ.get("OPENAI_RESPONSES_ENDPOINT"), os.environ.get("RESPONSES_IMAGE_ENDPOINT", "").strip())
    api_key = choose_setting(args.api_key, os.environ.get("OPENAI_API_KEY"), os.environ.get("RESPONSES_IMAGE_API_KEY", "").strip())
    if not endpoint or not api_key:
        raise BuildError(
            "Missing endpoint or API key for image generation. "
            "Set --endpoint/--api-key or RESPONSES_IMAGE_ENDPOINT/RESPONSES_IMAGE_API_KEY."
        )

    client = ResponsesImageClient(endpoint, api_key)
    probe = client.probe_endpoint_route(args.text_model)
    if not probe.get("ok"):
        raise BuildError(f"Responses endpoint probe failed: {probe.get('message') or probe}")

    for index, segment in enumerate(normalized_manifest["segments"], start=1):
        image_path = Path(segment["image"])
        prompt = str(segment.get("image_prompt") or "").strip()
        if image_path.exists() and not args.overwrite_images:
            continue
        if not prompt:
            raise BuildError(f"Segment {index} needs image generation but has no image_prompt.")

        final_prompt = prompt
        if not args.skip_optimize_prompts:
            final_prompt = client.optimize_prompt(
                prompt,
                text_model=args.text_model,
                reasoning_effort=args.reasoning_effort,
            )

        result = client.generate_image(
            prompt=final_prompt,
            text_model=args.text_model,
            image_model=args.image_model,
            image_size=args.image_size,
            image_quality=args.image_quality,
            reasoning_effort=args.reasoning_effort,
        )
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(result.image_bytes)
        metadata = {
            "segment": index,
            "image": str(image_path),
            "original_prompt": prompt,
            "final_prompt": final_prompt,
            "revised_prompt": result.revised_prompt,
        }
        image_path.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if args.delay_seconds > 0:
            time.sleep(args.delay_seconds)


def write_normalized_manifest(normalized_manifest: dict[str, Any], work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / "normalized_storyboard.json"
    manifest_path.write_text(
        json.dumps(normalized_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def run_story_video_pipeline(args: argparse.Namespace, normalized_manifest_path: Path) -> None:
    script_path = Path(__file__).resolve().parent / "story_video_pipeline.py"
    command = [sys.executable, str(script_path), "--manifest", str(normalized_manifest_path)]
    if args.font_name:
        command.extend(["--font-name", args.font_name])
    if args.font_size is not None:
        command.extend(["--font-size", str(args.font_size)])
    if args.margin_v is not None:
        command.extend(["--margin-v", str(args.margin_v)])
    if args.no_burn_subtitles:
        command.append("--no-burn-subtitles")

    child_env = dict(os.environ)
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    child_env.setdefault("PYTHONUTF8", "1")

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
    )
    if completed.returncode != 0:
        raise BuildError(completed.stderr.strip() or completed.stdout.strip() or "Video pipeline failed.")
    if completed.stdout.strip():
        emit_console_text(completed.stdout.strip())


def main() -> int:
    try:
        args = parse_args()
        manifest_path = Path(args.manifest).resolve()
        if not manifest_path.exists():
            raise BuildError(f"Manifest not found: {manifest_path}")

        field_map = parse_field_map(args.field_map)
        rows, root_config = load_source_manifest(manifest_path)
        normalized_manifest = normalize_manifest(
            manifest_path=manifest_path,
            rows=rows,
            root_config=root_config,
            field_map=field_map,
            args=args,
        )
        work_dir = Path(str(normalized_manifest["work_dir"]))
        ensure_images(normalized_manifest=normalized_manifest, args=args)
        normalized_manifest_path = write_normalized_manifest(normalized_manifest, work_dir)
        run_story_video_pipeline(args=args, normalized_manifest_path=normalized_manifest_path)
        print(
            json.dumps(
                {
                    "ok": True,
                    "normalized_manifest": str(normalized_manifest_path),
                    "final_video": str(normalized_manifest["output"]),
                    "work_dir": str(normalized_manifest["work_dir"]),
                    "segment_count": len(normalized_manifest["segments"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except BuildError as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
