#!/usr/bin/env bash
# Confirm a deployment-match fp32 winner END-TO-END through the normal HELM
# replay path — the "authoritative confirm" step the sweep's confirm_plan.md
# describes, automated. One invocation per model:
#
#   ./60_confirm_fp32_e2e.sh 7b          # OLMo-2-1124-7B-Instruct, ifeval
#   ./60_confirm_fp32_e2e.sh 13b         # OLMo-2-1124-13B-Instruct, ifeval
#
# What it does (each step idempotent, loud on failure):
#   1. Builds the agp0 chat template (official runs rendered WITHOUT the
#      trailing generation prompt — old-transformers behavior; ranking.txt:
#      fp32+agp0 MATCH 0.915/0.904, fp32+agp1 PARTIAL 0.158/0.250) and shows
#      what changed for the operator to eyeball.
#   2. Patches the sweep's confirm catalog to serve with that template
#      (--chat-template in extra_args).
#   3. Generates an ifeval-only variant bundle (manifest + model_deployments)
#      pointing at the dm fp32 endpoint; fresh suite name so skip_existing
#      never collides with the bf16 baseline.
#   4. Launches eval-audit-run --lease (self-acquires the endpoint; GPU
#      placement is infer-stack's job — no manual acquire, no GPU pinning).
#
# The scientific question: our bf16/modern-template locals score ~+0.10 ABOVE
# the officials on ifeval_strict_accuracy (7B 0.791 vs 0.693; 13B 0.856 vs
# 0.730). If the recovered config (fp32 + agp0) brings the full-run aggregate
# back to the official, the published number is exactly recoverable from
# recovered execution context. Outcome definitions are FROZEN in
# overnight_confirm_plan.md — classify there, do not improvise.
#
# NB: written 2026-07-21 for the overnight confirm; syntax-checked but not
# yet executed on the GPU host — each step fails loudly and independently.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_lib.sh"
cd "$ROOT"

SIZE="${1:-}"
case "$SIZE" in
  7b)  SLUG="olmo-2-1124-7b-instruct";  HF_ID="allenai/OLMo-2-1124-7B-Instruct" ;;
  13b) SLUG="olmo-2-1124-13b-instruct"; HF_ID="allenai/OLMo-2-1124-13B-Instruct" ;;
  *)   echo "usage: $0 <7b|13b>" >&2; exit 1 ;;
esac

DM_DIR="$STORE_ROOT/deployment-match/${SLUG}--ifeval-vllm"
ENDPOINT="dm-${SLUG}-fp32-attnflash-attn"
SERVE_CATALOG="$DM_DIR/confirm/serve/catalog.yaml"
SRC_BUNDLE="$STORE_ROOT/local-bundles/allenai-${SLUG}"
VAR_BUNDLE="${SRC_BUNDLE}-ifeval-fp32"
TEMPLATE="$STORE_ROOT/deployment-match/chat-templates/${HF_ID##*/}-agp0.jinja"

for f in "$SERVE_CATALOG" "$SRC_BUNDLE/full_manifest.yaml" "$SRC_BUNDLE/model_deployments.yaml"; do
  [[ -f "$f" ]] || { echo "FAIL: missing $f (rsync incomplete, or confirm phase never ran?)" >&2; exit 1; }
done

echo "== [1/4] agp0 chat template =="
mkdir -p "$(dirname "$TEMPLATE")"
"$PYTHON_BIN" - "$HF_ID" "$TEMPLATE" <<'EOF'
import sys
from transformers import AutoTokenizer
hf_id, out = sys.argv[1], sys.argv[2]
t = AutoTokenizer.from_pretrained(hf_id).chat_template
n = t.count("{% if add_generation_prompt %}")
if n == 0:
    sys.exit(f"FAIL: no add_generation_prompt block found in {hf_id}'s template - inspect by hand")
patched = t.replace("{% if add_generation_prompt %}", "{% if false %}")
open(out, "w").write(patched)
print(f"  wrote {out}")
print(f"  disabled {n} add_generation_prompt block(s); EYEBALL the guarded text below:")
import re
for m in re.finditer(r"\{% if false %\}(.{0,80})", patched, re.S):
    print(f"    suppressed: {m.group(1)!r}")
EOF

echo "== [2/4] patch confirm catalog with --chat-template =="
"$PYTHON_BIN" - "$SERVE_CATALOG" "$ENDPOINT" "$TEMPLATE" <<'EOF'
import sys, yaml
cat_path, endpoint, template = sys.argv[1:4]
cat = yaml.safe_load(open(cat_path))
args = cat["endpoints"][endpoint]["runtime"]["extra_args"]
if "--chat-template" in args:
    i = args.index("--chat-template")
    args[i + 1] = template
    print(f"  already present; repointed to {template}")
else:
    args += ["--chat-template", template]
    print(f"  appended --chat-template {template}")
yaml.safe_dump(cat, open(cat_path, "w"), sort_keys=False)
EOF

echo "== [3/4] variant bundle (ifeval-only, fp32 endpoint) =="
MASTER_KEY="$(infer-stack env LITELLM_MASTER_KEY)" \
  || { echo "FAIL: cannot read LITELLM_MASTER_KEY — is the infer-stack gateway up?" >&2; exit 1; }
mkdir -p "$VAR_BUNDLE"
"$PYTHON_BIN" - "$SRC_BUNDLE" "$VAR_BUNDLE" "$SERVE_CATALOG" "$ENDPOINT" "$SLUG" "$MASTER_KEY" <<'EOF'
import sys, yaml
src, var, catalog, endpoint, slug, key = sys.argv[1:7]
man = yaml.safe_load(open(f"{src}/full_manifest.yaml"))
ifeval = [e for e in man["run_entries"] if e.startswith("ifeval:")]
assert ifeval, "no ifeval entry in the source manifest"
man["run_entries"] = ifeval
man["experiment_name"] = man["suite"] = f"audit-allenai-{slug}-ifeval-fp32"
man["lease_endpoint"] = endpoint
man["lease_catalog"] = catalog
man["model_deployments_fpath"] = f"{var}/model_deployments.yaml"
man["tmux_workers"] = 1
yaml.safe_dump(man, open(f"{var}/manifest.yaml", "w"), sort_keys=False)
dep = yaml.safe_load(open(f"{src}/model_deployments.yaml"))
args = dep["model_deployments"][0]["client_spec"]["args"]
args["openai_model_name"] = endpoint
args["api_key"] = key
yaml.safe_dump(dep, open(f"{var}/model_deployments.yaml", "w"), sort_keys=False)
print(f"  {var}/manifest.yaml  ({len(ifeval)} run entry)")
EOF

echo "== [4/4] launch (blocks; run in its own tmux pane) =="
echo "  eval-audit-run --run=1 $VAR_BUNDLE/manifest.yaml"
exec eval-audit-run --run=1 "$VAR_BUNDLE/manifest.yaml" \
  --container-image "$OLMO_CONTAINER_IMAGE" --lease --tmux-workers 1
