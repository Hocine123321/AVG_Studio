"""Provider error classification and retry policy.

One classifier (`classify`) and one policy table (`POLICY`) for every
provider failure in the app. This replaces eleven overlapping PowerShell
functions spread across three modules in the old app (`Get-ApiErrorClassification`,
`Get-ApiErrorMessage`, `Get-CompactApiErrorMessage`, `Test-ImageQuotaError`,
`Test-ImageBillingError`, `Test-HardImageProviderError`, `Get-ElevenLabsErrorMessage`,
`Resolve-AppError`, `Write-AppError`, `Write-CompactError`, `Get-HttpErrorBody`),
where every provider call site re-derived what a 429 meant. The cost of that
fragmentation was real: ElevenLabs backup-key behaviour took two releases to
get right because "does a different key help here?" lived in more than one
place and drifted out of sync.

Two rules keep that from happening again:

  1. `classify()` is the ONLY place a status code or body string is examined
     anywhere in this app. Providers report what happened (status, body,
     exception); this module decides what it means. No other module should
     grep an error string or branch on an HTTP status.
  2. `POLICY` is data, not code: a plain dict keyed by `ErrorKind`. A caller
     reaches a retry decision by looking a kind up in this table, never by
     re-inspecting the failure itself.

`quota` and `billing` are kept as distinct `ErrorKind` values on purpose: a
quota may reset on its own (a daily/monthly cap); a billing failure will not
resolve until the user's payment method changes. Both happen to be
non-retryable with the same `switch_provider` outcome today, but a future
"try again later" affordance for quota — and never for billing — depends on
the two staying distinguishable all the way through. Collapsing them back
into one bucket (as the old `IsQuotaRate` flag did) is the exact mistake
this module exists to undo.

This module is pure: no HTTP calls, no provider SDKs, no I/O, no imports
beyond the standard library and `avg.core.errors`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from avg.core.errors import ErrorKind

OnExhausted = Literal["fail", "switch_key", "switch_provider", "switch_model"]


@dataclass(frozen=True, slots=True)
class RetryRule:
    """What to do about one `ErrorKind`, independent of which provider raised it."""

    retryable: bool
    max_attempts: int
    backoff_seconds: float
    on_exhausted: OnExhausted


# The policy, stated once — see .superpowers/sdd/s3-task-1-brief.md's table.
# retryable / on_exhausted are taken verbatim from that table. The brief
# specifies "yes, exponential backoff" / "yes" / "no" rather than exact
# numbers, so max_attempts / backoff_seconds are this implementation's only
# free choice, made conservatively:
#   - rate_limit gets the deepest budget with a real exponential base: a 429
#     is the one failure genuinely expected to clear if given enough room.
#   - network / timeout get a short, fixed retry — enough to ride out a
#     blip, not enough to mask a truly dead endpoint before switch_provider.
#   - every non-retryable kind is a single attempt: there is nothing to gain
#     from calling the same provider again, so max_attempts=1/backoff=0.0
#     just means "decide immediately."
POLICY: dict[ErrorKind, RetryRule] = {
    ErrorKind.rate_limit: RetryRule(retryable=True, max_attempts=5, backoff_seconds=2.0, on_exhausted="switch_provider"),
    ErrorKind.network: RetryRule(retryable=True, max_attempts=3, backoff_seconds=1.0, on_exhausted="switch_provider"),
    ErrorKind.timeout: RetryRule(retryable=True, max_attempts=3, backoff_seconds=1.0, on_exhausted="switch_provider"),
    ErrorKind.auth: RetryRule(retryable=False, max_attempts=1, backoff_seconds=0.0, on_exhausted="switch_key"),
    ErrorKind.quota: RetryRule(retryable=False, max_attempts=1, backoff_seconds=0.0, on_exhausted="switch_provider"),
    ErrorKind.billing: RetryRule(retryable=False, max_attempts=1, backoff_seconds=0.0, on_exhausted="switch_provider"),
    ErrorKind.model_missing: RetryRule(retryable=False, max_attempts=1, backoff_seconds=0.0, on_exhausted="switch_model"),
    ErrorKind.content_filter: RetryRule(retryable=False, max_attempts=1, backoff_seconds=0.0, on_exhausted="fail"),
    ErrorKind.bug: RetryRule(retryable=False, max_attempts=1, backoff_seconds=0.0, on_exhausted="fail"),
    ErrorKind.dependency: RetryRule(retryable=False, max_attempts=1, backoff_seconds=0.0, on_exhausted="fail"),
}
# NOTE: ErrorKind.resource has no row here, deliberately. It is not a
# provider failure — it is avg.media.engine.classify_exit's kind for a local
# render child killed by the OS (OOM), a different subsystem this task does
# not own. classify() below never returns it.


def _rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# Checked top-to-bottom; first match wins. Distinguishing quota from billing
# is the reason this table exists (see module docstring), so billing (a
# payment problem) is checked before quota (a cap that resets on its own) —
# a body that mentions both loses to billing, which is the more actionable,
# more specific claim.
_CHECK_ORDER: tuple[ErrorKind, ...] = (
    ErrorKind.content_filter,
    ErrorKind.auth,
    ErrorKind.billing,
    ErrorKind.quota,
    ErrorKind.model_missing,
    ErrorKind.rate_limit,
    ErrorKind.network,
    ErrorKind.timeout,
)

# Patterns that hold regardless of which provider sent them. Every pattern
# here was seen (or is a direct synonym of something seen) in the legacy
# PowerShell classifiers during real production failures: `Get-
# ApiErrorClassification`'s pattern lists (modules/utilities.ps1) and
# `$script:QuotaKeywords` (modules/ai.ps1).
_GENERIC: dict[ErrorKind, tuple[re.Pattern[str], ...]] = {
    ErrorKind.content_filter: _rx(
        r"content[_ ]filter", r"content polic", r"content moderation",
        r"moderation flagged", r"safety system", r"blocked by safety",
        r"harmful content", r"violates.*polic", r"policy violation",
        r"prohibited_content", r"\bcensored\b", r"flagged.*safety",
    ),
    ErrorKind.auth: _rx(
        r"\b401\b", r"\b403\b", r"unauthori[sz]ed", r"invalid api key",
        r"invalid auth", r"incorrect api key", r"wrong api key",
        r"api key invalid", r"authentication failed", r"auth failed",
        r"access denied", r"\bforbidden\b", r"permission_denied",
        r"invalid token", r"expired token", r"invalid_api_key",
    ),
    ErrorKind.billing: _rx(
        # OpenAI's actual error code for "you are out of paid credits" is
        # literally named insufficient_quota, despite meaning billing, not
        # quota — exactly the collapse this module exists to undo.
        r"insufficient_quota", r"\bbilling\b", r"payment required",
        r"payment method", r"credit card", r"check your plan and billing",
        r"trial expired", r"\b402\b",
    ),
    ErrorKind.quota: _rx(
        r"quota exceeded", r"quota_exceeded", r"resource_exhausted",
        r"daily limit", r"monthly limit", r"weekly limit", r"usage limit",
        r"requests per (day|min)", r"tokens per min", r"\btpm\b", r"\brpm\b",
    ),
    ErrorKind.model_missing: _rx(
        r"model_not_found", r"model not found", r"no such model",
        r"unsupported model", r"invalid model", r"model .* not found",
        r"is not a valid model", r"voice_not_found", r"voice.*not found",
        # Context/token-length errors are grouped here on purpose, following
        # the legacy classifier's documented reasoning: they are fixed by
        # switching to a model with a bigger context window, i.e. the exact
        # action `POLICY[model_missing].on_exhausted` ("switch_model")
        # already prescribes — not because a model is literally absent.
        r"context length", r"context_length", r"maximum context",
        r"context window", r"max_tokens", r"exceeds.*context",
    ),
    ErrorKind.rate_limit: _rx(
        r"rate[_ -]?limit", r"too many requests", r"\b429\b", r"throttled",
        r"slow down", r"rate exceeded",
    ),
    ErrorKind.network: _rx(
        r"\bconnection\b", r"\bconnect\b", r"\bnetwork\b", r"unreachable",
        r"\b50[0-4]\b", r"bad gateway", r"service unavailable",
        r"gateway timeout", r"reset by peer", r"refused",
    ),
    ErrorKind.timeout: _rx(
        r"\btimeout\b", r"timed out",
    ),
}

# Provider-specific vocabulary the generic table above cannot know: each
# provider phrases quota vs. billing (and model-missing, and content safety)
# differently, and that per-provider phrasing is the real work this module
# does — see module docstring. Only distinctions actually grounded in the
# legacy code or the provider's documented API shape are listed; gaps are
# left as comments rather than guessed at.
_PROVIDER: dict[str, dict[ErrorKind, tuple[re.Pattern[str], ...]]] = {
    "huggingface": {
        # Legacy Test-HardImageProviderError / Invoke-HuggingFaceImageGeneration:
        # a served model that's gone or renamed surfaces as one of these
        # phrases, not as a bare 4xx (hf-inference routes to third-party
        # backends, so the failure is "no route to this model", not auth).
        ErrorKind.model_missing: _rx(
            r"not supported by provider", r"no longer supported",
            r"no inference provider", r"deprecated",
        ),
        # Not found in the legacy PS code (it never distinguished HF
        # quota/billing) — added from the current HF Inference Providers
        # API's own wording. Flagged in the task report for verification
        # against a live account if this path ever misfires.
        ErrorKind.billing: _rx(
            r"exceeded.*(monthly|included).*credit", r"upgrade.*pro plan",
        ),
    },
    "pollinations": {
        # Pollinations' own credit currency is literally called "pollen" —
        # legacy $script:QuotaKeywords (ai.ps1) carried it as a bare
        # keyword. Running out of pollen needs a top-up, not a wait.
        ErrorKind.billing: _rx(r"\bpollen\b", r"credit balance", r"insufficient.*credit"),
    },
    "stablehorde": {
        # `censored` is Stable Horde's own explicit, unambiguous flag —
        # checked separately from the generic `faulted` flag in the legacy
        # code (`if ($gen.censored)` was its own branch). A real content
        # decision, not a guess. (Already covered by _GENERIC too; kept
        # here as the documented per-provider source of that pattern.)
        ErrorKind.content_filter: _rx(r"\bcensored\b"),
        # Deliberately NOT mapping "no workers available or content
        # blocked" (the legacy `$check.faulted` message) to any specific
        # kind: the upstream API folds two different causes — no capacity,
        # vs. a content decision — into one flag with no way to tell them
        # apart. Stable Horde is also a free community queue with no
        # billing concept at all in the legacy code. Guessing wrong here
        # (in either direction — retrying a real content block, or failing
        # a transient capacity gap) is worse than the safe default: this
        # message falls through to `ErrorKind.bug` (see classify()).
    },
    "elevenlabs": {
        # ElevenLabs' JSON error body carries a machine-readable
        # detail.status/detail.code (see the legacy Get-ElevenLabsErrorMessage,
        # which surfaces detail.message/detail.code verbatim) — these are
        # that vocabulary.
        ErrorKind.auth: _rx(r"invalid_api_key"),
        ErrorKind.quota: _rx(r"quota_exceeded"),
        ErrorKind.model_missing: _rx(r"voice_not_found"),
        ErrorKind.network: _rx(r"system_busy"),
    },
    "gemini": {
        # Gemini's generateContent blocks content via finishReason /
        # promptFeedback.blockReason rather than an HTTP status, so a bare
        # "safety" is a strong signal here — but only here: elsewhere it is
        # too common an English word to trust as a generic pattern.
        ErrorKind.content_filter: _rx(r"\bsafety\b", r"prohibited_content", r"blockreason"),
        ErrorKind.auth: _rx(r"permission_denied"),
    },
    "openai": {
        # No OpenAI-specific entries: insufficient_quota (its actual
        # billing signal) is kept in _GENERIC since other providers/relays
        # can surface the identical literal string.
    },
    "groq": {
        # No quota-vs-billing string was found for Groq in the legacy code
        # — it went through the same generic $script:QuotaKeywords bucket
        # as every other text backend. Generic patterns apply as-is.
    },
}


def _provider_key(provider: str) -> str:
    return re.sub(r"[^a-z0-9]", "", provider.strip().lower())


def _match(text: str, provider_key: str, kind: ErrorKind) -> bool:
    patterns = _PROVIDER.get(provider_key, {}).get(kind, ()) + _GENERIC.get(kind, ())
    return any(p.search(text) for p in patterns)


def _classify_exception(exc: Exception) -> ErrorKind:
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    if "import" in name or "modulenotfound" in name:
        return ErrorKind.dependency
    if "timeout" in name or "timeout" in text or "timed out" in text:
        return ErrorKind.timeout
    if (
        any(k in name for k in ("connection", "connect", "gaierror", "urlerror", "oserror"))
        or any(k in text for k in ("connection", "network", "unreachable", "refused"))
    ):
        return ErrorKind.network
    return ErrorKind.bug


def classify(status: int | None, body: str, exc: Exception | None, provider: str) -> ErrorKind:
    """Classify one provider failure into an `ErrorKind`.

    `status` is the HTTP status code if there was an HTTP response (`None`
    if the call never got one — e.g. a DNS failure). `body` is the raw
    response text, possibly empty. `exc` is the exception the call raised,
    if any. `provider` is a free-text provider name ("OpenAI", "Stable
    Horde", "elevenlabs", ...) — matching is case- and whitespace/
    punctuation-insensitive.

    Never raises, and never echoes `body` anywhere — it is only pattern-
    matched, never logged or included in the returned value. An input this
    function doesn't recognise degrades to `ErrorKind.bug` (the
    non-retryable, fail-and-surface kind) rather than guessing at a more
    specific — and possibly wrong — classification.
    """
    provider_key = _provider_key(provider or "")
    text = body or (str(exc) if exc else "")

    # Two rules the brief states as flat facts, not defaults to be
    # overridden: "A 5xx is network", "A 401/403 is auth". Checked before
    # any body text so a coincidental keyword on a 500 page can't relabel
    # a plain server error.
    if status is not None:
        if status in (401, 403):
            return ErrorKind.auth
        if 500 <= status < 600:
            return ErrorKind.network

    # Body text next — this is what tells a 429 (or a 400, or a 200-with-
    # an-error-envelope) apart into quota vs. billing vs. model_missing vs.
    # content_filter vs. plain rate_limit. Provider-specific phrasing is
    # merged in ahead of the generic guesses (see _match / _PROVIDER).
    if text:
        for kind in _CHECK_ORDER:
            if _match(text, provider_key, kind):
                return kind

    # Remaining status defaults, once no body text said anything more
    # specific: "A 429 is rate_limit" (the brief's stated default, not a
    # hard rule — body text above already had first refusal). 402/404 are
    # standard HTTP semantics (payment required / not found) with no
    # provider counter-evidence found during research.
    if status is not None:
        if status == 429:
            return ErrorKind.rate_limit
        if status == 402:
            return ErrorKind.billing
        if status == 404:
            return ErrorKind.model_missing
        # Any other recognised-but-unmapped status: fail safe, don't guess.
        return ErrorKind.bug

    # No HTTP response at all — classify from the exception itself
    # (network/timeout/dependency), if there is one.
    if exc is not None:
        return _classify_exception(exc)

    return ErrorKind.bug
