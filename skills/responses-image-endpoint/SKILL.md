---
name: responses-image-endpoint
description: Use when the user wants to generate images through a user-provided OpenAI-compatible Responses endpoint and API key, especially when the endpoint must be probed first to confirm route validity, authentication, or image_generation compatibility.
---

# Responses Image Endpoint

## Overview

This skill probes and uses a user-provided Responses API endpoint to optimize prompts and generate images without a GUI. It is intended for relay or proxy endpoints where compatibility is uncertain and the safest workflow is to verify the route before trying a real image job.

## When to use

- The user gives a custom `url` and `api key` for an OpenAI-compatible endpoint.
- The user wants to know whether the endpoint really supports `image_generation`.
- The user wants a prompt-optimization plus image-generation workflow similar to a local desktop tool, but as a reusable Codex skill.
- The user wants a concrete reason when generation fails, such as bad route, auth problem, HTML response, or likely unsupported image capability.

## When not to use

- The user just wants normal built-in image generation. In that case use the built-in `imagegen` skill.
- The user has not provided an endpoint and does not want to use an external API.
- The task is about editing local images through the built-in Codex image workflow rather than testing a custom Responses API.

## Workflow

1. Ask for the endpoint URL and API key if they are missing. Do not store secrets in files unless the user explicitly asks.
2. Run `scripts/probe_endpoint.py` first for any new or changed endpoint.
3. Read the JSON probe result before making claims about compatibility.
4. Only run `scripts/generate_image.py` after the probe shows the route and auth are healthy enough to continue.
5. Return the final image path, the final prompt actually sent for image generation, and any compatibility caveat discovered during probing or generation.

## Probe-first rules

- Never claim an endpoint is usable without running the probe, unless the user already provided a fresh successful probe result.
- If the endpoint returns HTML or other non-JSON content, treat it as a route problem first. It often means the URL points to a console page, login page, or wrong path instead of `/v1/responses`.
- If the text probe succeeds but the image probe fails, explain that auth and base routing are probably fine and the failure is more likely inside `image_generation`, the proxy, or the upstream account/model pool.
- If the image stage fails with a stream-format error after a successful text probe, report that compatibility is uncertain. The endpoint may support image generation but emit a non-standard SSE payload.

## Scripts

- `scripts/probe_endpoint.py`
  Verifies the endpoint, auth, and optional image-generation capability. Prints structured JSON and exits non-zero when the endpoint is not image-ready.
- `scripts/generate_image.py`
  Optionally optimizes a prompt, generates an image, saves the image plus metadata, and prints a JSON summary.
- `scripts/responses_image_api.py`
  Shared client used by both entrypoints. It contains the Responses request logic, SSE parsing, and compatibility heuristics.

## Defaults

- Environment variables:
  - `RESPONSES_IMAGE_ENDPOINT`
  - `RESPONSES_IMAGE_API_KEY`
- Default text model: `gpt-5.4`
- Default image model: `gpt-image-2`
- Default probe prompt: a small cat portrait request that is cheap and easy to inspect

## References

- Read `references/usage.md` for CLI examples, expected exit codes, environment variables, and error interpretation.
