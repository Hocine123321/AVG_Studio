"""HTTP transport with record/replay fixtures and policy-driven retry.

This is the one place every provider (Task 3: text, Task 4: image, Task 5:
voice) sends an HTTP request through. It owns three concerns and nothing
else: sending the request, recording/replaying it against a fixture file,
and applying Task 1's retry policy (`avg.providers.errors.POLICY`). No
provider-specific knowledge, no prompt handling, no JSON-schema opinions.

Two modes, chosen by `AVG_HTTP_RECORD` (or the `mode=` override):

  - **record** (`AVG_HTTP_RECORD=1`): performs the real call through
    `httpx`, applies the retry policy, and writes the interaction to
    `tests/fixtures/http/<slug>.json`.
  - **replay** (the default): never touches the network. It recomputes the
    same match key the recorder used, looks up the fixture, and either
    returns the recorded response or raises `UnmatchedRequestError`. There
    is no fallback path to the network in this mode - that is the whole
    point of the module. A test that quietly makes a live call is worse
    than a failing one.

Three properties this module guarantees:

  1. **An unmatched request in replay mode raises.** `_replay()` never
     references the HTTP client at all - structurally, not just by
     convention - so there is no code path in replay mode that can reach
     the network, whether the fixture matches, doesn't match, or something
     in between (a redirect, a retry). Replay is pure filesystem + JSON.

  2. **Fixtures are scrubbed of credentials before they are written**, not
     after. `Authorization`/`X-Api-Key`-shaped headers, `api_key`/`key`-
     shaped query parameters, and key-shaped fields anywhere in a JSON
     request body (recursively) are replaced with `REDACTED` before the
     bytes touch disk. Response headers get the same treatment (a
     defensive measure - some APIs echo credentials back). There is no
     "redact on read" path; a fixture cannot be written any other way.

  3. **Matching is stable.** The match key is method + a normalised URL
     (query params sorted, credential values redacted) + a hash of the
     JSON body (also redacted, and serialised with `sort_keys=True` so
     dict insertion order never changes the hash). Redacting credentials
     *before* hashing is deliberate, not just a side effect of point 2: it
     is what lets the same logical request match its fixture even when
     the literal key differs between the machine that recorded it and the
     machine replaying it.
"""

from __future__ import annotations

import base64
import hashlib
import json as json_lib
import os
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from avg.config import provider_health
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import POLICY, classify, retry_after_seconds

Mode = Literal["live", "record", "replay"]

# Placeholder written in place of any credential value. Exported (not
# underscore-prefixed) so tests can assert against it by name rather than
# by a duplicated literal.
REDACTED = "***REDACTED***"

# tests/fixtures/http/, relative to the repo root (avg/providers/transport.py
# -> avg/providers -> avg -> repo root).
DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "http"


class UnmatchedRequestError(RuntimeError):
    """Raised in replay mode when no fixture matches a request.

    There is deliberately no fallback to the network here. Record the
    fixture with `AVG_HTTP_RECORD=1` (from Task 3+, against the real
    provider), or check whether the request changed since it was recorded.
    """


# ---------------------------------------------------------------------------
# Scrubbing - field-name based, not substring-in-value based. A field is
# "key-shaped" if its *name* (normalised: lowercased, separators stripped)
# is a known credential-ish name. This is deliberately narrow: providers
# routinely have legitimate fields like `max_tokens` / `prompt_tokens` /
# `access_token_count` that must survive into the fixture unredacted for the
# fixture to be useful; a bare substring match on "token" would nuke those.
# ---------------------------------------------------------------------------

_CREDENTIAL_NAMES_RAW = (
    "key", "api_key", "api-key", "apikey",
    "token", "access_token", "access-token", "accesstoken",
    "auth_token", "auth-token", "authtoken",
    "id_token", "id-token", "idtoken",
    "refresh_token", "refresh-token", "refreshtoken",
    "secret", "secret_key", "secret-key", "secretkey",
    "client_secret", "client-secret", "clientsecret",
    "password", "pwd",
    "authorization",
)


def _normalize_name(name: str) -> str:
    return re.sub(r"[_\-\s]", "", name).lower()


_CREDENTIAL_NAMES = {_normalize_name(n) for n in _CREDENTIAL_NAMES_RAW}

# Headers get a looser, substring-based check: the header namespace is
# small and security-sensitive, headers are never part of the match key (see
# module docstring point 3), so over-redacting a header carries no risk of
# breaking a fixture match - only the credential-leak risk matters here.
_CREDENTIAL_HEADER_SUBSTRINGS = (
    "authorization", "api-key", "apikey", "api_key", "key",
    "auth-token", "access-token", "secret", "token", "bearer", "cookie",
)


def _is_credential_name(name: str) -> bool:
    return _normalize_name(name) in _CREDENTIAL_NAMES


def _is_credential_header(name: str) -> bool:
    lname = name.lower()
    return any(marker in lname for marker in _CREDENTIAL_HEADER_SUBSTRINGS)


def _scrub_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {k: (REDACTED if _is_credential_header(k) else v) for k, v in headers.items()}


# httpx transparently gunzips (or brotli/deflate-decodes) a response's body
# the moment anything reads `.content`/`.text`/`.json()` - by the time
# `_write_fixture` stores `content_b64` (below), it is always the fully
# *decoded* body, never the compressed wire bytes. But if the response's
# original `Content-Encoding`/`Transfer-Encoding`/`Content-Length` headers
# are stored alongside it verbatim, `_replay()`'s `httpx.Response(headers=...,
# content=...)` reconstruction believes those headers over the bytes it was
# actually given - and tries to gunzip already-decoded JSON, which is not
# valid gzip and raises `httpx.DecodingError`. This was invisible in Task
# 2's own tests (every one used a hand-built `httpx.Response(...)` with no
# real compression header) and only surfaced once this task recorded fixtures
# against real, gzip-compressing APIs (Groq, Pollinations). These three
# headers describe how the *wire* body was transferred, not what the
# decoded content *is* - stale once `content_b64` is the final, decoded
# form - so they are dropped before a response is ever written to a fixture.
_STALE_TRANSFER_HEADER_NAMES = {"content-encoding", "transfer-encoding", "content-length"}


def _drop_stale_transfer_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _STALE_TRANSFER_HEADER_NAMES}


def _redact_json(value: Any) -> Any:
    """Recursively replace key-shaped fields anywhere in a JSON value."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_credential_name(str(k)) else _redact_json(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(v) for v in value]
    return value


def _canonical_url(url: str) -> str:
    """Normalise a URL for matching/storage: query params sorted by name
    (so dict/query-building order never changes the key) and any
    credential-shaped param value redacted."""
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs = sorted(
        ((k, REDACTED if _is_credential_name(k) else v) for k, v in pairs),
        key=lambda kv: kv[0],
    )
    query = urlencode(redacted_pairs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _canonical_body(body: Any) -> tuple[Any, str]:
    """Redact a JSON body and hash the canonical (sort_keys) form.

    `sort_keys=True` is what makes the hash immune to dict insertion-order
    differences between two calls that build "the same" body differently -
    the exact instability the brief warns about.
    """
    if body is None:
        redacted: Any = None
        canonical = ""
    else:
        redacted = _redact_json(body)
        canonical = json_lib.dumps(redacted, sort_keys=True, separators=(",", ":"), default=str)
    body_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return redacted, body_hash


def _slug_for(method: str, canonical_url: str, body_hash: str) -> str:
    """Deterministic, human-legible fixture filename (sans extension).

    Built only from method + the *redacted* canonical URL + the *redacted*
    body's hash - never from headers, never from the literal credential
    value - so the same logical request always resolves to the same file
    regardless of which machine (and which real/fake key) sent it.

    KNOWN LIMITATION, hit independently by two tasks - read this before
    recording anything:

    Because credentials and headers are excluded by design, **two requests
    that differ ONLY by which key they carry produce the same slug and
    therefore the same file.** Recording a 401-with-a-bad-key over a 200
    silently destroys the 200 - which is exactly how Task 5 lost a real,
    paid-for ElevenLabs synthesis. When you need both outcomes for one
    endpoint, **vary the request body** (e.g. "hi there" for the success
    case, "auth probe" for the failure); different body, different hash,
    different file.

    The same property means a **polled** endpoint cannot be recorded as a
    sequence: Stable Horde's check URL is identical on every poll, so only
    the last response survives (Task 4). Multi-poll behaviour is tested
    with a local mock instead, deliberately.

    Both limits are consequences of the cross-machine matching guarantee
    above, not defects to be fixed here - changing the key to include
    headers would break fixture portability, which is the more valuable
    property.
    """
    parts = urlsplit(canonical_url)
    readable = re.sub(r"[^a-zA-Z0-9]+", "_", f"{parts.netloc}{parts.path}").strip("_").lower()
    digest = hashlib.sha256(f"{method}|{canonical_url}|{body_hash}".encode("utf-8")).hexdigest()[:16]
    return f"{method.lower()}_{readable}_{digest}"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


#: The run log every outbound call is recorded on, or `None` when no job is
#: running (the normal state for a settings screen probing a provider).
#:
#: A module-level handle set and cleared by the job worker
#: (`avg.api.app`), rather than a parameter: this module has no idea which
#: project it is serving, and threading a project through every provider so
#: that it could find out would be a far worse coupling than one handle whose
#: lifetime the worker owns. Never read by tests, which assert on fixtures.
CURRENT_TAKE_LOG: Any = None


class Transport:
    """Sends one HTTP request, either for real (record mode, retried per
    `POLICY`) or against a recorded fixture (replay mode, never touching
    the network).

    `client` and `sleep` are injectable so tests can exercise the retry
    loop against a fake transport (`httpx.MockTransport`) with instant,
    recorded "sleeps" instead of a real network and real wall-clock delays.
    """

    def __init__(
        self,
        *,
        mode: Mode | None = None,
        fixtures_dir: Path | str | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Default is LIVE. This used to default to `replay`, which meant the
        # shipped app could not make a single real API call: every provider
        # constructs a bare `Transport()` when none is injected, so every
        # generation looked for a test fixture and died with "No recorded
        # fixture for POST https://api.groq.com/...". A default that only
        # made sense inside the test suite had become the product's default.
        #
        # Replay is now opt-in through `AVG_REPLAY_FIXTURES`, and
        # `tests/conftest.py` is what opts in - so the no-live-calls rule is
        # enforced by the test harness, where it belongs, instead of by a
        # default that leaks into the shipped app.
        if mode is not None:
            self.mode: Mode = mode
        elif _env_truthy("AVG_HTTP_RECORD"):
            self.mode = "record"
        elif _env_truthy("AVG_REPLAY_FIXTURES"):
            self.mode = "replay"
        else:
            self.mode = "live"
        if self.mode not in ("live", "record", "replay"):
            raise ValueError(
                f"unknown transport mode {self.mode!r} (expected 'live', 'record' or 'replay')"
            )
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir is not None else DEFAULT_FIXTURES_DIR
        self._owns_client = client is None
        # follow_redirects=False (also httpx's own default here) so a 3xx
        # is returned to the caller as data, never silently chased into a
        # second, unrecorded/unmocked request.
        self._client = client if client is not None else httpx.Client(follow_redirects=False)
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        timeout: float | None = None,
        provider: str = "",
    ) -> httpx.Response:
        """Send one request. Returns an `httpx.Response` in both modes.

        `provider` is passed straight through to `classify()` for
        provider-specific pattern matching (Task 1); it carries no
        provider-specific *logic* here and defaults to `""`, which still
        classifies correctly via the generic status/body rules.
        """
        method_u = method.upper()
        canonical_url = _canonical_url(url)
        _redacted_body, body_hash = _canonical_body(json)
        fixture_path = self.fixtures_dir / f"{_slug_for(method_u, canonical_url, body_hash)}.json"

        if self.mode == "replay":
            return self._replay(fixture_path, method_u, url)

        if provider:
            # `key_fingerprint` tells apart, say, ElevenLabs' primary key
            # from its backup - both share one provider name, so a bare
            # `active_cooldown(provider)` would let one key's cooldown
            # block the other's genuinely separate account.
            cooldown = provider_health.active_cooldown(
                provider, key_fingerprint=provider_health.fingerprint_headers(headers)
            )
            if cooldown is not None:
                raise provider_health.cooldown_error(cooldown)

        started = time.monotonic()
        try:
            response = self._send_with_retry(method_u, url, headers, json, timeout, provider)
        except Exception as exc:
            _log_call(method_u, url, None, started, provider=provider, outcome=str(exc))
            raise
        _log_call(method_u, url, response.status_code, started, provider=provider)
        # Live performs the call and stops there. Only `record` writes:
        # a live run writing fixtures would quietly rewrite the corpus the
        # test suite replays, from a real account, during normal use.
        if self.mode == "record":
            self._write_fixture(
                fixture_path, method_u, canonical_url, _redacted_body, body_hash, headers, response
            )
        return response

    # -- replay: filesystem + JSON only, never the client. ------------------

    def _replay(self, fixture_path: Path, method: str, url: str) -> httpx.Response:
        if not fixture_path.exists():
            raise UnmatchedRequestError(
                f"No recorded fixture for {method} {url} (expected {fixture_path.name} "
                f"in {self.fixtures_dir}). Record it with AVG_HTTP_RECORD=1, or check "
                "whether the request (method, URL, or body) changed since it was recorded."
            )
        data = json_lib.loads(fixture_path.read_text(encoding="utf-8"))
        resp = data["response"]
        content = base64.b64decode(resp["content_b64"]) if resp.get("content_b64") else b""
        return httpx.Response(
            status_code=resp["status_code"],
            headers=resp.get("headers", {}),
            content=content,
            request=httpx.Request(method, url),
        )

    # -- record: real call, POLICY-driven retry, then write. ----------------

    def _send_with_retry(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        json_body: Any,
        timeout: float | None,
        provider: str,
    ) -> httpx.Response:
        kwargs: dict[str, Any] = {"headers": headers, "json": json_body}
        # Only forward `timeout` if the caller actually set one: httpx
        # treats an *explicit* `timeout=None` as "disable timeouts
        # entirely", which is not what "the caller didn't ask for one"
        # should mean. Omitting the kwarg lets the client's own default
        # apply instead.
        if timeout is not None:
            kwargs["timeout"] = timeout

        attempt = 1
        while True:
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                kind = classify(None, "", exc, provider)
                rule = POLICY[kind]
                if rule.retryable and attempt < rule.max_attempts:
                    self._sleep(rule.backoff_seconds * (2 ** (attempt - 1)))
                    attempt += 1
                    continue
                if provider and kind in (ErrorKind.quota, ErrorKind.billing):
                    self._record_cooldown(provider, kind, headers)
                raise AvgError(kind, f"{provider or 'provider'} request failed: {exc}", detail=str(exc)) from exc

            if response.status_code >= 400:
                try:
                    body_text = response.text
                except Exception:
                    body_text = ""
                kind = classify(response.status_code, body_text, None, provider)
                rule = POLICY[kind]
                if rule.retryable and attempt < rule.max_attempts:
                    wait = None
                    if kind == ErrorKind.rate_limit:
                        wait = retry_after_seconds(response.headers, body_text)
                        if wait is not None:
                            wait = min(wait, _rate_limit_wait_ceiling_seconds())
                    self._sleep(wait if wait is not None else rule.backoff_seconds * (2 ** (attempt - 1)))
                    attempt += 1
                    continue
                if provider and kind in (ErrorKind.quota, ErrorKind.billing):
                    self._record_cooldown(provider, kind, headers)
                raise AvgError(
                    kind,
                    f"{provider or 'provider'} request failed with HTTP {response.status_code}",
                    detail=body_text,
                )

            return response

    def _write_fixture(
        self,
        fixture_path: Path,
        method: str,
        canonical_url: str,
        redacted_body: Any,
        body_hash: str,
        headers: dict[str, str] | None,
        response: httpx.Response,
    ) -> None:
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "request": {
                "method": method,
                "url": canonical_url,
                "headers": _scrub_headers(headers),
                "body": redacted_body,
                "body_hash": body_hash,
            },
            "response": {
                "status_code": response.status_code,
                "headers": _drop_stale_transfer_headers(_scrub_headers(dict(response.headers))),
                "content_b64": base64.b64encode(response.content).decode("ascii"),
            },
        }
        fixture_path.write_text(json_lib.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _record_cooldown(self, provider: str, kind: ErrorKind, headers: dict[str, str] | None) -> None:
        """Persist a cooldown after a quota/billing failure. Best-effort -
        a failure to write cooldown state must not turn an already-
        classified provider failure into a different, more confusing one.

        `headers` is this failed request's own headers - fingerprinted so
        the cooldown is scoped to whichever credential actually failed
        (see `provider_health.fingerprint_headers`), not the provider as a
        whole. `None`/no recognised credential header degrades to that
        module's original provider-name-only behaviour, unchanged."""
        with suppress(Exception):
            key_fingerprint = provider_health.fingerprint_headers(headers)
            if kind == ErrorKind.quota:
                provider_health.record_cooldown(
                    provider,
                    "quota",
                    cooldown_until=provider_health.default_quota_cooldown_until(provider),
                    key_fingerprint=key_fingerprint,
                )
            else:
                provider_health.record_cooldown(provider, "billing", key_fingerprint=key_fingerprint)


def _rate_limit_wait_ceiling_seconds() -> float:
    """`resilience_rate_limit_wait_ceiling_sec` (a `SettingSpec`, default
    120). Read fresh on every call, not cached - this only runs on an
    actual rate-limit retry, not a hot path."""
    from avg.config.settings import load_settings  # noqa: PLC0415 - avoids importing settings at module load for every provider caller

    value = load_settings().values.get("resilience_rate_limit_wait_ceiling_sec", 120)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 120.0


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    timeout: float | None = None,
    provider: str = "",
) -> httpx.Response:
    """Module-level convenience wrapper: one-off `Transport`, mode read
    fresh from `AVG_HTTP_RECORD` on every call (so tests can toggle the env
    var per-test without any cached state to reset).

    Providers that make many calls should prefer constructing their own
    `Transport` once and reusing it; this function exists to match the
    brief's flat call shape for simple/one-off use.
    """
    transport = Transport()
    try:
        return transport.request(method, url, headers=headers, json=json, timeout=timeout, provider=provider)
    finally:
        transport.close()


def _log_call(
    method: str, url: str, status: int | None, started: float, *, provider: str, outcome: str = ""
) -> None:
    """Record one outbound call on the run log, if a job owns one.

    Never raises and never blocks anything: a logging failure must not turn a
    successful provider call into a failed one.
    """
    log = CURRENT_TAKE_LOG
    if log is None:
        return
    with suppress(Exception):
        log.call(
            method,
            url,
            status,
            int((time.monotonic() - started) * 1000),
            provider=provider or None,
            outcome=outcome or None,
        )
