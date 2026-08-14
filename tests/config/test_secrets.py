import base64
import json

import pytest

from avg.config import secrets


def test_round_trips_a_stored_key(tmp_path):
    path = tmp_path / "secrets.dat"
    secrets.set_key("groq", "sk-real-secret-value", path)

    assert secrets.get_key("groq", path) == "sk-real-secret-value"
    assert secrets.has_key("groq", path) is True
    assert secrets.list_providers(path) == ["groq"]


def test_absent_file_yields_no_key_not_a_crash(tmp_path):
    path = tmp_path / "does_not_exist.dat"

    assert secrets.get_key("groq", path) is None
    assert secrets.has_key("groq", path) is False
    assert secrets.list_providers(path) == []


def test_corrupt_base64_returns_none_rather_than_raising(tmp_path):
    path = tmp_path / "secrets.dat"
    path.write_text(json.dumps({"groq": "not-valid-base64!!!"}), encoding="utf-8")

    assert secrets.get_key("groq", path) is None


def test_tampered_ciphertext_returns_none_rather_than_raising(tmp_path):
    path = tmp_path / "secrets.dat"
    secrets.set_key("groq", "sk-real-secret-value", path)

    store = json.loads(path.read_text(encoding="utf-8"))
    ciphertext = bytearray(base64.b64decode(store["groq"]))
    ciphertext[-3] ^= 0xFF  # flip a byte deep in the DPAPI blob's MAC/payload
    store["groq"] = base64.b64encode(bytes(ciphertext)).decode("ascii")
    path.write_text(json.dumps(store), encoding="utf-8")

    assert secrets.get_key("groq", path) is None


def test_env_var_takes_precedence_over_stored_key(tmp_path, monkeypatch):
    path = tmp_path / "secrets.dat"
    secrets.set_key("groq", "stored-value", path)
    monkeypatch.setenv("VAF_GROQ_API_KEY", "env-value")

    assert secrets.get_key("groq", path) == "env-value"


def test_env_var_used_when_nothing_is_stored(tmp_path, monkeypatch):
    path = tmp_path / "does_not_exist.dat"
    monkeypatch.setenv("VAF_ELEVENLABS_API_KEY", "env-only-value")

    assert secrets.get_key("elevenlabs", path) == "env-only-value"
    assert secrets.has_key("elevenlabs", path) is True


def test_empty_env_var_does_not_shadow_stored_key(tmp_path, monkeypatch):
    path = tmp_path / "secrets.dat"
    secrets.set_key("groq", "stored-value", path)
    monkeypatch.setenv("VAF_GROQ_API_KEY", "")

    assert secrets.get_key("groq", path) == "stored-value"


def test_delete_key_removes_it_and_is_a_noop_if_absent(tmp_path):
    path = tmp_path / "secrets.dat"
    secrets.set_key("groq", "sk-real-secret-value", path)
    secrets.set_key("gemini", "sk-other-value", path)

    secrets.delete_key("groq", path)
    assert secrets.get_key("groq", path) is None
    assert secrets.list_providers(path) == ["gemini"]

    secrets.delete_key("groq", path)  # no-op, must not raise
    assert secrets.list_providers(path) == ["gemini"]


def test_list_providers_is_sorted(tmp_path):
    path = tmp_path / "secrets.dat"
    secrets.set_key("stablehorde", "a", path)
    secrets.set_key("gemini", "b", path)
    secrets.set_key("assemblyai", "c", path)

    assert secrets.list_providers(path) == ["assemblyai", "gemini", "stablehorde"]


def test_store_file_on_disk_contains_no_plaintext(tmp_path):
    path = tmp_path / "secrets.dat"
    secret_value = "sk-super-secret-do-not-leak"
    secrets.set_key("groq", secret_value, path)

    on_disk = path.read_bytes()
    assert secret_value.encode("utf-8") not in on_disk
    assert secret_value.encode("utf-16-le") not in on_disk


def test_save_is_atomic_and_leaves_no_temp_files_behind(tmp_path):
    path = tmp_path / "secrets.dat"
    secrets.set_key("groq", "sk-real-secret-value", path)

    leftovers = [p for p in tmp_path.iterdir() if p.name != "secrets.dat"]
    assert leftovers == []


def test_corrupt_json_store_yields_no_providers_rather_than_raising(tmp_path):
    path = tmp_path / "secrets.dat"
    path.write_text("{ not valid json", encoding="utf-8")

    assert secrets.list_providers(path) == []
    assert secrets.get_key("groq", path) is None


class TestReadLegacySecrets:
    def test_absent_legacy_file_yields_empty_dict(self, tmp_path):
        result = secrets.read_legacy_secrets(tmp_path / "does_not_exist.json")
        assert result == {}

    def test_reads_back_a_dpapi_blob_written_in_the_legacy_hex_format(self, tmp_path):
        # Mirrors ConvertFrom-SecureString's on-disk shape: hex-encoded
        # DPAPI blob of the UTF-16LE plaintext, one key per provider.
        encrypted = secrets._protect("legacy-plaintext-key".encode("utf-16-le"))
        assert encrypted is not None
        legacy_path = tmp_path / "secrets.json"
        legacy_path.write_text(
            json.dumps({"groq": encrypted.hex()}), encoding="utf-8"
        )

        result = secrets.read_legacy_secrets(legacy_path)
        assert result == {"groq": "legacy-plaintext-key"}

    def test_skips_nested_non_provider_entries_like_fallbacks(self, tmp_path):
        encrypted = secrets._protect("legacy-plaintext-key".encode("utf-16-le"))
        legacy_path = tmp_path / "secrets.json"
        legacy_path.write_text(
            json.dumps({"groq": encrypted.hex(), "fallbacks": {"groq": "abc123"}}),
            encoding="utf-8",
        )

        result = secrets.read_legacy_secrets(legacy_path)
        assert result == {"groq": "legacy-plaintext-key"}

    def test_skips_entries_that_are_not_valid_hex_without_raising(self, tmp_path):
        legacy_path = tmp_path / "secrets.json"
        legacy_path.write_text(json.dumps({"groq": "not-hex-zzz"}), encoding="utf-8")

        result = secrets.read_legacy_secrets(legacy_path)
        assert result == {}

    def test_corrupt_legacy_json_yields_empty_dict_rather_than_raising(self, tmp_path):
        legacy_path = tmp_path / "secrets.json"
        legacy_path.write_text("{ not valid json", encoding="utf-8")

        assert secrets.read_legacy_secrets(legacy_path) == {}


@pytest.mark.skipif(
    not secrets.LEGACY_SECRETS_PATH.exists(),
    reason="legacy D:\\AI_Video_Gen install not present on this machine",
)
def test_reads_the_real_legacy_secrets_file_on_this_dev_machine():
    """Answers the program spec's open spike question directly: can
    Python's DPAPI decrypt what the PowerShell app actually wrote on disk?
    Only runs on a machine that still has the legacy install."""
    result = secrets.read_legacy_secrets()
    # Not asserting *which* providers decrypt (that's a per-machine fact
    # recorded in the task report) - just that this never raises and only
    # ever yields non-empty decrypted strings.
    assert isinstance(result, dict)
    for provider, value in result.items():
        assert isinstance(provider, str)
        assert isinstance(value, str)
        assert value != ""


def test_default_secrets_path_is_under_user_assets_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "user_assets_root", lambda: tmp_path)
    assert secrets.default_secrets_path() == tmp_path / "Secrets" / "secrets.dat"
