"""Tests for the per-phase router (`avg.providers.text.router.generate`).

All against the real, committed `tests/fixtures/http/` directory in replay
mode - no network. Keys are patched directly onto each backend module (see
`test_text.py`'s docstring) so these tests don't depend on the real DPAPI
secrets store.

The required "rate-limited backend falls through to its backup" scenario
uses the one hand-written fixture in this task (a synthetic Groq 429, built
from Task 1's mined `rate_limit` patterns - see
`.superpowers/sdd/s3-task-3-report.md`) as the phase's primary, and a real
recorded Groq happy-path fixture (a different, genuinely served model,
`llama-3.1-8b-instant`) as its backup. `router.generate()` forwards the
same prompt to both attempts, so the backup's fixture was recorded with
that exact prompt text against its own distinct model - a different
canonical request, no collision with the primary's fixture.
"""

from __future__ import annotations

import pytest

from avg.core.errors import AvgError, ErrorKind
from avg.providers.text.router import PhaseRoute, ProviderModel, RouterConfig, generate
from avg.providers.transport import DEFAULT_FIXTURES_DIR, Transport


def _replay_transport() -> Transport:
    return Transport(mode="replay", fixtures_dir=DEFAULT_FIXTURES_DIR)


@pytest.fixture(autouse=True)
def _fixture_keys(monkeypatch):
    """Every backend the router can construct sees a fixture-only fake key -
    matches test_text.py's approach; the literal value never affects
    fixture matching (headers are excluded from the match key)."""
    monkeypatch.setattr("avg.providers.text.groq.get_key", lambda _p: "fixture-test-key")
    monkeypatch.setattr("avg.providers.text.gemini.get_key", lambda _p: "fixture-test-key")
    monkeypatch.setattr("avg.providers.text.huggingface.get_key", lambda _p: "fixture-test-key")


def test_generate_uses_phase_primary_when_configured():
    # The phase's primary (groq) must win even though the router's
    # default points at a completely different provider (gemini, which
    # has no recorded fixture for this prompt at all - if the router used
    # the default instead of the phase override, this would raise
    # UnmatchedRequestError rather than return successfully).
    config = RouterConfig(
        default=ProviderModel("gemini", "gemini-2.0-flash"),
        phases={
            "script": PhaseRoute(primary=ProviderModel("groq", "llama-3.3-70b-versatile")),
        },
        max_tokens=8,
        temperature=0.7,
        transport=_replay_transport(),
    )
    result = generate("Reply with the single word: ok", "script", config=config)
    assert result.text == "ok"
    assert result.provider == "groq"
    assert result.model == "llama-3.3-70b-versatile"
    assert result.used_backup is False


def test_generate_falls_back_to_default_when_phase_unset():
    config = RouterConfig(
        default=ProviderModel("groq", "llama-3.3-70b-versatile"),
        phases={},
        max_tokens=8,
        temperature=0.7,
        transport=_replay_transport(),
    )
    result = generate("Reply with the single word: ok", "brief", config=config)
    assert result.text == "ok"
    assert result.provider == "groq"
    assert result.model == "llama-3.3-70b-versatile"
    assert result.used_backup is False


def test_generate_rate_limited_primary_falls_through_to_backup():
    config = RouterConfig(
        default=ProviderModel("groq", "llama-3.3-70b-versatile"),
        phases={
            "script": PhaseRoute(
                primary=ProviderModel("groq", "llama-3.3-70b-versatile"),
                backup=ProviderModel("groq", "llama-3.1-8b-instant"),
            ),
        },
        max_tokens=8,
        temperature=0.7,
        transport=_replay_transport(),
    )
    result = generate("router fallback test: primary should fail", "script", config=config)
    assert result.used_backup is True
    assert result.provider == "groq"
    assert result.model == "llama-3.1-8b-instant"
    assert result.text  # the real recorded backup response, non-empty


def test_generate_without_backup_propagates_the_original_error():
    config = RouterConfig(
        default=ProviderModel("groq", "llama-3.3-70b-versatile"),
        phases={
            "script": PhaseRoute(primary=ProviderModel("groq", "llama-3.3-70b-versatile")),
        },
        max_tokens=8,
        temperature=0.7,
        transport=_replay_transport(),
    )
    with pytest.raises(AvgError) as excinfo:
        generate("router fallback test: primary should fail", "script", config=config)
    assert excinfo.value.kind == ErrorKind.rate_limit


def test_generate_does_not_use_backup_when_policy_says_fail(monkeypatch, tmp_path):
    # content_filter's on_exhausted is "fail" - switching provider/model
    # would not help (the same prompt would be blocked anywhere), so a
    # configured backup must never even be attempted. Uses a local mock
    # (see test_text.py's rationale for Gemini) since content_filter needs
    # a 200-with-blocked-content shape, not an HTTP failure status.
    monkeypatch.setattr("avg.providers.text.gemini.get_key", lambda _p: "fixture-test-key")
    import httpx

    def blocked_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [{"finishReason": "SAFETY", "content": {}}],
                "promptFeedback": {"blockReason": "SAFETY"},
            },
        )

    def poison_handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("backup must never be called for a content_filter failure")

    from avg.providers.transport import Transport as _Transport

    class _DualTransport:
        """Routes gemini calls to the blocked handler and anything else
        (i.e. a backup attempt) to a handler that fails the test."""

        def __init__(self) -> None:
            self._gemini = _Transport(
                mode="record",
                fixtures_dir=tmp_path,
                client=httpx.Client(transport=httpx.MockTransport(blocked_handler)),
                sleep=lambda _s: None,
            )
            self._other = _Transport(
                mode="record",
                fixtures_dir=tmp_path,
                client=httpx.Client(transport=httpx.MockTransport(poison_handler)),
                sleep=lambda _s: None,
            )

        def request(self, method, url, **kwargs):
            target = self._gemini if "generativelanguage" in url else self._other
            return target.request(method, url, **kwargs)

    config = RouterConfig(
        default=ProviderModel("gemini", "gemini-2.0-flash"),
        phases={
            "script": PhaseRoute(
                primary=ProviderModel("gemini", "gemini-2.0-flash"),
                backup=ProviderModel("groq", "llama-3.3-70b-versatile"),
            ),
        },
        max_tokens=8,
        temperature=0.7,
        transport=_DualTransport(),  # type: ignore[arg-type]
    )
    with pytest.raises(AvgError) as excinfo:
        generate("a deliberately blocked prompt", "script", config=config)
    assert excinfo.value.kind == ErrorKind.content_filter


def test_generate_unknown_provider_raises_value_error():
    config = RouterConfig(
        default=ProviderModel("not-a-real-provider", "some-model"),
        transport=_replay_transport(),
    )
    with pytest.raises(ValueError):
        generate("hello", "brief", config=config)
