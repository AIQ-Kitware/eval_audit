#!/usr/bin/env python3
"""Render the exact `vllm serve` command each catalog endpoint would launch.

Static, GPU-free check: parses catalog.yaml with the *real* infer-stack catalog
+ compose code and prints the container command line per endpoint. Use it to
confirm — before leasing anything — that each deployment variant actually passes
the flags it is supposed to (especially `--dtype …`, which the matrix hinges on,
and which only reaches vLLM via `runtime.extra_args`; see catalog.yaml).

    python render_commands.py                 # uses sibling catalog.yaml
    python render_commands.py --catalog path/to/catalog.yaml

Needs the infer_stack package importable. From the eval_audit repo with the
`.venv` (which has pyyaml + ubelt):

    PYTHONPATH=submodules/infer_stack .venv/bin/python \
        reproduce/olmo_models/debug/olmo7b_deploy_matrix/render_commands.py
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path


def _ensure_infer_stack_importable() -> None:
    try:
        import infer_stack  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    # Walk up to the eval_audit repo root and add submodules/infer_stack.
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "submodules" / "infer_stack"
        if (cand / "infer_stack" / "__init__.py").exists():
            sys.path.insert(0, str(cand))
            return


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", default=str(Path(__file__).with_name("catalog.yaml")))
    args = ap.parse_args()

    _ensure_infer_stack_importable()
    from infer_stack.leasing.catalog import Catalog
    from infer_stack.leasing.compose import _vllm_service
    from infer_stack.leasing.models import Deployment

    cat = Catalog.load(args.catalog)
    print(f"catalog: {args.catalog}")
    print(f"models:    {', '.join(sorted(cat.models))}")
    print(f"endpoints: {', '.join(sorted(cat.endpoints))}")
    print(f"bundles:   {', '.join(sorted(cat.bundles))}\n")

    images = {"vllm": "vllm/vllm-openai:<pinned>"}
    state = {"hf_cache": "<hf-cache>", "ollama": "<ollama>"}

    for name in sorted(cat.endpoints):
        req = cat.resolve_endpoint(name)
        protocol = req.served.get("protocol", "chat")
        if req.engine != "vllm":
            print(f"# {name}  [engine={req.engine}]  tag={req.served.get('model')}")
            continue
        dep = Deployment(
            id=f"dep-{name}", compat_key=req.compat_key, engine="vllm",
            sharing=req.sharing, capacity=req.capacity, spec=req.spec,
            served={req.endpoint: req.served}, state="live",
            created_at=0.0, updated_at=0.0,
        )
        svc = _vllm_service(dep, gpus=[0], host_port=None, images=images, state=state)
        cmd = "vllm serve " + " ".join(shlex.quote(a) for a in svc["command"])
        print(f"# {name}  [engine=vllm, protocol={protocol}, "
              f"source={req.structural['model_ref']}, compat_key={req.compat_key}]")
        print(f"  {cmd}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
