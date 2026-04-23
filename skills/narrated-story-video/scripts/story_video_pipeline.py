from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imageio_ffmpeg

os.environ.setdefault("FFMPEG_BINARY", imageio_ffmpeg.get_ffmpeg_exe())

import edge_tts
from moviepy import AudioFileClip, ImageClip, concatenate_videoclips
from PIL import Image


DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = "+0%"
DEFAULT_VOLUME = "+0%"
DEFAULT_PITCH = "+0Hz"
DEFAULT_BOUNDARY = "SentenceBoundary"
DEFAULT_SIZE = (1080, 1920)
DEFAULT_FPS = 24
DEFAULT_FONT_NAME = "Microsoft YaHei"
DEFAULT_FONT_SIZE = 9
DEFAULT_MARGIN_V = 18


class PipelineError(RuntimeError):
    """Raised when the video pipeline cannot continue."""


@dataclass(slots=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(slots=True)
class Segment:
    order: int
    image: Path
    text: str
    speaker: str | None = None
    scene_title: str | None = None
    voice: str | None = None
    rate: str | None = None
    volume: str | None = None
    pitch: str | None = None
    duration_seconds: float | None = None
    image_prompt: str | None = None


@dataclass(slots=True)
class SegmentAssets:
    segment: Segment
    audio_path: Path | None
    subtitle_path: Path | None
    cues: list[Cue]
    duration: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate narration, subtitles, and a stitched video from images plus a JSON/CSV manifest."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to the JSON or CSV manifest file.",
    )
    parser.add_argument(
        "--output",
        help="Final output video path. Defaults to <work-dir>/final_video.mp4.",
    )
    parser.add_argument(
        "--work-dir",
        help="Directory for generated audio, subtitles, and intermediate video files.",
    )
    parser.add_argument(
        "--voice",
        help=f"edge-tts voice name. Defaults to {DEFAULT_VOICE}.",
    )
    parser.add_argument(
        "--rate",
        help=(
            "edge-tts rate, such as +10%% or -5%%. "
            f"Defaults to {DEFAULT_RATE.replace('%', '%%')}."
        ),
    )
    parser.add_argument(
        "--volume",
        help=(
            "edge-tts volume, such as +0%%. "
            f"Defaults to {DEFAULT_VOLUME.replace('%', '%%')}."
        ),
    )
    parser.add_argument(
        "--pitch",
        help=f"edge-tts pitch, such as +0Hz. Defaults to {DEFAULT_PITCH}.",
    )
    parser.add_argument(
        "--boundary",
        choices=("SentenceBoundary", "WordBoundary"),
        default=None,
        help=f"Subtitle timing granularity. Defaults to {DEFAULT_BOUNDARY}.",
    )
    parser.add_argument(
        "--size",
        help="Video size in WIDTHxHEIGHT format, for example 1080x1920.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        help=f"Video frames per second. Defaults to {DEFAULT_FPS}.",
    )
    parser.add_argument(
        "--font-name",
        default=DEFAULT_FONT_NAME,
        help=f"Subtitle font name used by ffmpeg. Defaults to {DEFAULT_FONT_NAME}.",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=DEFAULT_FONT_SIZE,
        help=f"Subtitle font size used by ffmpeg. Defaults to {DEFAULT_FONT_SIZE}.",
    )
    parser.add_argument(
        "--margin-v",
        type=int,
        default=DEFAULT_MARGIN_V,
        help=f"Bottom subtitle margin in pixels. Defaults to {DEFAULT_MARGIN_V}.",
    )
    parser.add_argument(
        "--no-burn-subtitles",
        action="store_true",
        help="Skip hard-subtitle burn-in and only keep the intermediate no-subtitle video plus SRT.",
    )
    return parser.parse_args()


def parse_size(raw_size: str | None) -> tuple[int, int] | None:
    if not raw_size:
        return None
    normalised = raw_size.lower().replace(" ", "")
    if "x" not in normalised:
        raise PipelineError(f"Invalid size format: {raw_size}. Expected WIDTHxHEIGHT.")
    width_raw, height_raw = normalised.split("x", 1)
    try:
        width = int(width_raw)
        height = int(height_raw)
    except ValueError as exc:
        raise PipelineError(f"Invalid size value: {raw_size}.") from exc
    if width <= 0 or height <= 0:
        raise PipelineError(f"Size must be positive: {raw_size}.")
    return width, height


def parse_size_from_config(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return parse_size(value)
    if isinstance(value, dict):
        width = value.get("width")
        height = value.get("height")
        if width is None or height is None:
            raise PipelineError("Config size object must include width and height.")
        return int(width), int(height)
    raise PipelineError("Unsupported size format in manifest config.")


def resolve_manifest_path(raw_path: str, manifest_path: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (manifest_path.parent / candidate).resolve()


def resolve_optional_path(raw_path: str | None, manifest_path: Path) -> Path | None:
    if raw_path in (None, ""):
        return None
    candidate = Path(str(raw_path))
    if candidate.is_absolute():
        return candidate.resolve()
    return (manifest_path.parent / candidate).resolve()


def pick_first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def normalise_segment(
    payload: dict[str, Any],
    order: int,
    manifest_path: Path,
    require_existing_images: bool = True,
) -> Segment:
    if not isinstance(payload, dict):
        raise PipelineError(f"Segment {order} must be an object.")
    image_value = pick_first(payload, "image", "image_path", "file", "path")
    text_value = pick_first(payload, "text", "script", "narration", "subtitle")
    image_prompt = pick_first(payload, "image_prompt", "prompt", "imagePrompt")
    speaker = pick_first(payload, "speaker", "role", "character")
    scene_title = pick_first(payload, "scene_title", "sceneTitle", "title", "label")
    voice = pick_first(payload, "voice")
    rate = pick_first(payload, "rate")
    volume = pick_first(payload, "volume")
    pitch = pick_first(payload, "pitch")
    duration_seconds = pick_first(payload, "duration_seconds", "duration", "seconds")
    if not image_value:
        raise PipelineError(f"Segment {order} is missing an image field.")
    has_text = bool(text_value and str(text_value).strip())
    has_duration = duration_seconds not in (None, "")
    if not has_text and not has_duration:
        raise PipelineError(f"Segment {order} is missing narration text.")
    image_path = resolve_manifest_path(str(image_value), manifest_path)
    if require_existing_images and not image_path.exists():
        raise PipelineError(f"Segment {order} image not found: {image_path}")
    parsed_duration: float | None = None
    if has_duration:
        try:
            parsed_duration = float(duration_seconds)
        except (TypeError, ValueError) as exc:
            raise PipelineError(f"Segment {order} has an invalid duration_seconds value.") from exc
        if parsed_duration <= 0:
            raise PipelineError(f"Segment {order} duration_seconds must be positive.")
    return Segment(
        order=order,
        image=image_path,
        text=str(text_value).strip() if has_text else "",
        speaker=str(speaker).strip() if speaker else None,
        scene_title=str(scene_title).strip() if scene_title else None,
        voice=str(voice).strip() if voice else None,
        rate=str(rate).strip() if rate else None,
        volume=str(volume).strip() if volume else None,
        pitch=str(pitch).strip() if pitch else None,
        duration_seconds=parsed_duration,
        image_prompt=str(image_prompt).strip() if image_prompt else None,
    )


def load_json_manifest(
    manifest_path: Path,
    require_existing_images: bool = True,
) -> tuple[list[Segment], dict[str, Any]]:
    raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(raw_data, list):
        items = raw_data
        config: dict[str, Any] = {}
    elif isinstance(raw_data, dict):
        items = raw_data.get("segments") or raw_data.get("items") or raw_data.get("scenes")
        if not isinstance(items, list):
            raise PipelineError("JSON manifest must contain a segments/items/scenes list.")
        config = raw_data
    else:
        raise PipelineError("JSON manifest must be a list or an object.")
    segments = [
        normalise_segment(
            item,
            index,
            manifest_path,
            require_existing_images=require_existing_images,
        )
        for index, item in enumerate(items, start=1)
    ]
    return segments, config


def load_csv_manifest(
    manifest_path: Path,
    require_existing_images: bool = True,
) -> tuple[list[Segment], dict[str, Any]]:
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise PipelineError("CSV manifest is empty.")
    segments = [
        normalise_segment(
            row,
            index,
            manifest_path,
            require_existing_images=require_existing_images,
        )
        for index, row in enumerate(rows, start=1)
    ]
    return segments, {}


def load_manifest(
    manifest_path: Path,
    require_existing_images: bool = True,
) -> tuple[list[Segment], dict[str, Any]]:
    suffix = manifest_path.suffix.lower()
    if suffix == ".json":
        return load_json_manifest(
            manifest_path,
            require_existing_images=require_existing_images,
        )
    if suffix == ".csv":
        return load_csv_manifest(
            manifest_path,
            require_existing_images=require_existing_images,
        )
    raise PipelineError("Manifest must be a .json or .csv file.")


def format_srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def wrap_subtitle_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    if "\n" in cleaned:
        return "\n".join(part.strip() for part in cleaned.splitlines() if part.strip())
    if len(cleaned) <= 18:
        return cleaned

    target = len(cleaned) // 2
    best_index = target
    best_score = float("inf")
    punctuation = "，。！？；：,.!?;:"

    for index in range(1, len(cleaned)):
        score = abs(index - target)
        previous_char = cleaned[index - 1]
        if previous_char in punctuation:
            score -= 2.5
        if cleaned[index] in punctuation:
            score += 1.5
        if previous_char in "“\"'":
            score += 1
        if score < best_score:
            best_score = score
            best_index = index

    first_line = cleaned[:best_index].rstrip()
    second_line = cleaned[best_index:].lstrip()
    if not first_line or not second_line:
        return cleaned
    return f"{first_line}\n{second_line}"


def cues_to_srt(cues: list[Cue]) -> str:
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        end = cue.end if cue.end > cue.start else cue.start + 0.1
        lines.extend(
            [
                str(index),
                f"{format_srt_timestamp(cue.start)} --> {format_srt_timestamp(end)}",
                wrap_subtitle_text(cue.text),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def shift_cues(cues: list[Cue], offset_seconds: float) -> list[Cue]:
    return [
        Cue(
            start=cue.start + offset_seconds,
            end=cue.end + offset_seconds,
            text=cue.text,
        )
        for cue in cues
    ]


async def synthesize_segment(
    segment: Segment,
    audio_path: Path,
    subtitle_path: Path,
    default_voice: str,
    default_rate: str,
    default_volume: str,
    default_pitch: str,
    boundary: str,
) -> SegmentAssets:
    if not segment.text.strip():
        if segment.duration_seconds is None:
            raise PipelineError(
                f"Segment {segment.order} has no text and no duration_seconds."
            )
        return SegmentAssets(
            segment=segment,
            audio_path=None,
            subtitle_path=None,
            cues=[],
            duration=float(segment.duration_seconds),
        )

    voice = segment.voice or default_voice
    rate = segment.rate or default_rate
    volume = segment.volume or default_volume
    pitch = segment.pitch or default_pitch
    communicate = edge_tts.Communicate(
        segment.text,
        voice=voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
        boundary=boundary,
    )

    cues: list[Cue] = []
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)

    with audio_path.open("wb") as audio_file:
        async for chunk in communicate.stream():
            chunk_type = chunk["type"]
            if chunk_type == "audio":
                audio_file.write(chunk["data"])
                continue
            if chunk_type != boundary:
                continue
            text = str(chunk.get("text", "")).strip()
            if not text:
                continue
            start = float(chunk["offset"]) / 10_000_000
            end = float(chunk["offset"] + chunk["duration"]) / 10_000_000
            cues.append(Cue(start=start, end=end, text=text))

    if not cues:
        raise PipelineError(
            f"edge-tts did not return subtitle timing events for segment {segment.order}."
        )

    if segment.speaker:
        first_cue = cues[0]
        cues[0] = Cue(
            start=first_cue.start,
            end=first_cue.end,
            text=f"{segment.speaker}：{first_cue.text}",
        )

    subtitle_path.write_text(cues_to_srt(cues), encoding="utf-8")

    with AudioFileClip(str(audio_path)) as audio_clip:
        duration = float(audio_clip.duration or 0)

    if duration <= 0:
        raise PipelineError(f"Generated audio is empty for segment {segment.order}.")

    return SegmentAssets(
        segment=segment,
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        cues=cues,
        duration=duration,
    )


async def build_tts_assets(
    segments: list[Segment],
    work_dir: Path,
    voice: str,
    rate: str,
    volume: str,
    pitch: str,
    boundary: str,
) -> list[SegmentAssets]:
    assets: list[SegmentAssets] = []
    for segment in segments:
        stem = f"{segment.order:02d}"
        active_voice = segment.voice or voice
        label = segment.scene_title or segment.speaker or f"segment {stem}"
        print(f"[TTS] Generating audio and subtitles for {stem} ({label}) with {active_voice}...")
        assets.append(
            await synthesize_segment(
                segment=segment,
                audio_path=work_dir / "audio" / f"{stem}.mp3",
                subtitle_path=work_dir / "subtitles" / f"{stem}.srt",
                default_voice=voice,
                default_rate=rate,
                default_volume=volume,
                default_pitch=pitch,
                boundary=boundary,
            )
        )
    return assets


def create_cover_image_clip(
    image_path: Path,
    duration: float,
    size: tuple[int, int],
) -> ImageClip:
    target_width, target_height = size
    with Image.open(image_path) as image:
        source_width, source_height = image.size
    scale = max(target_width / source_width, target_height / source_height)
    scaled_width = max(target_width, int(round(source_width * scale)))
    scaled_height = max(target_height, int(round(source_height * scale)))
    return (
        ImageClip(str(image_path), duration=duration)
        .resized((scaled_width, scaled_height))
        .cropped(
            x_center=scaled_width / 2,
            y_center=scaled_height / 2,
            width=target_width,
            height=target_height,
        )
        .with_duration(duration)
    )


def assemble_video(
    segment_assets: list[SegmentAssets],
    output_path: Path,
    size: tuple[int, int],
    fps: int,
) -> float:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clips: list[ImageClip] = []
    audio_clips: list[AudioFileClip] = []
    final_clip = None
    try:
        for assets in segment_assets:
            duration = max(assets.duration, 0.1)
            image_clip = create_cover_image_clip(
                image_path=assets.segment.image,
                duration=duration,
                size=size,
            )
            if assets.audio_path is not None:
                audio_clip = AudioFileClip(str(assets.audio_path))
                audio_clips.append(audio_clip)
                image_clip = image_clip.with_audio(audio_clip)
            clips.append(image_clip)
        if not clips:
            raise PipelineError("No clips were created. The manifest may be empty.")
        final_clip = concatenate_videoclips(clips, method="compose")
        print(f"[Video] Writing intermediate video: {output_path}")
        final_clip.write_videofile(
            str(output_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            logger="bar",
        )
        return float(final_clip.duration or 0)
    finally:
        if final_clip is not None:
            final_clip.close()
        for clip in clips:
            clip.close()
        for audio_clip in audio_clips:
            audio_clip.close()


def build_combined_subtitles(
    segment_assets: list[SegmentAssets],
    combined_subtitle_path: Path,
    opening_subtitle_text: str | None = None,
    opening_subtitle_duration: float | None = None,
) -> tuple[Path, float]:
    combined_subtitle_path.parent.mkdir(parents=True, exist_ok=True)
    combined_cues: list[Cue] = []
    if opening_subtitle_text:
        intro_duration = max(float(opening_subtitle_duration or 3.0), 0.5)
        combined_cues.append(
            Cue(
                start=0.0,
                end=intro_duration,
                text=opening_subtitle_text,
            )
        )
    offset_seconds = 0.0
    for assets in segment_assets:
        combined_cues.extend(shift_cues(assets.cues, offset_seconds))
        offset_seconds += assets.duration
    combined_subtitle_path.write_text(cues_to_srt(combined_cues), encoding="utf-8")
    return combined_subtitle_path, offset_seconds


def subtitle_filter(font_name: str, font_size: int, margin_v: int) -> str:
    style = (
        f"FontName={font_name},"
        f"FontSize={font_size},"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=1.0,"
        "Shadow=0,"
        "Alignment=2,"
        "WrapStyle=2,"
        "MarginL=120,"
        "MarginR=120,"
        f"MarginV={margin_v}"
    )
    return f"subtitles=filename=combined_subtitles.srt:charenc=UTF-8:force_style='{style}'"


def burn_subtitles(
    input_video_path: Path,
    subtitle_path: Path,
    output_video_path: Path,
    font_name: str,
    font_size: int,
    margin_v: int,
) -> None:
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_video_path),
        "-vf",
        subtitle_filter(font_name=font_name, font_size=font_size, margin_v=margin_v),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "copy",
        str(output_video_path),
    ]
    try:
        subprocess.run(
            command,
            cwd=str(subtitle_path.parent),
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        raise PipelineError(
            "Subtitle burn-in failed. "
            f"Intermediate video is still available at {input_video_path}. "
            f"ffmpeg output: {stderr}"
        ) from exc


def choose_setting(
    cli_value: Any,
    config_value: Any,
    default_value: Any,
) -> Any:
    if cli_value not in (None, ""):
        return cli_value
    if config_value not in (None, ""):
        return config_value
    return default_value


def print_duration_summary(total_duration: float, segment_count: int) -> None:
    minutes = int(total_duration // 60)
    seconds = total_duration % 60
    print(
        f"[Summary] {segment_count} segments, estimated final duration "
        f"{minutes:02d}:{seconds:05.2f}."
    )
    if segment_count == 12 and not (110 <= total_duration <= 130):
        print(
            "[Summary] 12 segments are currently outside the 2-minute target. "
            "You may want to lengthen or shorten the narration text."
        )


def main() -> int:
    try:
        args = parse_args()
        manifest_path = Path(args.manifest).resolve()
        if not manifest_path.exists():
            raise PipelineError(f"Manifest file not found: {manifest_path}")

        segments, config = load_manifest(manifest_path)
        if not segments:
            raise PipelineError("Manifest does not contain any segments.")

        size = choose_setting(
            parse_size(args.size),
            parse_size_from_config(config.get("size")),
            DEFAULT_SIZE,
        )
        fps = int(choose_setting(args.fps, config.get("fps"), DEFAULT_FPS))
        voice = str(choose_setting(args.voice, config.get("voice"), DEFAULT_VOICE))
        rate = str(choose_setting(args.rate, config.get("rate"), DEFAULT_RATE))
        volume = str(choose_setting(args.volume, config.get("volume"), DEFAULT_VOLUME))
        pitch = str(choose_setting(args.pitch, config.get("pitch"), DEFAULT_PITCH))
        boundary = str(
            choose_setting(args.boundary, config.get("boundary"), DEFAULT_BOUNDARY)
        )

        config_work_dir = resolve_optional_path(config.get("work_dir"), manifest_path)
        cli_work_dir = Path(args.work_dir).resolve() if args.work_dir else None
        work_dir = Path(
            choose_setting(
                cli_work_dir,
                config_work_dir,
                manifest_path.parent / "build" / "story_video",
            )
        ).resolve()

        config_output = resolve_optional_path(config.get("output"), manifest_path)
        cli_output = Path(args.output).resolve() if args.output else None
        output_path = Path(
            choose_setting(
                cli_output,
                config_output,
                work_dir / "final_video.mp4",
            )
        ).resolve()

        print(f"[Manifest] Loaded {len(segments)} segments from {manifest_path}")
        print(f"[Config] Voice={voice} Rate={rate} Volume={volume} Size={size[0]}x{size[1]} FPS={fps}")
        print(f"[Config] Working directory: {work_dir}")

        segment_assets = asyncio.run(
            build_tts_assets(
                segments=segments,
                work_dir=work_dir,
                voice=voice,
                rate=rate,
                volume=volume,
                pitch=pitch,
                boundary=boundary,
            )
        )

        opening_subtitle_text = str(config.get("opening_subtitle_text") or "").strip()
        opening_subtitle_duration = config.get("opening_subtitle_duration")
        if opening_subtitle_text and opening_subtitle_duration in (None, ""):
            first_segment = segment_assets[0] if segment_assets else None
            opening_subtitle_duration = first_segment.duration if first_segment else 3.0

        combined_subtitle_path, total_duration = build_combined_subtitles(
            segment_assets=segment_assets,
            combined_subtitle_path=work_dir / "subtitles" / "combined_subtitles.srt",
            opening_subtitle_text=opening_subtitle_text or None,
            opening_subtitle_duration=opening_subtitle_duration,
        )
        print_duration_summary(total_duration=total_duration, segment_count=len(segments))

        no_subtitle_video_path = work_dir / "output_no_subs.mp4"
        assemble_video(
            segment_assets=segment_assets,
            output_path=no_subtitle_video_path,
            size=size,
            fps=fps,
        )

        if args.no_burn_subtitles:
            shutil.copy2(no_subtitle_video_path, output_path)
            print(f"[Output] Subtitle burn-in skipped. Video saved to {output_path}")
            print(f"[Output] External subtitles saved to {combined_subtitle_path}")
            return 0

        print(f"[Video] Burning subtitles into final video: {output_path}")
        burn_subtitles(
            input_video_path=no_subtitle_video_path,
            subtitle_path=combined_subtitle_path,
            output_video_path=output_path,
            font_name=args.font_name,
            font_size=args.font_size,
            margin_v=args.margin_v,
        )
        print(f"[Output] Final video saved to {output_path}")
        print(f"[Output] Combined subtitles saved to {combined_subtitle_path}")
        return 0
    except PipelineError as exc:
        print(f"[Error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
