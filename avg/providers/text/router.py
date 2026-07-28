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
from typing import Mapping

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
    optional backup. A phase absent from `RouterConfig.phases` entirely
    behaves the same as one with no backup - see `generate()`."""

    primary: ProviderModel
    backup: ProviderModel | None = None


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

    backend = _build_backend(primary.provider, config.transport)
    try:
        text = backend.generate(
            prompt, primary.model, max_tokens=config.max_tokens, temperature=config.temperature
        )
        return TextResult(text=text, provider=primary.provider, model=primary.model)
    except AvgError as exc:
        backup = route.backup if route is not None else None
        if backup is None or POLICY[exc.kind].on_exhausted not in _SWITCH_OUTCOMES:
            raise

        backup_backend = _build_backend(backup.provider, config.transport)
        text = backup_backend.generate(
            prompt, backup.model, max_tokens=config.max_tokens, temperature=config.temperature
        )
        return TextResult(text=text, provider=backup.provider, model=backup.model, used_backup=True)
