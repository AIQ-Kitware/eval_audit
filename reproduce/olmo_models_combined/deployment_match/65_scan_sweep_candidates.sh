#!/usr/bin/env bash
# Frozen selection scan for tonight's cross-family probe sweeps (see
# overnight_confirm_plan.md §sweeps — the rule is frozen BEFORE looking at any
# cell's discrepancies; this script only APPLIES it and prints the launch
# commands, it runs nothing).
#
# Rule: eligible corpus models with a HuggingFaceClient OFFICIAL run
# (run_spec model_deployment starts with "huggingface/"), family != allenai,
# <= 13B params (fp32 fits one 96GB card), having >= 1 generative-completion
# benchmark. Take the top TWO by run count from DISTINCT families. A
# dtype-PINNED candidate (per HELM's model_deployments.yaml) is kept
# deliberately as the control cell: prediction there is that the pinned dtype
# wins and fp32 does NOT.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_lib.sh"

"$PYTHON_BIN" - "$STORE_ROOT" <<'EOF'
import sys, json, pathlib, collections
store = sys.argv[1]
inv = json.load(open(f"{store}/analysis/filter_inventory.json"))
GENERATIVE = ("gsm", "narrative_qa", "med_qa", "wmt_14", "ifeval")
cand = collections.defaultdict(list)
for r in inv:
    if not (r.get("eligible_candidate") and r.get("model_has_hf_client")):
        continue
    if r["model"].startswith("allenai/"):
        continue
    p = r.get("model_num_parameters") or 0
    if p and p > 13e9:
        continue
    cand[r["model"]].append(r)

print(f"{'':14}{'model':45} {'runs':>4}  generative benchmarks")
picks = []
for m, rows in sorted(cand.items(), key=lambda kv: -len(kv[1])):
    spec = pathlib.Path(rows[0]["run_dir"]) / "run_spec.json"
    try:
        rs = json.load(open(spec))
    except OSError:
        print(f"{'no run_spec':14}{m:45} {len(rows):4}  ({spec})")
        continue
    dep = rs.get("adapter_spec", {}).get("model_deployment") or rs.get("model_deployment", "")
    gen = sorted({r["benchmark"] for r in rows if r["benchmark"] in GENERATIVE})
    tag = "HF-OFFICIAL" if str(dep).startswith("huggingface/") else "skip"
    print(f"{tag:14}{m:45} {len(rows):4}  {gen}")
    if tag == "HF-OFFICIAL" and gen:
        fam = m.split("/")[0]
        if fam not in {p[0] for p in picks}:
            run = next(r for r in rows if r["benchmark"] == gen[0])
            picks.append((fam, m, gen[0], run["run_dir"]))

print()
print("Launch commands (frozen rule -> top two distinct families).")
print("Write the per-cell PREDICTION into overnight_confirm_plan.md FIRST,")
print("then check dtype pinning:  grep -B2 -A6 '<deployment>' <helm>/model_deployments.yaml")
for i, (fam, m, bench, run_dir) in enumerate(picks[:2]):
    slug = m.split("/")[-1].lower().replace(".", "-")
    print(f"""
# sweep {i+1}: {m} on {bench} (GPU {2+i})
DM_RUN='{run_dir}' \\
DM_OUT='{store}/deployment-match/{slug}--{bench}-vllm' \\
DM_ALLOWED_GPUS={2+i} DM_PROFILE=hf-match ./run_deployment_match.sh""")
EOF
