#!/usr/bin/env bash
# Bring up (or switch to) the gpt-oss-20b-completions infer_stack profile.
#
# Uses the standalone single-model profile (gpt-oss-20b on one GPU via the
# legacy completions protocol) that the gpt_oss_20b_core_grid preset expects.
# If a mixed profile (e.g. pythia-qwen25-gptoss-mixed-4x96) is already
# running and includes gpt-oss-20b, set VLLM_PROFILE to that name instead
# to avoid an unnecessary service restart.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFER_STACK_ROOT="$ROOT/submodules/infer_stack"
PROFILE="${VLLM_PROFILE:-gpt-oss-20b-completions-dp4}"

cd "$INFER_STACK_ROOT"

if python manage.py status 2>/dev/null | grep -q "active_profile"; then
  ACTIVE="$(python manage.py status --format json 2>/dev/null \
    | python -c 'import sys, json; print(json.load(sys.stdin).get("active_profile", ""))' \
    || echo '')"
  if [[ "$ACTIVE" == "$PROFILE" ]]; then
    echo "Profile '$PROFILE' already active."
  else
    echo "Switching from active profile '${ACTIVE:-<none>}' to '$PROFILE'."
    python manage.py switch "$PROFILE" --apply
  fi
else
  echo "Bringing up profile '$PROFILE' from a clean state."
  python manage.py setup --backend compose --profile "$PROFILE"
  python manage.py render
  python manage.py up -d
fi

echo
echo "Profile up. Verify endpoints:"
echo "  curl -s http://localhost:14000/v1/models | jq '.data[].id'"
