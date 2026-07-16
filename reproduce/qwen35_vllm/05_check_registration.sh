#!/usr/bin/env bash
# CPU-only preflight: qwen/qwen3.5-9b-base is a NET-NEW id registered via
# REGISTRY SIDECARS (model_metadata.yaml + tokenizer_configs.yaml under
# configs/local_models/qwen35_9b_vllm/), declared by the preset's
# model_metadata_fpath / tokenizer_configs_fpath and copied into prod_env at
# run time. This validates preset <-> sidecar consistency before any GPU work —
# no helm install needs the id baked in.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

QWEN35_PRESET="$QWEN35_PRESET" "$PYTHON_BIN" - <<'EOF'
import os
import sys

import yaml

from eval_audit.integrations.infer_stack.presets import PRESET_CONFIGS

preset_name = os.environ["QWEN35_PRESET"]
preset = PRESET_CONFIGS[preset_name]
model_id = preset["helm_model_name"]
problems = []

if preset.get("protocol_mode") != "completions":
    problems.append(
        f"preset protocol_mode={preset.get('protocol_mode')!r}; a base model must be 'completions'"
    )

for key, doc_key in (
    ("model_metadata_fpath", "models"),
    ("tokenizer_configs_fpath", "tokenizer_configs"),
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
    names = {item.get("name") for item in doc.get(doc_key, []) or []}
    want = model_id if doc_key == "models" else preset["helm_tokenizer_name"]
    if want not in names:
        problems.append(f"{fpath} does not register {want!r} (has: {sorted(names)})")

for spec_key in ("smoke_manifest", "full_manifest"):
    for entry in preset[spec_key]["run_entries"]:
        if f"model={model_id}" not in entry:
            problems.append(f"{spec_key} run_entry not targeting {model_id}: {entry}")
    if preset[spec_key].get("precomputed_root"):
        problems.append(f"{spec_key} sets precomputed_root — this is a COMPUTE preset")

if problems:
    print("FAIL:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    raise SystemExit(1)

print(
    f"OK: {preset_name} consistent — {model_id} registered via sidecars, "
    f"completions protocol, compute mode, deployment {preset['model_deployment_name']}."
)
EOF
