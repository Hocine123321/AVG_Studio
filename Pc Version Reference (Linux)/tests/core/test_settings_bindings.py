"""Every setting in the catalogue must be read by some Python code.

The defect this guards: the settings screen renders itself from the
catalogue, so an entry with no backend reader still appears, still saves,
and still looks to the user like a control that does something. Eleven of
them were dead this way at once - `theme` chief among them, offering nine
values none of which the app implemented.

This is a structural test, not a behavioural one: it proves a reader
EXISTS, not that it does the right thing. That is still the difference
between a switch that is wired and one that is decorative.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from avg.core.settings_schema import SETTINGS

REPO = Path(__file__).resolve().parents[2]

# Ids these modules build by interpolation rather than spelling out - the
# per-phase model slots are `f"{phase}_model"` and friends in
# `avg.providers.text.router`, so a plain text search cannot see them.
INTERPOLATED = re.compile(r"^(brief|script_draft|script_segmentation|director1|director2)_(model|backup|on_failure)$")


def _mentioned_in_backend(setting_id: str) -> bool:
    result = subprocess.run(
        ["git", "grep", "-l", "--", setting_id, "--", "avg/"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    files = [
        f
        for f in result.stdout.split()
        # The catalogue itself declares every id; that is not a reader. The
        # vendored engine is a frozen copy and reads none of them.
        if "settings_schema.py" not in f and "/vendor/" not in f
    ]
    return bool(files)


# Still unwired, and recorded here rather than quietly excluded, so the
# guard below keeps working for everything else instead of being deleted.
# Each needs more than a binding, which is why none was done in the sweep
# that wired the rest:
#
#   captions_provider        - only AssemblyAI is implemented. Offering
#                              "whisper" needs a second transcription
#                              backend, not a wire.
#   voice_stability          - ElevenLabs request-body fields. Adding them
#   voice_speed                changes the request hash, which invalidates
#                              the recorded fixtures the no-live-calls rule
#                              depends on; one paid recording has already
#                              been lost that way.
#   image_quality            - image providers take a size, not a quality
#                              tier. Needs a tier-to-size mapping decision.
#   auto_style_lock          - "derive the style lock automatically" is a
#                              feature that does not exist; nothing to bind.
#   motion_scale              - its one reader is `web/src/tokens.css`'s
#                              `--motion-scale` custom property, which this
#                              guard cannot see because it only searches
#                              `avg/` for a Python reader. Slice 12's Task 6
#                              wires the frontend consumer; no backend
#                              mention will ever exist for this one, and
#                              that is expected, not a gap to close.
#
# Shrink this list. Never grow it without a reason as specific as the ones
# above.
KNOWN_UNWIRED = {
    "captions_provider",
    "voice_stability",
    "voice_speed",
    "image_quality",
    "auto_style_lock",
    "motion_scale",
}


def test_no_new_setting_ships_without_a_backend_reader():
    dead = {
        spec.id
        for spec in SETTINGS
        if spec.type != "secret" and not INTERPOLATED.match(spec.id) and not _mentioned_in_backend(spec.id)
    }
    assert dead - KNOWN_UNWIRED == set(), (
        "settings with no Python reader - either wire them or remove them, "
        f"but do not ship a control that does nothing: {sorted(dead - KNOWN_UNWIRED)}"
    )


def test_the_known_unwired_list_does_not_outlive_the_problem():
    """Fails when a listed setting HAS been wired - so the list shrinks as
    the gap closes instead of sitting there stale and believed."""
    still_dead = {sid for sid in KNOWN_UNWIRED if not _mentioned_in_backend(sid)}
    assert still_dead == KNOWN_UNWIRED, (
        "these are wired now - remove them from KNOWN_UNWIRED: "
        f"{sorted(KNOWN_UNWIRED - still_dead)}"
    )


def test_images_have_a_backup_and_a_failure_policy():
    """Mirrors the per-phase text triplet the Settings screen already
    renders - `— model`, `— backup`, `— if both fail`."""
    from avg.core.settings_schema import BY_ID

    for setting_id in ("image_provider_backup", "image_model_backup", "image_on_failure"):
        spec = BY_ID[setting_id]
        assert spec.group == "images"
        assert spec.scope == "channel"
        assert spec.invalidates == "visuals"

    assert BY_ID["image_on_failure"].choices == ["stop", "fall_back"]
    assert BY_ID["image_model_backup"].source == "live_list:image"


def test_the_app_group_replaces_runtime():
    from avg.core.settings_schema import SETTINGS

    groups = {s.group for s in SETTINGS}
    assert "app" in groups
    assert "runtime" not in groups


def test_the_app_group_holds_the_apps_own_behaviour():
    from avg.core.settings_schema import BY_ID

    for setting_id in ("theme", "verbose_log", "motion_scale", "startup_check"):
        assert BY_ID[setting_id].group == "app", setting_id
        assert BY_ID[setting_id].scope == "app", setting_id

    assert BY_ID["motion_scale"].choices == ["full", "reduced", "none"]
    assert BY_ID["startup_check"].default is True
