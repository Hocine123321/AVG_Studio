import json as json_lib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import POLICY
from avg.providers.transport import (
    REDACTED,
    Transport,
    UnmatchedRequestError,
    request as transport_request,
)

FAKE_KEY = "sk-FAKE-not-a-real-key-1234567890abcdef"
FAKE_KEY_2 = "sk-FAKE-different-machine-key-000000000"


def _client(handler):
    """An httpx.Client wired to a fake transport - no sockets, ever."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _poison_client():
    """A client that fails the test loudly if it is ever called."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("transport reached the (fake) network in replay mode")

    return _client(handler)


def _ok_json_handler(payload, headers=None):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers=headers or {})

    return handler


class _SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ---------------------------------------------------------------------------
# Property 1: an unmatched request in replay mode raises, never reaches
# the network.
# ---------------------------------------------------------------------------


def test_unmatched_replay_request_raises_and_never_touches_client(tmp_path):
    transport = Transport(mode="replay", fixtures_dir=tmp_path, client=_poison_client())
    with pytest.raises(UnmatchedRequestError):
        transport.request("GET", "https://api.example.com/v1/models")


def test_matched_replay_request_also_never_touches_client(tmp_path):
    # Record once (with a working client)...
    recorder = Transport(
        mode="record",
        fixtures_dir=tmp_path,
        client=_client(_ok_json_handler({"ok": True})),
        sleep=lambda s: None,
    )
    recorder.request("GET", "https://api.example.com/v1/models", provider="testprov")

    # ...then replay with a client that blows up if it's ever invoked. A
    # *matched* fixture must be served from disk, not by touching the
    # client at all.
    replayer = Transport(mode="replay", fixtures_dir=tmp_path, client=_poison_client())
    response = replayer.request("GET", "https://api.example.com/v1/models", provider="testprov")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Record -> replay round trip.
# ---------------------------------------------------------------------------


def test_record_writes_one_fixture_and_replay_returns_the_same_response(tmp_path):
    recorder = Transport(
        mode="record",
        fixtures_dir=tmp_path,
        client=_client(_ok_json_handler({"choices": ["hello"]}, headers={"X-Test": "1"})),
        sleep=lambda s: None,
    )
    recorded = recorder.request(
        "POST",
        "https://api.example.com/v1/chat",
        json={"prompt": "say hi"},
        provider="testprov",
    )
    assert recorded.status_code == 200
    assert recorded.json() == {"choices": ["hello"]}

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1

    replayer = Transport(mode="replay", fixtures_dir=tmp_path, client=_poison_client())
    replayed = replayer.request(
        "POST",
        "https://api.example.com/v1/chat",
        json={"prompt": "say hi"},
        provider="testprov",
    )
    assert replayed.status_code == 200
    assert replayed.json() == {"choices": ["hello"]}


def test_different_body_produces_a_different_fixture_no_collision(tmp_path):
    client = _client(_ok_json_handler({"ok": True}))
    recorder = Transport(mode="record", fixtures_dir=tmp_path, client=client, sleep=lambda s: None)

    recorder.request("POST", "https://api.example.com/v1/chat", json={"prompt": "say hi"})
    recorder.request("POST", "https://api.example.com/v1/chat", json={"prompt": "say bye"})

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 2, "two different prompts must not collide onto the same fixture"


def test_dict_key_ordering_does_not_change_the_match_key(tmp_path):
    client = _client(_ok_json_handler({"ok": True}))
    recorder = Transport(mode="record", fixtures_dir=tmp_path, client=client, sleep=lambda s: None)
    recorder.request(
        "POST",
        "https://api.example.com/v1/chat",
        json={"a": 1, "b": 2, "c": {"x": 1, "y": 2}},
    )
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1

    # Same values, different insertion order (a real risk: two call sites
    # or two dict-comprehension orders building "the same" body).
    replayer = Transport(mode="replay", fixtures_dir=tmp_path, client=_poison_client())
    replayed = replayer.request(
        "POST",
        "https://api.example.com/v1/chat",
        json={"c": {"y": 2, "x": 1}, "b": 2, "a": 1},
    )
    assert replayed.status_code == 200


# ---------------------------------------------------------------------------
# Property 3: matching is stable across a different literal credential
# (i.e. across machines/runs where the real key differs).
# ---------------------------------------------------------------------------


def test_same_logical_request_matches_despite_a_different_key_value(tmp_path):
    client = _client(_ok_json_handler({"ok": True}))
    recorder = Transport(mode="record", fixtures_dir=tmp_path, client=client, sleep=lambda s: None)
    recorder.request(
        "POST",
        f"https://api.example.com/v1/chat?api_key={FAKE_KEY}",
        headers={"Authorization": f"Bearer {FAKE_KEY}"},
        json={"prompt": "say hi", "api_key": FAKE_KEY},
    )

    # A different machine (or a different run) with a *different* key,
    # same logical request otherwise, must still hit the recorded fixture.
    replayer = Transport(mode="replay", fixtures_dir=tmp_path, client=_poison_client())
    replayed = replayer.request(
        "POST",
        f"https://api.example.com/v1/chat?api_key={FAKE_KEY_2}",
        headers={"Authorization": f"Bearer {FAKE_KEY_2}"},
        json={"prompt": "say hi", "api_key": FAKE_KEY_2},
    )
    assert replayed.status_code == 200
    assert replayed.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Property 2: a recorded fixture contains no credential.
# ---------------------------------------------------------------------------


def test_fixture_scrubs_authorization_header_api_key_query_and_body_field(tmp_path):
    client = _client(_ok_json_handler({"ok": True}, headers={"X-Echo-Key": FAKE_KEY}))
    recorder = Transport(mode="record", fixtures_dir=tmp_path, client=client, sleep=lambda s: None)

    recorder.request(
        "POST",
        f"https://api.example.com/v1/chat?api_key={FAKE_KEY}&topic=cats",
        headers={"Authorization": f"Bearer {FAKE_KEY}", "Content-Type": "application/json"},
        json={"prompt": "hi", "api_key": FAKE_KEY, "nested": {"token": FAKE_KEY}},
    )

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    raw_text = files[0].read_text(encoding="utf-8")

    # The one thing that must never be true: the raw key string appears
    # anywhere in the file that gets committed to git.
    assert FAKE_KEY not in raw_text

    data = json_lib.loads(raw_text)
    assert data["request"]["headers"]["Authorization"] == REDACTED
    assert data["request"]["body"]["api_key"] == REDACTED
    assert data["request"]["body"]["nested"]["token"] == REDACTED
    stored_query = parse_qs(urlsplit(data["request"]["url"]).query)
    assert stored_query["api_key"] == [REDACTED]
    # A benign field/query param must survive untouched.
    assert data["request"]["body"]["prompt"] == "hi"
    assert stored_query["topic"] == ["cats"]
    # Defense in depth: a credential-shaped response header is scrubbed too.
    # (httpx.Response normalises header names to lowercase internally.)
    assert data["response"]["headers"]["x-echo-key"] == REDACTED


def test_scrubbing_does_not_redact_legitimate_token_count_fields(tmp_path):
    # max_tokens / prompt_tokens are common, legitimate LLM-API fields.
    # A substring-based scrubber would nuke them; ours must not.
    client = _client(_ok_json_handler({"usage": {"prompt_tokens": 12, "total_tokens": 34}}))
    recorder = Transport(mode="record", fixtures_dir=tmp_path, client=client, sleep=lambda s: None)
    recorder.request(
        "POST",
        "https://api.example.com/v1/chat",
        json={"prompt": "hi", "max_tokens": 256},
    )
    data = json_lib.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert data["request"]["body"]["max_tokens"] == 256


# ---------------------------------------------------------------------------
# Retry/backoff comes from POLICY.
# ---------------------------------------------------------------------------


def test_rate_limit_status_is_retried_per_policy_then_raises(tmp_path):
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="too many requests")

    sleeper = _SleepRecorder()
    transport = Transport(mode="record", fixtures_dir=tmp_path, client=_client(handler), sleep=sleeper)

    with pytest.raises(AvgError) as excinfo:
        transport.request("GET", "https://api.example.com/v1/models", provider="groq")

    assert excinfo.value.kind == ErrorKind.rate_limit
    rule = POLICY[ErrorKind.rate_limit]
    assert calls["n"] == rule.max_attempts == 5
    # Exponential: backoff_seconds * 2**(attempt-1) for each retry.
    assert sleeper.calls == [rule.backoff_seconds * (2 ** i) for i in range(rule.max_attempts - 1)]
    # A call that never succeeded must not leave a fixture behind.
    assert list(tmp_path.glob("*.json")) == []


def test_auth_status_is_not_retried(tmp_path):
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="invalid api key")

    sleeper = _SleepRecorder()
    transport = Transport(mode="record", fixtures_dir=tmp_path, client=_client(handler), sleep=sleeper)

    with pytest.raises(AvgError) as excinfo:
        transport.request("GET", "https://api.example.com/v1/models", provider="openai")

    assert excinfo.value.kind == ErrorKind.auth
    assert calls["n"] == 1, "a non-retryable kind must not be retried"
    assert sleeper.calls == []


def test_network_exception_is_retried_per_policy_then_raises(tmp_path):
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("simulated connection failure")

    sleeper = _SleepRecorder()
    transport = Transport(mode="record", fixtures_dir=tmp_path, client=_client(handler), sleep=sleeper)

    with pytest.raises(AvgError) as excinfo:
        transport.request("GET", "https://api.example.com/v1/models", provider="openai")

    assert excinfo.value.kind == ErrorKind.network
    rule = POLICY[ErrorKind.network]
    assert calls["n"] == rule.max_attempts == 3
    assert sleeper.calls == [rule.backoff_seconds * (2 ** i) for i in range(rule.max_attempts - 1)]


def test_successful_response_is_never_passed_through_classify(tmp_path):
    # A 200 with a body that happens to contain no error language must be
    # returned as-is, with zero retries/sleeps - classify() is only ever
    # consulted on a failure path (status >= 400 or a raised exception).
    sleeper = _SleepRecorder()
    client = _client(_ok_json_handler({"result": "fine"}))
    transport = Transport(mode="record", fixtures_dir=tmp_path, client=client, sleep=sleeper)
    response = transport.request("GET", "https://api.example.com/v1/models")
    assert response.status_code == 200
    assert sleeper.calls == []


# ---------------------------------------------------------------------------
# Mode selection.
# ---------------------------------------------------------------------------


def test_mode_defaults_from_avg_http_record_env_var(monkeypatch):
    monkeypatch.setenv("AVG_HTTP_RECORD", "1")
    assert Transport().mode == "record"

    monkeypatch.delenv("AVG_HTTP_RECORD", raising=False)
    assert Transport().mode == "replay"


def test_explicit_mode_overrides_env_var(monkeypatch):
    monkeypatch.setenv("AVG_HTTP_RECORD", "1")
    assert Transport(mode="replay").mode == "replay"


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        Transport(mode="not-a-real-mode")  # type: ignore[arg-type]


def test_module_level_request_is_replay_safe_by_default(monkeypatch):
    # No env var, no injected client: the module-level convenience function
    # must default to replay (never touch the real network) even against
    # the real fixtures directory, for a request that was never recorded.
    monkeypatch.delenv("AVG_HTTP_RECORD", raising=False)
    with pytest.raises(UnmatchedRequestError):
        transport_request("GET", "https://this-was-never-recorded.example.invalid/nope")
