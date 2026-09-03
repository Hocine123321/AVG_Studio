"""Per-user-protected storage for provider API keys.

On Windows: the legacy PowerShell app encrypts keys with
`ConvertFrom-SecureString` (no `-Key`), which is a thin wrapper over
`CryptProtectData`: DPAPI-encrypt the UTF-16LE bytes of the string, then
hex-encode. This module talks to the same Win32 API directly via `ctypes`
so AVG Studio's own store is protected the same way, current-user scope,
no key file to lose.

On Linux: there is no DPAPI equivalent syscall, so this module instead
generates a random Fernet key (`cryptography.fernet`), stores THAT key in
the desktop's OS-level secret storage via the `keyring` package (Secret
Service - GNOME Keyring, or KWallet in Secret-Service-compatibility mode,
which is what a KDE Plasma session normally runs), and uses it to encrypt
every value the same way DPAPI would have. The net properties are the
same ones DPAPI gave this module: the actual key never touches this app's
own files, is scoped to the logged-in user's session, and a
locked/unavailable keyring fails closed (returns `None`) rather than
prompting a UI this headless-looking call has no context to handle. See
`_linux_encryption_key` for the retrieval/creation logic.

Both stores share the same on-disk shape either way -
(`.../Secrets/secrets.dat`, JSON of provider -> base64 ciphertext, from
`avg.config.user_assets.default_secrets_path` by way of
`default_secrets_path` below) - only what `_protect`/`_unprotect` do with
the bytes differs per platform.

Distinct from `avg.config.settings`: that module is a small trusted
document the app freely reads/writes/logs. This one holds live credentials.
Every function here treats a "key" as something that must never reach a
log line, an exception message, or a `repr()` - only *whether* a provider
has a key is safe to surface, never the value.

One responsibility: protect, unprotect, store, retrieve. No HTTP, no UI,
no provider-specific logic, no caching, no key rotation - nothing here asks
for those.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

from avg.config.user_assets import user_assets_root

#: Computed once, not per-call - see `avg.config.platform_paths` for the
#: same pattern and reasoning.
_WINDOWS = sys.platform == "win32"

# The legacy PowerShell app's DPAPI secrets file. Read-only: D:\AI_Video_Gen
# is the old app's live install and this module must never write to it.
# Windows-only by construction (the path itself is a Windows drive letter,
# so `.exists()`/`.read_text()` simply fail closed to "not present" on
# Linux - no platform branch needed here).
LEGACY_SECRETS_PATH = Path(r"D:\AI_Video_Gen\config\secrets.json")

# Keys in the legacy file that hold nested data rather than a hex-encoded
# ciphertext string - not a provider, skipped by read_legacy_secrets.
_LEGACY_NON_PROVIDER_KEYS = {"fallbacks"}

# Where the Linux Fernet key itself lives inside the OS keyring - not a
# provider name, so it can never collide with one in the same collection.
_LINUX_KEYRING_SERVICE = "AVG Studio"
_LINUX_KEYRING_USERNAME = "secrets-encryption-key"

if _WINDOWS:
    import ctypes
    import ctypes.wintypes as wintypes

    # CRYPTPROTECT_UI_FORBIDDEN: never let the Win32 API pop a UI prompt - a
    # headless desktop app must fail closed (return None), not hang on a
    # modal.
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _DataBlob(ctypes.Structure):
        """Mirrors Win32 `DATA_BLOB` - the struct both crypt32 calls exchange."""

        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL


if _WINDOWS:

    def _blob_from(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
        """Build a `_DataBlob` pointing at `data`.

        Returns the blob plus the buffer it points into - the caller must
        keep the buffer alive (assign it to a local) for as long as the
        blob is used, since the blob only holds a raw pointer into it.
        """
        buf = ctypes.create_string_buffer(data, len(data)) if data else ctypes.create_string_buffer(0)
        return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _protect(plaintext: bytes) -> bytes | None:
        """DPAPI-encrypt `plaintext` for the current user. `None` on failure.

        Wrapped in a blanket `except Exception` (not just a falsy return
        from the API call) because this talks to ctypes/Win32 directly -
        any marshalling problem must degrade to "couldn't protect it",
        never an unhandled exception that could otherwise carry buffer
        contents in its traceback.
        """
        try:
            in_blob, _keepalive = _blob_from(plaintext)
            out_blob = _DataBlob()
            ok = _crypt32.CryptProtectData(
                ctypes.byref(in_blob),
                None,
                None,
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out_blob),
            )
            if not ok:
                return None
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                if out_blob.pbData:
                    _kernel32.LocalFree(out_blob.pbData)
        except Exception:
            return None

    def _unprotect(ciphertext: bytes) -> bytes | None:
        """DPAPI-decrypt `ciphertext`. `None` on any failure - never raises.

        This is the one function most exposed to hostile/garbled input (a
        tampered secrets.dat, a copy from another machine or user account,
        a truncated file). Every failure mode - wrong-user blob, tampered
        MAC, malformed structure, or a ctypes marshalling error - must
        land here as `None`, matching "no key set" rather than surfacing
        as a crash.
        """
        try:
            in_blob, _keepalive = _blob_from(ciphertext)
            out_blob = _DataBlob()
            ok = _crypt32.CryptUnprotectData(
                ctypes.byref(in_blob),
                None,
                None,
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out_blob),
            )
            if not ok:
                return None
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                if out_blob.pbData:
                    _kernel32.LocalFree(out_blob.pbData)
        except Exception:
            return None

else:

    def _linux_encryption_key() -> bytes | None:
        """Fetch (generating on first use) the Fernet key that protects
        every value in the on-disk store, from the desktop's OS-level
        secret storage via the `keyring` package - Secret Service (GNOME
        Keyring) or KWallet's Secret-Service-compatibility mode, whichever
        the session provides. `None` if no backend is reachable (headless
        session, keyring locked, package not installed) - callers must
        treat that exactly like a DPAPI failure: fail closed, never raise
        past this function.

        A separate seam from `_protect`/`_unprotect` on purpose:
        `tests/conftest.py` monkeypatches this one function to a fixed
        in-memory key for the whole suite, so the round-trip tests below
        exercise real Fernet encrypt/decrypt/tamper-detection without
        needing a live D-Bus Secret Service session - the same "no live
        external service in tests" rule `avg.providers.transport` already
        enforces for HTTP.

        `keyring`/`cryptography` are imported here, not at module scope,
        so a Windows install - where this branch never runs - never needs
        either installed.
        """
        try:
            import keyring
            from cryptography.fernet import Fernet
        except Exception:
            return None
        try:
            existing = keyring.get_password(_LINUX_KEYRING_SERVICE, _LINUX_KEYRING_USERNAME)
            if existing:
                return existing.encode("ascii")
            new_key = Fernet.generate_key()
            keyring.set_password(_LINUX_KEYRING_SERVICE, _LINUX_KEYRING_USERNAME, new_key.decode("ascii"))
            return new_key
        except Exception:
            return None

    def _protect(plaintext: bytes) -> bytes | None:
        """Fernet-encrypt `plaintext` under the keyring-sourced key.
        `None` on any failure, including no key being available."""
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
        """Fernet-decrypt `ciphertext`. `None` on any failure - never
        raises. Fernet's own MAC check means a tampered or foreign-key
        ciphertext raises `InvalidToken`, caught here like every other
        failure mode."""
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

    Composed from `avg.config.user_assets.user_assets_root()`. DPAPI ties
    the ciphertext to the Windows user account, not to the file's path -
    moving this file on the same machine, same account, never invalidates
    it, so this move is exactly as safe as every other document's.
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

    Raises `RuntimeError` if DPAPI itself refuses to encrypt (e.g. no
    loaded user profile) - unlike read paths, a failure to *store* a key
    must not be silently swallowed as "saved", since that would silently
    lose the credential the caller just supplied.
    """
    encrypted = _protect(key.encode("utf-16-le"))
    if encrypted is None:
        raise RuntimeError("DPAPI could not encrypt the key for the current user")

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


def read_legacy_secrets(path: Path | None = None) -> dict[str, str]:
    """Best-effort read of the legacy PowerShell app's DPAPI secrets file.

    `ConvertFrom-SecureString` (no `-Key`) writes a hex-encoded DPAPI blob
    of the UTF-16LE string; this decodes the hex and runs it through the
    same `_unprotect` used for our own store. Returns only the providers
    that actually decrypted - anything that fails (wrong user/machine,
    corrupt entry, or a non-string nested value like the legacy file's
    "fallbacks" dict) is silently omitted, matching this module's rule
    that decrypt failure means "no key", never an exception.

    This is a one-way read for migration/reporting purposes; it never
    writes back to the legacy file.
    """
    resolved = Path(path) if path is not None else LEGACY_SECRETS_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}

    found: dict[str, str] = {}
    for provider, hex_blob in raw.items():
        if provider in _LEGACY_NON_PROVIDER_KEYS or not isinstance(hex_blob, str):
            continue
        try:
            ciphertext = bytes.fromhex(hex_blob)
        except ValueError:
            continue
        plaintext = _unprotect(ciphertext)
        if plaintext is None:
            continue
        try:
            found[provider] = plaintext.decode("utf-16-le")
        except UnicodeDecodeError:
            continue
    return found
