"""Unit tests for the era-pinned HELM registry resolver (eval_audit/eras.py).

Covers registry loading, ``(public_track, suite_version)`` resolution, run-dir
path parsing, and the mixed-era rejection that keeps one manifest = one era.

See docs/planning/era-pinned-helm-containers-plan.md.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from eval_audit.eras import (
    ERA_SHIM_FROM_SPEC,
    EraSpec,
    era_for_run_dir,
    load_era_registry,
    parse_public_signal_from_run_dir,
    resolve_era,
    resolve_era_for_sources,
)


def _write_registry(tmp_path: Path, body: str) -> Path:
    fpath = tmp_path / "eras.yaml"
    fpath.write_text(textwrap.dedent(body))
    return fpath


TWO_ERA_REGISTRY = """
    eras:
      helm-v0.2.4:
        helm_git_ref: "626d8609"
        python_version: "3.10"
        constraints: docker/eras/constraints-helm-v0.2.4.txt
        helm_extras: all
        capability: era-shim-from-spec
        image_name: helm-runner-era-v0-2-4
        matches:
          - public_track: classic
            suite_version: v0.2.4
      helm-v0.3.0:
        helm_git_ref: "8ea285f7"
        python_version: "3.10"
        constraints: docker/eras/constraints-helm-v0.3.0.txt
        helm_extras: all
        capability: era-shim-from-spec
        image_name: helm-runner-era-v0-3-0
        matches:
          - public_track: classic
            suite_version: v0.3.0
"""


def test_load_registry_parses_both_eras(tmp_path):
    reg = load_era_registry(_write_registry(tmp_path, TWO_ERA_REGISTRY))
    assert set(reg) == {"helm-v0.2.4", "helm-v0.3.0"}
    era = reg["helm-v0.2.4"]
    assert isinstance(era, EraSpec)
    assert era.helm_git_ref == "626d8609"
    assert era.python_version == "3.10"
    assert era.capability == ERA_SHIM_FROM_SPEC
    assert era.image_name == "helm-runner-era-v0-2-4"


def test_resolve_era_hits_and_misses(tmp_path):
    reg = load_era_registry(_write_registry(tmp_path, TWO_ERA_REGISTRY))
    assert resolve_era("classic", "v0.2.4", registry=reg).key == "helm-v0.2.4"
    assert resolve_era("classic", "v0.3.0", registry=reg).key == "helm-v0.3.0"
    # No match => modern era, represented as None.
    assert resolve_era("classic", "v0.5.14", registry=reg) is None
    assert resolve_era("mmlu", "v0.2.4", registry=reg) is None
    assert resolve_era(None, None, registry=reg) is None


def test_resolve_era_track_less_era_suite_fails_loud(tmp_path):
    """Finding 9: public_track undecidable (None) but suite_version names an era
    => fail loud instead of silently resolving to modern (a track-rooted mirror)."""
    reg = load_era_registry(_write_registry(tmp_path, TWO_ERA_REGISTRY))
    with pytest.raises(ValueError, match="cannot derive public_track"):
        resolve_era(None, "v0.2.4", registry=reg)
    # A suite_version that names NO era with public_track None is genuinely modern.
    assert resolve_era(None, "v0.5.14", registry=reg) is None


def test_era_for_run_dir_track_rooted_mirror_fails_loud(tmp_path):
    """A rel_path rooted at benchmark_output (no track component) resolves
    public_track=None; an era suite_version there must fail loud, not go modern."""
    from eval_audit.eras import era_for_run_dir

    reg = load_era_registry(_write_registry(tmp_path, TWO_ERA_REGISTRY))
    track_rooted = Path("benchmark_output/runs/v0.2.4/babi_qa:task=15,model=eleutherai_pythia-6.9b")
    with pytest.raises(ValueError, match="cannot derive public_track"):
        era_for_run_dir(track_rooted, registry=reg)


def test_resolve_era_ambiguous_registry_raises(tmp_path):
    reg = load_era_registry(
        _write_registry(
            tmp_path,
            """
            eras:
              era-a:
                helm_git_ref: aaa
                python_version: "3.10"
                constraints: c-a.txt
                helm_extras: all
                capability: era-shim-from-spec
                image_name: img-a
                matches:
                  - public_track: classic
                    suite_version: v0.2.4
              era-b:
                helm_git_ref: bbb
                python_version: "3.10"
                constraints: c-b.txt
                helm_extras: all
                capability: era-shim-from-spec
                image_name: img-b
                matches:
                  - suite_version: v0.2.4
            """,
        )
    )
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_era("classic", "v0.2.4", registry=reg)


def test_missing_required_key_raises(tmp_path):
    fpath = _write_registry(
        tmp_path,
        """
        eras:
          broken:
            python_version: "3.10"
            constraints: c.txt
            image_name: img
        """,
    )
    with pytest.raises(ValueError, match="missing required keys"):
        load_era_registry(fpath)


@pytest.mark.parametrize(
    "run_dir, expected",
    [
        (
            "/data/crfm-helm-public/classic/benchmark_output/runs/v0.2.4/babi_qa:task=15,model=x",
            ("classic", "v0.2.4"),
        ),
        (
            "/data/crfm-helm-public/classic/benchmark_output/runs/v0.3.0/math:model=y",
            ("classic", "v0.3.0"),
        ),
        ("/some/path/without/the/convention", (None, None)),
    ],
)
def test_parse_public_signal_from_run_dir(run_dir, expected):
    assert parse_public_signal_from_run_dir(run_dir) == expected


def test_era_for_run_dir(tmp_path):
    reg = load_era_registry(_write_registry(tmp_path, TWO_ERA_REGISTRY))
    run_dir = "/data/crfm-helm-public/classic/benchmark_output/runs/v0.2.4/math:model=y"
    assert era_for_run_dir(run_dir, registry=reg).key == "helm-v0.2.4"
    modern = "/data/crfm-helm-public/mmlu/benchmark_output/runs/v0.5.14/mmlu:model=z"
    assert era_for_run_dir(modern, registry=reg) is None


def test_resolve_era_for_sources_single_era(tmp_path):
    reg = load_era_registry(_write_registry(tmp_path, TWO_ERA_REGISTRY))
    root = "/data/crfm-helm-public"
    sources = [
        {"run_entry": "a", "rel_path": "classic/benchmark_output/runs/v0.2.4/aa:model=x"},
        {"run_entry": "b", "rel_path": "classic/benchmark_output/runs/v0.2.4/bb:model=y"},
    ]
    era = resolve_era_for_sources(root, sources, registry=reg)
    assert era.key == "helm-v0.2.4"


def test_resolve_era_for_sources_mixed_raises(tmp_path):
    reg = load_era_registry(_write_registry(tmp_path, TWO_ERA_REGISTRY))
    root = "/data/crfm-helm-public"
    sources = [
        {"run_entry": "a", "rel_path": "classic/benchmark_output/runs/v0.2.4/aa:model=x"},
        {"run_entry": "b", "rel_path": "classic/benchmark_output/runs/v0.3.0/bb:model=y"},
    ]
    with pytest.raises(ValueError, match="mixed-era"):
        resolve_era_for_sources(root, sources, registry=reg)


def test_resolve_era_for_sources_all_modern(tmp_path):
    reg = load_era_registry(_write_registry(tmp_path, TWO_ERA_REGISTRY))
    root = "/data/crfm-helm-public"
    sources = [
        {"run_entry": "a", "rel_path": "mmlu/benchmark_output/runs/v0.5.14/aa:model=x"},
    ]
    assert resolve_era_for_sources(root, sources, registry=reg) is None
