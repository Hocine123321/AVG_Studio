import pytest

from avg.core.errors import ErrorKind
from avg.providers.errors import POLICY, RetryRule, classify

# ---------------------------------------------------------------------------
# POLICY — every row of the brief's table, verbatim.
# ---------------------------------------------------------------------------


def test_policy_covers_exactly_the_briefs_ten_kinds():
    # ErrorKind.resource is intentionally absent: it belongs to
    # avg.media.engine.classify_exit (a local OOM-kill classification), not
    # to a provider failure, and classify() below never returns it.
    expected = {
        ErrorKind.rate_limit, ErrorKind.network, ErrorKind.timeout,
        ErrorKind.auth, ErrorKind.quota, ErrorKind.billing,
        ErrorKind.model_missing, ErrorKind.content_filter,
        ErrorKind.bug, ErrorKind.dependency,
    }
    assert set(POLICY.keys()) == expected
    assert ErrorKind.resource not in POLICY


@pytest.mark.parametrize(
    "kind,retryable,on_exhausted",
    [
        (ErrorKind.rate_limit, True, "switch_provider"),
        (ErrorKind.network, True, "switch_provider"),
        (ErrorKind.timeout, True, "switch_provider"),
        (ErrorKind.auth, False, "switch_key"),
        (ErrorKind.quota, False, "switch_provider"),
        (ErrorKind.billing, False, "switch_provider"),
        (ErrorKind.model_missing, False, "switch_model"),
        (ErrorKind.content_filter, False, "fail"),
        (ErrorKind.bug, False, "fail"),
        (ErrorKind.dependency, False, "fail"),
    ],
)
def test_policy_row_matches_brief_table(kind, retryable, on_exhausted):
    rule = POLICY[kind]
    assert isinstance(rule, RetryRule)
    assert rule.retryable is retryable
    assert rule.on_exhausted == on_exhausted


def test_retryable_kinds_have_a_real_attempt_budget_and_non_retryable_dont():
    for kind, rule in POLICY.items():
        if rule.retryable:
            assert rule.max_attempts > 1
        else:
            assert rule.max_attempts == 1
            assert rule.backoff_seconds == 0.0


def test_rate_limit_gets_exponential_backoff_deeper_than_network_and_timeout():
    # Brief: rate_limit = "yes, exponential backoff"; network/timeout = "yes"
    # (plain). Not identical treatment — rate_limit is the one failure
    # genuinely expected to clear if given more room.
    rl = POLICY[ErrorKind.rate_limit]
    net = POLICY[ErrorKind.network]
    assert rl.max_attempts > net.max_attempts
    assert rl.backoff_seconds > 0


# ---------------------------------------------------------------------------
# classify() — status-code hard rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_is_always_auth(status):
    assert classify(status, "", None, "openai") == ErrorKind.auth
    # Even with an unrelated/misleading body, per the brief's flat rule.
    assert classify(status, "quota exceeded", None, "openai") == ErrorKind.auth


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_is_always_network(status):
    assert classify(status, "", None, "gemini") == ErrorKind.network
    assert classify(status, "internal server error", None, "gemini") == ErrorKind.network


def test_429_defaults_to_rate_limit_with_no_more_specific_body():
    assert classify(429, "too many requests", None, "groq") == ErrorKind.rate_limit
    assert classify(429, "", None, "groq") == ErrorKind.rate_limit


def test_402_defaults_to_billing():
    assert classify(402, "", None, "openai") == ErrorKind.billing


def test_404_defaults_to_model_missing():
    assert classify(404, "", None, "openai") == ErrorKind.model_missing


def test_unrecognised_status_degrades_to_bug_without_raising():
    assert classify(418, "I'm a teapot", None, "openai") == ErrorKind.bug
    assert classify(999, "", None, "unknown-provider") == ErrorKind.bug


def test_no_status_no_body_no_exc_degrades_to_bug():
    assert classify(None, "", None, "openai") == ErrorKind.bug


# ---------------------------------------------------------------------------
# classify() — per-provider quota vs. billing (the distinction that matters)
# ---------------------------------------------------------------------------


def test_openai_insufficient_quota_is_billing_not_quota():
    # OpenAI's real error code for "out of paid credits" is literally named
    # insufficient_quota, despite meaning billing. This is the exact
    # collapse this module exists to prevent.
    body = '{"error": {"code": "insufficient_quota", "message": "You exceeded your current quota, please check your plan and billing details."}}'
    assert classify(429, body, None, "openai") == ErrorKind.billing


def test_openai_plain_daily_limit_is_quota_not_billing():
    body = '{"error": {"message": "You have hit the daily limit for this model."}}'
    assert classify(429, body, None, "openai") == ErrorKind.quota


def test_openai_plain_rate_limit_message_is_rate_limit():
    body = '{"error": {"message": "Rate limit reached for requests"}}'
    assert classify(429, body, None, "openai") == ErrorKind.rate_limit


def test_gemini_resource_exhausted_alone_is_quota():
    # Free-tier Gemini caps reset on their own — a quota, not a billing
    # failure.
    body = '{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded."}}'
    assert classify(429, body, None, "gemini") == ErrorKind.quota


def test_gemini_resource_exhausted_with_billing_mention_is_billing():
    body = '{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Check your plan and billing details."}}'
    assert classify(429, body, None, "gemini") == ErrorKind.billing


def test_gemini_safety_finish_reason_is_content_filter():
    body = '{"candidates": [{"finishReason": "SAFETY"}], "promptFeedback": {"blockReason": "SAFETY"}}'
    assert classify(200, body, None, "gemini") == ErrorKind.content_filter


def test_gemini_permission_denied_is_auth():
    body = '{"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "no"}}'
    assert classify(403, body, None, "gemini") == ErrorKind.auth


def test_huggingface_model_unavailable_is_model_missing():
    body = "Model black-forest-labs/FLUX.1-dev is not supported by provider hf-inference"
    assert classify(404, body, None, "huggingface") == ErrorKind.model_missing


def test_huggingface_monthly_credits_is_billing():
    body = "You have exceeded your monthly included credits, upgrade to a PRO plan to continue."
    assert classify(402, body, None, "huggingface") == ErrorKind.billing


def test_pollinations_pollen_is_billing():
    body = "Insufficient pollen: your credit balance is too low for this request."
    assert classify(402, body, None, "pollinations") == ErrorKind.billing


def test_pollinations_generic_rate_limit_is_rate_limit():
    body = "Too many requests, please slow down."
    assert classify(429, body, None, "pollinations") == ErrorKind.rate_limit


def test_stablehorde_censored_is_content_filter():
    body = "Stable Horde: image was censored. Try a different prompt or enable NSFW in settings."
    assert classify(None, body, None, "stablehorde") == ErrorKind.content_filter


def test_stablehorde_ambiguous_faulted_message_degrades_to_bug():
    # The upstream `faulted` flag bundles two different causes (no capacity
    # vs. a content decision) with no way to tell them apart. Guessing
    # wrong in either direction is worse than the safe, non-retrying
    # default.
    body = "Stable Horde: generation faulted (no workers available or content blocked)."
    assert classify(None, body, None, "stablehorde") == ErrorKind.bug


def test_stablehorde_provider_name_variants_normalise():
    body = "Stable Horde: image was censored."
    for name in ("Stable Horde", "stablehorde", "STABLE-HORDE", " stable_horde "):
        assert classify(None, body, None, name) == ErrorKind.content_filter


def test_elevenlabs_invalid_api_key_is_auth():
    body = '{"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}'
    assert classify(401, body, None, "elevenlabs") == ErrorKind.auth


def test_elevenlabs_quota_exceeded_is_quota_not_billing():
    # Subscription character quota resets on renewal — a quota, not a
    # payment failure.
    body = '{"detail": {"status": "quota_exceeded", "message": "You have used all of your allotted characters."}}'
    assert classify(400, body, None, "elevenlabs") == ErrorKind.quota


def test_elevenlabs_voice_not_found_is_model_missing():
    body = '{"detail": {"status": "voice_not_found", "message": "A voice for the voice_id was not found."}}'
    assert classify(404, body, None, "elevenlabs") == ErrorKind.model_missing


def test_elevenlabs_system_busy_is_network():
    body = '{"detail": {"status": "system_busy", "message": "The system is busy, please retry."}}'
    assert classify(503, body, None, "elevenlabs") == ErrorKind.network


def test_groq_has_no_provider_specific_quota_billing_split_uses_generic():
    # No distinct Groq quota/billing string was found in the legacy code —
    # the generic patterns are what carry Groq.
    assert classify(429, "quota exceeded, daily limit reached", None, "groq") == ErrorKind.quota
    assert classify(429, "rate limit exceeded", None, "groq") == ErrorKind.rate_limit


# ---------------------------------------------------------------------------
# classify() — exception-only failures (no HTTP response at all)
# ---------------------------------------------------------------------------


def test_timeout_error_with_no_status_is_timeout():
    assert classify(None, "", TimeoutError("timed out"), "gemini") == ErrorKind.timeout


def test_bare_timeout_error_with_empty_message_is_still_timeout():
    assert classify(None, "", TimeoutError(), "gemini") == ErrorKind.timeout


def test_connection_error_with_no_status_is_network():
    assert classify(None, "", ConnectionError("Connection refused"), "openai") == ErrorKind.network


def test_import_error_is_dependency():
    assert classify(None, "", ModuleNotFoundError("No module named 'elevenlabs'"), "elevenlabs") == ErrorKind.dependency


def test_unrecognised_exception_degrades_to_bug():
    assert classify(None, "", ValueError("something odd"), "openai") == ErrorKind.bug


# ---------------------------------------------------------------------------
# classify() — never raises, never returns ErrorKind.resource
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,body,exc,provider",
    [
        (None, "", None, ""),
        (0, "", None, "totally-unknown-provider"),
        (-1, "garbage \x00 bytes", None, "openai"),
        (200, "", RuntimeError("boom"), "gemini"),
    ],
)
def test_classify_never_raises(status, body, exc, provider):
    result = classify(status, body, exc, provider)
    assert isinstance(result, ErrorKind)


def test_classify_never_returns_resource():
    # ErrorKind.resource is out of scope for provider failures — it belongs
    # to the local render engine's OOM classification.
    samples = [
        (401, "", None, "openai"),
        (429, "insufficient_quota billing", None, "openai"),
        (503, "system_busy", None, "elevenlabs"),
        (None, "out of memory killed", RuntimeError("oom"), "gemini"),
        (999, "nonsense", None, "?"),
    ]
    for status, body, exc, provider in samples:
        assert classify(status, body, exc, provider) != ErrorKind.resource
