#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# CPU-only preflight: the qwen/qwen3.5-9b-base registration ships as prod_env
# SIDECARS (model_metadata.yaml + tokenizer_configs.yaml next to the
# deployment yaml), copied in at run time via the manifest's
# model_metadata_fpath / tokenizer_configs_fpath. No helm install needs the
# ids baked in — this validates the sidecars are well-formed and consistent
# with the deployment + manifest before any GPU work.
python - <<'EOF'
import sys
import yaml

CONFIG_DIR = "configs/local_models/qwen35_9b_vllm"
MODEL_ID = "qwen/qwen3.5-9b-base"

metadata = yaml.safe_load(open(f"{CONFIG_DIR}/model_metadata.yaml"))
tokenizers = yaml.safe_load(open(f"{CONFIG_DIR}/tokenizer_configs.yaml"))
deployments = yaml.safe_load(open(f"{CONFIG_DIR}/model_deployments.yaml"))
manifest = yaml.safe_load(open("configs/qwen35_vllm_smoke_manifest.yaml"))

problems = []

model_names = {m["name"] for m in metadata.get("models", [])}
if MODEL_ID not in model_names:
    problems.append(f"model_metadata.yaml missing {MODEL_ID}")

tokenizer_names = {t["name"] for t in tokenizers.get("tokenizer_configs", [])}
deployment = deployments["model_deployments"][0]
if deployment["model_name"] != MODEL_ID:
    problems.append(f"deployment model_name={deployment['model_name']!r} != {MODEL_ID!r}")
if deployment["tokenizer_name"] not in tokenizer_names:
    problems.append(f"deployment tokenizer {deployment['tokenizer_name']!r} not in tokenizer_configs.yaml")

for key in ("model_deployments_fpath", "model_metadata_fpath", "tokenizer_configs_fpath"):
    fpath = manifest.get(key)
    if not fpath:
        problems.append(f"manifest missing {key}")
    else:
        try:
            yaml.safe_load(open(fpath))
        except OSError as ex:
            problems.append(f"manifest {key}={fpath!r} unreadable: {ex}")

for entry in manifest["run_entries"]:
    if f"model={MODEL_ID}" not in entry:
        problems.append(f"run_entry not targeting {MODEL_ID}: {entry}")

if problems:
    print("FAIL:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    raise SystemExit(1)

print(f"OK: {MODEL_ID} sidecar registration consistent "
      f"(deployment={deployment['name']}, {len(manifest['run_entries'])} run_entries).")
EOF
