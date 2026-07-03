r"""
Compile a reproduction list from existing HELM outputs on disk.

Given one or more roots that contain HELM outputs, discover all run directories
and emit a list of run specs you can feed into kwdagger / helm-run.

Outputs are structured so you can:
- reproduce exact run directories (by using run_entry == run directory name)
- optionally include max_eval_instances inferred from per_instance_stats.json

Ignore:

    ls /data/crfm-helm-public/thaiexam/benchmark_output/runs/v1.1.0/thai_exam:exam=tpat1,method=multiple_choice_joint,model=aisingapore_llama3-8b-cpt-sea-lionv2.1-instruct

    python -m eval_audit.cli.index_historic_helm_runs /data/crfm-helm-public --out_fpath /data/crfm-helm-audit-store/configs/run_specs.yaml --out_detail_fpath /data/crfm-helm-audit-store/configs/run_details.yaml --out_inventory_json /data/crfm-helm-audit-store/analysis/filter_inventory.json

    cat /data/crfm-helm-audit-store/configs/run_specs.yaml | grep -v together > run_specs2.yaml

    python ~/code/aiq-magnet/dev/poc/inspect_historic_helm_runs.py /data/Public/AIQ/crfm-helm-public/

    # we need fully featured helm installed
    uv pip install crfm-helm[all] -U

    # Need to login to huggingface can pass token via --token
    hf auth login

    # Need TogetherAPI credentials

    kwdagger schedule \
      --params="
        pipeline: 'magnet.backends.helm.pipeline.helm_single_run_pipeline()'
        matrix:
          helm.run_entry:
            - __include__: run_specs2.yaml
          helm.max_eval_instances:
            - 1000
          helm.precomputed_root: null
      " \
      --devices="0,1,2,3" \
      --tmux_workers=4 \
      --root_dpath=$PWD/results \
      --backend=tmux \
      --skip_existing=1 \
      --run=1
"""

from __future__ import annotations
import fnmatch
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Any

import ubelt as ub
import kwutil
import scriptconfig as scfg
from loguru import logger

from eval_audit.infra.logging import setup_cli_logging
from eval_audit.infra.api import repo_run_details_fpath, repo_run_specs_fpath
from eval_audit.helm.run_entries import (
    parse_run_entry_description,
    parse_run_name_to_kv,
    reconstruct_run_entry_from_run_spec,
)
from eval_audit.indexing.schema import (
    KNOWN_STRUCTURAL_JUNK_NAMES,
    OFFICIAL_COMPONENT_COLUMNS,
    classify_run_entry as _classify_run_entry_impl,
    component_id_for_official,
    compute_run_spec_hash as _compute_run_spec_hash_impl,
    extract_run_spec_fields,
    logical_run_key_for_official,
    normalize_for_hash as _normalize_for_hash_impl,
    now_utc_iso,
)
from eval_audit.model_registry import local_model_registry_by_name

# --- compat re-exports -------------------------------------------------
# Stage 1 library logic moved to eval_audit.indexing.* on 2026-06-11
# (Phase 2 of docs/planning/repo-refactor-plan.md). filter_analysis and
# the index/filter tests import these names from this module; keep
# re-exporting them.
from eval_audit.indexing.historic_filtering import (  # noqa: F401
    MISSING_MODEL_METADATA_REASON,
    CLOSED_JUDGE_REQUIRED_REASON,
    GATED_DATASET_REASON,
    CLOSED_JUDGE_BENCHMARKS,
    GATED_DATASET_BENCHMARKS,
    gather_runs,
    build_run_table,
    dedupe_rows,
    format_params_human,
    build_failure_reason_details,
    build_run_failure_reason_details,
    short_scenario_name,
    describe_run_spec,
    build_incomplete_inventory_row,
    build_filter_inventory_rows,
)
from eval_audit.indexing.official_public_index import (  # noqa: F401
    OFFICIAL_INDEX_COLUMNS,
    _normalize_for_hash,
    _compute_run_spec_hash,
    _classify_run_entry,
    _scan_benchmark_output_dir,
    build_official_public_index_rows,
    write_official_public_index,
)


class CompileHelmReproListConfig(scfg.DataConfig):
    roots = scfg.Value(
        ['/data/crfm-helm-public'],
        nargs="+",
        help=(
            "One or more roots that either ARE a benchmark_output dir, contain "
            "benchmark_output dirs, or contain suite/benchmark_output dirs."
        ),
        position=1,
    )

    suite_pattern = scfg.Value(
        "*",
        help="Glob applied to benchmark_output/runs/<suite> directories.",
    )

    run_pattern = scfg.Value(
        "*:*",
        help="Glob applied within each suite to select runs (default selects HELM run dirs).",
    )

    require_per_instance_stats = scfg.Value(
        False,
        help="If True, only include runs that have per_instance_stats.json.",
    )

    include_max_eval_instances = scfg.Value(
        False,
        help="If True, infer max_eval_instances from per_instance_stats.json when possible. CAN BE VERY SLOW",
    )

    out_fpath = scfg.Value(
        str(repo_run_specs_fpath()),
        help="Where to write selected run specs. Defaults to $AUDIT_STORE_ROOT/configs/run_specs.yaml.",
    )

    out_detail_fpath = scfg.Value(
        str(repo_run_details_fpath()),
        help="Where to write detailed rows. Defaults to $AUDIT_STORE_ROOT/configs/run_details.yaml.",
    )

    out_report_dpath = scfg.Value(
        None,
        help="Deprecated. Reporting is now handled by eval_audit.reports.filter_analysis.",
    )

    out_inventory_json = scfg.Value(
        None,
        help="If provided, write the full filter inventory as JSON for later analysis.",
    )

    out_official_index_dpath = scfg.Value(
        None,
        help=(
            "If provided, emit the canonical official/public index as a timestamped CSV "
            "plus a .csv symlink in this directory.  This index captures ALL "
            "public HELM run entries (including structural junk) with explicit "
            "public_track and suite_version provenance. "
            "It is separate from Stage 1 selected-run artifacts."
        ),
    )

    dedupe = scfg.Value(
        True,
        help="If True, dedupe identical (suite, run_entry, max_eval_instances) rows.",
    )

    allow_closed_judge_benchmarks = scfg.Value(
        False,
        isflag=True,
        help=(
            "Open-judge extension (Phase 3 / 4.9): admit benchmarks that "
            "normally require a closed judge (CLOSED_JUDGE_BENCHMARKS) as "
            "planned judge substitutions instead of excluding them. "
            "Admitted runs flow through the distinct 'judge-substitution' "
            "selection path in the filter report, and their run_details "
            "rows carry judge_substitution_planned=True so the planner "
            "declares the substitution on every comparison they join."
        ),
    )

    @classmethod
    def main(cls, argv=None, **kwargs):
        """
        Example:
            >>> # It's a good idea to setup a doctest.
            >>> from eval_audit.cli.index_historic_helm_runs import *  # NOQA
            >>> argv = False
            >>> kwargs = dict()
            >>> cls = CompileHelmReproListConfig
            >>> config = cls(**kwargs)
            >>> cls.main(argv=argv, **config)
        """
        setup_cli_logging()
        config = cls.cli(argv=argv, data=kwargs, verbose="auto")
        roots = [Path(r).expanduser() for r in config.roots]
        if not roots:
            raise SystemExit("Must provide at least one root")

        suite_pattern = config.suite_pattern
        run_pattern = config.run_pattern
        require_per_instance_stats = config.require_per_instance_stats
        include_max_eval_instances = config.include_max_eval_instances
        if config.out_report_dpath:
            raise SystemExit(
                '--out_report_dpath is no longer supported here. '
                'Use --out_inventory_json to save the Stage 1 inventory, then run '
                '`python -m eval_audit.reports.filter_analysis --report-dpath <reports/filtering> '
                '--inventory-json <inventory.json>`.'
            )

        runs, incomplete_rows = gather_runs(
            roots=roots,
            suite_pattern=suite_pattern,
            run_pattern=run_pattern,
            require_per_instance_stats=require_per_instance_stats,
            include_max_eval_instances=include_max_eval_instances,
        )
        rows = build_run_table(
            runs,
            include_max_eval_instances=include_max_eval_instances,
        )
        if config.dedupe:
            rows = dedupe_rows(rows)

        scenario_histo = ub.dict_hist([r['scenario_class'] for r in rows])
        model_histo = ub.dict_hist([r['model'] for r in rows])
        scenario_histo = ub.udict.sorted_values(scenario_histo)
        model_histo = ub.udict.sorted_values(model_histo)
        print(f'scenario_histo = {ub.urepr(scenario_histo, nl=1)}')
        print(f'model_histo = {ub.urepr(model_histo, nl=1)}')

        from helm.benchmark import config_registry
        from helm.benchmark import  model_deployment_registry
        config_registry.register_builtin_configs_from_helm_package()
        model_rows = []
        missing_model_metadata: dict[str, str] = {}
        for model_name, count in model_histo.items():
            HF_CLIENT = 'helm.clients.huggingface_client.HuggingFaceClient'
            try:
                model_meta = model_deployment_registry.get_model_metadata(model_name)
                model_row = model_meta.__dict__ | {'count': count}

                clients = {}
                if model_meta.deployment_names:
                    for deploy_name in model_meta.deployment_names:
                        deploy_info = model_deployment_registry.get_model_deployment(deploy_name)
                        clients[deploy_name] = deploy_info.client_spec.class_name

                model_row['clients'] = clients
                model_row['has_hf_client'] = HF_CLIENT in clients.values()
                model_rows.append(model_row)
            except (TypeError, ValueError) as ex:
                logger.warning(f'missing: model_name = {ub.urepr(model_name, nl=1)} {ex}')
                missing_model_metadata[model_name] = str(ex)

        # Filter to text models that will fit in memory
        HF_CLIENT = 'helm.clients.huggingface_client.HuggingFaceClient'

        SOFT_TEXT_TAGS = {
            'TEXT_MODEL_TAG',
            'FULL_FUNCTIONALITY_TEXT_MODEL_TAG',
            'INSTRUCTION_FOLLOWING_MODEL_TAG',
        }

        EXCLUDE_TAGS = {
            'VISION_LANGUAGE_MODEL_TAG',
            'AUDIO_LANGUAGE_MODEL_TAG',
            'IMAGE_MODEL_TAG',
            'TEXT_TO_IMAGE_MODEL_TAG',
            'CODE_MODEL_TAG',
        }

        # Keep this conservative if you want, but allow unknown sizes through.
        MAX_PARAMS = 10e9
        # MAX_PARAMS = 200e9

        # Optional manual escape hatch for models that are probably HF-runnable
        # even if HELM currently resolves them to a non-HF deployment.
        KNOWN_HF_OVERRIDES = {
            'qwen/qwen2.5-7b-instruct-turbo',
            'qwen/qwen2-72b-instruct',
            'qwen/qwen2.5-72b-instruct-turbo',
        }

        chosen_model_rows = []
        for r in model_rows:
            tags = set(r.get('tags', []))

            is_text_like = bool(tags & SOFT_TEXT_TAGS)
            has_excluded_tags = bool(tags & EXCLUDE_TAGS)
            size_ok = (r.get('num_parameters') is None or r['num_parameters'] <= MAX_PARAMS)
            access_ok = (r.get('access') == 'open')
            has_local_hf_path = (
                r.get('has_hf_client', False) or
                r['name'] in KNOWN_HF_OVERRIDES
            )

            if (
                is_text_like and
                not has_excluded_tags and
                size_ok and
                access_ok and
                has_local_hf_path
            ):
                chosen_model_rows.append(r)

        chosen_model_names = {r['name'] for r in chosen_model_rows}
        logger.info('Filter to {} / {} models', len(chosen_model_rows), len(model_rows))

        allow_closed_judge = bool(config.allow_closed_judge_benchmarks)
        chosen_rows = []
        for row in rows:
            if row['model'] not in chosen_model_names:
                continue
            benchmark = describe_run_spec(row['run_spec_name'], row.get('scenario_class'))['benchmark']
            run_failure_reason_details = build_run_failure_reason_details(
                benchmark=benchmark,
                allow_closed_judge=allow_closed_judge,
            )
            if run_failure_reason_details:
                continue
            if allow_closed_judge and benchmark in CLOSED_JUDGE_BENCHMARKS:
                # The flag rides run_details.yaml -> manifest 'detail'
                # entries -> the local audit index, where the planner
                # turns it into a declared judge substitution.
                row = {**row, 'judge_substitution_planned': True}
            chosen_rows.append(row)
        logger.info('Filter to {} / {} runs', len(chosen_rows), len(rows))

        # Prepare filter-step analysis data (for report generation)
        model_filter_rows = []  # one dict per model with all failure reasons
        for r in model_rows:
            tags = set(r.get('tags', []))
            is_text_like = bool(tags & SOFT_TEXT_TAGS)
            has_excluded_tags = bool(tags & EXCLUDE_TAGS)
            size_ok = (r.get('num_parameters') is None or r['num_parameters'] <= MAX_PARAMS)
            access_ok = (r.get('access') == 'open')
            has_local_hf_path = (
                r.get('has_hf_client', False) or
                r['name'] in KNOWN_HF_OVERRIDES
            )

            # Collect ALL failing reasons (not just the first)
            failure_reasons = []
            if not is_text_like:
                failure_reasons.append('not-text-like')
            if has_excluded_tags:
                failure_reasons.append('excluded-tags')
            if not size_ok:
                failure_reasons.append('too-large')
            if not access_ok:
                failure_reasons.append('not-open-access')
            if not has_local_hf_path:
                failure_reasons.append('no-local-helm-deployment')

            eligible = (
                is_text_like and
                not has_excluded_tags and
                size_ok and
                access_ok and
                has_local_hf_path
            )

            model_filter_rows.append({
                'model': r['name'],
                'n_runs': model_histo.get(r['name'], 0),
                'failure_reasons': failure_reasons,
                'failure_reason_details': build_failure_reason_details(
                    tags=tags,
                    is_text_like=is_text_like,
                    has_excluded_tags=has_excluded_tags,
                    size_ok=size_ok,
                    access_ok=access_ok,
                    has_local_hf_path=has_local_hf_path,
                    num_parameters=r.get('num_parameters'),
                    access=r.get('access'),
                    has_hf_client=r.get('has_hf_client', False),
                    model_name=r['name'],
                    known_hf_overrides=KNOWN_HF_OVERRIDES,
                    max_params=MAX_PARAMS,
                    exclude_tags=EXCLUDE_TAGS,
                ),
                'eligible': eligible,
                'num_parameters': r.get('num_parameters'),
                'access': r.get('access'),
                'tags': sorted(tags),
                'has_hf_client': r.get('has_hf_client', False),
                'size_threshold_params': MAX_PARAMS,
            })

        for model_name, error_text in missing_model_metadata.items():
            model_filter_rows.append({
                'model': model_name,
                'n_runs': model_histo.get(model_name, 0),
                'failure_reasons': [MISSING_MODEL_METADATA_REASON],
                'failure_reason_details': {
                    MISSING_MODEL_METADATA_REASON: (
                        'HELM could not resolve model metadata for this model name via '
                        f'model_deployment_registry: {error_text}'
                    ),
                },
                'eligible': False,
                'num_parameters': None,
                'access': None,
                'tags': [],
                'has_hf_client': False,
                'size_threshold_params': MAX_PARAMS,
            })
        # logger.info(f'chosen_rows = {ub.urepr(chosen_rows, nl=1)}')

        if 1:
            # Show filtered histograms
            scenario_histo = ub.dict_hist([r['scenario_class'] for r in chosen_rows])
            model_histo = ub.dict_hist([r['model'] for r in chosen_rows])
            scenario_histo = ub.udict.sorted_values(scenario_histo)
            model_histo = ub.udict.sorted_values(model_histo)
            logger.info(f'scenario_histo = {ub.urepr(scenario_histo, nl=1)}')
            logger.info(f'model_histo = {ub.urepr(model_histo, nl=1)}')

        # Generate filter-step report if requested
        inventory_rows = None
        if config.out_inventory_json:
            inventory_rows = build_filter_inventory_rows(
                complete_rows=rows,
                incomplete_rows=incomplete_rows,
                model_filter_rows=model_filter_rows,
                chosen_model_names=chosen_model_names,
                allow_closed_judge=allow_closed_judge,
            )
            inventory_fpath = Path(config.out_inventory_json).expanduser().resolve()
            inventory_fpath.parent.mkdir(parents=True, exist_ok=True)
            inventory_fpath.write_text(
                json.dumps(
                    kwutil.Json.ensure_serializable(inventory_rows),
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ) + '\n'
            )
            logger.success("Wrote ⚙ {}", inventory_fpath)

        if config.out_official_index_dpath:
            official_rows = build_official_public_index_rows(
                roots=roots,
                suite_pattern=suite_pattern,
            )
            ts_fpath, latest_fpath = write_official_public_index(
                rows=official_rows,
                out_dpath=Path(config.out_official_index_dpath).expanduser().resolve(),
            )
            logger.success(
                "Wrote official public index {} ({} rows)",
                ts_fpath, len(official_rows),
            )
            logger.success("Latest alias: {}", latest_fpath)

        if config.out_detail_fpath:
            text = kwutil.Yaml.dumps(chosen_rows)
            ub.Path(config.out_detail_fpath).parent.ensuredir()
            Path(config.out_detail_fpath).write_text(text)
            logger.success("Wrote ⚙ {}", config.out_detail_fpath)

        run_spec_names = [r["run_spec_name"] for r in chosen_rows]
        text = kwutil.Yaml.dumps(run_spec_names)
        if config.out_fpath:
            Path(config.out_fpath).write_text(text)
            logger.success("Wrote ⚙ {}", config.out_fpath)
        else:
            print(text, end="")


__cli__ = CompileHelmReproListConfig


def main(argv: list[str] | None = None) -> None:
    setup_cli_logging()
    __cli__.main(argv=argv)


if __name__ == "__main__":
    main()
