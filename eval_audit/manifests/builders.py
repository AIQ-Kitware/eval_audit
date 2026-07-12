from __future__ import annotations

import argparse

from eval_audit.infra.logging import setup_cli_logging
import fnmatch
import math
from pathlib import Path
from typing import Any

import kwutil

from eval_audit.infra.api import dump_yaml, env_defaults, repo_run_details_fpath, repo_run_specs_fpath
from eval_audit.manifests.models import ManifestSpec


REPRO_MODEL_OVERRIDES = (
    "configs/debug/"
    "repro_model_overrides.yaml"
)

MODELS_REQUIRING_LOCAL_OVERRIDE = {
    "lmsys/vicuna-7b-v1.3",
    "qwen/qwen2-72b-instruct",
    "qwen/qwen2.5-7b-instruct-turbo",
    "qwen/qwen2.5-72b-instruct-turbo",
}

# Verbatim-replay sentinel for --max-eval-instances (D-5). When set, the
# exact-path replay materializer leaves the official run_spec.json cap
# untouched (default_max_eval_instances=None) instead of rewriting it to a
# numeric value — the only way to express "keep the official cap" under the
# replay-verbatim rule. A distinguishable non-null marker (not None) is used so
# it cannot be confused with "cap unset, fall through to the 1000 default".
OFFICIAL_CAP_SENTINEL = "official"


def _max_eval_instances_arg(value: str) -> int | str:
    """Parse ``--max-eval-instances``: an integer cap, or the literal ``official``.

    ``official`` is the verbatim-replay sentinel (keep the official run_spec.json
    cap). Only meaningful on the exact-path replay path (``--run-spec-sources-fpath``).
    """
    if value == OFFICIAL_CAP_SENTINEL:
        return OFFICIAL_CAP_SENTINEL
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--max-eval-instances must be an integer or '{OFFICIAL_CAP_SENTINEL}', "
            f"got {value!r}"
        )


def _load_run_specs(fpath: str | None) -> list[str]:
    path = Path(fpath) if fpath else repo_run_specs_fpath()
    data = kwutil.Yaml.load(path)
    if not isinstance(data, list):
        raise TypeError(f"run specs at {path} must decode to a list")
    run_specs = [str(x) for x in data]
    return list(dict.fromkeys(run_specs))


def _load_run_spec_sources(fpath: str) -> list[dict[str, Any]]:
    """Load exact-path replay sources (rel-path plan §4.5).

    A YAML/JSON list of ``{run_entry, rel_path, model_deployment?, lease_endpoint?,
    max_eval_instances?}``. Validated + normalized through ``RunSpecSource`` so the
    same coercion the materializer uses guards the manifest.
    """
    from eval_audit.manifests.run_spec_materializer import (
        RunSpecSource,
        source_to_dict,
    )

    data = kwutil.Yaml.load(Path(fpath))
    if not isinstance(data, list):
        raise TypeError(f"run_spec sources at {fpath} must decode to a list")
    return [source_to_dict(RunSpecSource.from_dict(dict(item))) for item in data]


def _load_run_details(fpath: str | None) -> list[dict[str, Any]]:
    path = Path(fpath) if fpath else repo_run_details_fpath()
    if not path.exists():
        return []
    data = kwutil.Yaml.load(path)
    if not isinstance(data, list):
        raise TypeError(f"run details at {path} must decode to a list")
    rows = [row for row in data if isinstance(row, dict)]
    return rows


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(text, pat) for pat in patterns)


def _infer_benchmark(run_entry: str) -> str:
    left = run_entry.split(":", 1)[0]
    return left.split(",", 1)[0]


def _infer_model(run_entry: str) -> str | None:
    for part in run_entry.replace(":", ",").split(","):
        if part.startswith("model="):
            return part.split("=", 1)[1]
    return None


def _sort_key(run_entry: str) -> tuple[str, str, str]:
    model = _infer_model(run_entry) or ""
    benchmark = _infer_benchmark(run_entry)
    return (model, benchmark, run_entry)


def _filter_run_entries(
    run_entries: list[str],
    *,
    include_patterns: list[str],
    exclude_patterns: list[str],
    models: list[str],
    benchmarks: list[str],
) -> list[str]:
    filtered = []
    for run_entry in run_entries:
        if include_patterns and not _matches_any(run_entry, include_patterns):
            continue
        if exclude_patterns and _matches_any(run_entry, exclude_patterns):
            continue
        model = _infer_model(run_entry)
        benchmark = _infer_benchmark(run_entry)
        if models and model not in set(models):
            continue
        if benchmarks and benchmark not in set(benchmarks):
            continue
        filtered.append(run_entry)
    return filtered


def _shard_entries(
    run_entries: list[str],
    *,
    num_shards: int | None,
    shard_index: int | None,
) -> list[str]:
    if num_shards is None and shard_index is None:
        return run_entries
    if num_shards is None or shard_index is None:
        raise SystemExit("--num-shards and --shard-index must be provided together")
    if num_shards <= 0:
        raise SystemExit("--num-shards must be positive")
    if shard_index < 0 or shard_index >= num_shards:
        raise SystemExit("--shard-index must satisfy 0 <= shard-index < num-shards")
    return [entry for idx, entry in enumerate(run_entries) if idx % num_shards == shard_index]


def _choose_model_override(run_entries: list[str], force_nochat: bool) -> str | None:
    models = {_infer_model(entry) for entry in run_entries}
    needs_override = bool(models & MODELS_REQUIRING_LOCAL_OVERRIDE)
    if force_nochat or needs_override:
        return REPRO_MODEL_OVERRIDES
    return None


def _detail_lut(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lut: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("run_spec_name")
        if isinstance(key, str) and key not in lut:
            lut[key] = row
    return lut


def _resolve_manifest_era(
    *,
    era_arg: str,
    run_spec_sources: list[dict[str, Any]],
    precomputed_root: str | None,
) -> str | None:
    """Resolve ``--era`` (auto | modern | <key>) to a concrete era key or None.

    ``auto`` derives the era from the sources' rel-paths (one manifest = one era;
    a mixed-era set raises). ``modern`` forces None (modern image). An explicit
    key pins that era but still runs the mixed-era check so an inconsistent source
    set is rejected rather than silently mislabeled. Returns the era key string,
    or ``None`` for the modern era.
    """
    from eval_audit.eras import load_era_registry, resolve_era_for_sources

    if era_arg == "modern":
        return None
    if not run_spec_sources:
        # No exact-path sources: only 'auto'/'modern' are meaningful (both modern).
        if era_arg != "auto":
            raise SystemExit(
                f"--era {era_arg} requires --run-spec-sources-fpath (exact-path "
                "replay); era has no meaning without pinned sources."
            )
        return None
    if not precomputed_root:
        raise SystemExit(
            "--era resolution requires --precomputed-root (the host root the "
            "run_spec source rel-paths resolve against)."
        )
    registry = load_era_registry()
    # Raises on a mixed-era source set regardless of auto/explicit; surface it as a
    # clean CLI error rather than a traceback.
    try:
        resolved = resolve_era_for_sources(
            precomputed_root, run_spec_sources, registry=registry
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if era_arg == "auto":
        return resolved.key if resolved is not None else None
    # Explicit key: must be known, and must agree with what the sources resolve to.
    if era_arg not in registry:
        raise SystemExit(
            f"unknown --era {era_arg!r}; known: {', '.join(sorted(registry)) or '<none>'}"
        )
    resolved_key = resolved.key if resolved is not None else None
    if resolved_key is not None and resolved_key != era_arg:
        raise SystemExit(
            f"--era {era_arg} disagrees with the run_spec sources, which resolve to "
            f"{resolved_key!r}. Fix the sources or pass --era auto."
        )
    return era_arg


def _build_manifest(
    *,
    experiment_name: str,
    description: str,
    suite: str,
    run_entries: list[str],
    max_eval_instances: int | str,
    tmux_workers: int,
    devices: str,
    model_deployments_fpath: str | None,
    from_run_spec: bool = False,
    precomputed_root: str | None = None,
    model_deployment: str | None = None,
    run_spec_sources: list[dict[str, Any]] | None = None,
    era: str | None = None,
) -> dict[str, Any]:
    return ManifestSpec(
        experiment_name=experiment_name,
        description=description,
        run_entries=run_entries,
        max_eval_instances=max_eval_instances,
        suite=suite,
        devices=devices,
        tmux_workers=tmux_workers,
        model_deployments_fpath=model_deployments_fpath,
        from_run_spec=from_run_spec,
        precomputed_root=precomputed_root,
        model_deployment=model_deployment,
        run_spec_sources=list(run_spec_sources or []),
        era=era,
    ).to_dict()


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--selection-output", default=None)
    parser.add_argument("--run-specs-fpath", default=None)
    parser.add_argument("--run-details-fpath", default=None)
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--description", default=None)
    parser.add_argument("--devices", default=None)
    parser.add_argument("--tmux-workers", type=int, default=None)
    parser.add_argument(
        "--max-eval-instances",
        type=_max_eval_instances_arg,
        default=None,
        help=(
            "Instance cap applied to every replayed run. An integer rewrites "
            "adapter_spec.max_eval_instances; the literal 'official' keeps the "
            "official run_spec.json cap unchanged (verbatim replay, only valid "
            "with --run-spec-sources-fpath). Defaults to 1000 when omitted."
        ),
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--num-shards", type=int, default=None)
    parser.add_argument("--shard-index", type=int, default=None)
    parser.add_argument("--single-gpu", action="store_true")
    parser.add_argument("--sort", default="model_benchmark", choices=["model_benchmark", "input"])
    parser.add_argument("--force-vicuna-nochat", action="store_true")
    parser.add_argument("--include-pattern", action="append", default=[])
    parser.add_argument("--exclude-pattern", action="append", default=[])
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--benchmark", action="append", default=[])
    parser.add_argument(
        "--from-run-spec",
        action="store_true",
        help=(
            "Replay each run's fully-resolved run_spec.json directly (faithful "
            "replay) instead of reconstructing a run-entry string and re-parsing "
            "it through helm-run. Requires --precomputed-root (the recipe source)."
        ),
    )
    parser.add_argument(
        "--precomputed-root",
        default=None,
        help=(
            "Root searched for official HELM run dirs. In --from-run-spec mode "
            "this is the RECIPE SOURCE the run_spec.json is read from (mandatory). "
            "On the run-entry discovery path it is bind-mounted read-only into the "
            "container; on the exact-path replay path (--run-spec-sources-fpath) it "
            "is the HOST root the rel_paths resolve against (the container instead "
            "mounts the tiny staging dir of materialized copies)."
        ),
    )
    parser.add_argument(
        "--run-spec-sources-fpath",
        default=None,
        help=(
            "Exact-path replay (rel-path plan): a YAML/JSON list of "
            "{run_entry, rel_path, model_deployment?, lease_endpoint?, "
            "max_eval_instances?}. Each names an official run by its path relative "
            "to --precomputed-root; the schedule-time materializer reads it, applies "
            "the substitutions as raw-JSON edits, and Stage 3 replays the copy. "
            "Implies --from-run-spec; supersedes run-entry token discovery."
        ),
    )
    parser.add_argument(
        "--model-deployment",
        default=None,
        help=(
            "Deployment-rewrite target (only meaningful with --from-run-spec). The "
            "from-spec CLI rewrites adapter_spec.model_deployment to this LOCAL "
            "deployment name so the produced run records the endpoint that served "
            "it and the audit reports same_deployment=no. It MUST name a deployment "
            "registered in the run's model_deployments.yaml. When omitted, the "
            "official deployment name replays verbatim (pure by-name)."
        ),
    )
    parser.add_argument(
        "--era",
        default="auto",
        help=(
            "Era-pinned replay (pre-v0.5). 'auto' (default) resolves the era from "
            "the run_spec sources' rel-paths (one manifest = one era; a mixed-era "
            "set is a hard error); 'modern' forces the modern image; an explicit "
            "era key (e.g. helm-v0.2.4, from docker/eras.yaml) pins that era. Only "
            "valid with --run-spec-sources-fpath (exact-path verbatim replay)."
        ),
    )
    args = parser.parse_args(argv)

    run_spec_sources: list[dict[str, Any]] = []
    if args.run_spec_sources_fpath:
        # Exact-path replay is a from-spec variant; turn it on implicitly so the
        # bridge routes to the from-spec pipeline.
        args.from_run_spec = True
        run_spec_sources = _load_run_spec_sources(args.run_spec_sources_fpath)
        if not run_spec_sources:
            raise SystemExit(
                f"{args.run_spec_sources_fpath} contained no run_spec sources"
            )

    if args.from_run_spec and not args.precomputed_root:
        raise SystemExit(
            "--from-run-spec requires --precomputed-root (the recipe source from "
            "which each official run_spec.json is read)"
        )
    if args.model_deployment and not args.from_run_spec:
        raise SystemExit(
            "--model-deployment is only meaningful with --from-run-spec (it "
            "rewrites the replayed run_spec.json's adapter_spec.model_deployment); "
            "the run-entry path carries the deployment in the run-entry string."
        )
    if args.max_eval_instances == OFFICIAL_CAP_SENTINEL and not run_spec_sources:
        # The 'official' sentinel means "leave the official cap untouched", which
        # is only realizable on the exact-path replay path where the materializer
        # edits the run_spec.json. The run-entry / from-spec-discovery paths pass
        # the cap to helm-run as an integer, so they cannot honor the sentinel.
        raise SystemExit(
            "--max-eval-instances official is only supported with "
            "--run-spec-sources-fpath (exact-path verbatim replay); the run-entry "
            "and from-spec-discovery paths require a numeric cap."
        )

    defaults = env_defaults()
    sources_by_label: dict[str, dict[str, Any]] = {}
    if run_spec_sources:
        # The run-entry "list" is the sources' labels; reuse the existing
        # filter/sort/shard/limit machinery on them, then keep the matching sources.
        # P1-23: duplicate labels used to collapse silently via setdefault, so
        # the manifest scheduled fewer runs than declared. Raise instead.
        for source in run_spec_sources:
            label = source["run_entry"]
            if label in sources_by_label:
                raise SystemExit(
                    f"Duplicate run_entry label in run_spec sources: {label!r}. "
                    "Each source must carry a unique run_entry label; duplicates "
                    "would silently schedule fewer runs than declared."
                )
            sources_by_label[label] = source
        run_entries = list(sources_by_label)
        run_details = []
        detail_lut = {}
    else:
        run_entries = _load_run_specs(args.run_specs_fpath)
        run_details = _load_run_details(args.run_details_fpath)
        detail_lut = _detail_lut(run_details)

    run_entries = _filter_run_entries(
        run_entries,
        include_patterns=args.include_pattern,
        exclude_patterns=args.exclude_pattern,
        models=args.model,
        benchmarks=args.benchmark,
    )
    if args.sort == "model_benchmark":
        run_entries = sorted(run_entries, key=_sort_key)
    run_entries = _shard_entries(
        run_entries,
        num_shards=args.num_shards,
        shard_index=args.shard_index,
    )
    if args.limit is not None:
        run_entries = run_entries[: args.limit]
    if not run_entries:
        raise SystemExit("No run entries matched the requested filters")

    # Exact-path replay: keep the sources whose label survived filtering, in the
    # resulting order, so run_spec_sources and run_entries stay aligned.
    if run_spec_sources:
        run_spec_sources = [sources_by_label[entry] for entry in run_entries]

    # Era-pinned replay resolution. Era is exact-path only (the shim has no
    # discovery mode); 'auto'/'modern' with no run_spec_sources = modern.
    era_key = _resolve_manifest_era(
        era_arg=args.era,
        run_spec_sources=run_spec_sources,
        precomputed_root=args.precomputed_root,
    )
    if era_key is not None:
        # Validate the era manifest invariants (one manifest = one era = one image).
        if not args.from_run_spec or not run_spec_sources:
            raise SystemExit(
                f"--era {era_key} requires exact-path replay (--run-spec-sources-fpath "
                "with --precomputed-root): the era shim has no discovery mode."
            )
        if args.model_deployment is not None:
            raise SystemExit(
                f"--era {era_key} is incompatible with --model-deployment: a pre-v0.5 "
                "adapter_spec has no model_deployment field to rewrite (era replay is "
                "verbatim by-name)."
            )
        rewrite_targets = [
            s["run_entry"] for s in run_spec_sources if s.get("model_deployment")
        ]
        if rewrite_targets:
            raise SystemExit(
                f"--era {era_key} run_spec sources must not carry model_deployment "
                f"rewrite targets (era replay is verbatim); offending: {rewrite_targets}"
            )

    max_eval_instances = (
        args.max_eval_instances
        if args.max_eval_instances is not None
        else 1000
    )
    if args.single_gpu:
        devices = args.devices if args.devices is not None else "0"
        tmux_workers = args.tmux_workers if args.tmux_workers is not None else 1
    else:
        devices = args.devices if args.devices is not None else "0,1"
        tmux_workers = (
            args.tmux_workers
            if args.tmux_workers is not None
            else int(defaults["AUDIT_DEFAULT_TMUX_WORKERS"])
        )

    model_override = _choose_model_override(run_entries, args.force_vicuna_nochat)
    description = args.description or (
        f"Historic reproducibility batch with {len(run_entries)} run entries"
    )
    manifest = _build_manifest(
        experiment_name=args.experiment_name,
        description=description,
        suite=args.suite,
        run_entries=run_entries,
        max_eval_instances=max_eval_instances,
        tmux_workers=tmux_workers,
        devices=devices,
        model_deployments_fpath=model_override,
        from_run_spec=args.from_run_spec,
        precomputed_root=args.precomputed_root,
        model_deployment=args.model_deployment,
        run_spec_sources=run_spec_sources,
        era=era_key,
    )

    out_fpath = Path(args.output)
    out_fpath.parent.mkdir(parents=True, exist_ok=True)
    out_fpath.write_text(dump_yaml(manifest))

    selection_rows = []
    for idx, run_entry in enumerate(run_entries):
        row = {
            "index": idx,
            "run_entry": run_entry,
            "benchmark": _infer_benchmark(run_entry),
            "model": _infer_model(run_entry),
            "detail": detail_lut.get(run_entry),
        }
        selection_rows.append(row)

    selection = {
        "experiment_name": args.experiment_name,
        "suite": args.suite,
        "manifest_fpath": str(out_fpath),
        "selection_count": len(run_entries),
        "num_shards": args.num_shards,
        "shard_index": args.shard_index,
        "limit": args.limit,
        "devices": devices,
        "tmux_workers": tmux_workers,
        "max_eval_instances": max_eval_instances,
        "model_deployments_fpath": model_override,
        "from_run_spec": args.from_run_spec,
        "precomputed_root": args.precomputed_root,
        "model_deployment": args.model_deployment,
        "era": era_key,
        "include_patterns": args.include_pattern,
        "exclude_patterns": args.exclude_pattern,
        "models": args.model,
        "benchmarks": args.benchmark,
        "entries": selection_rows,
    }
    selection_fpath = (
        Path(args.selection_output)
        if args.selection_output
        else out_fpath.with_suffix(out_fpath.suffix + ".selection.yaml")
    )
    selection_fpath.write_text(dump_yaml(selection))
    print(out_fpath)
    print(selection_fpath)


if __name__ == "__main__":
    setup_cli_logging()
    main()
