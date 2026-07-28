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
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: "fixture-test-key")
    monkeypatch.setattr("avg.providers.text.gemini.get_key", lambda _p: "fixture-test-key")


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


# ---------------------------------------------------------------------------
# Settings -> RouterConfig (Slice 6B)


def test_parse_target_rejects_anything_that_is_not_a_provider_model_pair():
    from avg.providers.text.router import ProviderModel, parse_target

    assert parse_target("  ") is None
    assert parse_target("groq/llama-3.3-70b-versatile") == ProviderModel("groq", "llama-3.3-70b-versatile")
    # A typo must never be silently ignored - that would route a phase at
    # whatever the default happens to be, which is not what was asked for.
    for bad in ("groq", "groq/", "/llama", "nope/some-model"):
        with pytest.raises(ValueError):
            parse_target(bad)


def test_config_from_settings_reads_the_catalogue_defaults():
    from avg.core.settings_schema import channel_defaults, get_spec
    from avg.providers.text.router import config_from_settings

    spec = get_spec("text_model_default")
    assert spec is not None
    config = config_from_settings(channel_defaults(), fallback_default=str(spec.default))
    assert config.default.provider == "groq"
    # Every per-phase slot ships blank, so nothing is pinned and every
    # phase routes to the default.
    assert config.phases == {}


def test_config_from_settings_survives_a_channel_written_before_the_models_group():
    from avg.providers.text.router import config_from_settings

    # Not an error: every channel document on disk predates these settings.
    config = config_from_settings({}, fallback_default="groq/llama-3.3-70b-versatile")
    assert config.default.model == "llama-3.3-70b-versatile"


def test_config_from_settings_builds_a_per_phase_route():
    from avg.providers.text.router import config_from_settings

    config = config_from_settings(
        {
            "text_model_default": "groq/llama-3.3-70b-versatile",
            "script_segmentation_model": "groq/llama-3.1-8b-instant",
            "script_segmentation_backup": "gemini/gemini-2.0-flash",
            "script_segmentation_on_failure": "fall_back",
        },
        fallback_default="groq/llama-3.3-70b-versatile",
    )
    route = config.phases["script_segmentation"]
    assert route.primary.model == "llama-3.1-8b-instant"
    assert route.backup is not None and route.backup.provider == "gemini"
    assert route.on_failure == "fall_back"
    # A phase with no `_model` gets no entry at all rather than an entry
    # duplicating the default.
    assert "brief" not in config.phases


def test_fall_back_tries_the_default_after_primary_and_backup_are_exhausted():
    from avg.providers.text.router import PhaseRoute, ProviderModel, RouterConfig, generate

    # Primary and backup are both the hand-written 429 fixture's target; the
    # default is the real recorded happy path. With on_failure="stop" this
    # would raise (see the next test) - "fall_back" is the only reason it
    # returns.
    config = RouterConfig(
        default=ProviderModel("groq", "llama-3.1-8b-instant"),
        phases={
            "script": PhaseRoute(
                primary=ProviderModel("groq", "llama-3.3-70b-versatile"),
                backup=ProviderModel("groq", "llama-3.3-70b-versatile"),
                on_failure="fall_back",
            ),
        },
        max_tokens=8,
        temperature=0.7,
        transport=_replay_transport(),
    )
    result = generate("router fallback test: primary should fail", "script", config=config)
    assert result.used_backup is True
    assert result.model == "llama-3.1-8b-instant"


def test_stop_is_the_default_and_never_reaches_the_router_default():
    from avg.providers.text.router import PhaseRoute, ProviderModel, RouterConfig, generate

    config = RouterConfig(
        default=ProviderModel("groq", "llama-3.1-8b-instant"),
        phases={
            "script": PhaseRoute(primary=ProviderModel("groq", "llama-3.3-70b-versatile")),
        },
        max_tokens=8,
        temperature=0.7,
        transport=_replay_transport(),
    )
    assert config.phases["script"].on_failure == "stop"
    with pytest.raises(AvgError) as excinfo:
        generate("router fallback test: primary should fail", "script", config=config)
    assert excinfo.value.kind == ErrorKind.rate_limit
