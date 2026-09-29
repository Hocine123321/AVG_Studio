"""Tests for the maintained cost table (`avg.core.costs`).

These deliberately do NOT assert particular prices - every figure in that
module is a number a human maintains and is expected to change. What is
tested is the structure that would silently produce a wrong total: a leaf
that belongs to no phase, a registry phase the table has never heard of, a
length preset the settings catalogue offers but the multiplier map does
not, and the `script` phase's per-leaf accounting.
"""

from __future__ import annotations

from avg.core import costs
from avg.core.settings_schema import get_spec
from avg.pipeline.registry import REGISTRY


def test_phase_leaves_covers_the_registry_exactly():
    assert set(costs.PHASE_LEAVES) == {phase.key for phase in REGISTRY}


def test_every_leaf_belongs_to_exactly_one_phase():
    mapped = [leaf for leaves in costs.PHASE_LEAVES.values() for leaf in leaves]
    assert sorted(mapped) == sorted(costs.LEAF_COST)
    assert len(mapped) == len(set(mapped))


def test_every_length_preset_the_catalogue_offers_has_a_multiplier():
    spec = get_spec("length_preset")
    assert spec is not None and spec.choices
    missing = [choice for choice in spec.choices if choice not in costs.LENGTH_MULTIPLIER]
    assert missing == [], f"length presets with no cost multiplier: {missing}"


def test_base_cost_is_the_sum_of_every_phase():
    assert costs.base_cost() == sum(costs.phase_cost(key) for key in costs.PHASE_LEAVES)


def test_estimate_scales_by_the_preset_and_never_raises_on_an_unknown_one():
    assert costs.estimate("30s") < costs.estimate("60s") < costs.estimate("8m")
    assert costs.estimate("not-a-preset") == costs.base_cost()


def test_spent_counts_done_and_running_and_ignores_pending_and_failed():
    statuses = {"brief": "done", "script": "done", "director1": "running", "visuals": "pending"}
    expected = costs.phase_cost("brief") + costs.phase_cost("script") + costs.phase_cost("director1")
    assert costs.spent(statuses) == expected

    assert costs.spent({"brief": "failed"}) == 0.0
    assert costs.spent({}) == 0.0


def test_a_partial_script_has_paid_for_the_draft_only():
    """The one place the display-only Script split changes a number.

    A `done` script phase whose scenes.json is missing (Slice 6A's
    `partial`) has run pass 1 and not pass 2, so it must not be charged for
    segmentation.
    """
    done_both = costs.spent({"script": "done"}, script_substate="done")
    partial = costs.spent({"script": "done"}, script_substate="partial")
    assert partial == costs.LEAF_COST["draft"]
    assert done_both == costs.LEAF_COST["draft"] + costs.LEAF_COST["segmentation"]
    assert partial < done_both


def test_a_running_script_is_charged_for_the_draft_only():
    # Pass 2 has not happened yet by definition.
    assert costs.spent({"script": "running"}) == costs.LEAF_COST["draft"]
