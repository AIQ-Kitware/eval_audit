"""Generate the EEE-only demo fixtures.

The product story: "you have a set of official evals in EEE format and a
set of locally-reproduced evals in EEE format; run them through eval_audit
and get a per-pair core-metric report + an aggregate publication tree."

This script synthesizes a minimal but exercisable corpus of EEE artifacts
(3 toy models × 3 toy datasets, plus a duplicate local "repeat" attempt
on one cell) so the demo runs in seconds and the agreement / drift
patterns are interpretable. Run it whenever the EEE schema changes; the
generated tree is checked in for tests + tutorials.

Drift patterns (chosen so each cell exercises a different report path):

  cell                       local agree@0   notes
  -------------------------- --------------- ----------------------------------
  toy/m1-small  × arc_easy        1.000   perfect baseline; also has a second
                                          local attempt (another uuid in the
                                          SAME experiment/model dir) so the
                                          planner emits a local_repeat
                                          comparison alongside official_vs_local
  toy/m1-small  × truthful_qa     0.750   single-instance flip on quasi_em
  toy/m1-small  × imdb            0.000   complete divergence (sentiment flip)
  toy/m2-medium × arc_easy        1.000
  toy/m2-medium × truthful_qa     1.000
  toy/m2-medium × imdb            0.750   single-instance flip
  toy/m3-large  × arc_easy        1.000
  toy/m3-large  × truthful_qa     1.000
  toy/m3-large  × imdb            1.000

"""
from __future__ import annotations

import argparse
import json
import shutil
import uuid as uuidlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


MODELS = [
    {"id": "toy/m1-small", "developer": "toy", "name": "m1-small"},
    {"id": "toy/m2-medium", "developer": "toy", "name": "m2-medium"},
    {"id": "toy/m3-large", "developer": "toy", "name": "m3-large"},
]

# Each benchmark gets a fixed sample id list and a "ground truth" choice
# that the official run gets right; the local run is then either the same
# (perfect agreement), a flipped subset (partial drift), or the opposite
# (complete divergence).
BENCHMARKS = {
    "arc_easy": {
        "metrics": ["exact_match", "quasi_exact_match"],
        "samples": [
            {"id": "arc_easy/0", "ref": "C"},
            {"id": "arc_easy/1", "ref": "B"},
            {"id": "arc_easy/2", "ref": "A"},
            {"id": "arc_easy/3", "ref": "D"},
        ],
    },
    "truthful_qa": {
        "metrics": ["exact_match", "quasi_exact_match"],
        "samples": [
            {"id": "truthful_qa/0", "ref": "B"},
            {"id": "truthful_qa/1", "ref": "A"},
            {"id": "truthful_qa/2", "ref": "C"},
            {"id": "truthful_qa/3", "ref": "B"},
        ],
    },
    "imdb": {
        "metrics": ["exact_match", "quasi_exact_match"],
        "samples": [
            {"id": "imdb/0", "ref": "positive"},
            {"id": "imdb/1", "ref": "negative"},
            {"id": "imdb/2", "ref": "positive"},
            {"id": "imdb/3", "ref": "negative"},
        ],
    },
}


# Drift maps: per (model, benchmark), which sample indices the local run
# gets WRONG (and which the official run also gets wrong, if any). 1.0 ==
# correct, 0.0 == incorrect.
DRIFT = {
    ("toy/m1-small", "arc_easy"): {"official_wrong": [], "local_wrong": []},
    ("toy/m1-small", "truthful_qa"): {"official_wrong": [], "local_wrong": [1]},
    ("toy/m1-small", "imdb"): {"official_wrong": [], "local_wrong": [0, 1, 2, 3]},
    ("toy/m2-medium", "arc_easy"): {"official_wrong": [], "local_wrong": []},
    ("toy/m2-medium", "truthful_qa"): {"official_wrong": [], "local_wrong": []},
    ("toy/m2-medium", "imdb"): {"official_wrong": [], "local_wrong": [2]},
    ("toy/m3-large", "arc_easy"): {"official_wrong": [], "local_wrong": []},
    ("toy/m3-large", "truthful_qa"): {"official_wrong": [], "local_wrong": []},
    ("toy/m3-large", "imdb"): {"official_wrong": [], "local_wrong": []},
}


def _stable_uuid(*parts: str) -> str:
    """Deterministic uuid5 so the fixture is bit-stable across regenerations."""
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, "::".join(parts)))


def _aggregate_log(
    *,
    model: dict,
    benchmark: str,
    source_org: str,
    eval_uuid: str,
    metric_scores: dict[str, float],
    sample_ids: list[str],
) -> dict:
    """Build a minimal-but-valid EvaluationLog dict.

    Schema-correctness is verified at load time by every_eval_ever's
    Pydantic models — keep this in sync if the schema changes.
    """
    return {
        "schema_version": "0.2.2",
        "evaluation_id": f"{benchmark}/{model['id']}/{eval_uuid}",
        "evaluation_timestamp": "1700000000",
        "retrieved_timestamp": "1700100000",
        "source_metadata": {
            "source_name": "eee_only_demo",
            "source_type": "evaluation_run",
            "source_organization_name": source_org,
            "evaluator_relationship": "third_party",
        },
        "eval_library": {"name": "eee_only_demo", "version": "0.0.1"},
        "model_info": {
            "name": model["id"],
            "id": model["id"],
            "developer": model["developer"],
            "inference_platform": "demo",
        },
        "evaluation_results": [
            {
                "evaluation_result_id": metric_id,
                "evaluation_name": benchmark,
                "source_data": {
                    "dataset_name": benchmark,
                    "source_type": "hf_dataset",
                    "samples_number": len(sample_ids),
                    "sample_ids": list(sample_ids),
                },
                "metric_config": {
                    "metric_id": metric_id,
                    "metric_name": metric_id,
                    "score_type": "binary",
                    "lower_is_better": False,
                },
                "score_details": {"score": float(score)},
            }
            for metric_id, score in metric_scores.items()
        ],
    }


def _instance_records(
    *,
    model: dict,
    benchmark: str,
    eval_uuid: str,
    samples: list[dict],
    metrics: list[str],
    correct_mask: list[bool],
) -> list[dict]:
    rows = []
    for sample, is_correct in zip(samples, correct_mask):
        for metric_id in metrics:
            rows.append({
                "schema_version": "0.2.2",
                "evaluation_id": f"{eval_uuid}_samples",
                "model_id": model["id"],
                "evaluation_name": benchmark,
                "evaluation_result_id": metric_id,
                "sample_id": sample["id"],
                "sample_hash": _stable_uuid("hash", benchmark, sample["id"]),
                "interaction_type": "single_turn",
                "input": {
                    "raw": f"<demo prompt for {sample['id']}>",
                    "reference": [sample["ref"]],
                    "choices": ["A", "B", "C", "D"] if benchmark != "imdb" else ["positive", "negative"],
                },
                "output": {
                    "raw": [sample["ref"] if is_correct else "WRONG"],
                    "reasoning_trace": [],
                },
                "messages": None,
                "answer_attribution": [],
                "evaluation": {
                    "score": 1.0 if is_correct else 0.0,
                    "is_correct": is_correct,
                    "num_turns": None,
                    "tool_calls_count": None,
                },
                "token_usage": None,
                "performance": None,
                "error": None,
                "metadata": None,
            })
    return rows


def _write_artifact(
    out_dir: Path,
    *,
    model: dict,
    benchmark: str,
    source_org: str,
    correct_mask: list[bool],
    eval_uuid: str | None = None,
) -> Path:
    bench_def = BENCHMARKS[benchmark]
    samples = bench_def["samples"]
    metrics = bench_def["metrics"]

    if eval_uuid is None:
        eval_uuid = _stable_uuid(source_org, benchmark, model["id"])

    # Aggregate run-level score: fraction correct (same across metrics in
    # this toy demo; real data would have per-metric variation).
    fraction = sum(correct_mask) / len(correct_mask)
    metric_scores = {m: fraction for m in metrics}

    artifact_dir = out_dir / benchmark / model["developer"] / model["name"]
    artifact_dir.mkdir(parents=True, exist_ok=True)

    aggregate = _aggregate_log(
        model=model,
        benchmark=benchmark,
        source_org=source_org,
        eval_uuid=eval_uuid,
        metric_scores=metric_scores,
        sample_ids=[s["id"] for s in samples],
    )
    aggregate_fpath = artifact_dir / f"{eval_uuid}.json"
    aggregate_fpath.write_text(json.dumps(aggregate, indent=2) + "\n")

    instance_rows = _instance_records(
        model=model,
        benchmark=benchmark,
        eval_uuid=eval_uuid,
        samples=samples,
        metrics=metrics,
        correct_mask=correct_mask,
    )
    samples_fpath = artifact_dir / f"{eval_uuid}_samples.jsonl"
    samples_fpath.write_text(
        "\n".join(json.dumps(row) for row in instance_rows) + "\n"
    )
    return aggregate_fpath


def build(out_root: Path) -> dict[str, list[str]]:
    """Generate all official + local EEE artifacts under ``out_root``.

    Returns a manifest dict with the relative paths so callers can verify
    or regenerate downstream pairings.
    """
    if out_root.exists():
        shutil.rmtree(out_root)
    official_root = out_root / "official"
    local_root = out_root / "local"
    official_root.mkdir(parents=True)
    local_root.mkdir(parents=True)

    manifest: dict[str, list[str]] = {"official": [], "local": []}
    for model in MODELS:
        for benchmark, bench_def in BENCHMARKS.items():
            samples = bench_def["samples"]
            drift = DRIFT[(model["id"], benchmark)]
            official_correct = [i not in drift["official_wrong"] for i in range(len(samples))]
            local_correct = [i not in drift["local_wrong"] for i in range(len(samples))]

            official_fpath = _write_artifact(
                official_root,
                model=model,
                benchmark=benchmark,
                source_org="eee_only_demo_official",
                correct_mask=official_correct,
            )
            manifest["official"].append(str(official_fpath.relative_to(out_root)))

            local_fpath = _write_artifact(
                local_root / "primary",
                model=model,
                benchmark=benchmark,
                source_org="eee_only_demo_local",
                correct_mask=local_correct,
            )
            manifest["local"].append(str(local_fpath.relative_to(out_root)))

            # Multi-attempt: a second local attempt of the m1×arc_easy run to
            # flex the planner's local_repeat path. Per the documented EEE
            # layout, repeat attempts are additional ``<uuid>`` pairs in the
            # SAME ``<experiment>/<benchmark>/<dev>/<model>/`` directory (D-1) —
            # not a separate ``repeat/`` experiment, which would split the pair
            # across experiments (distinct experiment_names) and drop the
            # cross-experiment packet from the aggregate roll-up. The duplicate
            # uses a distinct eval_uuid (distinct filename) but the same data.
            if (model["id"], benchmark) == ("toy/m1-small", "arc_easy"):
                repeat_fpath = _write_artifact(
                    local_root / "primary",
                    model=model,
                    benchmark=benchmark,
                    source_org="eee_only_demo_local",
                    correct_mask=local_correct,
                    eval_uuid=_stable_uuid("repeat", benchmark, model["id"]),
                )
                manifest["local"].append(str(repeat_fpath.relative_to(out_root)))

    manifest_fpath = out_root / "fixture_manifest.json"
    manifest_fpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-root",
        default=str(Path(__file__).parent / "eee_artifacts"),
        help="Output directory for the generated EEE artifact tree.",
    )
    args = parser.parse_args()
    out_root = Path(args.out_root).expanduser().resolve()
    manifest = build(out_root)
    print(f"wrote {len(manifest['official'])} official + {len(manifest['local'])} local artifacts under {out_root}")


if __name__ == "__main__":
    main()
