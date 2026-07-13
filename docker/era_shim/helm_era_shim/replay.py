r"""Pre-v0.5 (era) verbatim from-spec replay CLI.

``python -m helm_era_shim.replay`` is the inner executable the eval_audit era
docker node invokes. It is **flag-compatible** with magnet's
``materialize_helm_run_from_spec`` (it accepts the exact underscore flag set
``render_magnet_command`` emits), so the docker-node contract is unchanged — but
it decodes the run_spec.json into the *era* ``helm.benchmark.runner.RunSpec`` and
drives era ``run_benchmarking`` in-process.

Why a separate era CLI (not magnet's): magnet's from-spec CLI imports v0.5+
module paths (``helm.common.codec``, ``helm.benchmark.run_spec``) that do not
exist pre-v0.5, and depends on magnet + scriptconfig + kwutil (none installed in
the era image). This module imports only stdlib + era ``helm.*`` + ``dacite``
(an era dep).

Replay is **verbatim**: a pre-v0.5 ``adapter_spec`` has no ``model_deployment``
field, so there is nothing to rewrite. Routing to the local vLLM endpoint happens
purely by-name — the host registers an era-schema deployment under the exact
official model name bound to
``helm_era_shim.openai_compat_client.OpenAICompatCompletionsClient``. The only
opt-in mutation is ``adapter_spec.max_eval_instances`` (truncation).

Version drift is the detector:
* **Field/shape drift** — the run_spec.json is decoded with ``dacite`` in
  ``strict=True`` mode, so an era-mismatched key (a field the era RunSpec/
  AdapterSpec does not have, or one it requires that is absent) fails loud
  instead of silently dropping.
* **Class availability** — a preflight resolves every ``class_name`` reachable
  from the spec via the era's ``get_class_by_name`` and reports all failures at
  once (drift vs a missing optional extra).
"""
from __future__ import annotations

import argparse
import dataclasses
import getpass
import json
import os
import platform
import socket
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# --- flag contract ------------------------------------------------------------
# The underscore flag set render_magnet_command emits for the from-spec docker
# node. The era path is EXACT-PATH ONLY (the materializer stages a run_spec.json
# copy and passes --run_spec_json); there is no discovery matcher in the shim.
_KNOWN_FLAGS = (
    "run_entry",
    "run_spec_json",
    "suite",
    "out_dpath",
    "precomputed_root",
    "max_eval_instances",
    "model_deployment",
    "require_per_instance_stats",
    "mode",
    "materialize",
    "num_threads",
    "local_path",
    "model_deployments_fpath",
    "enable_huggingface_models",
    "enable_local_huggingface_models",
    "done_fname",
    "manifest_fname",
)

#: Env var carrying the per-deployment API key written into credentials.conf.
#: Defaults to "EMPTY" (vLLM ignores it; v0.2.4 merely requires the key exist).
_ERA_API_KEY_ENV = "EVAL_AUDIT_ERA_API_KEY"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m helm_era_shim.replay",
        description="Verbatim pre-v0.5 HELM run_spec.json replay (era shim).",
    )
    for flag in _KNOWN_FLAGS:
        p.add_argument(f"--{flag}", default=None)
    return p


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_optional(value: Optional[str]) -> Optional[str]:
    """Collapse a rendered ``None`` / empty scalar to a real ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "None":
        return None
    return text


def main(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    parser = _build_parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        # Do not crash on a future magnet perf param, but do not swallow silently
        # either — an unexpected flag is worth surfacing for drift triage.
        print(f"[helm_era_shim.replay] ignoring unrecognized flags: {unknown}", file=sys.stderr)

    run_spec_json = _normalize_optional(args.run_spec_json)
    model_deployment = _normalize_optional(args.model_deployment)
    suite = _normalize_optional(args.suite) or "era-suite"
    out_dpath_arg = _normalize_optional(args.out_dpath)
    local_path = _normalize_optional(args.local_path) or "prod_env"
    model_deployments_fpath = _normalize_optional(args.model_deployments_fpath)
    num_threads = int(_normalize_optional(args.num_threads) or "1")
    require_per_instance_stats = (
        _truthy(args.require_per_instance_stats)
        if args.require_per_instance_stats is not None
        else True
    )
    max_eval_instances = _normalize_optional(args.max_eval_instances)
    max_eval_instances_int = int(max_eval_instances) if max_eval_instances is not None else None

    # Verbatim rule: a pre-v0.5 adapter_spec has no model_deployment to rewrite.
    if model_deployment is not None:
        raise SystemExit(
            "pre-v0.5 adapter_spec has no model_deployment; era replay is verbatim "
            "(routing is by-name via the era deployment registry). Remove "
            "--model_deployment."
        )
    if out_dpath_arg is None:
        raise SystemExit("Missing required --out_dpath")
    if run_spec_json is None:
        raise SystemExit(
            "era replay is exact-path only: --run_spec_json is required (the "
            "materializer stages the run_spec.json copy; the shim has no discovery "
            "mode). Got neither."
        )

    out_dpath = Path(out_dpath_arg).expanduser().resolve()
    out_dpath.mkdir(parents=True, exist_ok=True)
    done_fpath = out_dpath / (_normalize_optional(args.done_fname) or "DONE")
    manifest_fpath = out_dpath / (_normalize_optional(args.manifest_fname) or "adapter_manifest.json")

    run_spec_path = Path(run_spec_json).expanduser().resolve()
    if not run_spec_path.is_file():
        raise SystemExit(f"--run_spec_json does not exist: {run_spec_path}")

    era_key = os.environ.get("EVAL_AUDIT_ERA_KEY")
    era_helm_ref = os.environ.get("EVAL_AUDIT_ERA_HELM_REF")

    manifest: Dict[str, Any] = {
        "requested": {
            "run_entry": _normalize_optional(args.run_entry),
            "run_spec_json": str(run_spec_path),
            "suite": suite,
            "max_eval_instances": max_eval_instances_int,
            "require_per_instance_stats": require_per_instance_stats,
            "local_path": local_path,
            "model_deployments_fpath": model_deployments_fpath,
        },
        "recipe": {"run_spec_path": str(run_spec_path), "source": "explicit"},
        "substitution": (
            "verbatim by-name (era deployment registry binds the official model "
            "name to the shim client); adapter_spec.max_eval_instances truncated "
            "when --max_eval_instances is set. No model_deployment field pre-v0.5."
        ),
        "status": None,
        "replay": {"era": era_key, "helm_git_ref": era_helm_ref},
        "out_dpath": str(out_dpath),
        "timestamp": time.time(),
    }
    process_context = _capture_process_context(out_dpath, argv, args)
    manifest["process_context_fpath"] = str(out_dpath / "process_context.json")
    manifest["process_context"] = process_context

    # 1) Strict-decode the resolved recipe into the era RunSpec (drift detector).
    run_spec = _decode_era_run_spec(run_spec_path)
    manifest["recipe"]["run_spec_name"] = run_spec.name

    # 1b) Canonicalize relocated class paths (DECLARED substitution). The classic
    #     public corpus stores metric class_names under the pre-refactor FLAT path
    #     `helm.benchmark.<X>_metrics.<Class>` — a naive `benchmark.`->`helm.benchmark.`
    #     migration of run_specs produced by unreleased pre-v0.1.0 HELM (a layout that
    #     exists in NO commit; see docs/helm-gotchas.md G13). The era build has these
    #     under the `metrics/` subpackage. Remap ONLY when the stored path is
    #     unresolvable AND its metrics-subpackage relocation resolves (same leaf
    #     class), so a genuinely-wrong era pin still fails the preflight (step 5)
    #     loudly. Applied to the in-memory run_spec so both preflight and scoring use
    #     the resolvable class; the run dir's emitted run_spec.json reflects it.
    run_spec, class_path_subs = _canonicalize_class_paths(run_spec)
    manifest["class_path_substitutions"] = class_path_subs
    if class_path_subs:
        print(
            f"Canonicalized {len(class_path_subs)} relocated class path(s) "
            "(pre-refactor flat -> era metrics subpackage; declared substitution):"
        )
        for sub in class_path_subs:
            print(f"  - {sub['from']} -> {sub['to']}")

    # 2) Opt-in max_eval_instances truncation (adapter_spec.model never touched).
    applied_cap = None
    if max_eval_instances_int is not None:
        applied_cap = max_eval_instances_int
        run_spec = dataclasses.replace(
            run_spec,
            adapter_spec=dataclasses.replace(
                run_spec.adapter_spec, max_eval_instances=applied_cap
            ),
        )
    manifest["replay"]["applied_max_eval_instances"] = applied_cap

    # 3) Prepare the local HELM config (prod_env): era deployments yaml +
    #    synthesized credentials.conf.
    prepared_local_path = _prepare_local_helm_config(
        out_dpath=out_dpath,
        local_path=local_path,
        model_deployments_fpath=model_deployments_fpath,
        model_name=run_spec.adapter_spec.model,
    )

    # 3b) Explicitly register the era deployments yaml. v0.2.4 has NO base-path
    #     auto-registration (that arrived at v0.3.0 via ServerService's
    #     maybe_register_model_deployments_from_base_path); the only era caller of
    #     register_model_deployments_from_path is run.py's --model-deployment-paths
    #     handling, which run_benchmarking() bypasses. Without this, at v0.2.4 the
    #     registry stays empty, get_model_deployment(model) returns None, and
    #     AutoClient falls through to the hardcoded org dispatch (eleutherai/lmsys/
    #     meta/... -> TogetherClient), silently routing every request to
    #     api.together.xyz — the exact deployment artifact the audit exists to
    #     eliminate. Registering explicitly is idempotent at v0.3.0 (ServerService
    #     re-registers the same entries).
    _register_era_deployments(prepared_local_path)

    # 4) Optional HF model registration (mirrors the era helm-run preamble).
    _register_optional_hf_models(args)

    # 5) Preflight: resolve every class the spec references (loud on drift).
    _preflight_resolve_classes(run_spec)

    # 6) Replay in-process.
    output_path = out_dpath / "benchmark_output"
    try:
        _replay_run_spec(
            run_spec=run_spec,
            suite=suite,
            output_path=output_path,
            local_path=prepared_local_path,
            num_threads=num_threads,
        )
    except BaseException:
        tb = traceback.format_exc()
        _persist_stderr(out_dpath, tb)
        _stamp_process_context_stop(out_dpath, process_context)
        manifest["status"] = "error"
        manifest["error"] = tb.strip().splitlines()[-1] if tb.strip() else None
        manifest_fpath.write_text(json.dumps(manifest, indent=2))
        raise

    # 7) Locate the produced run dir + finalize.
    computed_run_dir = _locate_run_dir(
        output_path=output_path,
        suite=suite,
        run_spec_name=run_spec.name,
        require_per_instance_stats=require_per_instance_stats,
    )
    if computed_run_dir is None:
        manifest["status"] = "error"
        manifest_fpath.write_text(json.dumps(manifest, indent=2))
        raise RuntimeError(
            "run_benchmarking completed, but the produced run directory could not "
            f"be located under {output_path}"
        )

    _stamp_process_context_stop(out_dpath, process_context)
    manifest["status"] = "replayed"
    manifest["replay"].update(
        {
            "computed_run_dir": str(computed_run_dir),
            "computed_run_name": computed_run_dir.name,
        }
    )
    manifest_fpath.write_text(json.dumps(manifest, indent=2))
    done_fpath.write_text("ok\n")
    return manifest


# ---------------------------------------------------------------------------
# Era decode + preflight
# ---------------------------------------------------------------------------
def _decode_era_run_spec(run_spec_path: Path) -> Any:
    """Strict-decode a run_spec.json into the era ``RunSpec`` via dacite.

    ``strict=True`` is the era-drift detector: a key the era RunSpec/AdapterSpec
    does not have (a newer-HELM field) raises instead of being silently dropped.
    """
    import dacite

    from helm.benchmark.runner import RunSpec

    raw = json.loads(run_spec_path.read_text())
    try:
        return dacite.from_dict(
            data_class=RunSpec, data=raw, config=dacite.Config(strict=True)
        )
    except Exception as ex:  # noqa: BLE001 - convert to an actionable message
        raise SystemExit(
            "Failed to strict-decode run_spec.json into the era RunSpec — this is "
            "the era-drift detector firing. Either the spec was produced by a "
            f"different crfm-helm version than this era image, or the era pin is "
            f"wrong. dacite error:\n  {type(ex).__name__}: {ex}"
        )


def _iter_object_specs(value: Any) -> Iterator[Any]:
    """Yield every ObjectSpec / ``{'class_name': ...}`` reachable from ``value``."""
    from helm.common.object_spec import ObjectSpec

    if isinstance(value, ObjectSpec):
        yield value
        for sub in value.args.values():
            yield from _iter_object_specs(sub)
    elif isinstance(value, dict):
        if isinstance(value.get("class_name"), str):
            yield value
        for sub in value.values():
            yield from _iter_object_specs(sub)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_object_specs(item)


def _collect_class_names(run_spec: Any) -> List[str]:
    """Collect every distinct ``class_name`` referenced by the era RunSpec.

    Roots: ``scenario_spec`` + each ``metric_specs`` entry. The era RunSpec has
    no ``annotators`` field (that is a v0.5+ addition). ``adapter_spec`` is
    excluded — it carries deployment *names*, not importable class paths.
    """
    from helm.common.object_spec import ObjectSpec

    roots: List[Any] = [run_spec.scenario_spec, *(run_spec.metric_specs or [])]
    names: List[str] = []
    seen = set()
    for root in roots:
        for spec in _iter_object_specs(root):
            class_name = (
                spec.class_name if isinstance(spec, ObjectSpec) else spec.get("class_name")
            )
            if isinstance(class_name, str) and class_name not in seen:
                seen.add(class_name)
                names.append(class_name)
    return names


def _canonical_class_name(class_name: str) -> Tuple[str, bool]:
    """Return ``(resolvable_class_name, substituted?)`` for one class path.

    If ``class_name`` already resolves in this era build, return it unchanged.
    Otherwise, if it is a pre-refactor FLAT ``helm.benchmark.<mod>.<Class>`` whose
    ``metrics/`` subpackage relocation ``helm.benchmark.metrics.<mod>.<Class>``
    DOES resolve (same leaf class), return the relocated path. Otherwise return it
    unchanged (the preflight will report it). Self-verifying: only substitutes a
    move that the era build actually satisfies — a genuinely-absent class (wrong
    era pin) resolves neither way and is left for the preflight to fail loudly.
    See docs/helm-gotchas.md G13.
    """
    from helm.common.object_spec import get_class_by_name

    def _resolves(name: str) -> bool:
        try:
            get_class_by_name(name)
            return True
        except Exception:  # noqa: BLE001 - resolution probe, never crashes
            return False

    if _resolves(class_name):
        return class_name, False
    parts = class_name.split(".")
    # Only the known relocation: helm.benchmark.<mod>.<Class> -> insert `metrics.`.
    # <mod> already == "metrics" means it is not the flat form; leave it alone.
    if len(parts) >= 4 and parts[:2] == ["helm", "benchmark"] and parts[2] != "metrics":
        candidate = "helm.benchmark.metrics." + ".".join(parts[2:])
        if _resolves(candidate):
            return candidate, True
    return class_name, False


def _remap_object_spec_tree(value: Any, subs: List[Dict[str, str]]) -> Any:
    """Rebuild an ObjectSpec tree with canonicalized ``class_name``s.

    Mirrors ``_iter_object_specs``' shape handling but returns a rewritten copy
    (ObjectSpec is frozen, so use ``dataclasses.replace``). Appends each applied
    substitution to ``subs``.
    """
    from helm.common.object_spec import ObjectSpec

    if isinstance(value, ObjectSpec):
        new_class, changed = _canonical_class_name(value.class_name)
        if changed:
            subs.append({"from": value.class_name, "to": new_class})
        new_args = {k: _remap_object_spec_tree(v, subs) for k, v in value.args.items()}
        return dataclasses.replace(value, class_name=new_class, args=new_args)
    if isinstance(value, dict):
        return {k: _remap_object_spec_tree(v, subs) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap_object_spec_tree(v, subs) for v in value]
    if isinstance(value, tuple):
        return tuple(_remap_object_spec_tree(v, subs) for v in value)
    return value


def _canonicalize_class_paths(run_spec: Any) -> Tuple[Any, List[Dict[str, str]]]:
    """Canonicalize relocated class paths on the same roots the preflight checks
    (``scenario_spec`` + ``metric_specs``). Returns ``(run_spec, substitutions)``;
    the run_spec is unchanged (same object) when nothing was substituted.
    """
    subs: List[Dict[str, str]] = []
    new_scenario = _remap_object_spec_tree(run_spec.scenario_spec, subs)
    new_metrics = [_remap_object_spec_tree(m, subs) for m in (run_spec.metric_specs or [])]
    if not subs:
        return run_spec, []
    run_spec = dataclasses.replace(
        run_spec, scenario_spec=new_scenario, metric_specs=new_metrics
    )
    return run_spec, subs


def _preflight_resolve_classes(run_spec: Any) -> None:
    """Resolve every class the spec references; fail once, listing all failures."""
    from helm.common.object_spec import get_class_by_name

    unresolved: List[Tuple[str, str]] = []
    for class_name in _collect_class_names(run_spec):
        if "." not in class_name:
            continue
        try:
            get_class_by_name(class_name)
        except Exception as ex:  # noqa: BLE001 - preflight reports, never crashes
            unresolved.append((class_name, f"{type(ex).__name__}: {ex}"))
    if unresolved:
        detail = "\n".join(f"  - {name}: {err}" for name, err in unresolved)
        raise SystemExit(
            f"Preflight failed: this era crfm-helm build cannot resolve "
            f"{len(unresolved)} class(es) referenced by the run_spec.json:\n{detail}\n"
            "Either the era pin is wrong for this run, or a required optional "
            "dependency is missing (an environment/recipe filter reason, not a "
            "reproducibility failure)."
        )


# ---------------------------------------------------------------------------
# Local HELM config + replay
# ---------------------------------------------------------------------------
def _prepare_local_helm_config(
    *,
    out_dpath: Path,
    local_path: str,
    model_deployments_fpath: Optional[str],
    model_name: str,
) -> Path:
    """Create the prod_env dir with the era deployments yaml + credentials.conf.

    Relative ``local_path`` resolves inside ``out_dpath`` (so a run is
    self-contained). The credentials.conf carries a ``deployments`` block keyed
    on the exact model/deployment name — v0.2.4's ``AutoClient`` eagerly demands
    a per-deployment credential before constructing the client; v0.3.0 tolerates
    it. The key value is the one the exporter BAKED into the model_deployments.yaml
    (``client_spec.args.api_key``) — the single source of truth — falling back to
    ``$EVAL_AUDIT_ERA_API_KEY`` then "EMPTY". Reading the baked value (not the env)
    is what makes v0.2.4 work under kwdagger: its tmux worker ships an EMPTY environ
    (secrets-hygiene), so a ``-e EVAL_AUDIT_ERA_API_KEY`` passthrough arrives empty
    and credentials.conf would render EMPTY — which v0.2.4's AutoClient uses as
    ``additional_args``, overriding the (correct) args key and 401-ing at the
    gateway. v0.3.0 survived only because it reads the args key directly.
    """
    lp = Path(local_path)
    prepared = lp if lp.is_absolute() else (out_dpath / lp)
    prepared.mkdir(parents=True, exist_ok=True)

    if model_deployments_fpath:
        src = Path(model_deployments_fpath)
        if not src.is_file():
            raise SystemExit(f"--model_deployments_fpath does not exist: {src}")
        (prepared / "model_deployments.yaml").write_text(src.read_text())

    api_key = (
        _api_key_from_deployments(model_deployments_fpath, model_name)
        or os.environ.get(_ERA_API_KEY_ENV)
        or "EMPTY"
    )
    (prepared / "credentials.conf").write_text(
        _render_credentials_conf(model_name, api_key)
    )
    # Both files can carry a live gateway key (the exported yaml embeds it in
    # client_spec.args; credentials.conf mirrors it for v0.2.4's eager lookup) and
    # prod_env persists inside the run's output dir — tighten to owner-only, the
    # same posture the exporter applies to the bundle yaml. Best-effort: chmod can
    # fail on some mounts, and a perms failure must not kill the replay.
    for sensitive in ("model_deployments.yaml", "credentials.conf"):
        try:
            os.chmod(prepared / sensitive, 0o600)
        except OSError:
            pass
    return prepared


def _api_key_from_deployments(fpath: Optional[str], model_name: str) -> Optional[str]:
    """The baked ``client_spec.args.api_key`` for ``model_name`` from the exported
    model_deployments.yaml, or None if unavailable.

    The exporter bakes the live LiteLLM master key here (``--api-key-value``); this
    is the credential HELM actually authenticates with. Reading it here — rather
    than ``$EVAL_AUDIT_ERA_API_KEY`` — decouples the v0.2.4 credentials.conf from
    the env-forwarding channel that kwdagger's empty-environ tmux worker breaks.
    """
    if not fpath:
        return None
    try:
        import yaml

        doc = yaml.safe_load(Path(fpath).read_text()) or {}
    except Exception:
        return None
    for entry in doc.get("model_deployments", []) or []:
        if entry.get("name") == model_name:
            key = ((entry.get("client_spec") or {}).get("args") or {}).get("api_key")
            return str(key) if key else None
    return None


def _hocon_nested_deployment_key(model: str, value: str) -> str:
    """Render one ``deployments`` entry so pyhocon's dotted-path lookup resolves it.

    pyhocon's ``ConfigTree`` path-splits lookup keys on ``.`` even when the key
    was written quoted, so a flat ``"eleutherai/pythia-6.9b": "EMPTY"`` entry is
    unreachable: HELM's ``AutoClient`` looks the credential up with the raw model
    string, pyhocon resolves the *path* ``eleutherai/pythia-6`` -> ``9b``, and the
    lookup raises ``ConfigMissingException``. Nest the entry along the dot-split
    path so the path lookup lands on the value::

        "eleutherai/pythia-6" { "9b" = "EMPTY" }

    A model with no dot stays flat (``"together/gpt2" = "EMPTY"``). Slashes are not
    path separators in HOCON, so the quoted segments keep them verbatim. Returns
    the entry text only (no ``deployments`` wrapper) so it is unit-testable.
    """
    segments = model.split(".")
    entry = f'"{segments[-1]}" = "{value}"'
    for segment in reversed(segments[:-1]):
        entry = f'"{segment}" {{ {entry} }}'
    return entry


def _render_credentials_conf(model_name: str, api_key: str) -> str:
    """The full credentials.conf text with a pyhocon-addressable deployment key."""
    return "deployments {\n  " + _hocon_nested_deployment_key(model_name, api_key) + "\n}\n"


def _register_era_deployments(prepared_local_path: Path) -> None:
    """Register the era ``model_deployments.yaml`` at the era deployment registry.

    Required at v0.2.4 (no base-path auto-registration) and idempotent at v0.3.0;
    see the step-3b comment in ``main`` for why silent Together routing results
    without it. Uses ``register_model_deployments_from_path`` — it exists
    identically at both eras.
    """
    deployments_yaml = prepared_local_path / "model_deployments.yaml"
    if not deployments_yaml.exists():
        return
    from helm.benchmark.model_deployment_registry import (
        register_model_deployments_from_path,
    )

    register_model_deployments_from_path(os.fspath(deployments_yaml))


def _register_optional_hf_models(args: argparse.Namespace) -> None:
    """Register HF hub / local models if the flags carry any (usually empty).

    The registration API moved between eras: at v0.3.0+ it lives in
    ``helm.benchmark.huggingface_registration``
    (``register_huggingface_{hub,local}_model_from_flag_value``); at v0.2.4 it is
    ``helm.proxy.clients.huggingface_model_registry``
    (``register_huggingface_{hub,local}_model_config``). Version-dispatch so a
    v0.2.4 replay with non-empty enable flags does not ModuleNotFoundError
    mid-run. The empty-flag era path (the standard case) never reaches the import.
    """
    hub = _coerce_str_list(args.enable_huggingface_models)
    local = _coerce_str_list(args.enable_local_huggingface_models)
    if hub:
        try:  # v0.3.0+
            from helm.benchmark.huggingface_registration import (
                register_huggingface_hub_model_from_flag_value as _reg_hub,
            )
        except ImportError:  # v0.2.4
            from helm.proxy.clients.huggingface_model_registry import (
                register_huggingface_hub_model_config as _reg_hub,
            )
        for name in hub:
            _reg_hub(str(name))
    if local:
        try:  # v0.3.0+
            from helm.benchmark.huggingface_registration import (
                register_huggingface_local_model_from_flag_value as _reg_local,
            )
        except ImportError:  # v0.2.4
            from helm.proxy.clients.huggingface_model_registry import (
                register_huggingface_local_model_config as _reg_local,
            )
        for path in local:
            _reg_local(str(path))


def _coerce_str_list(value: Optional[str]) -> List[str]:
    """Parse a rendered list flag (``"['a','b']"`` / JSON / single token)."""
    text = _normalize_optional(value)
    if text is None:
        return []
    for loader in (json.loads, _literal_eval):
        try:
            parsed = loader(text)
        except Exception:
            continue
        if isinstance(parsed, (list, tuple)):
            return [str(x) for x in parsed]
        return [str(parsed)]
    return [text]


def _literal_eval(text: str) -> Any:
    import ast

    return ast.literal_eval(text)


def _replay_run_spec(
    *,
    run_spec: Any,
    suite: str,
    output_path: Path,
    local_path: Path,
    num_threads: int,
) -> None:
    """Drive era ``run_benchmarking`` in-process on the single resolved spec."""
    from helm.benchmark.run import run_benchmarking
    from helm.common.authentication import Authentication
    from helm.common.general import ensure_directory_exists

    ensure_directory_exists(os.fspath(output_path))
    run_benchmarking(
        run_specs=[run_spec],
        auth=Authentication(""),
        url=None,
        local_path=os.fspath(local_path),
        num_threads=num_threads,
        output_path=os.fspath(output_path),
        suite=suite,
        dry_run=False,
        skip_instances=False,
        cache_instances=False,
        cache_instances_only=False,
        skip_completed_runs=False,
        exit_on_error=True,
        runner_class_name=None,
    )


def _locate_run_dir(
    *,
    output_path: Path,
    suite: str,
    run_spec_name: str,
    require_per_instance_stats: bool,
) -> Optional[Path]:
    """Locate the produced run dir under ``benchmark_output/runs/<suite>/``.

    HELM names the run dir after ``run_spec.name`` (os.sep replaced by ``_``);
    prefer that exact match, then fall back to a *unique* run dir carrying the
    expected artifacts. If the suite dir holds more than one valid run dir (a
    reused / mounted output_path), return None so the caller raises loudly rather
    than recording ``computed_run_dir`` for an unrelated run — a silent-provenance
    corruption.
    """
    suite_dir = output_path / "runs" / suite
    if not suite_dir.is_dir():
        return None

    def _ok(d: Path) -> bool:
        if not d.is_dir():
            return False
        if require_per_instance_stats and not (d / "per_instance_stats.json").exists():
            return False
        return (d / "run_spec.json").exists()

    sanitized = run_spec_name.replace(os.path.sep, "_")
    exact = suite_dir / sanitized
    if _ok(exact):
        return exact
    # Exact-name lookup failed; auto-pick only when the suite dir is unambiguous.
    candidates = [d for d in sorted(suite_dir.iterdir()) if _ok(d)]
    if len(candidates) == 1:
        return candidates[0]
    return None


# ---------------------------------------------------------------------------
# Process context (Stage-4-indexer-compatible shape) + stderr capture
# ---------------------------------------------------------------------------
def _capture_process_context(
    out_dpath: Path, argv: Optional[List[str]], args: argparse.Namespace
) -> Dict[str, Any]:
    """Emit a ``process_context.json`` in the shape the Stage 4 indexer reads.

    The indexer reads ``properties.{uuid,start_timestamp,machine,extra.env,
    extra.nvidia_smi}`` (see eval_audit/workflows/index_results.py). We do NOT
    depend on kwutil (absent in the era image), so build the dict directly.
    ``start``/``stop`` bracket the whole replay is not possible here (this runs
    before the replay), so timestamps mark process start; the manifest carries
    ``status`` for completion.
    """
    now = time.time()
    props: Dict[str, Any] = {
        "uuid": str(uuid.uuid4()),
        "start_timestamp": now,
        "machine": {
            "host": socket.gethostname(),
            "user": _safe(getpass.getuser),
            "os_name": platform.system(),
            "arch": platform.machine(),
            "py_version": platform.python_version(),
        },
        "extra": {
            "env": {
                "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
                "HOSTNAME": os.environ.get("HOSTNAME"),
                "EVAL_AUDIT_ERA_KEY": os.environ.get("EVAL_AUDIT_ERA_KEY"),
            },
            "argv": list(argv) if argv is not None else list(sys.argv[1:]),
        },
    }
    ctx = {"name": "helm_era_shim.replay", "properties": props}
    try:
        (out_dpath / "process_context.json").write_text(json.dumps(ctx, indent=2))
    except Exception:
        pass
    return ctx


def _stamp_process_context_stop(
    out_dpath: Path, process_context: Dict[str, Any]
) -> None:
    """Fill ``stop_timestamp`` / ``duration`` into process_context.json.

    ``_capture_process_context`` runs before the replay, so it can only record
    ``start_timestamp``; the Stage-4 indexer also reads
    ``properties.{stop_timestamp,duration}`` (``index_results.py``). Re-write the
    file after the replay (success or error) so era rows carry the same timing
    the modern kwutil ProcessContext provides. Best-effort — never raises.
    """
    props = process_context.get("properties") if isinstance(process_context, dict) else None
    if not isinstance(props, dict):
        return
    stop = time.time()
    props["stop_timestamp"] = stop
    start = props.get("start_timestamp")
    if isinstance(start, (int, float)):
        props["duration"] = stop - start
    try:
        (out_dpath / "process_context.json").write_text(json.dumps(process_context, indent=2))
    except Exception:
        pass


def _safe(fn: Any) -> Optional[str]:
    try:
        return fn()
    except Exception:
        return None


def _persist_stderr(out_dpath: Path, text: str, tail_bytes: int = 200_000) -> None:
    try:
        tail = text[-tail_bytes:] if len(text) > tail_bytes else text
        (out_dpath / "cmd_stderr.txt").write_text(tail)
    except Exception:
        pass


if __name__ == "__main__":
    main()
