"""Tests for the four text backends, against recorded fixtures wherever a
real one was obtainable, and a locally-mocked transport where it wasn't.

Every fixture-based test runs against `Transport(mode="replay", ...)`
pointed at the committed `tests/fixtures/http/` directory - no network,
ever (see `avg/providers/transport.py`'s own guarantee that replay mode
never references its `client`). Backends that require a key call
`avg.config.secrets.get_key` internally; tests patch that function inside
each backend module directly (`monkeypatch.setattr("avg.providers.text.
<name>.get_key", ...)`) rather than relying on the real DPAPI-encrypted
store, so these tests are portable to any machine/CI - a fixture's HTTP
match key never depends on the literal key value anyway (headers are
excluded from matching, see transport.py).

Recording notes (full detail in `.superpowers/sdd/s3-task-3-report.md`):
  - Groq: every fixture here is real (happy path, a real 401 from a
    deliberately wrong key, live model list). The rate-limit (429) fixture
    is the one deliberately hand-written one in the whole suite, per the
    task's constraint not to burn real quota to capture it.
  - Gemini: the model-list fixture is real; `generate()`'s happy/blocked
    paths use a locally mocked transport because the available key's
    account has a real, hard 0-request free-tier quota (a genuine 429/
    billing response was hit organically while recording, confirming this
    is an account limit, not a transient blip) - see the Concerns section
    of the report.
  - HuggingFace: the model-list fixture is real, and so is the
    `model_not_supported` failure (captured from two different real,
    presently-unserved model ids). No working chat model could be reached
    on this account within this task's "one call per shape" budget (its
    enabled inference providers appear to serve none of the catalogue's
    listed models) - `generate()`'s happy path uses a locally mocked
    transport instead. Note the real failure's wording ("not supported by
    ANY provider you have enabled") does not match Task 1's mined
    model_missing pattern ("not supported by provider") and instead falls
    through to the safe default, `ErrorKind.bug` - flagged as a finding,
    not fixed here (errors.py is outside this task's file list).
  - Pollinations: the model-list fixture is real (keyless-capable, though
    this particular recording happened to run with a key present in
    headers - irrelevant to fixture matching). `generate()`'s happy path
    uses a locally mocked transport: the recording account's Pollinations
    "pollen" budget is 0 for both its own key AND anonymous access (a
    real 402 was hit for both), so no live success was obtainable.
"""

from __future__ import annotations

import json

import httpx
import pytest

from avg.core.errors import AvgError, ErrorKind
from avg.providers import models as models_module
from avg.providers.text.gemini import GeminiBackend
from avg.providers.text.community import GroqBackend, HuggingFaceBackend, PollinationsBackend
from avg.providers.transport import DEFAULT_FIXTURES_DIR, Transport


def _replay_transport() -> Transport:
    """Points at the real, committed fixtures directory - never the network."""
    return Transport(mode="replay", fixtures_dir=DEFAULT_FIXTURES_DIR)


def _poison_transport(tmp_path) -> Transport:
    """A transport that blows up if it is ever asked to send anything -
    for tests proving a backend's own guard (e.g. "no key") short-circuits
    before reaching the transport at all."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("backend reached the transport when it should have short-circuited")

    return Transport(
        mode="replay",
        fixtures_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _mock_record_transport(tmp_path, handler) -> Transport:
    """A record-mode transport wired to a local, in-process handler - used
    only where this task could not obtain a real recorded fixture (see the
    module docstring). Never touches the network; writes an ephemeral
    fixture into `tmp_path` that the test discards."""
    return Transport(
        mode="record",
        fixtures_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )


PROMPT_OK = "Reply with the single word: ok"


# ---------------------------------------------------------------------------
# Groq - real fixtures throughout.
# ---------------------------------------------------------------------------


def test_groq_generate_happy_path(monkeypatch):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: "fixture-test-key")
    backend = GroqBackend(transport=_replay_transport())
    text = backend.generate(PROMPT_OK, "llama-3.3-70b-versatile", max_tokens=8, temperature=0.7)
    assert text == "ok"


def test_groq_generate_auth_failure_raises_auth(monkeypatch):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: "fixture-test-key")
    backend = GroqBackend(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        backend.generate(
            "auth failure probe - please ignore", "llama-3.3-70b-versatile", max_tokens=8, temperature=0.7
        )
    assert excinfo.value.kind == ErrorKind.auth


def test_groq_generate_rate_limited_raises_rate_limit(monkeypatch):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: "fixture-test-key")
    backend = GroqBackend(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        backend.generate(
            "router fallback test: primary should fail",
            "llama-3.3-70b-versatile",
            max_tokens=8,
            temperature=0.7,
        )
    assert excinfo.value.kind == ErrorKind.rate_limit


def test_groq_generate_without_key_raises_auth_and_never_reaches_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: None)
    backend = GroqBackend(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        backend.generate(PROMPT_OK, "llama-3.3-70b-versatile")
    assert excinfo.value.kind == ErrorKind.auth


def test_groq_list_models_happy_path(monkeypatch):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: "fixture-test-key")
    backend = GroqBackend(transport=_replay_transport())
    models = backend.list_models()
    assert models, "expected a non-empty live model list"
    assert all(not any(bad in m for bad in ("whisper", "guard")) for m in models)


def test_groq_list_models_falls_back_to_cache_when_api_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: "fixture-test-key")
    cache_path = tmp_path / "model_cache.json"
    cache_path.write_text(json.dumps({"groq": ["cached-llama-a", "cached-llama-b"]}), encoding="utf-8")
    empty_fixtures = tmp_path / "empty_fixtures"
    empty_fixtures.mkdir()

    backend = GroqBackend(
        transport=Transport(mode="replay", fixtures_dir=empty_fixtures),
        cache_path=cache_path,
    )
    assert backend.list_models() == ["cached-llama-a", "cached-llama-b"]


# ---------------------------------------------------------------------------
# Gemini - list_models is a real fixture; generate() is mocked (see module
# docstring: the recording account's free tier has zero request quota).
# ---------------------------------------------------------------------------


def test_gemini_list_models_happy_path(monkeypatch):
    monkeypatch.setattr("avg.providers.text.gemini.get_key", lambda _p: "fixture-test-key")
    backend = GeminiBackend(transport=_replay_transport())
    models = backend.list_models()
    assert models, "expected a non-empty live model list"
    assert all(not any(bad in m.lower() for bad in ("image", "embedding", "vision")) for m in models)


def test_gemini_generate_happy_path_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.text.gemini.get_key", lambda _p: "fixture-test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]},
        )

    backend = GeminiBackend(transport=_mock_record_transport(tmp_path, handler))
    text = backend.generate(PROMPT_OK, "gemini-2.0-flash", max_tokens=8, temperature=0.7)
    assert text == "ok"


def test_gemini_generate_blocked_content_raises_content_filter(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.text.gemini.get_key", lambda _p: "fixture-test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [{"finishReason": "SAFETY", "content": {}}],
                "promptFeedback": {"blockReason": "SAFETY"},
            },
        )

    backend = GeminiBackend(transport=_mock_record_transport(tmp_path, handler))
    with pytest.raises(AvgError) as excinfo:
        backend.generate("a deliberately blocked prompt", "gemini-2.0-flash", max_tokens=8, temperature=0.7)
    assert excinfo.value.kind == ErrorKind.content_filter


def test_gemini_generate_without_key_raises_auth_and_never_reaches_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.text.gemini.get_key", lambda _p: None)
    backend = GeminiBackend(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        backend.generate(PROMPT_OK, "gemini-2.0-flash")
    assert excinfo.value.kind == ErrorKind.auth


# ---------------------------------------------------------------------------
# HuggingFace - list_models and the model_not_supported failure are real
# fixtures; generate()'s happy path is mocked (see module docstring: no
# model reachable on this account within the recording budget).
# ---------------------------------------------------------------------------


def test_huggingface_list_models_happy_path():
    backend = HuggingFaceBackend(transport=_replay_transport())
    models = backend.list_models()
    assert models, "expected a non-empty live model list"


def test_huggingface_generate_unsupported_model_raises_bug(monkeypatch):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: "fixture-test-key")
    backend = HuggingFaceBackend(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        backend.generate(PROMPT_OK, "meta-llama/Meta-Llama-3-8B-Instruct", max_tokens=8, temperature=0.7)
    # Real HF wording ("not supported by ANY provider you have enabled")
    # does not match Task 1's mined model_missing pattern ("not supported
    # by provider") - see this module's docstring and the report's
    # Concerns section. It falls through to the safe default rather than
    # mis-classifying.
    assert excinfo.value.kind == ErrorKind.bug


def test_huggingface_generate_happy_path_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: "fixture-test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    backend = HuggingFaceBackend(transport=_mock_record_transport(tmp_path, handler))
    text = backend.generate(PROMPT_OK, "some/served-model", max_tokens=8, temperature=0.7)
    assert text == "ok"


def test_huggingface_generate_without_key_raises_auth_and_never_reaches_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: None)
    backend = HuggingFaceBackend(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        backend.generate(PROMPT_OK, "some/model")
    assert excinfo.value.kind == ErrorKind.auth


# ---------------------------------------------------------------------------
# Pollinations - list_models is a real fixture; generate() is mocked (see
# module docstring: the recording account's pollen budget is 0, for both
# its own key and anonymous access).
# ---------------------------------------------------------------------------


def test_pollinations_list_models_happy_path():
    backend = PollinationsBackend(transport=_replay_transport())
    models = backend.list_models()
    assert models == ["openai-fast"]


def test_pollinations_generate_happy_path_mocked_dict_envelope(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    backend = PollinationsBackend(transport=_mock_record_transport(tmp_path, handler))
    text = backend.generate(PROMPT_OK, "openai-fast", max_tokens=8, temperature=0.7)
    assert text == "ok"


def test_pollinations_generate_bare_string_response(tmp_path):
    # A real, ported quirk: some Pollinations models answer with a bare
    # JSON string instead of the OpenAI choices/message envelope (see
    # pollinations.py's module docstring / the legacy backend's comment).
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'"ok"', headers={"content-type": "application/json"})

    backend = PollinationsBackend(transport=_mock_record_transport(tmp_path, handler))
    text = backend.generate(PROMPT_OK, "openai-fast", max_tokens=8, temperature=0.7)
    assert text == "ok"


def test_pollinations_generate_works_without_a_key(monkeypatch, tmp_path):
    # The one backend where a missing key is NOT ErrorKind.auth - keyless
    # is Pollinations' primary, intended mode.
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: None)
    seen_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    backend = PollinationsBackend(transport=_mock_record_transport(tmp_path, handler))
    text = backend.generate(PROMPT_OK, "openai-fast", max_tokens=8, temperature=0.7)
    assert text == "ok"
    assert "authorization" not in seen_headers


# ---------------------------------------------------------------------------
# avg.providers.models - the cache mechanism directly, independent of any
# one backend.
# ---------------------------------------------------------------------------


def test_get_models_returns_cache_when_fetch_live_raises(tmp_path):
    cache_path = tmp_path / "model_cache.json"
    cache_path.write_text(json.dumps({"groq": ["cached-a", "cached-b"]}), encoding="utf-8")

    def _unreachable():
        raise RuntimeError("simulated network failure")

    assert models_module.get_models("groq", _unreachable, cache_path=cache_path) == ["cached-a", "cached-b"]


def test_get_models_writes_cache_on_success_and_returns_the_live_result(tmp_path):
    cache_path = tmp_path / "model_cache.json"
    result = models_module.get_models("groq", lambda: ["fresh-a", "fresh-b"], cache_path=cache_path)
    assert result == ["fresh-a", "fresh-b"]
    assert json.loads(cache_path.read_text(encoding="utf-8"))["groq"] == ["fresh-a", "fresh-b"]


def test_get_models_returns_empty_list_when_unreachable_and_nothing_cached(tmp_path):
    cache_path = tmp_path / "model_cache.json"

    def _unreachable():
        raise RuntimeError("simulated network failure")

    assert models_module.get_models("groq", _unreachable, cache_path=cache_path) == []


def test_get_models_never_falls_back_to_a_hardcoded_list_only_the_cache(tmp_path):
    # A live fetch that succeeds but returns nothing must not resurrect an
    # older cached entry for a DIFFERENT provider, and an empty result is
    # never silently replaced by a baked-in literal - only ever the cache.
    cache_path = tmp_path / "model_cache.json"
    cache_path.write_text(json.dumps({"other_provider": ["x"]}), encoding="utf-8")
    assert models_module.get_models("groq", lambda: [], cache_path=cache_path) == []
