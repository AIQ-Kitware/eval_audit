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
# History: 2026-07-21 launch failed twice live — (1) template matcher used an
# exact string vs OLMo-2's compound conditional (fixed: identifier regex);
# (2) template written to a path the vLLM container cannot see (fixed: write
# to the hf-cache mount's host side, reference the container path, scrub the
# stale extra_args). Steps fail loudly and independently.
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
# The template must be READABLE INSIDE THE vLLM CONTAINER, which mounts ONLY
# infer-stack's cache dirs (compose.py: hf-cache -> /root/.cache/huggingface).
# Writing it anywhere else fails at serve time with no artifact written
# (observed 2026-07-21 overnight: both e2e runs died this way — the audit
# store is not mounted in the container). So: write to the HOST side of the
# hf-cache mount, reference by the CONTAINER path.
DATA_DIR="${INFER_STACK_DATA_DIR:-/data/service/infer-stack}"
TEMPLATE_HOST="$DATA_DIR/hf-cache/chat-templates/${HF_ID##*/}-agp0.jinja"
TEMPLATE_CONTAINER="/root/.cache/huggingface/chat-templates/${HF_ID##*/}-agp0.jinja"

for f in "$SERVE_CATALOG" "$SRC_BUNDLE/full_manifest.yaml" "$SRC_BUNDLE/model_deployments.yaml"; do
  [[ -f "$f" ]] || { echo "FAIL: missing $f (rsync incomplete, or confirm phase never ran?)" >&2; exit 1; }
done

# A previous failed launch can leave a leaked lease and a crash-looping
# container (restart: unless-stopped + a bad serve arg). Sweep before start.
echo "Reclaiming any leaked leases before start (infer-stack gc)…"
infer-stack gc --yes || echo "WARN: 'infer-stack gc' returned nonzero; continuing." >&2

echo "== [1/4] agp0 chat template (host side of the hf-cache mount) =="
TEMPLATE_DIR="$(dirname "$TEMPLATE_HOST")"
# The hf-cache mount is root-owned (the container writes weights there as root).
# The template must live ON it so the vLLM container can read it, so its subdir
# needs to be made writable ONCE — setgid so it stays writable for the team.
if ! mkdir -p "$TEMPLATE_DIR" 2>/dev/null || [[ ! -w "$TEMPLATE_DIR" ]]; then
  echo "FAIL: cannot write $TEMPLATE_DIR (root-owned hf-cache mount)." >&2
  echo "      Create it once, setgid so both you and teammates keep write access:" >&2
  echo "        sudo install -d -o \"\$USER\" -g \"domain users\" -m 2775 $TEMPLATE_DIR" >&2
  exit 1
fi
"$PYTHON_BIN" - "$HF_ID" "$TEMPLATE_HOST" <<'EOF'
import re, sys
from transformers import AutoTokenizer
hf_id, out = sys.argv[1], sys.argv[2]
t = AutoTokenizer.from_pretrained(hf_id).chat_template
# The flag appears inside COMPOUND conditionals (OLMo-2:
# "{% if loop.last and add_generation_prompt %}"), so replace the bare
# identifier, never an exact conditional string (that failed 2026-07-21).
hits = [m for m in re.finditer(r".{0,50}\badd_generation_prompt\b.{0,60}", t)]
if not hits:
    sys.exit(f"FAIL: identifier add_generation_prompt not in {hf_id}'s template - inspect by hand")
patched = re.sub(r"\badd_generation_prompt\b", "false", t)
open(out, "w").write(patched)
print(f"  wrote {out}")
print(f"  neutralized {len(hits)} use(s); EYEBALL each original context:")
for m in hits:
    print(f"    ...{m.group(0)!r}...")
EOF

echo "== [2/4] patch confirm catalog: chat_template (container path) =="
"$PYTHON_BIN" - "$SERVE_CATALOG" "$ENDPOINT" "$TEMPLATE_CONTAINER" <<'EOF'
import sys, yaml
cat_path, endpoint, template = sys.argv[1:4]
cat = yaml.safe_load(open(cat_path))
rt = cat["endpoints"][endpoint]["runtime"]
# Scrub any stale --chat-template pair from extra_args (the 2026-07-21 attempt
# left a host path the container cannot see; leaving it would still crash the
# serve even with the runtime key set correctly below).
args = rt.get("extra_args") or []
while "--chat-template" in args:
    i = args.index("--chat-template")
    stale = args[i : i + 2]
    del args[i : i + 2]
    print(f"  scrubbed stale extra_args entry: {stale}")
# Use the NATIVE runtime key: it is part of the endpoint's structural identity
# (infer_stack models.py STRUCTURAL_KEYS), so changing the template makes a
# distinct deployment rather than silently reusing a stale container.
rt["chat_template"] = template
print(f"  runtime.chat_template = {template}")
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
