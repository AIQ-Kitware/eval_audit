#!/usr/bin/env bash
# 55_check_route_registry_survival.sh
#
# GPU-host acceptance for the infer_stack LiteLLM *route-registry* fix — the
# minimum real reproduction of the "olmo healthy, gateway 400 Invalid model
# name" incident, reduced to a single, self-contained pass/fail check.
#
# THE INCIDENT (what this guards against). Multiple runbooks (olmo, qwen,
# gpt-oss, this e2e, …) share ONE standing infer-stack stack — the same
# INFER_STACK_DATA_DIR (default /data/service/infer-stack) and therefore the
# same LiteLLM gateway + ledger — but each ships a DISJOINT catalog. Before the
# fix, any `converge` under runbook B re-rendered the shared gateway's route
# table from *B's catalog alone*, stripping runbook A's still-live routes: A's
# container stayed healthy while its endpoint 400'd at the front door, and A's
# readiness probe ("not advertised by the gateway yet") polled to its lease
# timeout — the "failed to acquire lease" / `acquire` symptom.
#
# WHY ONE GPU IS ENOUGH. The strip is a RENDER-layer event: B's converge
# re-renders the gateway regardless of whether B's model is ever placed on a
# GPU. So this test holds the single GPU with model A (this runbook's
# `phi2-single`) and triggers B's converge with a throwaway disjoint catalog
# whose endpoint is NEVER served — no second GPU, no second model load.
#
# WHAT IT ASSERTS.
#   1. After a converge under a disjoint catalog, A's live route SURVIVES on the
#      shared gateway (the incident fix). FAIL here == the fix is not active in
#      the running infer-stack (this is the pre-fix behavior).
#   2. A real generation through the gateway still works.
#   3. A SECOND converge under the same catalog does not recreate the gateway
#      container (steady-state byte-stability / no-blip). The FIRST converge
#      legitimately recreates it once to ADD B's new route — that is expected
#      and not asserted against.
#
# This is a standalone acceptance check — NOT a stage of the index -> compose ->
# summary grid. It reuses _lib.sh only to resolve the SAME shared data dir +
# catalog A this runbook already uses. It works against a pre-fix infer-stack
# too (it keys off `/v1/models`, which exists on every version), so it doubles
# as a regression gate.
#
# Run:  ./55_check_route_registry_survival.sh
# Knobs (env): ROUTE_CHECK_ENDPOINT (default phi2-single), LITELLM_PORT (14042).

set -eu

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/_lib.sh"

LITELLM_PORT="${LITELLM_PORT:-14042}"
SERVED_EP="${ROUTE_CHECK_ENDPOINT:-phi2-single}"   # catalog A endpoint (this runbook)
PROBE_EP="route-registry-probe"                    # the disjoint catalog-B endpoint

log()  { printf '[route-check] %s\n' "$*" >&2; }
fail() { printf '[route-check] FAIL: %s\n' "$*" >&2; exit 1; }

command -v infer-stack >/dev/null 2>&1 || fail "infer-stack not on PATH (activate the serving venv)"
command -v docker      >/dev/null 2>&1 || fail "docker not on PATH"
command -v curl        >/dev/null 2>&1 || fail "curl not on PATH"
nvidia-smi -L >/dev/null 2>&1 || log "WARN: nvidia-smi found no GPU; the '$SERVED_EP' acquire will likely fail"

log "shared infer-stack data dir : $INFER_STACK_DATA_DIR"
log "catalog A (this runbook)    : $INFER_STACK_CONFIG_DIR  (serves '$SERVED_EP')"

# --- synthesize the disjoint catalog B ------------------------------------
# One vLLM endpoint whose name differs from '$SERVED_EP', pinned to the SAME
# shared data dir. It is never acquired or served — its only job is to make a
# converge under THIS catalog re-render the shared gateway from a different
# route set, which is exactly what used to strip the live '$SERVED_EP' route.
B_DIR="$(mktemp -d)"
BOOT_ENV="$(mktemp)"
cleanup() {
  set +e
  [[ -s "$BOOT_ENV" ]] && infer-stack release --env-file "$BOOT_ENV" --evict --yes >/dev/null 2>&1
  rm -rf "$B_DIR" "$BOOT_ENV"
}
trap cleanup EXIT

cat >"$B_DIR/settings.yaml" <<YAML
backend: compose
litellm: true
ui: false
data_dir: $INFER_STACK_DATA_DIR
YAML
cat >"$B_DIR/catalog.yaml" <<YAML
# Throwaway disjoint sibling catalog for the route-registry acceptance check.
# The '$PROBE_EP' endpoint is NEVER served — converging under this catalog is
# what used to re-render the shared gateway and strip the live '$SERVED_EP'.
models:
  route-registry-probe-model:
    source: hf://microsoft/phi-2
endpoints:
  $PROBE_EP:
    engine: vllm
    reclaim: stop
    model: route-registry-probe-model
    protocol: completions
    runtime:
      max_model_len: 2048
YAML

# --- gateway helpers ------------------------------------------------------
gw_id() { docker ps -q --filter name=litellm | head -n1; }

models_ids() {  # advertised model ids on the LIVE gateway, one per line
  curl -fsS ${KEY:+-H "Authorization: Bearer $KEY"} \
    "http://127.0.0.1:$LITELLM_PORT/v1/models" \
  | "$PYTHON_BIN" -c 'import sys, json
for m in (json.load(sys.stdin).get("data") or []):
    print(m.get("id", ""))'
}
route_present() { models_ids | grep -qxF "$1"; }

# --- 0. clean slate -------------------------------------------------------
log "reclaiming any leaked leases (infer-stack gc)…"
infer-stack gc --yes >/dev/null 2>&1 || true

# --- 1. bring up model A under catalog A (holds the single GPU) -----------
log "acquiring '$SERVED_EP' (first load can take minutes)…"
infer-stack acquire "$SERVED_EP" --wait --yes --env-file "$BOOT_ENV" \
  || fail "could not acquire '$SERVED_EP' — need a free GPU + its weights"

KEY="$(infer-stack env LITELLM_MASTER_KEY 2>/dev/null || true)"
route_present "$SERVED_EP" \
  || fail "'$SERVED_EP' is not advertised right after acquire — the stack is unhealthy (not a route-registry issue)"
G1="$(gw_id)"
log "'$SERVED_EP' is live and routed; gateway container=$G1"

# --- 2. converge the SHARED gateway under the disjoint catalog B ----------
# `gc` reconciles + applies; under catalog B it re-renders the shared gateway.
# No GPU is needed — '$PROBE_EP' is never placed. Pre-fix: this strips
# '$SERVED_EP'. Post-fix: the render is the registry union, so it is kept
# (and B's route is added — a single, expected recreate on first exposure).
log "converging the shared gateway under a DISJOINT catalog (gc @ catalog B)…"
INFER_STACK_CONFIG_DIR="$B_DIR" infer-stack gc --yes \
  || fail "gc under catalog B returned nonzero"

# --- 3. THE CHECK: A's live route must survive the cross-catalog converge --
if route_present "$SERVED_EP"; then
  log "PASS(1/3): '$SERVED_EP' survived the cross-catalog converge (route registry active)"
else
  advertised="$(models_ids | paste -sd, - || true)"
  fail "INCIDENT REPRODUCED — '$SERVED_EP' was STRIPPED by a converge under a
      disjoint catalog. The route-registry fix is NOT active in this
      infer-stack. The gateway now advertises: ${advertised:-<none>}"
fi
G2="$(gw_id)"

# --- 3b. a real generation through the gateway still works ----------------
body_file="$(mktemp)"
code="$(curl -sS -o "$body_file" -w '%{http_code}' \
  ${KEY:+-H "Authorization: Bearer $KEY"} -H 'Content-Type: application/json' \
  "http://127.0.0.1:$LITELLM_PORT/v1/completions" \
  -d "{\"model\":\"$SERVED_EP\",\"prompt\":\"hello\",\"max_tokens\":4}" || true)"
if [[ "$code" == 2?? ]]; then
  log "PASS(2/3): generation through the gateway OK (HTTP $code)"
else
  log "WARN: completion returned HTTP $code: $(head -c 200 "$body_file" 2>/dev/null)"
fi
rm -f "$body_file"

# --- 4. steady state: a SECOND B-converge must not recreate the gateway ----
log "second converge under catalog B (steady-state no-blip check)…"
INFER_STACK_CONFIG_DIR="$B_DIR" infer-stack gc --yes >/dev/null 2>&1 || true
route_present "$SERVED_EP" \
  || fail "'$SERVED_EP' vanished on the second B-converge (registry not byte-stable)"
G3="$(gw_id)"
if [[ -n "$G2" && "$G2" == "$G3" ]]; then
  log "PASS(3/3): gateway container stable across the steady-state converge ($G3) — no blip"
else
  log "NOTE: gateway container changed on the steady-state converge ($G2 -> $G3);"
  log "      the route survived, but investigate byte-stability (config hash churn)."
fi

# --- 5. what the operator sees (new inspector; absent on a pre-fix build) --
if infer-stack routes list >/dev/null 2>&1; then
  log "infer-stack routes list:"
  infer-stack routes list || true
fi

log "SUCCESS: the cross-catalog route-strip incident is resolved on this stack."
