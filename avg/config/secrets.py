r"""Per-user-protected storage for provider API keys.

The Windows build (`main`/`development`) encrypts keys with DPAPI
(`CryptProtectData`, via `ctypes`) - see that build's `avg/config/
secrets.py` for the untouched original. There is no DPAPI equivalent
syscall on Linux, so this fork instead generates a random Fernet key
(`cryptography.fernet`), stores THAT key in the desktop's OS-level secret
storage via the `keyring` package (Secret Service - GNOME Keyring, or
KWallet in Secret-Service-compatibility mode, which is what a KDE Plasma
session normally runs), and uses it to encrypt every value the same way
DPAPI did on Windows. The net properties are the ones DPAPI gave the
Windows build: the actual key never touches this app's own files, is
scoped to the logged-in user's session, and a locked/unavailable keyring
fails closed (returns `None`) rather than prompting a UI this
headless-looking call has no context to handle. See
`_linux_encryption_key` for the retrieval/creation logic. Full rationale
and the Windows-side comparison: `docs/linux-port/WINDOWS_VS_LINUX.md`.

The on-disk shape is unchanged from the Windows build -
`.../Secrets/secrets.dat`, JSON of provider -> base64 ciphertext, from
`avg.config.user_assets.default_secrets_path` by way of
`default_secrets_path` below - only what `_protect`/`_unprotect` do with
the bytes is different.

Distinct from `avg.config.settings`: that module is a small trusted
document the app freely reads/writes/logs. This one holds live credentials.
Every function here treats a "key" as something that must never reach a
log line, an exception message, or a `repr()` - only *whether* a provider
has a key is safe to surface, never the value.

One responsibility: protect, unprotect, store, retrieve. No HTTP, no UI,
no provider-specific logic, no caching, no key rotation - nothing here asks
for those.

**Not ported**: `read_legacy_secrets`, which read the old PowerShell
app's DPAPI-encrypted `D:\AI_Video_Gen\config\secrets.json` for migration
purposes. It had no callers anywhere in this app even on Windows (dead
code there too), and on Linux it is doubly meaningless: there is no
`D:\` drive to find that file on, and even if the bytes were somehow
present, this module's `_unprotect` is Fernet, not DPAPI, so it could
never decrypt a real legacy blob regardless of path. Left out rather than
kept as an inert stub - see the divergence doc if a future feature needs
migrating a *Linux* legacy install and wants the shape this was.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from avg.config.user_assets import user_assets_root

# Where the Fernet key itself lives inside the OS keyring - not a
# provider name, so it can never collide with one in the same collection.
_KEYRING_SERVICE = "AVG Studio"
_KEYRING_USERNAME = "secrets-encryption-key"


def _linux_encryption_key() -> bytes | None:
    """Fetch (generating on first use) the Fernet key that protects every
    value in the on-disk store, from the desktop's OS-level secret storage
    via the `keyring` package - Secret Service (GNOME Keyring) or
    KWallet's Secret-Service-compatibility mode, whichever the session
    provides. `None` if no backend is reachable (headless session,
    keyring locked, package not installed) - callers must fail closed,
    never raise past this function.

    A separate seam from `_protect`/`_unprotect` on purpose:
    `tests/conftest.py` monkeypatches this one function to a fixed
    in-memory key for the whole suite, so the round-trip tests below
    exercise real Fernet encrypt/decrypt/tamper-detection without needing
    a live D-Bus Secret Service session - the same "no live external
    service in tests" rule `avg.providers.transport` already enforces for
    HTTP.
    """
    try:
        import keyring
        from cryptography.fernet import Fernet
    except Exception:
        return None
    try:
        existing = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if existing:
            return existing.encode("ascii")
        new_key = Fernet.generate_key()
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, new_key.decode("ascii"))
        return new_key
    except Exception:
        return None


def _protect(plaintext: bytes) -> bytes | None:
    """Fernet-encrypt `plaintext` under the keyring-sourced key. `None`
    on any failure, including no key being available - the current
    user's DPAPI-equivalent protection, see module docstring."""
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    key = _linux_encryption_key()
    if key is None:
        return None
    try:
        return Fernet(key).encrypt(plaintext)
    except Exception:
        return None


def _unprotect(ciphertext: bytes) -> bytes | None:
    """Fernet-decrypt `ciphertext`. `None` on any failure - never raises.

    This is the one function most exposed to hostile/garbled input (a
    tampered secrets.dat, a copy from another machine or user account, a
    truncated file). Fernet's own MAC check means a tampered or
    foreign-key ciphertext raises `InvalidToken`; every failure mode
    lands here as `None`, matching "no key set" rather than surfacing as
    a crash.
    """
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    key = _linux_encryption_key()
    if key is None:
        return None
    try:
        return Fernet(key).decrypt(ciphertext)
    except Exception:
        return None


def default_secrets_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Secrets/secrets.dat`.

    Composed from `avg.config.user_assets.user_assets_root()`. Like DPAPI
    on the Windows build, the Fernet key this ties the ciphertext to is
    not derived from the file's path - moving this file on the same
    machine, same user, never invalidates it, so this move is exactly as
    safe as every other document's.
    """
    return user_assets_root() / "Secrets" / "secrets.dat"


def _load_store(path: Path | None) -> dict[str, str]:
    """Read the on-disk `{provider: base64-ciphertext}` map.

    A missing file, unreadable JSON, or a non-dict/non-string-valued shape
    all yield `{}` rather than raising - a broken or absent secrets.dat must
    mean "no keys set", not a crash.
    """
    resolved = Path(path) if path is not None else default_secrets_path()
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def _save_store(store: dict[str, str], path: Path | None) -> None:
    """Write the store atomically (temp file + `os.replace`), matching
    `avg.config.settings.save_settings`."""
    resolved = Path(path) if path is not None else default_secrets_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=resolved.parent, prefix=".secrets-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f)
        os.replace(tmp_name, resolved)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


def _env_var_name(provider: str) -> str:
    return f"VAF_{provider.upper()}_API_KEY"


def set_key(provider: str, key: str, path: Path | None = None) -> None:
    """Encrypt `key` for the current user and persist it under `provider`.

    Raises `RuntimeError` if the keyring-backed encryption itself refuses
    (e.g. no reachable Secret Service) - unlike read paths, a failure to
    *store* a key must not be silently swallowed as "saved", since that
    would silently lose the credential the caller just supplied.
    """
    encrypted = _protect(key.encode("utf-16-le"))
    if encrypted is None:
        raise RuntimeError("could not encrypt the key for the current user (no reachable Secret Service)")

    store = _load_store(path)
    store[provider] = base64.b64encode(encrypted).decode("ascii")
    _save_store(store, path)


def get_key(provider: str, path: Path | None = None) -> str | None:
    """Return the provider's key, or `None` if unset/undecryptable.

    `VAF_<PROVIDER>_API_KEY` (env var) wins over anything in the store -
    the escape hatch the legacy app also honored. Any decrypt failure
    (corrupt base64, tampered ciphertext, blob from a different machine or
    user account) yields `None`, identical to "never set".
    """
    env_val = os.environ.get(_env_var_name(provider))
    if env_val:
        return env_val

    store = _load_store(path)
    encoded = store.get(provider)
    if encoded is None:
        return None

    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except Exception:
        return None

    plaintext = _unprotect(ciphertext)
    if plaintext is None:
        return None

    try:
        return plaintext.decode("utf-16-le")
    except UnicodeDecodeError:
        return None


def has_key(provider: str, path: Path | None = None) -> bool:
    """Whether `provider` resolves to a usable key (env var or store)."""
    return get_key(provider, path) is not None


def list_providers(path: Path | None = None) -> list[str]:
    """Providers with an entry in the on-disk store (not env-var-only ones -
    there's no enumerable list of environment variables to check)."""
    return sorted(_load_store(path).keys())


def delete_key(provider: str, path: Path | None = None) -> None:
    """Remove `provider`'s stored key, if any. A no-op if it isn't present."""
    store = _load_store(path)
    if provider in store:
        del store[provider]
        _save_store(store, path)
