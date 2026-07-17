#!/usr/bin/env bash
# CPU-only preflight: the three small base ids are NET-NEW, registered via one
# shared REGISTRY SIDECAR pair (model_metadata.yaml + tokenizer_configs.yaml
# under configs/local_models/qwen35_small_vllm/), declared by the combined
# preset's model_metadata_fpath / tokenizer_configs_fpath and copied into
# prod_env at run time. Validates preset <-> sidecar consistency for EVERY
# profile before any GPU work.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

QWEN35S_PRESET="$QWEN35S_PRESET" "$PYTHON_BIN" - <<'EOF'
import os
import sys

import yaml

from eval_audit.integrations.infer_stack.presets import PRESET_CONFIGS

preset_name = os.environ["QWEN35S_PRESET"]
preset = PRESET_CONFIGS[preset_name]
profiles = preset["profiles"]
problems = []

registered_models: set[str] = set()
registered_tokenizers: set[str] = set()
for key, doc_key, sink in (
    ("model_metadata_fpath", "models", registered_models),
    ("tokenizer_configs_fpath", "tokenizer_configs", registered_tokenizers),
):
    fpath = preset.get(key)
    if not fpath:
        problems.append(f"preset missing {key} (the sidecar registration)")
        continue
    try:
        doc = yaml.safe_load(open(fpath)) or {}
    except OSError as ex:
        problems.append(f"preset {key}={fpath!r} unreadable: {ex}")
        continue
    sink.update(item.get("name") for item in doc.get(doc_key, []) or [])

model_ids = []
for profile in profiles:
    model_id = profile["helm_model_name"]
    model_ids.append(model_id)
    if profile.get("protocol_mode") != "completions":
        problems.append(
            f"profile {profile['profile']}: protocol_mode="
            f"{profile.get('protocol_mode')!r}; a base model must be 'completions'"
        )
    if not profile.get("newline_tolerant"):
        problems.append(
            f"profile {profile['profile']}: newline_tolerant missing — the "
            f"paragraph-style base family needs the nlstrip client"
        )
    if "nlstrip" not in profile["model_deployment_name"]:
        problems.append(
            f"profile {profile['profile']}: deployment "
            f"{profile['model_deployment_name']!r} lacks the 'nlstrip' marker"
        )
    if model_id not in registered_models:
        problems.append(
            f"model_metadata sidecar does not register {model_id!r} "
            f"(has: {sorted(registered_models)})"
        )
    if profile["helm_tokenizer_name"] not in registered_tokenizers:
        problems.append(
            f"tokenizer sidecar does not register "
            f"{profile['helm_tokenizer_name']!r} "
            f"(has: {sorted(registered_tokenizers)})"
        )

for spec_key in ("smoke_manifest", "full_manifest"):
    spec = preset[spec_key]
    if spec.get("precomputed_root"):
        problems.append(f"{spec_key} sets precomputed_root — this is a COMPUTE preset")
    for entry in spec["run_entries"]:
        if not any(f"model={mid}" in entry for mid in model_ids):
            problems.append(f"{spec_key} run_entry targets no family member: {entry}")
        if "model_deployment=" not in entry:
            problems.append(
                f"{spec_key} run_entry lacks its inline model_deployment= token "
                f"(the multi-model per-run lease key): {entry}"
            )

if problems:
    print("FAIL:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    raise SystemExit(1)

n_full = len(preset["full_manifest"]["run_entries"])
print(
    f"OK: {preset_name} consistent — {len(profiles)} profiles "
    f"({', '.join(model_ids)}) registered via shared sidecars, completions "
    f"protocol, compute mode, {n_full} full run_entries."
)
EOF
