#!/usr/bin/env bash
# Preflight: one candidate run pulls a GATED HuggingFace dataset — `gpqa`
# (Idavidrein/gpqa), which appears in the full manifest. HELM's dataset loader
# needs an HF token whose account has accepted that dataset's terms. This script
# verifies a usable token is present and fails fast with guidance otherwise.
# (bbq/ifeval/mmlu_pro are public and need no token — only gpqa gates.)
#
# _lib.sh already exported HF_TOKEN / HUGGING_FACE_HUB_TOKEN if one was found in
# the env or the cached `huggingface-cli login`; here we just check + report.
#
# How the token reaches the container: NOT via the docker node's `-e HF_TOKEN`
# (that bare form can't survive kwdagger's fresh tmux pane). Instead
# eval-audit-run's scheduler writes the resolved token into the mounted HF cache
# dir as `<hf_cache_dir>/token`, which the container reads at `$HF_HOME/token`.
# So all this preflight confirms is that a usable token is present in THIS env.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "FAIL: no HuggingFace token found." >&2
  echo "  The gpqa run cannot download Idavidrein/gpqa (gated) without one." >&2
  echo "  Provide a token via one of:" >&2
  echo "    huggingface-cli login            # caches a token under ~/.cache/huggingface" >&2
  echo "    export HF_TOKEN=hf_xxx            # or set it directly in the env" >&2
  echo >&2
  echo "  The token's account must ALSO have accepted the gated dataset terms:" >&2
  echo "    https://huggingface.co/datasets/Idavidrein/gpqa" >&2
  echo >&2
  echo "  (bbq / ifeval / mmlu_pro are public — to run only those, drop gpqa from" >&2
  echo "   the 'openai-gpt-oss-20b' preset's full_manifest and skip this gate.)" >&2
  exit 1
fi

# Confirm the token actually authenticates (identity check, not access check).
if command -v huggingface-cli >/dev/null 2>&1; then
  if who="$(hf auth whoami 2>/dev/null)"; then
    echo "OK: authenticated to HuggingFace as: $who"
  else
    echo "FAIL: a token is set but 'huggingface-cli whoami' rejected it (expired/invalid)." >&2
    exit 1
  fi
else
  echo "OK: HF_TOKEN is set (huggingface-cli not on PATH; cannot verify identity here)."
fi

echo "Note: identity is confirmed, but gated-dataset *access* (accepted terms) is"
echo "      only proven when the gpqa run actually downloads the dataset."
echo "Note: at run time the scheduler writes this token into the mounted HF cache"
echo "      (<hf_cache_dir>/token); the container reads it at \$HF_HOME/token."
