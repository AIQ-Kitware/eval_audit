"""Structural + discovery tests for the combined Qwen text-family from-spec preset.

Mirrors the combined-preset tests in ``test_olmo_from_spec.py`` (the OLMo combined
preset is the reference). Because ``qwen-combined`` is built through the SAME
``_build_combined_preset`` helper as ``allenai-olmo-combined``, these tests also
guard that shared builder against regressions (plan §4.2B).

The corpus-gated test is the test-level analogue of V2/V3: it strips each inline
``model_deployment=<local>`` token and classifies the bare discovery key against the
live corpus, asserting 0 NO_MATCH / 0 AMBIGUOUS under the shared parent root — i.e.
a ``--freeze-rel-paths`` export would resolve every source 1:1.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# check_precomputed_discovery imports the vendored HELM submodule lazily; match the
# OLMo test's path setup so the discovery helpers import cleanly.
_SUBMODULE = Path(__file__).resolve().parents[1] / "submodules" / "helm" / "src"
if _SUBMODULE.exists() and str(_SUBMODULE) not in sys.path:
    sys.path.insert(0, str(_SUBMODULE))

from eval_audit.cli import check_precomputed_discovery as dc  # noqa: E402
from eval_audit.integrations.infer_stack.adapter import (  # noqa: E402
    PRESET_CONFIGS,
    _strip_local_deployment,
)

PUBLIC_ROOT = Path("/data/crfm-helm-public")
COMBINED_PRESET = "qwen-combined"

# The eight single-model members that compose the combined bundle. A drift in this
# count means a member preset was added/removed without updating the plan.
QWEN_MEMBERS = (
    "qwen-1-5-7b",
    "qwen-1-5-14b",
    "qwen-1-5-32b",
    "qwen-1-5-72b",
    "qwen-1-5-110b-chat",
    "qwen-2-72b-instruct",
    "qwen-2-5-7b-instruct-turbo",
    "qwen-2-5-72b-instruct-turbo",
)
# Expected reproducible-whitelist row counts per member (classic core + capabilities).
EXPECTED_FULL_ROWS = {
    "qwen-1-5-7b": 85,
    "qwen-1-5-14b": 85,
    "qwen-1-5-32b": 85,
    "qwen-1-5-72b": 85,
    "qwen-1-5-110b-chat": 85,
    "qwen-2-72b-instruct": 86,
    "qwen-2-5-7b-instruct-turbo": 132,
    "qwen-2-5-72b-instruct-turbo": 132,
}

requires_corpus = pytest.mark.skipif(
    not PUBLIC_ROOT.exists(),
    reason="public HELM corpus not present at /data/crfm-helm-public",
)


def test_eight_qwen_members_registered():
    for key in QWEN_MEMBERS:
        cfg = PRESET_CONFIGS[key]
        assert "profiles" not in cfg, f"{key} should be a single-model preset"
        assert cfg["profile"] == f"{key}-single"
        # member run_entries carry NO inline deployment token (the exporter injects it)
        for mode in ("smoke", "full"):
            for entry in cfg[f"{mode}_manifest"]["run_entries"]:
                assert "model_deployment=" not in entry, f"{key}/{mode}: {entry!r}"
        assert len(cfg["full_manifest"]["run_entries"]) == EXPECTED_FULL_ROWS[key]
    assert COMBINED_PRESET in PRESET_CONFIGS


def test_combined_preset_wiring():
    # Pure structural checks (no corpus). Every run_entry must carry an inline
    # model_deployment naming one of the bundle's own eight profiles — that inline
    # token is the per-run rewrite target + lease key a multi-deployment freeze uses.
    # Each profile must declare a protocol_mode (guards a chat/completions mismatch).
    cfg = PRESET_CONFIGS[COMBINED_PRESET]
    profiles = cfg["profiles"]
    assert len(profiles) == 8, profiles
    local_names = frozenset(p["model_deployment_name"] for p in profiles)
    assert len(local_names) == 8, "duplicate deployment name across profiles"
    # 4 base Qwen1.5 served completions, the other four chat (confirmed from HELM
    # model_deployments.yaml).
    modes = [p["protocol_mode"] for p in profiles]
    assert modes.count("completions") == 4 and modes.count("chat") == 4, modes
    for p in profiles:
        assert p.get("protocol_mode") in ("chat", "completions"), p
    for mode in ("smoke", "full"):
        entries = cfg[f"{mode}_manifest"]["run_entries"]
        assert entries, f"{mode} has no run_entries"
        for entry in entries:
            _query, token = _strip_local_deployment(entry, local_names)
            assert token in local_names, (
                f"{mode}: {entry!r} has no inline local deployment token"
            )
    # full = union of the members' full rows (775 total).
    assert len(cfg["full_manifest"]["run_entries"]) == sum(EXPECTED_FULL_ROWS.values())


@requires_corpus
def test_combined_preset_resolves_with_local_strip():
    # Test-level V2/V3: strip the inline token, classify the bare key against the
    # live corpus, assert 0 NO_MATCH / 0 AMBIGUOUS under the shared parent root — so
    # a --freeze-rel-paths export would resolve every source 1:1 (no member splits).
    cfg = PRESET_CONFIGS[COMBINED_PRESET]
    local_names = frozenset(p["model_deployment_name"] for p in cfg["profiles"])
    cache: dict[str, list] = {}

    def runs_for(root: str) -> list:
        if root not in cache:
            cache[root] = dc._enumerate_runs(Path(root))
        return cache[root]

    for mode in ("smoke", "full"):
        block = cfg[f"{mode}_manifest"]
        root = block["precomputed_root"]
        assert root, f"{mode} has no precomputed_root"
        runs = runs_for(root)
        no_match, ambiguous = [], []
        for entry in block["run_entries"]:
            query, _token = _strip_local_deployment(entry, local_names)
            result = dc._classify(query, runs)
            if result.status == "NO_MATCH":
                no_match.append(entry)
            elif result.status == "AMBIGUOUS":
                ambiguous.append(entry)
        assert not no_match, f"{mode} NO_MATCH ({len(no_match)}): {no_match[:3]}"
        assert not ambiguous, f"{mode} AMBIGUOUS ({len(ambiguous)}): {ambiguous[:3]}"
