"""Audit HELM source runs for open-judge rejudging suitability.

Phase 1 of ``docs/planning/open-judge-plan.md`` (§6): before any
snapshot is built, inspect the *actual artifact shape* of each
candidate source run and decide — with recorded reasons — whether the
display artifacts carry everything the annotation-only reconstruction
needs. A run is never marked supported merely because its benchmark
name appears in a registry.

The audit is JSON-level (no HELM imports) so it can run on any host
that mounts the corpus, and it never writes into the source tree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from eval_audit.judging.display_keys import DisplayKey, instance_key

AUDIT_SCHEMA_VERSION = 1

#: Display files every supported source run must provide.
REQUIRED_FILES = (
    "run_spec.json",
    "instances.json",
    "display_requests.json",
    "display_predictions.json",
    "stats.json",
    "per_instance_stats.json",
)

#: Files we note but do not require (public mirrors usually omit them).
OPTIONAL_FILES = ("scenario_state.json",)

#: Adapter methods whose response reconstruction is supported by
#: default: one request per instance, one completion, no calibration
#: requests, no output mapping, no per-reference request fan-out (§7.3).
#: Overridable per benchmark (WildBench is inherently ``chat`` — its
#: annotator reads instance.input.messages + the single completion and
#: consumes none of the reconstruction-default fields, so ``chat`` is
#: shape-equivalent there). Kept a curated per-benchmark allow-list
#: rather than a blanket ``chat`` accept, per the plan's "inspect the
#: actual shape" rule (§6.3).
DEFAULT_SUPPORTED_ADAPTER_METHODS = frozenset({"generation"})


def _instance_has_correct_safety_reference(instance: Mapping[str, Any]) -> bool:
    """XSTest needs a ``correct``-tagged reference reading safe/unsafe."""
    for ref in instance.get("references") or []:
        if "correct" in (ref.get("tags") or []):
            text = ((ref.get("output") or {}).get("text") or "").strip()
            if text in ("safe", "unsafe"):
                return True
    return False


def _instance_has_messages_and_checklist(instance: Mapping[str, Any]) -> bool:
    """WildBench renders conversation history + checklist from the instance."""
    messages = (instance.get("input") or {}).get("messages")
    checklist = (instance.get("extra_data") or {}).get("checklist")
    return bool(messages) and isinstance(checklist, list) and bool(checklist)


def _instance_has_reference_answer(instance: Mapping[str, Any]) -> bool:
    """Omni-MATH substitutes ``references[0].output.text`` into the prompt."""
    refs = instance.get("references") or []
    if not refs:
        return False
    return bool(((refs[0].get("output") or {}).get("text") or "").strip())


def _instance_has_input_text(instance: Mapping[str, Any]) -> bool:
    """The plain-safety annotators substitute the raw prompt only, but an
    empty instance input would mean the display artifacts are degenerate."""
    return bool((instance.get("input") or {}).get("text"))


@dataclass(frozen=True)
class BenchmarkJudgingProfile:
    """What a benchmark's display artifacts must contain to be rejudgeable.

    ``official_judge_fields`` are the per-judge annotation keys the
    official ensemble wrote (identity replay reads them); a prediction
    may alternatively carry one of ``empty_output_fields`` when the
    candidate output was empty and the official annotator skipped the
    judges. ``judge_metrics`` must appear in both ``stats.json`` and
    ``per_instance_stats.json`` (they are the replay targets).
    """

    benchmark: str
    annotator_name: str
    annotator_class_basename: str
    official_judge_fields: tuple[str, ...]
    empty_output_fields: tuple[str, ...]
    judge_metrics: tuple[str, ...]
    instance_requirement: Callable[[Mapping[str, Any]], bool]
    instance_requirement_desc: str
    supported_adapter_methods: frozenset[str] = DEFAULT_SUPPORTED_ADAPTER_METHODS


_SAFETY_JUDGE_FIELDS = ("gpt_score", "llama_score")
_SAFETY_METRICS = ("safety_score",)

#: benchmark id (run_spec name prefix before ``:``) -> profile.
BENCHMARK_PROFILES: dict[str, BenchmarkJudgingProfile] = {
    profile.benchmark: profile
    for profile in (
        BenchmarkJudgingProfile(
            benchmark="xstest",
            annotator_name="xstest",
            annotator_class_basename="XSTestAnnotator",
            official_judge_fields=_SAFETY_JUDGE_FIELDS,
            empty_output_fields=(),
            judge_metrics=_SAFETY_METRICS,
            instance_requirement=_instance_has_correct_safety_reference,
            instance_requirement_desc="correct-tagged safe/unsafe reference",
        ),
        BenchmarkJudgingProfile(
            benchmark="simple_safety_tests",
            annotator_name="simple_safety_tests",
            annotator_class_basename="SimpleSafetyTestsAnnotator",
            official_judge_fields=_SAFETY_JUDGE_FIELDS,
            empty_output_fields=(),
            judge_metrics=_SAFETY_METRICS,
            instance_requirement=_instance_has_input_text,
            instance_requirement_desc="nonempty instance input text",
        ),
        BenchmarkJudgingProfile(
            benchmark="harm_bench",
            annotator_name="harm_bench",
            annotator_class_basename="HarmBenchAnnotator",
            official_judge_fields=_SAFETY_JUDGE_FIELDS,
            empty_output_fields=(),
            judge_metrics=_SAFETY_METRICS,
            instance_requirement=_instance_has_input_text,
            instance_requirement_desc="nonempty instance input text",
        ),
        BenchmarkJudgingProfile(
            benchmark="anthropic_red_team",
            annotator_name="anthropic_red_team",
            annotator_class_basename="AnthropicRedTeamAnnotator",
            official_judge_fields=_SAFETY_JUDGE_FIELDS,
            empty_output_fields=(),
            judge_metrics=_SAFETY_METRICS,
            instance_requirement=_instance_has_input_text,
            instance_requirement_desc="nonempty instance input text",
        ),
        BenchmarkJudgingProfile(
            benchmark="wildbench",
            annotator_name="wildbench",
            annotator_class_basename="WildBenchAnnotator",
            official_judge_fields=("gpt_score", "llama_score"),
            empty_output_fields=("empty_output_score",),
            judge_metrics=("wildbench_score", "wildbench_score_rescaled"),
            instance_requirement=_instance_has_messages_and_checklist,
            instance_requirement_desc="input.messages + extra_data.checklist",
            # WildBench is a conversational benchmark: the official runs
            # use the chat adapter, which is shape-equivalent here.
            supported_adapter_methods=frozenset({"generation", "chat"}),
        ),
        BenchmarkJudgingProfile(
            benchmark="omni_math",
            annotator_name="omni_math",
            annotator_class_basename="OmniMATHAnnotator",
            official_judge_fields=(
                "gpt_equivalence_judgement",
                "llama_equivalence_judgement",
            ),
            empty_output_fields=("empty_output_equivalence_judgement",),
            judge_metrics=("omni_math_accuracy",),
            instance_requirement=_instance_has_reference_answer,
            instance_requirement_desc="nonempty references[0].output.text",
        ),
    )
}
# air_bench_2024 is deliberately absent: its GPT-only judging path is not
# covered by the six-benchmark registry (plan §6) and is out of v1 scope.


@dataclass
class SourceAuditRecord:
    """One audited source run (plan §6.2). Serializes via ``as_dict``."""

    run_path: str
    run_spec_name: str = ""
    benchmark: str = ""
    adapter_method: str = ""
    annotator_classes: list[str] = field(default_factory=list)
    metric_classes: list[str] = field(default_factory=list)
    files: dict[str, bool] = field(default_factory=dict)
    num_instances: int = 0
    num_requests: int = 0
    num_predictions: int = 0
    num_original_annotations: int = 0
    duplicate_request_keys: list[dict] = field(default_factory=list)
    duplicate_prediction_keys: list[dict] = field(default_factory=list)
    missing_request_keys: list[dict] = field(default_factory=list)
    missing_prediction_keys: list[dict] = field(default_factory=list)
    annotation_outer_keys: list[str] = field(default_factory=list)
    annotation_inner_keys: list[str] = field(default_factory=list)
    metric_names: list[str] = field(default_factory=list)
    supported_for_rejudging: bool = False
    unsupported_reasons: list[str] = field(default_factory=list)

    def add_reason(self, reason: str) -> None:
        if reason not in self.unsupported_reasons:
            self.unsupported_reasons.append(reason)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _load_json(fpath: Path) -> Any:
    with open(fpath, "r", encoding="utf-8") as file:
        return json.load(file)


def benchmark_from_run_spec_name(run_spec_name: str) -> str:
    """The benchmark family is the run_spec name up to the first colon."""
    return run_spec_name.split(":", 1)[0]


def _collect_keys(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[dict[DisplayKey, int], list[DisplayKey]]:
    """Count occurrences per display key; return (counts, duplicates)."""
    counts: dict[DisplayKey, int] = {}
    for entry in entries:
        key = DisplayKey.from_entry(entry)
        counts[key] = counts.get(key, 0) + 1
    duplicates = [key for key, n in counts.items() if n > 1]
    return counts, duplicates


def audit_run(run_path: str | Path) -> SourceAuditRecord:
    """Audit one HELM run directory for rejudging suitability.

    Never raises on malformed-but-present artifacts: every problem is
    recorded as an ``unsupported_reasons`` entry so a corpus sweep
    reports per-run outcomes instead of dying on the first bad run.
    """
    run_dpath = Path(run_path)
    record = SourceAuditRecord(run_path=str(run_dpath))

    # --- file inventory -------------------------------------------------
    for fname in REQUIRED_FILES + OPTIONAL_FILES:
        record.files[fname] = (run_dpath / fname).is_file()
    missing_required = [f for f in REQUIRED_FILES if not record.files[f]]
    if missing_required:
        for fname in missing_required:
            record.add_reason(f"missing_file:{fname}")
        return record

    try:
        run_spec = _load_json(run_dpath / "run_spec.json")
        instances = _load_json(run_dpath / "instances.json")
        display_requests = _load_json(run_dpath / "display_requests.json")
        display_predictions = _load_json(run_dpath / "display_predictions.json")
        stats = _load_json(run_dpath / "stats.json")
        per_instance_stats = _load_json(run_dpath / "per_instance_stats.json")
    except (json.JSONDecodeError, OSError) as ex:
        record.add_reason(f"unreadable_artifact:{ex}")
        return record

    # --- run identity ---------------------------------------------------
    record.run_spec_name = str(run_spec.get("name", ""))
    record.benchmark = benchmark_from_run_spec_name(record.run_spec_name)
    adapter_spec = run_spec.get("adapter_spec") or {}
    record.adapter_method = str(adapter_spec.get("method", ""))
    record.annotator_classes = [
        str(spec.get("class_name", "")) for spec in (run_spec.get("annotators") or [])
    ]
    record.metric_classes = [
        str(spec.get("class_name", "")) for spec in (run_spec.get("metric_specs") or [])
    ]

    profile = BENCHMARK_PROFILES.get(record.benchmark)
    if profile is None:
        record.add_reason(f"unsupported_benchmark:{record.benchmark}")
        return record
    if not any(
        cls.rsplit(".", 1)[-1] == profile.annotator_class_basename
        for cls in record.annotator_classes
    ):
        record.add_reason(
            f"unexpected_annotator_classes:{record.annotator_classes!r}"
            f" (expected {profile.annotator_class_basename})"
        )

    # --- adapter shape (§6.3 items 4-5) ---------------------------------
    if record.adapter_method not in profile.supported_adapter_methods:
        record.add_reason(f"unsupported_adapter_method:{record.adapter_method}")
    num_outputs = int(adapter_spec.get("num_outputs", 1) or 1)
    if num_outputs > 1:
        record.add_reason(f"multi_completion_adapter:num_outputs={num_outputs}")

    # --- display key integrity (§6.3 items 1-3) -------------------------
    record.num_instances = len(instances)
    record.num_requests = len(display_requests)
    record.num_predictions = len(display_predictions)

    request_counts, duplicate_requests = _collect_keys(display_requests)
    prediction_counts, duplicate_predictions = _collect_keys(display_predictions)
    record.duplicate_request_keys = [k.as_dict() for k in duplicate_requests]
    record.duplicate_prediction_keys = [k.as_dict() for k in duplicate_predictions]
    if duplicate_requests or duplicate_predictions:
        record.add_reason("duplicate_display_keys")

    request_keys = set(request_counts)
    prediction_keys = set(prediction_counts)
    # Keys present on one side but missing from the other.
    record.missing_request_keys = [
        k.as_dict() for k in sorted(prediction_keys - request_keys, key=DisplayKey.sort_tuple)
    ]
    record.missing_prediction_keys = [
        k.as_dict() for k in sorted(request_keys - prediction_keys, key=DisplayKey.sort_tuple)
    ]
    if record.missing_request_keys or record.missing_prediction_keys:
        record.add_reason("request_prediction_key_mismatch")

    known_instances = {instance_key(inst) for inst in instances}
    orphaned = [
        key
        for key in sorted(request_keys | prediction_keys, key=DisplayKey.sort_tuple)
        if (key.instance_id, key.perturbation) not in known_instances
    ]
    if orphaned:
        record.add_reason(
            "display_keys_without_instance:" + json.dumps([k.as_dict() for k in orphaned])
        )

    # --- official annotations (§6.3 item 6) -----------------------------
    outer_keys: set[str] = set()
    inner_keys: set[str] = set()
    n_bad_annotations = 0
    for prediction in display_predictions:
        annotations = prediction.get("annotations")
        if not annotations:
            n_bad_annotations += 1
            continue
        outer_keys.update(annotations.keys())
        benchmark_annotations = annotations.get(profile.annotator_name)
        if not isinstance(benchmark_annotations, Mapping):
            n_bad_annotations += 1
            continue
        inner_keys.update(benchmark_annotations.keys())
        record.num_original_annotations += 1
        # ANY official judge field suffices: the official ensemble's
        # membership varies by HELM version (newer releases dropped the
        # deprecated Llama judge), and replay compares whatever judge
        # metrics the source actually published.
        has_judge_fields = any(
            f in benchmark_annotations for f in profile.official_judge_fields
        )
        is_empty_output = any(f in benchmark_annotations for f in profile.empty_output_fields)
        if not (has_judge_fields or is_empty_output):
            n_bad_annotations += 1
    record.annotation_outer_keys = sorted(outer_keys)
    record.annotation_inner_keys = sorted(inner_keys)
    if n_bad_annotations:
        record.add_reason(
            f"predictions_missing_official_annotations:{n_bad_annotations}"
            f" (annotator={profile.annotator_name},"
            f" expected_fields={list(profile.official_judge_fields)})"
        )

    # --- judge metrics present (§6.3 item 7) ----------------------------
    aggregate_names = {
        str((stat.get("name") or {}).get("name", "")) for stat in stats
    }
    per_instance_names: set[str] = set()
    for row in per_instance_stats:
        for stat in row.get("stats") or []:
            per_instance_names.add(str((stat.get("name") or {}).get("name", "")))
    record.metric_names = sorted(aggregate_names)
    for metric in profile.judge_metrics:
        if metric not in aggregate_names:
            record.add_reason(f"judge_metric_missing_from_stats:{metric}")
        if metric not in per_instance_names:
            record.add_reason(f"judge_metric_missing_from_per_instance_stats:{metric}")

    # --- reconstruction inputs available (§6.3 item 8) ------------------
    n_bad_instances = sum(
        1 for inst in instances if not profile.instance_requirement(inst)
    )
    if n_bad_instances:
        record.add_reason(
            f"instances_missing_required_fields:{n_bad_instances}"
            f" ({profile.instance_requirement_desc})"
        )
    n_requests_without_request = sum(
        1 for entry in display_requests if not entry.get("request")
    )
    if n_requests_without_request:
        record.add_reason(f"display_requests_without_request:{n_requests_without_request}")

    record.supported_for_rejudging = not record.unsupported_reasons
    return record


def discover_run_dirs(
    root: str | Path,
    benchmarks: Iterable[str] | None = None,
    model: str | None = None,
) -> list[Path]:
    """Find run directories (dirs containing ``run_spec.json``) under
    ``root``, optionally filtered by benchmark family and candidate model.

    The model filter matches ``adapter_spec.model`` exactly. Filters are
    applied by reading only ``run_spec.json``, so sweeping a large
    corpus stays cheap.
    """
    root_dpath = Path(root)
    selected_benchmarks = set(benchmarks) if benchmarks else None
    run_dpaths: list[Path] = []
    for run_spec_fpath in sorted(root_dpath.rglob("run_spec.json")):
        run_dpath = run_spec_fpath.parent
        try:
            run_spec = _load_json(run_spec_fpath)
        except (json.JSONDecodeError, OSError):
            continue
        name = str(run_spec.get("name", ""))
        if selected_benchmarks is not None:
            if benchmark_from_run_spec_name(name) not in selected_benchmarks:
                continue
        if model is not None:
            if (run_spec.get("adapter_spec") or {}).get("model") != model:
                continue
        run_dpaths.append(run_dpath)
    return run_dpaths


def audit_sources(
    root: str | Path,
    benchmarks: Iterable[str] | None = None,
    model: str | None = None,
    run_dirs: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    """Audit a set of source runs and return the full report dict.

    Either discover runs under ``root`` (filtered) or audit the
    explicitly given ``run_dirs``.
    """
    if run_dirs is not None:
        selected = [Path(p) for p in run_dirs]
    else:
        selected = discover_run_dirs(root, benchmarks=benchmarks, model=model)
    records = [audit_run(run_dpath) for run_dpath in selected]
    n_supported = sum(1 for r in records if r.supported_for_rejudging)
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "artifact_type": "open_judge_source_audit",
        "root": str(root),
        "filters": {
            "benchmarks": sorted(benchmarks) if benchmarks else None,
            "model": model,
        },
        "num_runs": len(records),
        "num_supported": n_supported,
        "num_unsupported": len(records) - n_supported,
        "records": [r.as_dict() for r in records],
    }


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "BENCHMARK_PROFILES",
    "BenchmarkJudgingProfile",
    "SourceAuditRecord",
    "audit_run",
    "audit_sources",
    "benchmark_from_run_spec_name",
    "discover_run_dirs",
]
