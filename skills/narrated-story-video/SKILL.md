---
name: narrated-story-video
description: Use when the user needs a reusable pipeline that turns a JSON or CSV storyboard into generated images, narration, subtitles, and a final video, especially when scene count, duration, or source headers are not fixed.
---

# Narrated Story Video

## Overview

This skill packages a flexible storyboard-to-video workflow. It can normalize JSON or CSV manifests with mixed English and localized headers, optionally generate missing images through a Responses-compatible endpoint, then create `edge-tts` narration, line-wrapped subtitles, and a stitched MP4.

## When to use

- The user wants to turn a script, storyboard, shot list, or dialogue table into a narrated video.
- The number of images or scenes is variable.
- The target duration is approximate or driven by narration length rather than a fixed template.
- The source file uses non-standard headers and may mix English headers with localized column names.
- Some scenes already have images while others still need prompt-based generation.

## Workflow

1. Prepare a JSON or CSV manifest. If the schema is irregular, read `references/manifest.md` first.
2. Run `scripts/build_story_video.py` as the main entry point.
3. If images are missing, provide a Responses-compatible endpoint and API key. If images already exist, pass `--skip-image-generation`.
4. Let the builder normalize the manifest, write `normalized_storyboard.json`, then call `scripts/story_video_pipeline.py` to generate audio, subtitles, and the final video.
5. Return the final video path, the normalized manifest path, and any generated image metadata paths when relevant.

## Entry Points

- `scripts/build_story_video.py`
  Main entry point for flexible JSON or CSV manifests. Handles field mapping, optional prompt optimization, optional image generation, and final video assembly.
- `scripts/story_video_pipeline.py`
  Lower-level pipeline for already-normalized manifests.
- `scripts/responses_image_api.py`
  Shared Responses API client used for prompt optimization and image generation.

## Runtime Notes

- Python packages required by the pipeline:
  - `edge-tts`
  - `moviepy`
  - `imageio-ffmpeg`
  - `Pillow`
- Image generation is optional. If every referenced image already exists, the skill can run fully offline except for `edge-tts`.
- Do not store API keys in files unless the user explicitly asks.

## Image Generation Inputs

- Preferred environment variables:
  - `RESPONSES_IMAGE_ENDPOINT`
  - `RESPONSES_IMAGE_API_KEY`
- Builder fallback names:
  - `OPENAI_RESPONSES_ENDPOINT`
  - `OPENAI_API_KEY`
- Equivalent CLI flags:
  - `--endpoint`
  - `--api-key`

## Defaults

- Voice: `zh-CN-XiaoxiaoNeural`
- Boundary: `SentenceBoundary`
- Output size: `1080x1920`
- FPS: `24`
- If a segment omits `image`, the builder auto-assigns `images/01.png`, `images/02.png`, and so on next to the manifest.
- If a segment omits `text`, it must provide `duration` or `duration_seconds`.
- If `opening_subtitle_text` is absent, the builder derives it from root `title`, `author`, and `major_class` when available.

## Examples

```powershell
python scripts/build_story_video.py --manifest D:\project\storyboard.json
```

```powershell
python scripts/build_story_video.py `
  --manifest D:\project\shots.csv `
  --skip-image-generation `
  --field-map text=narration_body `
  --field-map prompt=visual_prompt `
  --field-map image=asset_path
```

```powershell
$env:RESPONSES_IMAGE_ENDPOINT="https://example.com/v1/responses"
$env:RESPONSES_IMAGE_API_KEY="sk-..."
python scripts/build_story_video.py --manifest D:\project\storyboard.csv
```

## Reference

- Read `references/manifest.md` for accepted schemas, alias rules, root config fields, localized-header examples, and `--field-map` usage.
