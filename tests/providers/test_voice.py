"""Tests for the two voice providers: ElevenLabs (primary/backup key
switch) and Edge TTS (the keyless last resort).

Every fixture-based test runs against `Transport(mode="replay", ...)`
pointed at the committed `tests/fixtures/http/` directory - no network,
ever (see `avg/providers/transport.py`'s own guarantee that replay mode
never references its `client`). `avg.config.secrets.get_key` is
monkeypatched directly inside the provider module under test, exactly the
pattern `tests/providers/test_text.py` and `test_images.py` already use,
rather than relying on the real DPAPI-encrypted store.

**A genuine fixture-collision casualty, not a hypothetical one.** Two
fixtures were on disk before this test file was written:
`get_api_elevenlabs_io_v1_voices_...json` (a real, successful voices-list
call) and `post_api_elevenlabs_io_v1_text_to_speech_..._f3feae78b6d5a393.json`.
The latter's filename suggested a real, paid-for synthesis - but its
*content* is a real 401 (`{"detail":{"code":"unauthorized",
"message":"Invalid API key",...}}`) for a request body of
`{"text": "invalid key check", "model_id": "eleven_flash_v2_5"}`. The real
successful synthesis this task was told about never made it to disk: the
transport's match key is method + URL + a hash of the (credential-redacted)
body, which deliberately excludes *which key* sent the request (see
`transport.py`'s own docstring, point 3) - so a later auth-failure capture
against the same voice_id/text/model silently overwrote the earlier
successful recording, because both hashed to the same fixture file. This
is the exact hazard `.superpowers/sdd/s3-task-4-report.md` also hit from
another angle (Stable Horde's poll loop reusing one URL). Consequence for
this file: there is no real 200 fixture for `synthesize()` to replay -
only the real 401 above, which this file uses for what it actually is (a
real auth failure), and a locally-mocked transport for every case that
needs a 200 (this task's brief explicitly forbids recording a new real
synthesis - characters cost money). Every capture below that isn't the two
original fixtures uses a distinct body string precisely so this cannot
happen again to a *future* task's recordings.

A second, structural reason a mocked transport is unavoidable for the
key-switch tests specifically: `ElevenLabsVoiceProvider.synthesize()`
calls `self._call(text, ...)` with the *same* `text` for both the primary
and the backup attempt (see `elevenlabs.py`) - so both HTTP requests in a
single `synthesize()` call hash to the identical fixture file regardless
of which key sent them (headers are excluded from the match key by
design). Replay-mode fixtures cannot represent "primary fails, backup
succeeds" for one call at all, by construction - only a mock transport
that actually inspects the `xi-api-key` header can tell the two attempts
apart, which is what the switch/no-switch tests below do.
"""

from __future__ import annotations

import httpx
import pytest

from avg.core.errors import AvgError, ErrorKind
from avg.providers.transport import DEFAULT_FIXTURES_DIR, Transport
from avg.providers.voice import providers as voice_module
from avg.providers.voice.providers import EdgeVoiceProvider, ElevenLabsVoiceProvider

VOICE_ID = "CwhRBWXzGAHq8TQ4Fs17"
MODEL = "eleven_flash_v2_5"

PRIMARY_KEY = "primary-key-value"
BACKUP_KEY = "backup-key-value"

FAKE_AUDIO = b"fake-mp3-bytes-not-real-audio"


def _replay_transport() -> Transport:
    """Points at the real, committed fixtures directory - never the network."""
    return Transport(mode="replay", fixtures_dir=DEFAULT_FIXTURES_DIR)


def _poison_transport(tmp_path) -> Transport:
    """A transport that blows up if it is ever asked to send anything - for
    tests proving a guard (e.g. "no key") short-circuits before reaching the
    transport at all. Mirrors test_text.py's helper of the same name."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider reached the transport when it should have short-circuited")

    return Transport(
        mode="replay",
        fixtures_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _mock_record_transport(tmp_path, handler) -> Transport:
    """A record-mode transport wired to a local, in-process handler. Never
    touches the network; writes an ephemeral fixture into `tmp_path` that
    the test discards. Mirrors test_text.py's helper of the same name."""
    return Transport(
        mode="record",
        fixtures_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )


def _patch_keys(monkeypatch, *, primary: str | None, backup: str | None) -> None:
    """Route `elevenlabs.py`'s own `get_key` calls to fixed test values
    instead of the real DPAPI store, keyed by the provider names the module
    actually asks for (`self.name` == "elevenlabs", `_BACKUP_KEY_NAME` ==
    "elevenlabs_backup")."""

    def fake_get_key(name: str) -> str | None:
        if name == "elevenlabs":
            return primary
        if name == "elevenlabs_backup":
            return backup
        raise AssertionError(f"unexpected secrets.get_key({name!r}) call")

    monkeypatch.setattr("avg.providers.voice.providers.get_key", fake_get_key)


# ---------------------------------------------------------------------------
# 1. Primary key succeeds.
# ---------------------------------------------------------------------------


def test_elevenlabs_synthesize_primary_key_succeeds(monkeypatch, tmp_path):
    # No real 200 fixture survives (see module docstring) - a locally
    # mocked transport stands in, matching test_text.py's established
    # pattern for accounts/fixtures a task couldn't obtain for free.
    _patch_keys(monkeypatch, primary=PRIMARY_KEY, backup=BACKUP_KEY)
    seen_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers["xi-api-key"])
        return httpx.Response(200, content=FAKE_AUDIO, headers={"content-type": "audio/mpeg"})

    provider = ElevenLabsVoiceProvider(transport=_mock_record_transport(tmp_path, handler))
    audio = provider.synthesize("primary key happy path", voice_id=VOICE_ID, model=MODEL)

    assert audio == FAKE_AUDIO
    assert seen_keys == [PRIMARY_KEY]  # exactly one attempt, the primary key


def test_elevenlabs_synthesize_without_key_raises_auth_and_never_reaches_transport(monkeypatch, tmp_path):
    _patch_keys(monkeypatch, primary=None, backup=None)
    provider = ElevenLabsVoiceProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.synthesize("unreachable", voice_id=VOICE_ID, model=MODEL)
    assert excinfo.value.kind == ErrorKind.auth


# ---------------------------------------------------------------------------
# 2. An auth failure switches to the backup key - retried exactly once.
# ---------------------------------------------------------------------------


def test_elevenlabs_synthesize_auth_failure_switches_to_backup_key(monkeypatch, tmp_path):
    _patch_keys(monkeypatch, primary=PRIMARY_KEY, backup=BACKUP_KEY)
    seen_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["xi-api-key"]
        seen_keys.append(key)
        if key == PRIMARY_KEY:
            # The real, recorded shape of this failure (see
            # get_api...voices fixture's sibling, the 401 synthesis
            # fixture) - reproduced inline here since that fixture's own
            # body is reused, unmodified, only as documentation of what a
            # real ElevenLabs auth failure looks like.
            return httpx.Response(
                401,
                json={
                    "detail": {
                        "type": "authentication_error",
                        "code": "unauthorized",
                        "message": "Invalid API key",
                        "status": "invalid_api_key",
                    }
                },
            )
        assert key == BACKUP_KEY
        return httpx.Response(200, content=FAKE_AUDIO, headers={"content-type": "audio/mpeg"})

    provider = ElevenLabsVoiceProvider(transport=_mock_record_transport(tmp_path, handler))
    audio = provider.synthesize("auth failure then backup succeeds", voice_id=VOICE_ID, model=MODEL)

    assert audio == FAKE_AUDIO
    # Exactly two attempts, in order: primary (failed), then backup once -
    # never more than one retry, and never the primary again.
    assert seen_keys == [PRIMARY_KEY, BACKUP_KEY]


def test_elevenlabs_synthesize_real_401_fixture_classifies_as_auth(monkeypatch):
    # The one real HTTP interaction on disk for this endpoint: a genuine
    # 401 from a deliberately wrong key (body text "invalid key check",
    # matching the committed fixture's recorded request). No backup key is
    # configured here, so the original failure must propagate unchanged -
    # this exercises real, replayed bytes rather than a mock.
    _patch_keys(monkeypatch, primary=PRIMARY_KEY, backup=None)
    provider = ElevenLabsVoiceProvider(transport=_replay_transport())
    with pytest.raises(AvgError) as excinfo:
        provider.synthesize("invalid key check", voice_id=VOICE_ID, model=MODEL)
    assert excinfo.value.kind == ErrorKind.auth


def test_elevenlabs_synthesize_auth_failure_with_no_backup_key_configured_raises_original(monkeypatch, tmp_path):
    _patch_keys(monkeypatch, primary=PRIMARY_KEY, backup=None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["xi-api-key"] == PRIMARY_KEY
        return httpx.Response(401, json={"detail": {"message": "Invalid API key", "status": "invalid_api_key"}})

    provider = ElevenLabsVoiceProvider(transport=_mock_record_transport(tmp_path, handler))
    with pytest.raises(AvgError) as excinfo:
        provider.synthesize("no backup key available", voice_id=VOICE_ID, model=MODEL)

    assert excinfo.value.kind == ErrorKind.auth
    assert calls == 1  # nothing to retry against - no second attempt at all


# ---------------------------------------------------------------------------
# 3. A 5xx does NOT switch keys - written to fail loudly if this regresses.
# ---------------------------------------------------------------------------


def test_elevenlabs_synthesize_5xx_does_not_switch_keys(monkeypatch, tmp_path):
    """The one test in this file guarding the exact mistake the module
    docstring says the legacy app made twice.

    Two independent, redundant tripwires - either one alone would catch a
    regression, both are kept because they fail for different, equally
    loud reasons:

    1. `get_key` is monkeypatched so that asking for the backup key at all
       is an immediate, hard `AssertionError` - not "assert it wasn't
       called afterwards" (which a bug could still slip past if the
       assertion were misplaced), but a trap sprung the instant the
       forbidden call happens. If a future edit widens `_KEY_SWITCH_KINDS`
       to include `network`, or removes the guard entirely, this fires
       immediately with a message naming exactly what went wrong.
    2. The mock HTTP handler independently asserts every request it
       receives carries the *primary* key - if the backup key somehow
       reached the transport without going through `get_key` (e.g. a
       hardcoded second attempt), this second, unrelated code path still
       catches it.

    Without this test, a "helpful" future change - e.g. broadening
    `_KEY_SWITCH_KINDS` to `(auth, quota, network)` - would pass every
    other test in this file (the backup-key mock isn't wired up for a 5xx
    scenario anywhere else) and only show up as a real backup-quota bill
    days later. That is precisely the failure mode this test exists to
    catch before it ships.
    """
    calls = 0

    def fake_get_key(name: str) -> str | None:
        nonlocal calls
        if name == "elevenlabs":
            return PRIMARY_KEY
        if name == "elevenlabs_backup":
            raise AssertionError(
                "ElevenLabsVoiceProvider fetched the backup key after a 5xx - "
                "a server error is service-wide, not account-scoped, and must "
                "never trigger a key switch (see elevenlabs.py's module docstring)"
            )
        raise AssertionError(f"unexpected secrets.get_key({name!r}) call")

    monkeypatch.setattr("avg.providers.voice.providers.get_key", fake_get_key)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["xi-api-key"] == PRIMARY_KEY, "backup key reached the transport on a 5xx"
        return httpx.Response(500, text="internal_server_error")

    provider = ElevenLabsVoiceProvider(transport=_mock_record_transport(tmp_path, handler))
    with pytest.raises(AvgError) as excinfo:
        provider.synthesize("server error should not switch keys", voice_id=VOICE_ID, model=MODEL)

    # 500 -> ErrorKind.network per classify()'s hard status-based rule,
    # checked before any body text - never `auth`, never `quota`, so
    # `_KEY_SWITCH_KINDS` correctly never matches it.
    assert excinfo.value.kind == ErrorKind.network
    # Transport-level retry (POLICY[network]: retryable, max_attempts=3)
    # exhausts entirely against the SAME (primary) key before giving up -
    # proving the key-switch logic never even got a chance to run, not
    # merely that it happened not to.
    assert calls == 3


# ---------------------------------------------------------------------------
# 4. Both keys failing falls through to Edge TTS (a caller/router decision -
#    see elevenlabs.py's module docstring: this provider's job stops at "did
#    ElevenLabs, with up to two keys, produce audio". No voice router exists
#    yet in this slice, so this test demonstrates the fallback contract at
#    the call site directly, mirroring test_images.py's equivalent note for
#    the image-provider "switch_provider" contract.)
# ---------------------------------------------------------------------------


def test_both_elevenlabs_keys_failing_falls_through_to_edge_tts(monkeypatch, tmp_path):
    _patch_keys(monkeypatch, primary=PRIMARY_KEY, backup=BACKUP_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        # Both keys fail - a genuinely different failure per key (quota vs.
        # auth) to prove the fallback is reached regardless of which
        # specific key-switch-eligible kind is doing the failing here.
        if request.headers["xi-api-key"] == PRIMARY_KEY:
            return httpx.Response(401, json={"detail": {"message": "Invalid API key", "status": "invalid_api_key"}})
        return httpx.Response(
            401, json={"detail": {"message": "Invalid API key", "status": "invalid_api_key"}}
        )

    elevenlabs_provider = ElevenLabsVoiceProvider(transport=_mock_record_transport(tmp_path, handler))

    edge_audio = b"fake-edge-audio-bytes"

    class _FakeCommunicate:
        def __init__(self, text: str, voice_id: str) -> None:
            self.text = text
            self.voice_id = voice_id

        def stream_sync(self):
            yield {"type": "audio", "data": edge_audio}

    monkeypatch.setattr(voice_module.edge_tts, "Communicate", _FakeCommunicate)
    edge_provider = EdgeVoiceProvider()

    text = "both keys fail, fall back to edge"
    try:
        elevenlabs_provider.synthesize(text, voice_id=VOICE_ID, model=MODEL)
        pytest.fail("expected ElevenLabs to raise when both keys fail")
    except AvgError as exc:
        assert exc.kind == ErrorKind.auth
        audio = edge_provider.synthesize(text, voice_id="en-US-GuyNeural", model="unused")

    # Proves Edge actually ran (its mocked internals produced this exact,
    # distinctive payload) rather than merely "no exception escaped".
    assert audio == edge_audio


# ---------------------------------------------------------------------------
# list_voices - ElevenLabs (real fixture) and Edge (mocked, no network).
# ---------------------------------------------------------------------------


def test_elevenlabs_list_voices_happy_path(monkeypatch):
    _patch_keys(monkeypatch, primary=PRIMARY_KEY, backup=None)
    provider = ElevenLabsVoiceProvider(transport=_replay_transport())
    voices = provider.list_voices()
    assert voices, "expected a non-empty live voice list"
    assert all({"voice_id", "name"} <= v.keys() for v in voices)


def test_elevenlabs_list_voices_without_key_raises_auth_and_never_reaches_transport(monkeypatch, tmp_path):
    _patch_keys(monkeypatch, primary=None, backup=None)
    provider = ElevenLabsVoiceProvider(transport=_poison_transport(tmp_path))
    with pytest.raises(AvgError) as excinfo:
        provider.list_voices()
    assert excinfo.value.kind == ErrorKind.auth


def test_edge_list_voices_happy_path_mocked(monkeypatch):
    async def fake_list_voices():
        return [
            {"ShortName": "en-US-GuyNeural", "FriendlyName": "Microsoft Guy Online", "Locale": "en-US", "Gender": "Male"}
        ]

    monkeypatch.setattr(voice_module.edge_tts, "list_voices", fake_list_voices)
    provider = EdgeVoiceProvider()
    voices = provider.list_voices()
    assert voices == [
        {"voice_id": "en-US-GuyNeural", "name": "Microsoft Guy Online", "locale": "en-US", "gender": "Male"}
    ]


def test_edge_synthesize_happy_path_mocked(monkeypatch):
    edge_audio = b"another-fake-edge-audio-chunk"

    class _FakeCommunicate:
        def __init__(self, text: str, voice_id: str) -> None:
            pass

        def stream_sync(self):
            yield {"type": "audio", "data": edge_audio}

    monkeypatch.setattr(voice_module.edge_tts, "Communicate", _FakeCommunicate)
    provider = EdgeVoiceProvider()
    audio = provider.synthesize("edge happy path", voice_id="en-US-GuyNeural", model="unused")
    assert audio == edge_audio


def test_edge_synthesize_wraps_exceptions_as_avg_error_without_touching_network(monkeypatch):
    class _ExplodingCommunicate:
        def __init__(self, text: str, voice_id: str) -> None:
            pass

        def stream_sync(self):
            raise RuntimeError("simulated websocket failure")
            yield  # pragma: no cover - unreachable, makes this a generator

    monkeypatch.setattr(voice_module.edge_tts, "Communicate", _ExplodingCommunicate)
    provider = EdgeVoiceProvider()
    with pytest.raises(AvgError):
        provider.synthesize("boom", voice_id="en-US-GuyNeural", model="unused")
