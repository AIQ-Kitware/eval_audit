#!/usr/bin/env python3
"""Deployment-match search CLI.

Subcommands:

  sample   Extract the oracle (recipe + sampled instances + official completions)
           from a public HELM run.  -> oracle.json
  grid     Resolve the model + generate the deployment grid.  -> catalog.yaml,
           cells.json, grid.json (+ rendered vllm commands if infer_stack imports)
  dry-run  sample + grid in one shot (CPU-only; no serving). The Phase-1 path.
  score    Rank cell result JSONs (from probe / run) against the oracle. ->
           ranking.txt, snippets.txt, best_deployment.yaml, scored.json
  selftest Run the scorer self-test (no run / no server).

Run under the repo .venv (needs pyyaml; eval_audit/infer_stack are optional
enrichment):

  PYTHONPATH=submodules/infer_stack .venv/bin/python \
      dev/tools/deployment_match/cli.py dry-run \
      --run /data/crfm-helm-public/lite/benchmark_output/runs/v1.2.0/narrative_qa:model=allenai_olmo-7b \
      --n 12 --out /tmp/dm-olmo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import grid as grid_mod          # noqa: E402
import oracle as oracle_mod      # noqa: E402
import registry as registry_mod  # noqa: E402
import report as report_mod      # noqa: E402
import score as score_mod        # noqa: E402
import serve as serve_mod        # noqa: E402
import confirm as confirm_mod    # noqa: E402

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


def _need_yaml() -> None:
    if yaml is None:
        raise SystemExit("pyyaml is required; run under the repo .venv "
                         "(.venv/bin/python).")


def _ensure_infer_stack_importable() -> bool:
    try:
        import infer_stack  # noqa: F401
        return True
    except ModuleNotFoundError:
        for parent in HERE.parents:
            cand = parent / "submodules" / "infer_stack"
            if (cand / "infer_stack" / "__init__.py").exists():
                sys.path.insert(0, str(cand))
                try:
                    import infer_stack  # noqa: F401
                    return True
                except Exception:  # noqa: BLE001
                    return False
    return False


def _render_vllm_commands(catalog_dict: dict) -> list[str]:
    """Best-effort: render the exact `vllm serve` line per endpoint."""
    if not _ensure_infer_stack_importable():
        return ["(infer_stack not importable — skipping command render; "
                "PYTHONPATH=submodules/infer_stack to enable)"]
    import shlex
    from infer_stack.leasing.catalog import Catalog
    from infer_stack.leasing.compose import _vllm_service
    from infer_stack.leasing.models import Deployment

    cat = Catalog.from_dict(catalog_dict)
    images = {"vllm": "vllm/vllm-openai:<pinned>"}
    state = {"hf_cache": "<hf-cache>"}
    out = []
    keys = {n: cat.resolve_endpoint(n).compat_key for n in cat.endpoints}
    out.append(f"{len(cat.endpoints)} endpoints, {len(set(keys.values()))} distinct "
               f"compat-keys ({'no coalescing' if len(set(keys.values())) == len(keys) else 'COALESCING!'})")
    for name in sorted(cat.endpoints):
        req = cat.resolve_endpoint(name)
        dep = Deployment(id=f"dep-{name}", compat_key=req.compat_key, engine="vllm",
                         sharing=req.sharing, capacity=req.capacity, spec=req.spec,
                         served={req.endpoint: req.served}, state="live",
                         created_at=0.0, updated_at=0.0)
        svc = _vllm_service(dep, gpus=[0], host_port=None, images=images, state=state)
        out.append(f"# {name}\n  vllm serve " +
                   " ".join(shlex.quote(a) for a in svc["command"]))
    return out


# --------------------------------------------------------------------------- #
def cmd_sample(args: argparse.Namespace) -> int:
    orc = oracle_mod.load_oracle(args.run, n=args.n, strategy=args.strategy)
    if not oracle_mod.has_official_completions(orc):
        print("WARN: no official completions in this run (prompt-only) — scoring "
              "against official will be impossible.", file=sys.stderr)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(orc.to_json(), indent=2))
    print(f"[sample] {orc.run_name}: {len(orc.sample)}/{orc.n_available} instances "
          f"-> {out}")
    print(f"[sample] model={orc.model} deployment={orc.model_deployment} recipe={orc.recipe}")
    return 0


def _build_grid_from_oracle(orc, args):
    resolution = registry_mod.resolve(
        orc.model, orc.model_deployment,
        source_override=args.source, protocol_override=args.protocol)
    spec = _load_spec(args)
    if getattr(args, "profile", None) == "hf-match":
        _warn_if_not_hf_client(resolution)
    g = grid_mod.build_grid(resolution, spec=spec)
    return resolution, g


def _load_spec(args) -> dict | None:
    """Merge the built-in ``--profile`` spec (if any) UNDER an optional ``--grid``
    YAML; the user's YAML overrides the profile per top-level key."""
    spec: dict = {}
    profile = getattr(args, "profile", None)
    if profile:
        prof = grid_mod.BUILTIN_PROFILES[profile]
        spec = {k: (dict(v) if isinstance(v, dict) else v) for k, v in prof.items()}
    if getattr(args, "grid", None):
        _need_yaml()
        user = yaml.safe_load(Path(args.grid).read_text()) or {}
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(spec.get(k), dict):
                spec[k] = {**spec[k], **v}       # deep-merge axes/runtime dicts
            else:
                spec[k] = v
    # Convenience axis overrides (win over profile + --grid): narrow the two
    # expensive serve-time axes without hand-writing a YAML. e.g. skip float32
    # on a MoE model that OOMs the Triton kernel: --dtypes auto,bfloat16,float16.
    axes_override: dict = {}
    if getattr(args, "dtypes", None):
        axes_override["dtype"] = [d.strip() for d in args.dtypes.split(",") if d.strip()]
    if getattr(args, "attention_backends", None):
        axes_override["attention_backend"] = [
            b.strip() for b in args.attention_backends.split(",") if b.strip()]
    if axes_override:
        spec["axes"] = {**(spec.get("axes") or {}), **axes_override}
    return spec or None


def _warn_if_not_hf_client(resolution) -> None:
    """`--profile hf-match` only makes sense when the official side WAS HF."""
    cc = resolution.official_client_class
    if cc and "HuggingFace" not in cc:
        print(f"WARN: --profile hf-match but official client_class={cc!r} is not a "
              "HuggingFaceClient — the official completions were not produced by a local "
              "transformers.generate(); matching-to-HF may be the wrong target.",
              file=sys.stderr)
    elif not cc:
        print("WARN: --profile hf-match but official client_class is unknown "
              "(not resolvable from model_deployments.yaml) — assuming HF; verify.",
              file=sys.stderr)


_SETTINGS_YAML = """\
# infer-stack settings for a deployment-match grid (generated by dev/tools/deployment_match).
# `run` points INFER_STACK_CONFIG_DIR here so the catalog.yaml beside this file is
# the active catalog. data_dir inherits env > default (set INFER_STACK_DATA_DIR to
# a docker-mountable big disk; sharing the production HF cache avoids re-downloads).
backend: compose
litellm: true
ui: false
skip_display_gpus: false
reverse_proxy: false
"""


def _write_grid(resolution, g, out_dir: Path) -> None:
    _need_yaml()
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog = g.to_catalog()
    (out_dir / "catalog.yaml").write_text(yaml.safe_dump(catalog, sort_keys=False))
    (out_dir / "settings.yaml").write_text(_SETTINGS_YAML)
    (out_dir / "cells.json").write_text(json.dumps([c.__dict__ for c in g.cells], indent=2))
    (out_dir / "grid.json").write_text(json.dumps(g.to_json(), indent=2))
    (out_dir / "resolution.json").write_text(json.dumps(resolution.__dict__, indent=2))


def _print_grid_summary(resolution, g) -> None:
    print(f"[grid] model={resolution.model} source={resolution.hf_source} "
          f"protocol={resolution.protocol}"
          f"{'' if resolution.protocol_resolved else ' (UNRESOLVED)'}")
    print(f"[grid] official: tokenizer={resolution.official_tokenizer} "
          f"max_seq_len={resolution.official_max_sequence_length}")
    for n in resolution.notes:
        print(f"[grid]   note: {n}")
    print(f"[grid] {len(g.serve_recipes)} serve-recipes x {len(g.request_variants)} "
          f"request-variants = {len(g.cells)} cells"
          f"{f' (+{g.capped} over cap dropped)' if g.capped else ''}")
    for n in g.notes:
        print(f"[grid]   note: {n}")


def cmd_grid(args: argparse.Namespace) -> int:
    orc = oracle_mod.Oracle.from_json(json.loads(Path(args.oracle).read_text()))
    resolution, g = _build_grid_from_oracle(orc, args)
    out_dir = Path(args.out)
    _write_grid(resolution, g, out_dir)
    _print_grid_summary(resolution, g)
    print("\n[grid] rendered vllm commands:")
    for line in _render_vllm_commands(g.to_catalog()):
        print(line)
    print(f"\n[grid] wrote catalog.yaml / cells.json / grid.json -> {out_dir}")
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    orc = oracle_mod.load_oracle(args.run, n=args.n, strategy=args.strategy)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "oracle.json").write_text(json.dumps(orc.to_json(), indent=2))
    print(f"[dry-run] {orc.run_name}: sampled {len(orc.sample)}/{orc.n_available}; "
          f"model={orc.model} deployment={orc.model_deployment}")
    print(f"[dry-run] recipe={orc.recipe}")
    if not oracle_mod.has_official_completions(orc):
        print("[dry-run] WARN: prompt-only run (no official completions).")
    resolution, g = _build_grid_from_oracle(orc, args)
    _write_grid(resolution, g, out_dir)
    _print_grid_summary(resolution, g)
    print("\n[dry-run] rendered vllm commands:")
    for line in _render_vllm_commands(g.to_catalog()):
        print(line)
    print(f"\n[dry-run] sampled instance ids: "
          f"{', '.join(s.instance_id for s in orc.sample)}")
    print(f"[dry-run] wrote oracle.json / catalog.yaml / cells.json / grid.json -> {out_dir}")
    print("[dry-run] next (GPU host): serve each catalog endpoint and probe the cells, "
          "then `score`.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    grid_dir = Path(args.grid_dir)
    out_dir = Path(args.out) if args.out else grid_dir / "results"
    serve_mod.run_grid(grid_dir, out_dir, allowed_gpus=args.allowed_gpus,
                       litellm_port=args.litellm_port, base_url=args.base_url,
                       timeout=args.timeout, dry=args.dry)
    if not args.dry:
        print(f"\n[run] next: score against the oracle:\n"
              f"  {Path(__file__).name} score --oracle {grid_dir}/oracle.json "
              f"--results {out_dir} --cells {grid_dir}/cells.json --out {out_dir}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    orc = oracle_mod.Oracle.from_json(json.loads(Path(args.oracle).read_text()))
    results_dir = Path(args.results)
    cell_docs = [json.loads(p.read_text()) for p in sorted(results_dir.glob("*.json"))
                 if p.name not in ("scored.json", "best_deployment.json")]
    if not cell_docs:
        raise SystemExit(f"no cell result JSONs in {results_dir}")
    scored = score_mod.rank(cell_docs, _oracle_sample_dicts(orc))
    # cells_by_id for serve-knob lookup in best_deployment
    cells_by_id: dict = {}
    cells_path = Path(args.cells) if args.cells else results_dir.parent / "cells.json"
    if cells_path.exists():
        for c in json.loads(cells_path.read_text()):
            cells_by_id[c["cell_id"]] = c

    resolution = _resolution_for_score(orc, args, cells_path)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ranking = report_mod.render_ranking(scored)
    snippets = report_mod.render_snippets(scored, _oracle_sample_dicts(orc))
    best = report_mod.best_deployment(scored, cells_by_id, resolution)
    print(ranking)
    print(snippets)
    (out_dir / "ranking.txt").write_text(ranking + "\n")
    (out_dir / "snippets.txt").write_text(snippets + "\n")
    (out_dir / "scored.json").write_text(json.dumps(scored, indent=2))
    _need_yaml()
    (out_dir / "best_deployment.yaml").write_text(yaml.safe_dump(best, sort_keys=False))
    print(f"\n[score] winner: {best.get('winner_cell')} "
          f"(composite={best.get('composite')})  -> {out_dir}/best_deployment.yaml")
    for n in best.get("notes", []):
        print(f"[score]   note: {n}")
    return 0


def _oracle_sample_dicts(orc) -> list[dict]:
    from dataclasses import asdict
    return [asdict(s) for s in orc.sample]


def _resolution_for_score(orc, args, cells_path):
    # Prefer the resolution.json written next to cells.json (has hf_source etc.).
    res_path = cells_path.parent / "resolution.json" if cells_path else None
    if res_path and res_path.exists():
        return registry_mod.Resolution(**json.loads(res_path.read_text()))
    return registry_mod.resolve(orc.model, orc.model_deployment,
                                source_override=getattr(args, "source", None))


def cmd_auto(args: argparse.Namespace) -> int:
    """End-to-end: dry-run (sample+grid) -> run (serve+probe) -> score -> confirm.

    `--dry` stops after emitting the grid + printing the serve plan (CPU-only, no
    GPU); drop it on a GPU host to serve, probe, score, and emit the confirm plan.
    """
    out = Path(args.out)
    orc = oracle_mod.load_oracle(args.run, n=args.n, strategy=args.strategy)
    out.mkdir(parents=True, exist_ok=True)
    (out / "oracle.json").write_text(json.dumps(orc.to_json(), indent=2))
    print(f"[auto] {orc.run_name}: sampled {len(orc.sample)}/{orc.n_available}; "
          f"model={orc.model} deployment={orc.model_deployment}")
    if not oracle_mod.has_official_completions(orc):
        print("[auto] WARN: prompt-only run (no official completions) — scoring "
              "against official will be impossible.")
    resolution, g = _build_grid_from_oracle(orc, args)
    _write_grid(resolution, g, out)
    _print_grid_summary(resolution, g)

    results = out / "results"
    serve_mod.run_grid(out, results, allowed_gpus=args.allowed_gpus,
                       litellm_port=args.litellm_port, base_url=args.base_url,
                       timeout=args.timeout, dry=args.dry)
    if args.dry:
        print("\n[auto] --dry: grid + serve plan emitted; re-run without --dry on a "
              "GPU host to serve, probe, score, and confirm.")
        return 0

    cmd_score(argparse.Namespace(
        oracle=str(out / "oracle.json"), results=str(results),
        cells=str(out / "cells.json"), source=args.source, out=str(results)))

    if not args.skip_confirm:
        best = results / "best_deployment.yaml"
        if best.exists():
            cmd_confirm(argparse.Namespace(
                best=str(best), run=str(args.run), local_run=None,
                out=str(out / "confirm")))
    print(f"\n[auto] done. ranking={results}/ranking.txt  "
          f"best={results}/best_deployment.yaml")
    if not args.skip_confirm:
        print(f"[auto] confirm plan={out}/confirm/confirm_plan.md — produce a full "
              f"local run per the plan, then: {Path(__file__).name} confirm "
              f"--best {results}/best_deployment.yaml --run {args.run} "
              f"--local-run <dir> --out {out}/confirm")
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    res = confirm_mod.confirm(args.best, args.run, args.out, local_run=args.local_run)
    print(f"[confirm] winner={res['winner_cell']}")
    print(f"[confirm] plan -> {res['plan']}")
    print(f"[confirm] winning serve catalog -> {res['serve_catalog']}")
    if "pair_report" in res:
        pr = res["pair_report"]
        print(f"[confirm] compare-pair diagnosis={pr['diagnosis_label']} "
              f"run_level_agree_ratio={pr['run_level_agree_ratio']}")
        print(f"[confirm] pair report -> {pr['txt']}")
    else:
        print("[confirm] no --local-run given; run the plan on a GPU host, then "
              "re-run with --local-run <dir> to compare.")
    return 0


def cmd_selftest(_args: argparse.Namespace) -> int:
    return score_mod.selftest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _run_opts(p):
        p.add_argument("--n", type=int, default=16)
        p.add_argument("--strategy", default="spread-by-length",
                       choices=["spread-by-length", "head", "random"])

    def _grid_opts(p):
        p.add_argument("--source", default=None, help="override the local HF source repo")
        p.add_argument("--protocol", default=None, choices=["completions", "chat"])
        p.add_argument("--grid", default=None, help="grid spec YAML (axes/runtime/cap)")
        p.add_argument("--profile", default=None, choices=sorted(grid_mod.BUILTIN_PROFILES),
                       help="built-in grid profile merged under --grid (hf-match: pin "
                       "vLLM determinism knobs to match a HuggingFaceClient run)")
        p.add_argument("--dtypes", default=None,
                       help="comma-separated dtype axis override (wins over profile/--grid), "
                       "e.g. 'auto,bfloat16,float16' to skip float32 on a MoE model that "
                       "OOMs the Triton kernel")
        p.add_argument("--attention-backends", default=None,
                       help="comma-separated attention_backend axis override, e.g. "
                       "'none,XFORMERS' to narrow the hf-match backend sweep")

    s = sub.add_parser("sample"); s.add_argument("--run", required=True)
    _run_opts(s); s.add_argument("--out", required=True); s.set_defaults(func=cmd_sample)

    g = sub.add_parser("grid"); g.add_argument("--oracle", required=True)
    _grid_opts(g); g.add_argument("--out", required=True); g.set_defaults(func=cmd_grid)

    d = sub.add_parser("dry-run"); d.add_argument("--run", required=True)
    _run_opts(d); _grid_opts(d); d.add_argument("--out", required=True)
    d.set_defaults(func=cmd_dry_run)

    rn = sub.add_parser("run", help="serve each endpoint and probe its cells (GPU host)")
    rn.add_argument("--grid-dir", required=True, help="dir with catalog.yaml/cells.json/oracle.json")
    rn.add_argument("--out", default=None, help="results dir (default: <grid-dir>/results)")
    rn.add_argument("--allowed-gpus", default=None,
                    help="OPTIONAL restrict placement to these GPUs "
                    "(INFER_STACK_ALLOWED_GPUS); default: let infer-stack place on "
                    "any available GPU (acquire --queue)")
    rn.add_argument("--litellm-port", type=int, default=14042)
    rn.add_argument("--base-url", default=None, help="override gateway base url")
    rn.add_argument("--timeout", type=float, default=120.0)
    rn.add_argument("--dry", action="store_true", help="print the plan; touch no GPU")
    rn.set_defaults(func=cmd_run)

    sc = sub.add_parser("score"); sc.add_argument("--oracle", required=True)
    sc.add_argument("--results", required=True); sc.add_argument("--cells", default=None)
    sc.add_argument("--source", default=None); sc.add_argument("--out", required=True)
    sc.set_defaults(func=cmd_score)

    au = sub.add_parser("auto", help="end-to-end: dry-run -> run -> score -> confirm")
    au.add_argument("--run", required=True, help="the public HELM run dir")
    _run_opts(au); _grid_opts(au)
    au.add_argument("--out", required=True)
    au.add_argument("--allowed-gpus", default=None,
                    help="OPTIONAL restrict placement to these GPUs; default: let "
                    "infer-stack place on any available GPU (acquire --queue)")
    au.add_argument("--litellm-port", type=int, default=14042)
    au.add_argument("--base-url", default=None)
    au.add_argument("--timeout", type=float, default=120.0)
    au.add_argument("--dry", action="store_true", help="stop after the grid + serve plan (no GPU)")
    au.add_argument("--skip-confirm", action="store_true", help="skip the confirm-plan step")
    au.set_defaults(func=cmd_auto)

    cf = sub.add_parser("confirm", help="confirm the winner vs official (plan + compare-pair)")
    cf.add_argument("--best", required=True, help="best_deployment.yaml from `score`")
    cf.add_argument("--run", required=True, help="the official HELM run dir")
    cf.add_argument("--local-run", default=None, help="full local run dir to compare (optional)")
    cf.add_argument("--out", required=True)
    cf.set_defaults(func=cmd_confirm)

    st = sub.add_parser("selftest"); st.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
