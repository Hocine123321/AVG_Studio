"""Defensive parsing of a text model's reply, shared by the AI-writing
phases.

A model's response is untrusted input: it will sometimes wrap valid JSON in
a markdown fence, add prose before/after it, or return the segmentation
block format with a piece missing. Every function here either returns a
usable value or raises a plain `ValueError` with a message a phase can wrap
directly into an `AvgError(ErrorKind.bug, ...)` - never a bare
`json.JSONDecodeError`/`KeyError`/`IndexError` a caller would have to
inspect to explain.

Ported from the legacy app's own defensive parsing
(`D:\\AI_Video_Gen\\modules\\ai.ps1`): `Invoke-CreativeBrief`'s fence-strip +
outermost-brace extraction, and `Invoke-SceneSegmentation`'s
`@@SCENE@@`/`@@IMAGE@@`/`@@LABEL@@` block splitter.
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9]*\s*(.*?)```\s*$", re.DOTALL)
_SCENE_SPLIT_RE = re.compile(r"^\s*@@SCENE@@\s*$", re.IGNORECASE | re.MULTILINE)
_IMAGE_SPLIT_RE = re.compile(r"^(.*?)^\s*@@IMAGE@@\s*$(.*)", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_LABEL_SPLIT_RE = re.compile(r"^(.*?)^\s*@@LABEL@@\s*$(.*)", re.IGNORECASE | re.MULTILINE | re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Unwrap a single ```...``` fence wrapping the WHOLE reply, if present.

    Models are told never to fence their reply and sometimes do it anyway;
    a fence around only PART of the reply (rare, and not what the legacy
    app guarded against either) is left alone."""
    match = _FENCE_RE.match(text)
    return match.group(1) if match else text


def extract_json_object(text: str) -> dict:
    """Parse the outermost `{...}` JSON object out of a model reply that may
    carry a markdown fence or leading/trailing prose around it.

    Raises `ValueError` (never a raw `json.JSONDecodeError`) if no `{`/`}`
    pair is found, the slice between them doesn't parse, or it parses to
    something other than a JSON object (e.g. a bare list or number)."""
    cleaned = strip_code_fence(text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in model response")
    candidate = cleaned[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model response's JSON was not an object")
    return parsed


def _collapse_to_one_line(text: str) -> str:
    """Join a (possibly multi-line) block back into a single spoken/prompt
    line: trim each line, drop blanks, collapse repeated internal
    whitespace - mirrors the legacy parser's identical collapse step."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return re.sub(r"\s{2,}", " ", " ".join(lines)).strip()


def parse_scene_blocks(text: str) -> list[dict[str, str]]:
    """Parse Pass 2's `@@SCENE@@` / `@@IMAGE@@` / `@@LABEL@@` block format
    into `[{"text": ..., "image_prompt": ..., "label": ...}, ...]`, in
    order.

    Never raises on malformed input - a block with no `@@IMAGE@@` marker is
    skipped (the same tolerance the legacy parser applies), a missing
    `@@LABEL@@` degrades to an empty label, and a reply with no recognisable
    blocks at all simply returns `[]`. The caller decides whether an empty
    result is fatal (it is, for Pass 2 - see `avg.pipeline.phases.script`)."""
    cleaned = strip_code_fence(text)
    chunks = _SCENE_SPLIT_RE.split(cleaned)
    scenes: list[dict[str, str]] = []
    for chunk in chunks[1:]:  # chunks[0] is any preamble before the first marker
        m_img = _IMAGE_SPLIT_RE.match(chunk)
        if not m_img:
            continue
        scene_text = _collapse_to_one_line(m_img.group(1))
        rest = m_img.group(2)

        m_lbl = _LABEL_SPLIT_RE.match(rest)
        if m_lbl:
            image_prompt = _collapse_to_one_line(m_lbl.group(1))
            label_lines = [ln.strip() for ln in m_lbl.group(2).strip().splitlines() if ln.strip()]
            label = label_lines[0] if label_lines else ""
        else:
            image_prompt = _collapse_to_one_line(rest)
            label = ""

        if not scene_text:
            continue
        scenes.append({"text": scene_text, "image_prompt": image_prompt, "label": label or "Scene"})
    return scenes
