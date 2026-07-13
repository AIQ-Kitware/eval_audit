"""Host-importable unit tests for the era shim's pure helpers (Findings 2, 7).

These exercise logic that must be correct BEFORE any era image exists: the
pyhocon-addressable credentials.conf key (Finding 2) and the unambiguous
run-dir locator (Finding 7). The shim's ``replay`` module imports era ``helm.*``
only *inside* functions, so it loads cleanly on the host via
``importlib.util.spec_from_file_location`` — no era image / no crfm-helm needed
(the same technique the ladder's in-container smoke check uses).
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from pyhocon import ConfigFactory

_REPLAY_PATH = (
    Path(__file__).resolve().parent.parent
    / "docker"
    / "era_shim"
    / "helm_era_shim"
    / "replay.py"
)


def _load_replay():
    spec = importlib.util.spec_from_file_location(
        "helm_era_shim_replay_hostside", _REPLAY_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = _load_replay()


# --- Finding 2: credentials.conf must be pyhocon-addressable ------------------
@pytest.mark.parametrize(
    "model",
    [
        "eleutherai/pythia-6.9b",       # the flagship demo model — dotted
        "eleutherai/pythia-2.8b-v0",    # two dots after the slash
        "eleutherai/pythia-1.4b-v0",
        "together/gpt2",                # no dot -> stays flat
        "meta/llama-2-7b",              # no dot
        "a.b.c",                        # degenerate multi-dot
    ],
)
def test_credentials_conf_is_pyhocon_addressable(model):
    """HELM's AutoClient looks up deployments[model] with the raw model string;
    pyhocon path-splits on '.', so the written text must resolve that path.
    v0.2.4 checks membership BEFORE getitem (`if model not in deployment_api_keys:
    raise AuthenticationError`), so `in` must resolve too, not just getitem."""
    text = replay._render_credentials_conf(model, "EMPTY")
    deps = ConfigFactory.parse_string(text)["deployments"]
    assert model in deps
    assert deps[model] == "EMPTY"


def test_hocon_nested_key_stays_flat_without_a_dot():
    assert (
        replay._hocon_nested_deployment_key("together/gpt2", "EMPTY")
        == '"together/gpt2" = "EMPTY"'
    )


def test_hocon_nested_key_nests_along_the_dot_split_path():
    assert (
        replay._hocon_nested_deployment_key("eleutherai/pythia-6.9b", "EMPTY")
        == '"eleutherai/pythia-6" { "9b" = "EMPTY" }'
    )


def test_credentials_conf_preserves_a_nonempty_key_value():
    text = replay._render_credentials_conf("eleutherai/pythia-6.9b", "sk-real-key")
    deps = ConfigFactory.parse_string(text)["deployments"]
    assert deps["eleutherai/pythia-6.9b"] == "sk-real-key"


# --- Finding 7: _locate_run_dir must never auto-pick an arbitrary dir ---------
def _make_run_dir(suite_dir: Path, name: str, *, per_instance=True, run_spec=True):
    d = suite_dir / name
    d.mkdir(parents=True)
    if run_spec:
        (d / "run_spec.json").write_text("{}")
    if per_instance:
        (d / "per_instance_stats.json").write_text("[]")
    return d


def test_locate_run_dir_exact_match(tmp_path):
    suite_dir = tmp_path / "runs" / "era-suite"
    made = _make_run_dir(suite_dir, "mmlu:subject=x")
    got = replay._locate_run_dir(
        output_path=tmp_path,
        suite="era-suite",
        run_spec_name="mmlu:subject=x",
        require_per_instance_stats=True,
    )
    assert got == made


def test_locate_run_dir_sanitizes_os_sep(tmp_path):
    suite_dir = tmp_path / "runs" / "era-suite"
    made = _make_run_dir(suite_dir, "a_b_c")
    got = replay._locate_run_dir(
        output_path=tmp_path,
        suite="era-suite",
        run_spec_name=f"a{os.path.sep}b{os.path.sep}c",
        require_per_instance_stats=True,
    )
    assert got == made


def test_locate_run_dir_single_candidate_fallback(tmp_path):
    """Exact-name miss but exactly one valid run dir -> return it."""
    suite_dir = tmp_path / "runs" / "era-suite"
    made = _make_run_dir(suite_dir, "the_only_run")
    got = replay._locate_run_dir(
        output_path=tmp_path,
        suite="era-suite",
        run_spec_name="a-name-that-does-not-match",
        require_per_instance_stats=True,
    )
    assert got == made


def test_locate_run_dir_ambiguous_returns_none(tmp_path):
    """Finding 7: two valid run dirs, neither the exact name -> None, never a
    silent arbitrary pick (which would corrupt computed_run_dir provenance)."""
    suite_dir = tmp_path / "runs" / "era-suite"
    _make_run_dir(suite_dir, "run_a")
    _make_run_dir(suite_dir, "run_b")
    got = replay._locate_run_dir(
        output_path=tmp_path,
        suite="era-suite",
        run_spec_name="neither",
        require_per_instance_stats=True,
    )
    assert got is None


def test_locate_run_dir_respects_require_per_instance_stats(tmp_path):
    suite_dir = tmp_path / "runs" / "era-suite"
    _make_run_dir(suite_dir, "no_stats", per_instance=False)
    # With the requirement on, the sole dir is filtered out -> None.
    assert (
        replay._locate_run_dir(
            output_path=tmp_path,
            suite="era-suite",
            run_spec_name="no_stats",
            require_per_instance_stats=True,
        )
        is None
    )
    # With it off, the exact-name match returns it.
    got = replay._locate_run_dir(
        output_path=tmp_path,
        suite="era-suite",
        run_spec_name="no_stats",
        require_per_instance_stats=False,
    )
    assert got == suite_dir / "no_stats"


def test_locate_run_dir_missing_suite_dir_returns_none(tmp_path):
    assert (
        replay._locate_run_dir(
            output_path=tmp_path,
            suite="absent",
            run_spec_name="x",
            require_per_instance_stats=True,
        )
        is None
    )


# --- cleanup: process_context stop/duration (Stage-4 indexer reads them) ------
def test_stamp_process_context_stop_fills_timing(tmp_path):
    ctx = {"name": "helm_era_shim.replay", "properties": {"start_timestamp": 100.0}}
    replay._stamp_process_context_stop(tmp_path, ctx)
    props = ctx["properties"]
    assert props["stop_timestamp"] >= 100.0
    assert props["duration"] == props["stop_timestamp"] - 100.0
    # It also (re)writes process_context.json for the indexer to read.
    written = json.loads((tmp_path / "process_context.json").read_text())
    assert written["properties"]["stop_timestamp"] == props["stop_timestamp"]


def test_stamp_process_context_stop_tolerates_missing_props(tmp_path):
    # No 'properties' -> no crash, no duration invented.
    replay._stamp_process_context_stop(tmp_path, {"name": "x"})


# --- 401 regression: credentials.conf must use the BAKED key, not the env -----
# kwdagger's tmux worker ships an empty environ, so $EVAL_AUDIT_ERA_API_KEY never
# reaches the container; v0.2.4 read that empty value into credentials.conf and
# 401'd at the gateway (v0.3.0 survived via the args key). The shim now sources
# the key from the exported model_deployments.yaml (client_spec.args.api_key).
def _write_deployments(tmp_path, model_name, api_key):
    import yaml
    doc = {"model_deployments": [{
        "name": model_name,
        "model_name": model_name,
        "client_spec": {"class_name": "x", "args": {"base_url": "http://g/v1", "api_key": api_key}},
    }]}
    p = tmp_path / "model_deployments.yaml"
    p.write_text(yaml.safe_dump(doc))
    return str(p)


def test_api_key_from_deployments_reads_baked_key(tmp_path):
    fp = _write_deployments(tmp_path, "together/redpajama-incite-base-3b-v1", "sk-master")
    assert replay._api_key_from_deployments(fp, "together/redpajama-incite-base-3b-v1") == "sk-master"
    assert replay._api_key_from_deployments(fp, "other/model") is None
    assert replay._api_key_from_deployments(None, "x") is None


def test_prepare_local_helm_config_prefers_baked_key_over_env(tmp_path, monkeypatch):
    model = "together/redpajama-incite-base-3b-v1"
    fp = _write_deployments(tmp_path, model, "sk-master")
    # Env is EMPTY (the broken tmux channel) — the baked key must still win.
    monkeypatch.setenv("EVAL_AUDIT_ERA_API_KEY", "EMPTY")
    prepared = replay._prepare_local_helm_config(
        out_dpath=tmp_path / "out", local_path="prod_env",
        model_deployments_fpath=fp, model_name=model,
    )
    deps = ConfigFactory.parse_file(str(prepared / "credentials.conf"))["deployments"]
    assert deps[model] == "sk-master"
