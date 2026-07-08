#!/usr/bin/env bash
# Search the best LOCAL serving recipe (dtype / tokenizer / max_model_len /
# attention_backend / add_special_tokens / protocol) that reproduces ONE public
# HELM run — here `ifeval` on `allenai/olmo-2-1124-7b-instruct` — via the
# deployment-match tool at dev/tools/deployment_match/. Defaults to the `hf-match`
# profile (see DM_PROFILE below), since the olmo-2-1124-7b-instruct official is a HuggingFaceClient run.
#
# This is an OPTIONAL diagnostic living in a SUBFOLDER of the combined olmo
# runbook, NOT part of the fan-out grid (../10_run_smoke.sh / ../15_run_full.sh).
# The grids replay each preset's official run_spec.json verbatim and serve through
# the hand-authored `<preset>-single` endpoints in ../config/infer_stack/catalog.yaml
# — they assume the serving recipe is already right. THIS script is the tool that
# *finds* that recipe empirically when it is not: given the public run, it extracts
# a small instance sample + the official completions (the oracle), sweeps a grid of
# serve-recipes for the model, probes each on the sample, and ranks them by
# agreement with the official outputs. The winner lands in
# `$DM_OUT/results/best_deployment.yaml`. See the tool's README at
# dev/tools/deployment_match/README.md and the design plan at
# docs/planning/deployment-match-search-plan.md.
#
# Reuses the parent runbook's _lib.sh. It computes ROOT from its own location
# (olmo_models_combined/_lib.sh -> ../.. = repo root), so sourcing it one level
# down from this subfolder still resolves ROOT / STORE_ROOT / PYTHON_BIN
# correctly. Two things it sets DO carry into the tool's subprocesses:
#   * INFER_STACK_DATA_DIR — resolved once to a docker-mountable big disk; the
#     tool's generated settings.yaml inherits it (`data_dir: env > default`), so
#     the vLLM HF-weight-cache bind-mount lands on a real disk (never NFS $HOME),
#     and re-uses the production HF cache to avoid re-downloads.
#   * HF_TOKEN / HUGGING_FACE_HUB_TOKEN — so pulling the OLMo-2-1124-7B-Instruct weights/tokenizer
#     works if the Hub rate-limits or the repo needs auth.
# (The olmo-specific env _lib.sh also sets — OLMO_CONTAINER_IMAGE, the combined
# INFER_STACK_CONFIG_DIR — is inert here: the deployment-match tool writes its OWN
# infer-stack catalog.yaml + settings.yaml into $DM_OUT and points
# INFER_STACK_CONFIG_DIR there for the serve phase, so it never touches the olmo
# `<preset>-single` endpoints.)
# GPU placement is infer-stack's job (`acquire --queue`): unset means every
# detected GPU; pin with DM_ALLOWED_GPUS (or an exported INFER_STACK_ALLOWED_GPUS).
#
# Phases. The tool's `auto` chains dry-run (sample + grid, CPU) -> run (serve +
# probe each cell, GPU) -> score (rank vs official) -> confirm (emit a
# single-endpoint catalog + reproduction plan). Set DM_DRY=1 to stop after the
# grid + serve plan with NO GPU, so you can inspect the recipes first.
#
# The cheap sample only RANKS; the winner is CONFIRMED authoritatively only by a
# full local run compared to the official one (eval-audit-compare-pair) — the
# confirm step emits that plan.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_lib.sh"
cd "$ROOT"

# --- Knobs -------------------------------------------------------------------
# The public HELM run to reproduce (has run_spec.json + scenario_state.json with
# the official completions). Default: OLMo-2-1124-7B-Instruct on ifeval.
DM_RUN="${DM_RUN:-/data/crfm-helm-public/capabilities/benchmark_output/runs/v1.8.0/ifeval:num_output_tokens=2048,model=allenai_olmo-2-1124-7b-instruct}"
# How many instances to sample for the cheap ranking (length-spread by default).
DM_N="${DM_N:-12}"
# Where the tool writes oracle.json / catalog.yaml / cells.json / results/.
DM_OUT="${DM_OUT:-$STORE_ROOT/deployment-match/olmo-2-1124-7b-instruct--ifeval}"
# DM_DRY=1 stops after the grid + serve plan (CPU only, no GPU).
DM_DRY="${DM_DRY:-0}"
# Optional: restrict serving placement to specific physical GPU indices (csv).
DM_ALLOWED_GPUS="${DM_ALLOWED_GPUS:-}"
# Grid profile. DEFAULTS to `hf-match` because the default target (the olmo-2-1124-7b-instruct
# ifeval run) is a HELM *HuggingFaceClient* run — i.e. the official completions
# came from a local transformers.generate(), so matching-to-HF is the right goal.
# hf-match pins vLLM's determinism knobs (enforce-eager, no chunked-prefill, no
# prefix-caching, max_num_seqs=1 — HF has no equivalent of these) and SWEEPS the
# attention backend {default, FLASH_ATTN, XFORMERS, TORCH_SDPA} to find the recipe
# that best reproduces the official outputs. See
# docs/vllm-vs-huggingface-deployment-match.md.
#   DM_PROFILE=          ./run_deployment_match.sh   # opt out -> plain default grid
#   DM_PROFILE=hf-match  ./run_deployment_match.sh   # explicit (the default)
# NB: hf-match sweeps 4 backends, so it brings up ~4x the serve endpoints (each a
# model load). Use DM_DRY=1 first to preview the grid, or DM_PROFILE= to opt out.
# (No colon in the default so an explicit empty value opts out.)
DM_PROFILE="${DM_PROFILE-hf-match}"
# Optional: narrow the dtype axis, e.g. DM_DTYPES=auto,bfloat16 to serve fewer
# endpoints. NB: unlike OLMoE, olmo-2-1124-7b-instruct is a DENSE model, so there is no
# fused-MoE-kernel shared-memory limit — but fp32 weights are ~28 GB, so the
# grid's preflight feasibility filter still auto-prunes any dtype x GPU combo
# that won't fit the card's VRAM (you don't need to exclude fp32 by hand).
DM_DTYPES="${DM_DTYPES:-}"
# Optional: narrow the attention_backend sweep, e.g. DM_ATTN=none,XFORMERS.
DM_ATTN="${DM_ATTN:-}"
# Optional: DM_LOG_REQUESTS=1 turns on vLLM request logging so each request's
# post-chat-template prompt + sampling params appear in the container logs
# (view with `infer-stack` TUI logs or `docker compose logs <vllm-service>`).
# Useful to verify the prompt vLLM actually tokenizes matches HELM's.
DM_LOG_REQUESTS="${DM_LOG_REQUESTS:-}"

# The deployment-match core imports its sibling modules by bare name (cli.py adds
# its own dir to sys.path); the serve phase additionally imports `infer_stack`, so
# put the vendored submodule on PYTHONPATH. Needs pyyaml in $PYTHON_BIN's env.
dm() {
  PYTHONPATH="$ROOT/submodules/infer_stack:${PYTHONPATH:-}" \
    "$PYTHON_BIN" dev/tools/deployment_match/cli.py "$@"
}

# --- Preflight ---------------------------------------------------------------
if [[ ! -d "$DM_RUN" ]]; then
  echo "ERROR: public run dir not found: $DM_RUN" >&2
  echo "       Set DM_RUN to a valid HELM run directory." >&2
  exit 1
fi
if [[ ! -f "$DM_RUN/scenario_state.json" ]]; then
  echo "ERROR: $DM_RUN has no scenario_state.json — the oracle needs the official" >&2
  echo "       completions to score candidates against (display_requests.json is" >&2
  echo "       prompt-only). Point DM_RUN at a run dir that carries completions." >&2
  exit 1
fi

echo "==================================================================="
echo "== deployment-match: olmo-2-1124-7b-instruct on ifeval"
echo "==================================================================="
echo "  run     : $DM_RUN"
echo "  sample  : n=$DM_N"
echo "  out     : $DM_OUT"
echo "  mode    : $([[ "$DM_DRY" == 1 ]] && echo 'dry-run (CPU, no GPU)' || echo 'full (serve + probe + score, GPU)')"
[[ -n "$DM_ALLOWED_GPUS" ]] && echo "  gpus    : restricted to $DM_ALLOWED_GPUS"
[[ -n "$DM_PROFILE" ]] && echo "  profile : $DM_PROFILE"
[[ -n "$DM_DTYPES" ]] && echo "  dtypes  : $DM_DTYPES"
[[ -n "$DM_ATTN" ]] && echo "  attn    : $DM_ATTN"
echo

# --- Run ---------------------------------------------------------------------
# One-shot `auto`: dry-run -> run -> score -> confirm. It prints the resolved
# recipe + grid summary before touching a GPU, then serves each cell one at a
# time (its own gc/acquire/probe/release loop over the generated catalog).
args=(auto --run "$DM_RUN" --n "$DM_N" --out "$DM_OUT")
[[ "$DM_DRY" == 1 ]] && args+=(--dry)
[[ -n "$DM_ALLOWED_GPUS" ]] && args+=(--allowed-gpus "$DM_ALLOWED_GPUS")
[[ -n "$DM_PROFILE" ]] && args+=(--profile "$DM_PROFILE")
[[ -n "$DM_DTYPES" ]] && args+=(--dtypes "$DM_DTYPES")
[[ -n "$DM_ATTN" ]] && args+=(--attention-backends "$DM_ATTN")
[[ -n "$DM_LOG_REQUESTS" ]] && args+=(--log-requests)
dm "${args[@]}"

# --- Report ------------------------------------------------------------------
if [[ "$DM_DRY" == 1 ]]; then
  echo
  echo "OK (dry-run): grid + serve plan written to $DM_OUT"
  echo "     Inspect resolution.json / catalog.yaml, then re-run with DM_DRY=0 on a GPU host."
  exit 0
fi

BEST="$DM_OUT/results/best_deployment.yaml"
RANKING="$DM_OUT/results/ranking.txt"
echo
if [[ -f "$RANKING" ]]; then
  echo "----- ranking (cells by agreement with the official completions) -----"
  cat "$RANKING"
  echo
fi
if [[ -f "$BEST" ]]; then
  echo "----- best_deployment.yaml -----"
  cat "$BEST"
  echo
  echo "OK: winning serve recipe at $BEST"
  echo "Next (authoritative): produce a full local run for this recipe, then"
  echo "  dev/tools/deployment_match/cli.py confirm --best '$BEST' \\"
  echo "    --run '$DM_RUN' --local-run <full_local_run_dir> --out '$DM_OUT/confirm'"
else
  echo "WARN: no best_deployment.yaml at $BEST — check the run/score output above." >&2
  exit 1
fi
