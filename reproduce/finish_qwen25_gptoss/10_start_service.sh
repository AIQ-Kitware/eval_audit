#!/usr/bin/env bash
# Bring up (or switch to) the pythia-qwen25-gptoss-mixed-4x96 profile.
#
# The profile co-resides four services on a 4x96GB host:
#   GPU 0  gpt-oss-20b              (~40 GiB, chat)
#   GPU 1  qwen2.5-7b-instruct       (~16 GiB, chat)
#   GPU 2  pythia-6.9b               (~16 GiB, completions)
#   GPU 3  pythia-2.8b-v0            (~ 8 GiB, completions)
#
# Pythia services are GPU-pinned identically to the existing
# ``pythia-qwen3.6-mixed-4x96`` profile someone else is using on this
# host, so a host already running those two pythia containers can be
# switched to this profile without recreating them.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFER_STACK_ROOT="$ROOT/submodules/infer_stack"
PROFILE="${VLLM_PROFILE:-pythia-qwen25-gptoss-mixed-4x96}"

cd "$INFER_STACK_ROOT"

# Detect whether the stack is already running. ``status`` exits non-zero
# when nothing is up; we treat both branches as fine.
if python manage.py status 2>/dev/null | grep -q "active_profile"; then
  ACTIVE="$(python manage.py status --format json 2>/dev/null | python -c 'import sys, json; print(json.load(sys.stdin).get("active_profile", ""))' || echo '')"
  if [[ "$ACTIVE" == "$PROFILE" ]]; then
    echo "Profile '$PROFILE' already active."
  else
    echo "Switching from active profile '${ACTIVE:-<none>}' to '$PROFILE' (in-place; pythia containers preserved)."
    python manage.py switch "$PROFILE" --apply
  fi
else
  echo "Bringing up profile '$PROFILE' from a clean state."
  python manage.py setup --backend compose --profile "$PROFILE"
  python manage.py render
  python manage.py up -d
fi

echo
echo "Profile up. Smoke-test endpoints:"
echo "  curl -s http://localhost:14000/v1/models | jq '.data[].id'"
