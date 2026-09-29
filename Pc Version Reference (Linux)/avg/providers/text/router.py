"""Per-phase text-generation routing with policy-driven fallback.

This is the old app's per-phase-models feature (`Get-PhaseModelConfig` /
`Invoke-AI`, `D:\\AI_Video_Gen\\modules\\ai.ps1` lines 368-513), rebuilt on
top of Task 1's error taxonomy instead of a duplicated keyword classifier.
The legacy `Invoke-AI` decided whether to switch to a phase's backup by
re-running its own quota/auth/model-missing keyword check
(`Get-ApiErrorClassification`) on the failure message; this module makes
that same decision by looking `POLICY[exc.kind].on_exhausted` up in Task
1's table - the *only* thing consulted. Nothing here inspects a status
code, a response body, or an error string; a backend already reduced the
failure to an `ErrorKind` before this module ever sees it.

Scope, deliberately narrow (see the module's home in the brief's Code
Organization note - "routing and fallback only, no HTTP, no provider
knowledge beyond names"):

  - Resolve `phase`'s configured primary provider+model, falling back to
    `config.default` if the phase has no override - mirrors
    `Get-PhaseModelConfig`'s HasPrimary flag.
  - Call it. On success, done.
  - On an `AvgError`, ask `POLICY` whether switching provider/key/model
    could plausibly help (`on_exhausted in {"switch_key", "switch_provider",
    "switch_model"}`). If so AND the phase has a configured backup, try the
    backup exactly once - matching the legacy `$triedFallback` flag, which
    never chains a second fallback after the first.  Otherwise (no backup,
    or `on_exhausted == "fail"` - content_filter/bug/dependency, where a
    different provider would fail identically) the original error
    propagates.

Deliberately NOT ported: the legacy per-provider *fallback key* (a second
key for the same provider, tried inline inside each `Invoke-*Backend`
function before this router ever saw a failure). `avg.config.secrets`
exposes one key per provider with no fallback-key concept, and this task's
brief describes only the provider+model phase-backup feature as in scope -
see `avg/providers/text/groq.py`'s module docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from avg.core.errors import AvgError
from avg.providers.errors import POLICY
from avg.providers.text.base import TextBackend
from avg.providers.text.gemini import GeminiBackend
from avg.providers.text.community import GroqBackend, HuggingFaceBackend, PollinationsBackend
from avg.providers.transport import Transport


@dataclass(frozen=True, slots=True)
class ProviderModel:
    """One concrete generation target: a provider name plus its model id."""

    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class PhaseRoute:
    """A pipeline phase's configured routing: a required primary, an
    optional backup, and what to do when both are exhausted. A phase absent
    from `RouterConfig.phases` entirely behaves the same as one with no
    backup and `on_failure="stop"` - see `generate()`.

    `on_failure` is the mockup's "If both fail" column
    (`F:\\Downloads\\organic_night_v2.html`, `MODEL_ROWS`). It offers three
    labels - `stop`, `switch provider`, `fall back` - but only two distinct
    behaviours exist here, so only two are modelled:

      - `"stop"`: the failure propagates. This is the pre-existing
        behaviour and stays the default, so a `PhaseRoute` built without
        thinking about this field behaves exactly as it did before.
      - `"fall_back"`: `RouterConfig.default` is tried as a last resort,
        once, if it is neither the primary nor the backup.

    The mockup's third label, "switch provider", would mean "try the
    default only when it names a different provider" - which is what
    `fall_back` already does in every case where it could differ, since a
    default that matches the primary is skipped. A separate value would be
    a distinction the router cannot act on differently.
    """

    primary: ProviderModel
    backup: ProviderModel | None = None
    on_failure: Literal["stop", "fall_back"] = "stop"


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """Everything `generate()` needs to route and call a backend.

    `default` is used for any phase with no entry in `phases` - the
    legacy app's "no phase override configured, use the global primary"
    behaviour. `transport`, when given, is passed to every backend this
    call constructs; its own mode/fixtures_dir decide record vs. replay,
    matching how every other test in this codebase injects a Transport
    rather than the router doing HTTP itself.
    """

    default: ProviderModel
    phases: Mapping[str, PhaseRoute] = field(default_factory=dict)
    max_tokens: int = 1024
    temperature: float = 0.7
    transport: Transport | None = None


@dataclass(frozen=True, slots=True)
class TextResult:
    """What `generate()` hands back: the text plus which target produced it."""

    text: str
    provider: str
    model: str
    used_backup: bool = False


_BACKENDS: dict[str, type[TextBackend]] = {
    "groq": GroqBackend,
    "gemini": GeminiBackend,
    "huggingface": HuggingFaceBackend,
    "pollinations": PollinationsBackend,
}

# POLICY.on_exhausted values for which trying a *different* provider/model
# could plausibly change the outcome. "fail" (content_filter, bug,
# dependency) means the same request would fail identically anywhere else,
# so a configured backup is never even attempted for those kinds.
_SWITCH_OUTCOMES = {"switch_key", "switch_provider", "switch_model"}


def _build_backend(provider: str, transport: Transport | None) -> TextBackend:
    cls = _BACKENDS.get(provider)
    if cls is None:
        raise ValueError(f"unknown text provider {provider!r} (known: {sorted(_BACKENDS)})")
    return cls(transport=transport)


def generate(prompt: str, phase: str, *, config: RouterConfig) -> TextResult:
    """Generate text for one pipeline phase (`brief` | `script` | `director1` | `director2`).

    Resolves the phase's configured primary provider+model, falls back to
    `config.default` if unset, and calls it. On a provider failure whose
    `POLICY[kind].on_exhausted` says switching provider/key/model could
    help, retries once against the phase's configured backup, if any -
    see this module's docstring for the exact rule. Any other failure (no
    backup configured, or a `POLICY[kind].on_exhausted == "fail"` kind)
    propagates the original `AvgError` unchanged.
    """
    route = config.phases.get(phase)
    primary = route.primary if route is not None else config.default

    # The ordered attempt list. Everything after the first entry is only
    # ever reached when POLICY says switching could help - see the loop.
    attempts = [primary]
    if route is not None:
        if route.backup is not None:
            attempts.append(route.backup)
        if route.on_failure == "fall_back" and config.default not in attempts:
            attempts.append(config.default)

    last = len(attempts) - 1
    for index, target in enumerate(attempts):
        backend = _build_backend(target.provider, config.transport)
        try:
            text = backend.generate(
                prompt, target.model, max_tokens=config.max_tokens, temperature=config.temperature
            )
        except AvgError as exc:
            # Stop on the final attempt, and stop early on any kind a
            # different provider/model would fail identically for.
            if index == last or POLICY[exc.kind].on_exhausted not in _SWITCH_OUTCOMES:
                raise
            continue
        return TextResult(
            text=text, provider=target.provider, model=target.model, used_backup=index > 0
        )
    raise AssertionError("unreachable: the final attempt either returns or raises")


# ── Building a config from settings ────────────────

# One `provider/model` slot per phase, plus the shared default. Deliberately
# a plain mapping rather than an import of `avg.core.settings_schema`: this
# module sits under `avg/providers/` and must not learn about the settings
# catalogue, so the caller passes the channel's own settings dict in.
_TEXT_PHASES = ("brief", "script_draft", "script_segmentation", "director1", "director2")


def parse_target(raw: str) -> ProviderModel | None:
    """Parse a `"provider/model"` settings string. Empty/blank means unset.

    Raises `ValueError` on a non-empty value that is not exactly one
    provider, one `/`, and a non-empty model id - a silently ignored typo
    here would route a phase somewhere the user did not choose.
    """
    text = raw.strip()
    if not text:
        return None
    provider, sep, model = text.partition("/")
    provider, model = provider.strip(), model.strip()
    if not sep or not provider or not model:
        raise ValueError(f"{raw!r} is not a 'provider/model' pair")
    if provider not in _BACKENDS:
        raise ValueError(f"unknown text provider {provider!r} (known: {sorted(_BACKENDS)})")
    return ProviderModel(provider=provider, model=model)


def config_from_settings(
    settings: Mapping[str, Any],
    *,
    fallback_default: str,
    transport: Transport | None = None,
) -> RouterConfig:
    """Build a `RouterConfig` from a channel's settings dict.

    Reads `text_model_default` plus, per text phase, `<phase>_model`,
    `<phase>_backup` and `<phase>_on_failure` (see
    `avg.core.settings_schema`'s `models` group). A phase whose `_model` is
    blank simply gets no entry, which `generate()` already treats as "use
    the default" - so an unconfigured channel routes everything to one
    model rather than failing.

    `fallback_default` is used when the channel document has no
    `text_model_default` key at all, which is every channel written before
    that setting existed. The caller passes the catalogue's own default
    (`get_spec("text_model_default").default`) rather than this module
    importing `avg.core.settings_schema`: nothing under `avg/providers/`
    knows about the settings catalogue, and a hardcoded copy here would be
    a second source of truth for that default.

    `script_draft` and `script_segmentation` are router phase keys, not
    pipeline phase keys: `ScriptPhase` makes two model calls and names them
    separately here so a cheap model can segment what a strong one wrote.
    `avg.pipeline.registry` still has exactly one `script` phase.
    """
    default = parse_target(str(settings.get("text_model_default") or fallback_default))
    if default is None:
        raise ValueError("text_model_default is not set — no default text model to route to")

    phases: dict[str, PhaseRoute] = {}
    for phase in _TEXT_PHASES:
        primary = parse_target(str(settings.get(f"{phase}_model") or ""))
        if primary is None:
            continue
        on_failure = str(settings.get(f"{phase}_on_failure") or "stop")
        phases[phase] = PhaseRoute(
            primary=primary,
            backup=parse_target(str(settings.get(f"{phase}_backup") or "")),
            on_failure="fall_back" if on_failure == "fall_back" else "stop",
        )
    return RouterConfig(default=default, phases=phases, transport=transport)
