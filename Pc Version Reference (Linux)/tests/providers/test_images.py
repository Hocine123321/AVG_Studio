"""Tests for the five image providers, against recorded fixtures wherever a
real one was obtainable, and a locally-mocked transport where it wasn't.

Every fixture-based test runs against `Transport(mode="replay", ...)`
pointed at the committed `tests/fixtures/http/` directory - no network,
ever (see `avg/providers/transport.py`'s own guarantee that replay mode
never references its `client`). Providers that require a key call
`avg.config.secrets.get_key` internally; tests patch that function inside
each provider module directly, rather than relying on the real
DPAPI-encrypted store - a fixture's HTTP match key never depends on the
literal key value anyway (headers, and any `key=`-shaped query parameter,
are excluded from/redacted in matching - see `transport.py`).

Recording notes (full detail in `.superpowers/sdd/s3-task-4-report.md`):
  - **OpenAI**: no key at all is configured in this environment. The one
    real fixture is a deliberately-wrong-key 401 (free, per the task's own
    guidance that an error shape "costs nothing"). `generate()`'s happy
    path (both the `b64_json` and `url` response shapes) uses a locally
    mocked transport.
  - **Gemini**: two real fixtures, both organic account limits, not
    engineered failures - the native `gemini-2.5-flash-image`
    (`:generateContent`) path returns a real 429 whose body says "check
    your plan and billing" (this account's free tier has 0 image-request
    quota, the same account limit Task 3 found for text generation); the
    Imagen `imagen-4.0-fast-generate-001` (`:predict`) path returns a real
    404 ("no longer available to new users"). Both are used directly as
    fixtures. `generate()`'s happy path (both routing branches) and the
    blocked-content case use a locally mocked transport.
  - **Pollinations**: BOTH real - a real 401 (an unauthenticated request;
    Pollinations now requires a key it did not at the time the legacy app
    was written) and a real, successful, keyed image generation. This is
    the only provider in this package with a fully real happy-path
    fixture.
  - **Hugging Face**: the one real fixture is a real 410
    ("deprecated and no longer supported") for the legacy code's own
    "known-served fallback" model, `black-forest-labs/FLUX.1-schnell` -
    apparently no longer true. `generate()`'s happy path uses a locally
    mocked transport.
  - **Stable Horde**: real end-to-end (submit -> poll -> download),
    but NOT at this package's production default size/steps - a real,
    organic 403 `KudosUpfront` confirmed the horde's current "heavy
    demand" mode requires upfront kudos this account (and even the
    anonymous key) doesn't have at 1024x1024/30 steps, or even at
    512x512/20 steps. The one real recording that succeeded used a
    smaller size (256x256) and step count (8) with the anonymous key -
    `test_stablehorde_generate_happy_path_real_recording` patches the
    module's `_SIZE_BY_RATIO`/`_DEFAULT_STEPS` to match so the real
    fixtures replay exactly; production defaults are untouched elsewhere.
    Because Stable Horde's poll `check` endpoint is the SAME URL on every
    poll (job id doesn't change), and `Transport`'s record mode
    overwrites a fixture file on every real call to that URL, only the
    *last* real response for that URL survives - i.e. a real recording
    can only ever capture a single-iteration "already done by the first
    check" poll loop, never a multi-poll "not done, not done, done"
    sequence. That is a structural property of the record/replay
    design (stateless request/response matching, not a numbered
    cassette), not something to work around here. The multi-iteration
    case, the timeout case, and the cancel case are therefore covered by
    a locally mocked transport with an in-process call counter instead.
"""

from __future__ import annotations

import base64

import httpx
import pytest

import avg.providers.images.community as images_community
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import POLICY
from avg.providers.images.community import (
    HuggingFaceImageProvider,
    PollinationsImageProvider,
    StableHordeImageProvider,
    _normalize_model,
)
from avg.providers.images.premium import GeminiImageProvider, OpenAIImageProvider, _size_for as openai_size_for
from avg.providers.transport import DEFAULT_FIXTURES_DIR, Transport

PROMPT_APPLE = "a small red apple on a white background, simple illustration"


def _replay_transport() -> Transport:
    """Points at the real, committed fixtures directory - never the network."""
    return Transport(mode="replay", fixtures_dir=DEFAULT_FIXTURES_DIR)


def _poison_transport(tmp_path) -> Transport:
    """A transport that blows up if it is ever asked to send anything - for
    tests proving a provider's own guard (no key, unsupported aspect
    ratio, ...) short-circuits before reaching the transport at all."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider reached the transport when it should have short-circuited")

    return Transport(
        mode="replay",
        fixtures_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _mock_record_transport(tmp_path, handler, *, sleep=lambda _seconds: None) -> Transport:
    """A record-mode transport wired to a local, in-process handler - used
    only where this task could not obtain a real recorded fixture, or
    (Stable Horde) where the real record/replay design cannot represent
    the shape being tested at all (see module docstring). Never touches
    the network; writes an ephemeral fixture into `tmp_path` that the
    test discards."""
    return Transport(
        mode="record",
        fixtures_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
    )


# ---------------------------------------------------------------------------
# OpenAI - no key configured in this environment; the one real fixture is a
# deliberately-wrong-key auth failure. generate()'s happy path is mocked.
# ---------------------------------------------------------------------------


def test_openai_generate_auth_failure_real_fixture(monkeypatch):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "sk-deliberately-invalid-test-key")
    provider = OpenAIImageProvider(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="gpt-image-1")
    assert excinfo.value.kind == ErrorKind.auth


def test_openai_generate_without_key_raises_auth_and_never_reaches_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: None)
    provider = OpenAIImageProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="gpt-image-1")
    assert excinfo.value.kind == ErrorKind.auth


def test_openai_generate_happy_path_mocked_b64(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"fake-png-bytes").decode()}]})

    provider = OpenAIImageProvider(transport=_mock_record_transport(tmp_path, handler))
    data = provider.generate(PROMPT_APPLE, aspect_ratio="9:16", model="gpt-image-1")
    assert data == b"fake-png-bytes"


def test_openai_generate_happy_path_mocked_url(monkeypatch, tmp_path):
    # Ported quirk: the API can answer with a `url` instead of inline
    # base64 - the provider must fetch it with a second request.
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if "images/generations" in str(request.url):
            return httpx.Response(200, json={"data": [{"url": "https://example.invalid/generated.png"}]})
        return httpx.Response(200, content=b"downloaded-bytes")

    provider = OpenAIImageProvider(transport=_mock_record_transport(tmp_path, handler))
    data = provider.generate(PROMPT_APPLE, aspect_ratio="16:9", model="gpt-image-1")
    assert data == b"downloaded-bytes"


def test_openai_dalle2_rejects_non_square_aspect_ratio_without_reaching_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")
    provider = OpenAIImageProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="9:16", model="dall-e-2")
    assert excinfo.value.kind == ErrorKind.bug


def test_openai_size_for_maps_each_model_family_correctly():
    assert openai_size_for("gpt-image-1", "9:16") == "1024x1536"
    assert openai_size_for("dall-e-3", "9:16") == "1024x1792"
    assert openai_size_for("dall-e-2", "1:1") == "1024x1024"


def test_openai_supports_the_three_canonical_ratios():
    provider = OpenAIImageProvider()
    assert provider.supports("1:1")
    assert provider.supports("9:16")
    assert provider.supports("16:9")
    assert not provider.supports("4:3")


# ---------------------------------------------------------------------------
# Gemini - two real fixtures (a native-model billing failure, an Imagen
# model_missing failure), both organic account limits. generate()'s happy
# path and blocked-content case are mocked.
# ---------------------------------------------------------------------------


def test_gemini_generate_native_model_billing_failure_real_fixture(monkeypatch):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")
    provider = GeminiImageProvider(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="gemini-2.5-flash-image")
    assert excinfo.value.kind == ErrorKind.billing


def test_gemini_generate_imagen_model_missing_real_fixture(monkeypatch):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")
    provider = GeminiImageProvider(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="imagen-4.0-fast-generate-001")
    assert excinfo.value.kind == ErrorKind.model_missing


def test_gemini_generate_without_key_raises_auth_and_never_reaches_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: None)
    provider = GeminiImageProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="gemini-2.5-flash-image")
    assert excinfo.value.kind == ErrorKind.auth


def test_gemini_generate_unsupported_aspect_ratio_raises_bug_without_reaching_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")
    provider = GeminiImageProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="4:3", model="gemini-2.5-flash-image")
    assert excinfo.value.kind == ErrorKind.bug


def test_gemini_generate_content_happy_path_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "gemini-3.1-flash-image:generateContent" in str(request.url)
        b64 = base64.b64encode(b"fake-inline-image-bytes").decode()
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"inlineData": {"data": b64}}]}, "finishReason": "STOP"}]},
        )

    provider = GeminiImageProvider(transport=_mock_record_transport(tmp_path, handler))
    # Also exercises the dead-preview-model remap: the shut-down preview id
    # must resolve to the GA id before the URL is ever built.
    data = provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="gemini-3.1-flash-image-preview")
    assert data == b"fake-inline-image-bytes"


def test_gemini_generate_imagen_happy_path_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert ":predict" in str(request.url)
        b64 = base64.b64encode(b"fake-predict-image-bytes").decode()
        return httpx.Response(200, json={"predictions": [{"bytesBase64Encoded": b64}]})

    provider = GeminiImageProvider(transport=_mock_record_transport(tmp_path, handler))
    data = provider.generate(PROMPT_APPLE, aspect_ratio="16:9", model="imagen-4.0-generate-001")
    assert data == b"fake-predict-image-bytes"


def test_gemini_generate_blocked_content_raises_content_filter_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [{"finishReason": "SAFETY", "content": {}}],
                "promptFeedback": {"blockReason": "SAFETY"},
            },
        )

    provider = GeminiImageProvider(transport=_mock_record_transport(tmp_path, handler))
    with pytest.raises(AvgError) as excinfo:
        provider.generate("a deliberately blocked prompt", aspect_ratio="1:1", model="gemini-2.5-flash-image")
    assert excinfo.value.kind == ErrorKind.content_filter


def test_gemini_supports_the_three_canonical_ratios():
    provider = GeminiImageProvider()
    assert provider.supports("1:1")
    assert provider.supports("9:16")
    assert provider.supports("16:9")
    assert not provider.supports("4:3")


# ---------------------------------------------------------------------------
# Pollinations - both a real success and a real auth failure.
# ---------------------------------------------------------------------------


def test_pollinations_generate_happy_path_real_fixture(monkeypatch):
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: "fixture-test-key")
    provider = PollinationsImageProvider(transport=_replay_transport())
    data = provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="flux")
    assert data, "expected real image bytes"
    assert len(data) > 1000


def test_pollinations_generate_auth_failure_real_fixture(monkeypatch):
    # A real, current API behaviour: unauthenticated image requests are now
    # rejected (Pollinations added this after the legacy PowerShell app,
    # which treated a key as optional here, was written). See module
    # docstring / the task report.
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: None)
    provider = PollinationsImageProvider(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        provider.generate("auth failure probe please ignore", aspect_ratio="1:1", model="flux")
    assert excinfo.value.kind == ErrorKind.auth


def test_pollinations_generate_unsupported_aspect_ratio_raises_bug_without_reaching_transport(tmp_path):
    provider = PollinationsImageProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="4:3", model="flux")
    assert excinfo.value.kind == ErrorKind.bug


def test_pollinations_normalize_model_replaces_foreign_ids():
    assert _normalize_model("flux") == "flux"
    assert _normalize_model("") == "flux"
    assert _normalize_model("gpt-image-1") == "flux"
    assert _normalize_model("black-forest-labs/FLUX.1-schnell") == "flux"
    assert _normalize_model("kontext") == "kontext"


def test_pollinations_supports_the_three_canonical_ratios():
    provider = PollinationsImageProvider()
    assert provider.supports("1:1")
    assert provider.supports("9:16")
    assert provider.supports("16:9")
    assert not provider.supports("4:3")


# ---------------------------------------------------------------------------
# Hugging Face - the one real fixture is a real model_missing failure.
# generate()'s happy path is mocked; it is also the one provider that
# refuses every non-square aspect ratio outright.
# ---------------------------------------------------------------------------


def test_huggingface_generate_deprecated_model_real_fixture(monkeypatch):
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: "fixture-test-key")
    provider = HuggingFaceImageProvider(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="black-forest-labs/FLUX.1-schnell")
    assert excinfo.value.kind == ErrorKind.model_missing


def test_huggingface_generate_without_key_raises_auth_and_never_reaches_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: None)
    provider = HuggingFaceImageProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="some/model")
    assert excinfo.value.kind == ErrorKind.auth


def test_huggingface_generate_non_square_aspect_ratio_refused_without_reaching_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: "fixture-test-key")
    provider = HuggingFaceImageProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.generate(PROMPT_APPLE, aspect_ratio="9:16", model="some/model")
    assert excinfo.value.kind == ErrorKind.bug


def test_huggingface_generate_happy_path_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: "fixture-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-png-bytes", headers={"content-type": "image/png"})

    provider = HuggingFaceImageProvider(transport=_mock_record_transport(tmp_path, handler))
    data = provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="some/served-model")
    assert data == b"fake-png-bytes"


def test_huggingface_generate_in_band_json_error_at_200_is_classified(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: "fixture-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "currently loading, please try again"}, headers={"content-type": "application/json"})

    provider = HuggingFaceImageProvider(transport=_mock_record_transport(tmp_path, handler))
    with pytest.raises(AvgError):
        provider.generate(PROMPT_APPLE, aspect_ratio="1:1", model="some/served-model")


def test_huggingface_supports_only_square():
    provider = HuggingFaceImageProvider()
    assert provider.supports("1:1")
    assert not provider.supports("9:16")
    assert not provider.supports("16:9")


# ---------------------------------------------------------------------------
# Stable Horde - a real end-to-end recording (at a reduced size the free
# queue would actually accept without upfront kudos - see module
# docstring), plus locally-mocked coverage for the shapes the real
# record/replay design cannot represent (multi-poll, timeout, cancel) or
# that would cost real free-queue capacity to reproduce on demand (faulted,
# censored).
# ---------------------------------------------------------------------------


def test_stablehorde_generate_happy_path_real_recording(monkeypatch):
    # See module docstring: this ONE real recording used 256x256/8 steps
    # and the anonymous key to stay under the horde's current "heavy
    # demand" kudos gate (confirmed via real, separate 403 KudosUpfront
    # probes at both the production 1024x1024/30-step default and a
    # smaller 512x512/20-step size - neither this account's stored key nor
    # the anonymous key had enough kudos for either). Patching these two
    # module constants to match is what lets the real fixtures replay
    # exactly; nothing about the production defaults themselves changes.
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: None)
    monkeypatch.setitem(images_community._SIZE_BY_RATIO, "1:1", (256, 256))
    monkeypatch.setattr(images_community, "_DEFAULT_STEPS", 8)

    provider = StableHordeImageProvider(transport=_replay_transport(), sleep=lambda _seconds: None)
    progress: list[tuple[int, int]] = []
    data = provider.generate(
        PROMPT_APPLE,
        aspect_ratio="1:1",
        model="Flux.1-Schnell fp8 (Compact)",
        on_progress=lambda w, q: progress.append((w, q)),
    )
    assert data, "expected real image bytes downloaded from the R2 CDN URL"
    assert len(data) > 100
    assert progress, "on_progress should have been called at least once"


def test_stablehorde_poll_loop_reaches_a_result_after_multiple_not_done_checks(tmp_path):
    # Real Stable Horde recordings cannot represent this shape at all (see
    # module docstring: the check endpoint is the same URL every poll, and
    # record mode overwrites in place) - this is a local, in-process
    # sequence standing in for "queued, queued, done".
    check_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/generate/async"):
            return httpx.Response(202, json={"id": "job-multi-poll"})
        if "/generate/check/" in url:
            check_calls["n"] += 1
            done = check_calls["n"] >= 3
            return httpx.Response(
                200,
                json={"done": done, "faulted": False, "wait_time": 0 if done else 6, "queue_position": 0 if done else 2},
            )
        if "/generate/status/" in url:
            b64 = base64.b64encode(b"fake-stablehorde-image-bytes").decode()
            return httpx.Response(200, json={"generations": [{"img": b64, "censored": False}]})
        raise AssertionError(f"unexpected URL {url}")

    provider = StableHordeImageProvider(transport=_mock_record_transport(tmp_path, handler), sleep=lambda _s: None)
    progress: list[tuple[int, int]] = []
    data = provider.generate(
        "prompt", aspect_ratio="1:1", model="some-model", on_progress=lambda w, q: progress.append((w, q))
    )
    assert data == b"fake-stablehorde-image-bytes"
    assert check_calls["n"] == 3, "the loop must actually have polled three times before resolving"
    assert progress == [(6, 2), (6, 2), (0, 0)]


def test_stablehorde_poll_loop_times_out_and_cancels_the_stalled_job(tmp_path):
    delete_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/generate/async"):
            return httpx.Response(202, json={"id": "job-timeout"})
        if request.method == "DELETE":
            delete_calls["n"] += 1
            return httpx.Response(200, json={})
        if "/generate/check/" in url:
            return httpx.Response(200, json={"done": False, "faulted": False, "wait_time": 30, "queue_position": 5})
        raise AssertionError(f"unexpected URL {url}")

    # A small bound so the loop MUST give up quickly - proving it cannot
    # spin forever regardless of what the (fake, always "still going") API
    # reports.
    provider = StableHordeImageProvider(
        transport=_mock_record_transport(tmp_path, handler), sleep=lambda _s: None, max_wait_seconds=10.0
    )
    with pytest.raises(AvgError) as excinfo:
        provider.generate("prompt", aspect_ratio="1:1", model="some-model")
    assert excinfo.value.kind == ErrorKind.timeout
    assert delete_calls["n"] == 1, "the stalled job must be cancelled, not abandoned"


def test_stablehorde_generate_can_be_cancelled_mid_wait(tmp_path):
    delete_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/generate/async"):
            return httpx.Response(202, json={"id": "job-cancel"})
        if request.method == "DELETE":
            delete_calls["n"] += 1
            return httpx.Response(200, json={})
        if "/generate/check/" in url:
            return httpx.Response(200, json={"done": False, "faulted": False, "wait_time": 10, "queue_position": 1})
        raise AssertionError(f"unexpected URL {url}")

    cancel_calls = {"n": 0}

    def cancel() -> bool:
        cancel_calls["n"] += 1
        return cancel_calls["n"] >= 2  # abort on the second check

    # A very large bound - proving cancellation (not the timeout budget)
    # is what ends this particular run.
    provider = StableHordeImageProvider(
        transport=_mock_record_transport(tmp_path, handler), sleep=lambda _s: None, max_wait_seconds=3600.0
    )
    with pytest.raises(AvgError) as excinfo:
        provider.generate("prompt", aspect_ratio="1:1", model="some-model", cancel=cancel)
    assert "cancelled" in str(excinfo.value)
    assert delete_calls["n"] == 1


def test_stablehorde_faulted_job_raises_bug_and_cancels(tmp_path):
    # errors.py deliberately does NOT map "faulted" to a specific kind -
    # the flag conflates "no worker available" with "a content decision"
    # with no way to tell them apart (see avg/providers/errors.py's
    # stablehorde comment) - it falls through to the safe default.
    delete_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/generate/async"):
            return httpx.Response(202, json={"id": "job-faulted"})
        if request.method == "DELETE":
            delete_calls["n"] += 1
            return httpx.Response(200, json={})
        if "/generate/check/" in url:
            return httpx.Response(200, json={"done": False, "faulted": True, "wait_time": 0, "queue_position": 0})
        raise AssertionError(f"unexpected URL {url}")

    provider = StableHordeImageProvider(transport=_mock_record_transport(tmp_path, handler), sleep=lambda _s: None)
    with pytest.raises(AvgError) as excinfo:
        provider.generate("prompt", aspect_ratio="1:1", model="some-model")
    assert excinfo.value.kind == ErrorKind.bug
    assert delete_calls["n"] == 1


def test_stablehorde_censored_result_raises_content_filter(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/generate/async"):
            return httpx.Response(202, json={"id": "job-censored"})
        if "/generate/check/" in url:
            return httpx.Response(200, json={"done": True, "faulted": False, "wait_time": 0, "queue_position": 0})
        if "/generate/status/" in url:
            return httpx.Response(200, json={"generations": [{"img": "unused", "censored": True}]})
        raise AssertionError(f"unexpected URL {url}")

    provider = StableHordeImageProvider(transport=_mock_record_transport(tmp_path, handler), sleep=lambda _s: None)
    with pytest.raises(AvgError) as excinfo:
        provider.generate("prompt", aspect_ratio="1:1", model="some-model")
    assert excinfo.value.kind == ErrorKind.content_filter


def test_stablehorde_generate_unsupported_aspect_ratio_raises_bug_without_reaching_transport(tmp_path):
    provider = StableHordeImageProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.generate("prompt", aspect_ratio="4:3", model="some-model")
    assert excinfo.value.kind == ErrorKind.bug


def test_stablehorde_supports_the_three_canonical_ratios():
    provider = StableHordeImageProvider()
    assert provider.supports("1:1")
    assert provider.supports("9:16")
    assert provider.supports("16:9")
    assert not provider.supports("4:3")


# ---------------------------------------------------------------------------
# Cross-provider: POLICY-driven fallback (no image router exists yet - this
# task's scope is the five providers themselves, see the brief - so this
# demonstrates the underlying contract callers rely on: a billing failure's
# `POLICY[...].on_exhausted` says "switch_provider", and a different
# provider's `generate()` can simply be called next).
# ---------------------------------------------------------------------------


def test_billing_failure_switches_provider_per_policy(monkeypatch):
    monkeypatch.setattr("avg.providers.images.premium.get_key", lambda _p: "fixture-test-key")
    gemini = GeminiImageProvider(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        gemini.generate(PROMPT_APPLE, aspect_ratio="1:1", model="gemini-2.5-flash-image")
    assert excinfo.value.kind == ErrorKind.billing
    assert POLICY[excinfo.value.kind].on_exhausted == "switch_provider"

    # Per POLICY, on a billing failure a caller should try a different
    # provider next - Pollinations' real, keyed success stands in for that.
    monkeypatch.setattr("avg.providers.images.community.get_key", lambda _p: "fixture-test-key")
    pollinations = PollinationsImageProvider(transport=_replay_transport())
    data = pollinations.generate(PROMPT_APPLE, aspect_ratio="1:1", model="flux")
    assert data, "the backup provider should have produced real image bytes"
