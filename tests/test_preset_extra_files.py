"""INFER_STACK_EXTRA_PRESET_FILES merge (self-contained runbook presets).

A runbook (e.g. reproduce/classic_together_combined) ships its own generated
preset file and merges it into PRESET_CONFIGS via this env var, keeping large
generated run_entry lists out of the shared preset_configs.yaml. The merge must
ADD keys and REFUSE to shadow an existing shared preset.
"""
from __future__ import annotations

import pytest
import yaml

from eval_audit.integrations.infer_stack import presets


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data))
    return str(p)


def test_unset_is_noop(monkeypatch):
    monkeypatch.delenv("INFER_STACK_EXTRA_PRESET_FILES", raising=False)
    catalog = {"a": {"x": 1}}
    presets._merge_extra_preset_files(catalog)
    assert catalog == {"a": {"x": 1}}


def test_merge_adds_keys(monkeypatch, tmp_path):
    f1 = _write(tmp_path, "one.yaml", {"runbook-a": {"profile": "p"}})
    f2 = _write(tmp_path, "two.yaml", {"runbook-b": {"profile": "q"}})
    import os
    monkeypatch.setenv("INFER_STACK_EXTRA_PRESET_FILES", os.pathsep.join([f1, f2]))
    catalog = {"shared": {"profile": "s"}}
    presets._merge_extra_preset_files(catalog)
    assert catalog["runbook-a"]["profile"] == "p"
    assert catalog["runbook-b"]["profile"] == "q"
    assert catalog["shared"]["profile"] == "s"  # untouched


def test_collision_is_hard_error(monkeypatch, tmp_path):
    f = _write(tmp_path, "clash.yaml", {"shared": {"profile": "override"}})
    monkeypatch.setenv("INFER_STACK_EXTRA_PRESET_FILES", f)
    catalog = {"shared": {"profile": "s"}}
    with pytest.raises(ValueError, match="redefines existing preset"):
        presets._merge_extra_preset_files(catalog)


def test_generated_runbook_presets_resolve(monkeypatch):
    """The shipped classic-together generated presets load + have the era shape."""
    import os
    from pathlib import Path
    gen = Path(__file__).resolve().parents[1] / "reproduce" / "classic_together_combined" / "config" / "presets.yaml"
    if not gen.exists():
        pytest.skip("generated presets.yaml not present (run gen_presets.py)")
    catalog: dict = {}
    monkeypatch.setenv("INFER_STACK_EXTRA_PRESET_FILES", str(gen))
    presets._merge_extra_preset_files(catalog)
    for key in ("era-gptj_6b-v0_2_4", "era-gptneox_20b-v0_3_0", "era-opt_66b-v0_2_4"):
        cfg = catalog[key]
        assert cfg["era"].startswith("helm-v0.")
        prof = cfg["profiles"][0]
        assert prof["protocol_mode"] == "completions"
        assert prof["helm_tokenizer_name"]  # required for the era window service
        assert cfg["full_manifest"]["run_entries"]  # non-empty all-runs enumeration
