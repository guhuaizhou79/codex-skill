# Manifest Reference

## Accepted source formats

- JSON array of segment objects
- JSON object containing one of these segment keys:
  - `segments`
  - `items`
  - `scenes`
  - `rows`
  - `clips`
  - `slides`
  - `data`
- CSV with a header row

The number of segments is not fixed. The final duration is also not fixed. It can be driven by narration audio, explicit segment durations, or a mix of both.

## Root config fields

These fields are optional when the source is a JSON object:

- `title`
- `author`
- `major_class`
- `class_name`
- `professional_class`
- `opening_subtitle_text`
- `opening_subtitle_duration`
- `voice`
- `rate`
- `volume`
- `pitch`
- `boundary`
- `size`
- `fps`
- `work_dir`
- `output`

If `opening_subtitle_text` is missing, the builder derives it from `title`, `author`, and `major_class` when present.

## Normalized segment fields

Each segment is normalized to this shape before the lower-level video pipeline runs:

```json
{
  "image": "images/01.png",
  "text": "Narration or dialogue",
  "speaker": "Optional speaker name",
  "voice": "Optional edge-tts voice override",
  "rate": "Optional rate override",
  "volume": "Optional volume override",
  "pitch": "Optional pitch override",
  "duration_seconds": 4.2,
  "scene_title": "Optional label",
  "image_prompt": "Optional prompt used to generate the image"
}
```

## Built-in field aliases

The builder already recognizes these common header names:

- `image`
  - `image`
  - `image_path`
  - `file`
  - `path`
  - `filename`
  - `\u56fe\u7247`
  - `\u56fe\u7247\u8def\u5f84`
  - `\u914d\u56fe`
  - `\u56fe\u50cf`
- `text`
  - `text`
  - `script`
  - `narration`
  - `subtitle`
  - `dialogue`
  - `line`
  - `\u53f0\u8bcd`
  - `\u6587\u6848`
  - `\u5b57\u5e55`
  - `\u65c1\u767d`
  - `\u5185\u5bb9`
- `prompt`
  - `image_prompt`
  - `prompt`
  - `visual_prompt`
  - `scene_prompt`
  - `\u753b\u9762\u63d0\u793a\u8bcd`
  - `\u63d0\u793a\u8bcd`
  - `\u753b\u9762`
  - `\u914d\u56fe\u63d0\u793a\u8bcd`
- `speaker`
  - `speaker`
  - `role`
  - `character`
  - `\u53d1\u8a00\u4eba`
  - `\u89d2\u8272`
  - `\u4eba\u7269`
- `duration`
  - `duration_seconds`
  - `duration`
  - `seconds`
  - `\u65f6\u957f`
  - `\u65f6\u957f\u79d2`
  - `\u79d2\u6570`
- `voice`
  - `voice`
  - `\u914d\u97f3`
  - `\u97f3\u8272`
  - `\u58f0\u7ebf`
- `rate`
  - `rate`
  - `\u8bed\u901f`
- `volume`
  - `volume`
  - `\u97f3\u91cf`
- `pitch`
  - `pitch`
  - `\u97f3\u9ad8`
- `scene_title`
  - `scene_title`
  - `sceneTitle`
  - `title`
  - `label`
  - `\u6807\u9898`
  - `\u955c\u5934\u6807\u9898`

## Custom headers with `--field-map`

If the built-in aliases do not match the source table, map fields explicitly:

```powershell
python scripts/build_story_video.py `
  --manifest D:\project\shots.csv `
  --field-map text=narration_body `
  --field-map prompt=visual_description `
  --field-map image=asset_path `
  --field-map duration=shot_seconds `
  --field-map speaker=character_name
```

The left side of each mapping must be one of:

- `image`
- `text`
- `prompt`
- `speaker`
- `duration`
- `voice`
- `rate`
- `volume`
- `pitch`
- `scene_title`

## Example: JSON object with generated images

When `image` is omitted, the builder auto-assigns `images/01.png`, `images/02.png`, and so on next to the manifest:

```json
{
  "title": "Debate Video",
  "author": "Yang Xinqi",
  "major_class": "HS2402",
  "voice": "zh-CN-XiaoxiaoNeural",
  "segments": [
    {
      "scene_title": "Opening",
      "text": "Truth lives in the mind, or under our feet?",
      "image_prompt": "A dramatic split-screen portrait of two philosophers in debate."
    },
    {
      "scene_title": "Response",
      "speaker": "Marx",
      "text": "Practice is the source of thought and the test of truth.",
      "image_prompt": "Karl Marx speaking firmly in an industrial-era setting."
    }
  ]
}
```

## Example: CSV with existing images

```csv
shot_name,asset_path,narration,character_name,shot_seconds
opening,images/01.png,Truth lives in the mind or under our feet?,Narrator,3.2
reply,images/02.png,Practice is the source of truth.,Marx,4.1
```

Run it like this:

```powershell
python scripts/build_story_video.py `
  --manifest D:\project\story.csv `
  --skip-image-generation `
  --field-map scene_title=shot_name `
  --field-map image=asset_path `
  --field-map text=narration `
  --field-map speaker=character_name `
  --field-map duration=shot_seconds
```

## Example: localized JSON keys

The builder can also read localized keys directly. For example:

```json
[
  {
    "\u56fe\u7247": "images/01.png",
    "\u53f0\u8bcd": "Opening line",
    "\u955c\u5934\u6807\u9898": "Opening"
  },
  {
    "\u753b\u9762\u63d0\u793a\u8bcd": "A philosopher in a library",
    "\u65c1\u767d": "Second line",
    "\u65f6\u957f\u79d2": 4.0
  }
]
```

## Segment rules

- A segment must have either:
  - `text`
  - or a positive `duration` or `duration_seconds`
- A segment must have either:
  - an existing image path
  - or an `image_prompt` so the builder can generate the image
- Per-segment `voice`, `rate`, `volume`, and `pitch` override the root defaults.

## Outputs

The builder writes a working directory containing:

- `normalized_storyboard.json`
- generated images and per-image metadata JSON when image generation is used
- `audio/`
- `subtitles/`
- `output_no_subs.mp4`
- `final_video.mp4`

Use `--no-burn-subtitles` if you only want the intermediate video and external subtitle file.
