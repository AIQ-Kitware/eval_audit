"""Generate the era-pinned from-spec presets for the classic Together open-weight
combined runbook (GPT-J 6B, GPT-NeoX 20B, OPT 66B) across the two era-supported
classic suites (v0.2.4, v0.3.0).

WHY GENERATED: era replay is from-spec and by-name — each official run is replayed
against its own frozen run_spec.json, so the preset must ENUMERATE every run_entry
(discovery has no model-level "all runs" locator; it classifies one scenario per
entry). Each model has ~226 official runs per suite, so we derive the run_entries
from the corpus rather than hand-maintain ~1.3k lines. Re-run this whenever the
corpus scope changes; commit the emitted presets.yaml.

The emitted file is loaded via INFER_STACK_EXTRA_PRESET_FILES (see _lib.sh) so the
~1.3k run_entries stay out of the shared preset_configs.yaml.

Usage:
    python gen_presets.py [--corpus-root <classic track root>] [--out <presets.yaml>]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

# HELM model name -> (run-dir model token, HF serve id, era tokenizer alias).
# tokenizer/context come from the era WindowServices (GPTJ/GPTNeoX/OPT), identical
# at v0.2.4 and v0.3.0; all three are base models served in completions mode.
MODELS = {
    "gptj_6b": {
        "helm_model_name": "together/gpt-j-6b",
        "run_dir_token": "together_gpt-j-6b",
        "hf_model_id": "EleutherAI/gpt-j-6b",
        "helm_tokenizer_name": "EleutherAI/gpt-j-6B",
        "endpoint": "gptj6b-single",
    },
    "gptneox_20b": {
        "helm_model_name": "together/gpt-neox-20b",
        "run_dir_token": "together_gpt-neox-20b",
        "hf_model_id": "EleutherAI/gpt-neox-20b",
        "helm_tokenizer_name": "EleutherAI/gpt-neox-20b",
        "endpoint": "gptneox20b-single",
    },
    "opt_66b": {
        "helm_model_name": "together/opt-66b",
        "run_dir_token": "together_opt-66b",
        "hf_model_id": "facebook/opt-66b",
        "helm_tokenizer_name": "facebook/opt-66b",
        "endpoint": "opt66b-single",
    },
}

# suite dir name -> era key (docker/eras.yaml). Only the era-supported classic
# suites; v0.2.2/v0.2.3 have no era image (deferred).
SUITES = {"v0.2.4": "helm-v0.2.4", "v0.3.0": "helm-v0.3.0"}

SMOKE_CAP = 5
FULL_CAP = 1000  # the official classic max_eval_instances ceiling


def _run_entries(corpus_root: Path, suite: str, token: str, helm_model_name: str) -> list[str]:
    runs_dir = corpus_root / "benchmark_output" / "runs" / suite
    names = sorted(os.listdir(runs_dir))
    matched = [n for n in names if (f",model={token}" in n) or n.endswith(f"model={token}")]
    if not matched:
        raise SystemExit(f"no runs for token {token!r} under {runs_dir}")
    # The run_entry is the official run NAME with the model token in HELM form.
    return [n.replace(f"model={token}", f"model={helm_model_name}") for n in matched]


def _preset(mslug: str, meta: dict, suite: str, era_key: str, corpus_root: Path) -> tuple[str, dict]:
    key = f"era-{mslug}-{suite.replace('.', '_')}"
    entries = _run_entries(corpus_root, suite, meta["run_dir_token"], meta["helm_model_name"])
    track_root = str(corpus_root)

    def manifest(mode: str, cap: int) -> dict:
        return {
            "experiment_name": f"{key}-{mode}",
            "description": (
                f"Classic-era ({suite}) {mode}: {meta['helm_model_name']} — "
                f"from-spec era replay of {len(entries)} official run(s)."
            ),
            "run_entries": entries,
            "suite": f"{key}-{mode}",
            "max_eval_instances": cap,
            # Broad classic track root; the grid overrides --precomputed-root with a
            # per-era suite-scoped view (era_corpus_view in _lib.sh) because these
            # models' runs collide across v0.2.4/v0.3.0 with identical names.
            "precomputed_root": track_root,
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        }

    cfg = {
        "profile": meta["endpoint"],
        "bundle_name": key,
        "access_kind": "openai-compatible",
        "era": era_key,
        "profiles": [
            {
                "profile": meta["endpoint"],
                "helm_model_name": meta["helm_model_name"],
                # REQUIRED for the era WindowServiceFactory deployment branch (a
                # null tokenizer_name hard-raises at v0.2.4). The era GPTJ/GPTNeoX/
                # OPT WindowService alias, so tokenization matches the official run.
                "helm_tokenizer_name": meta["helm_tokenizer_name"],
                "protocol_mode": "completions",
            }
        ],
        "smoke_manifest": manifest("smoke", SMOKE_CAP),
        "full_manifest": manifest("full", FULL_CAP),
    }
    return key, cfg


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-root", default="/data/crfm-helm-public/classic")
    ap.add_argument("--out", default=str(Path(__file__).parent / "config" / "presets.yaml"))
    args = ap.parse_args(argv)
    corpus_root = Path(args.corpus_root)

    presets: dict[str, dict] = {}
    for suite, era_key in SUITES.items():
        for mslug, meta in MODELS.items():
            key, cfg = _preset(mslug, meta, suite, era_key, corpus_root)
            presets[key] = cfg

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# GENERATED by gen_presets.py — do not hand-edit. Era-pinned from-spec\n"
        "# presets for the classic Together open-weight combined runbook.\n"
        "# Loaded via INFER_STACK_EXTRA_PRESET_FILES (see _lib.sh).\n"
    )
    out.write_text(header + yaml.safe_dump(presets, sort_keys=True, width=1000))
    counts = {k: len(v["full_manifest"]["run_entries"]) for k, v in presets.items()}
    print(f"wrote {out} — {len(presets)} presets")
    for k, n in counts.items():
        print(f"  {k}: {n} run_entries")


if __name__ == "__main__":
    main()
