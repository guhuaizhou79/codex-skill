# Usage

## Environment variables

- `RESPONSES_IMAGE_ENDPOINT`
- `RESPONSES_IMAGE_API_KEY`

If these are set, the CLI scripts can omit `--endpoint` and `--api-key`.

## Probe examples

```powershell
python "$env:USERPROFILE\.codex\skills\responses-image-endpoint\scripts\probe_endpoint.py" `
  --endpoint "https://example.com/v1/responses" `
  --api-key "sk-..." `
  --text-model "gpt-5.4" `
  --image-model "gpt-image-2"
```

```powershell
$env:RESPONSES_IMAGE_ENDPOINT="https://example.com/v1/responses"
$env:RESPONSES_IMAGE_API_KEY="sk-..."
python "C:\Users\凯丰达\.codex\skills\responses-image-endpoint\scripts\probe_endpoint.py"
```

## Generation example

```powershell
python "$env:USERPROFILE\.codex\skills\responses-image-endpoint\scripts\generate_image.py" `
  --endpoint "https://example.com/v1/responses" `
  --api-key "sk-..." `
  --prompt "A long-haired white cat sitting by a wooden window in morning light, realistic photography" `
  --output-dir "D:\images\outputs"
```

## Exit codes

- `probe_endpoint.py`
  - `0`: endpoint looks image-ready
  - `1`: route, auth, network, or other blocking failure
  - `2`: text route works but image generation is unavailable or uncertain
- `generate_image.py`
  - `0`: generation succeeded
  - `1`: generation failed

## Interpreting failures

- `html_response` or `non_json_response`
  Usually the URL is wrong or points to a management page rather than `/v1/responses`.
- `auth_error`
  The key is missing, invalid, expired, or blocked by the upstream relay.
- `image_generation_error` after a successful text probe
  The endpoint can handle normal Responses calls, but `image_generation` is likely unsupported on that route or unavailable for the current account/model pool.
- `image_generation_uncertain`
  The stream payload did not match the expected SSE shape. The relay may still support image generation, but the client needs a compatibility adjustment or raw payload inspection.

## Files produced by generation

- One image file in the chosen output directory
- One metadata JSON file beside the image, containing:
  - endpoint host
  - selected text and image models
  - original prompt
  - optimized prompt
  - final prompt
  - revised prompt returned by the model, when available
