#!/usr/bin/env bash
# Preflight: the natural_qa runs in the allenai-olmo-7b FULL manifest
#   natural_qa:mode=closedbook,model=allenai/olmo-7b
#   natural_qa:mode=openbook_longans,model=allenai/olmo-7b
# pull the NaturalQuestions dev shards from a public Google Cloud Storage
# bucket. That bucket revoked ANONYMOUS access (HTTP 403) — it now serves only
# authenticated callers — but HELM downloads the files with an unauthenticated
# urllib request, so the run cannot fetch them itself.
#
# Fix (script-only; no submodule edits): use authenticated gcloud to PRE-STAGE
# the five shards into EVAL_AUDIT_NQ_STAGE_DIR (exported by _lib.sh). The
# `helm-run` PATH shim (reproduce/olmo_models/bin/helm-run, put first on PATH by
# _lib.sh) symlinks those shards into each run's
# benchmark_output/scenarios/natural_qa/data before delegating to the real
# helm-run, and HELM's ensure_file_downloaded skips the network whenever the
# target file already exists — so the run reads them locally.
#
# This script (a) verifies gcloud auth works, then (b) stages the shards. It is
# idempotent — already-present shards are left alone. Only the olmo-7b full run
# needs it; the other five presets have no natural_qa entries. Set
# OLMO_SKIP_GCS_CHECK=1 to skip entirely (e.g. when you've chosen to filter
# natural_qa out as a dataset-access failure rather than stage the data).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
cd "$ROOT"

if [[ "${OLMO_SKIP_GCS_CHECK:-0}" == "1" ]]; then
  echo "OLMO_SKIP_GCS_CHECK=1: skipping gcloud auth check + natural_qa pre-stage."
  echo "  natural_qa:* will be left to fail/filter as a dataset-access blocker."
  exit 0
fi

# Already staged? ensure_file_downloaded keys purely on file existence, so
# mirror that: if every shard is present and non-empty, there's nothing to do.
all_present=1
for f in "${NQ_FILES[@]}"; do
  [[ -s "$NQ_CACHE_DATA_DIR/$f" ]] || { all_present=0; break; }
done
if [[ "$all_present" == "1" ]]; then
  echo "OK: all ${#NQ_FILES[@]} natural_questions shards already staged at:"
  echo "    $NQ_CACHE_DATA_DIR"
  exit 0
fi

# Verify gcloud authentication resolves a usable access token.
TOKEN="$(olmo_resolve_gcloud_token)"
if [[ -z "$TOKEN" ]]; then
  echo "FAIL: no working gcloud authentication found." >&2
  echo "  natural_qa needs the NaturalQuestions dev shards from gs://$NQ_GCS_BUCKET," >&2
  echo "  whose bucket no longer allows anonymous reads. Authenticate, then re-run:" >&2
  echo "    # install the SDK if needed: https://cloud.google.com/sdk/docs/install" >&2
  echo "    gcloud auth login                       # interactive user creds" >&2
  echo "    #   or, for headless hosts:" >&2
  echo "    gcloud auth application-default login" >&2
  echo "    #   or export a token directly:" >&2
  echo "    export GOOGLE_OAUTH_ACCESS_TOKEN=\$(gcloud auth print-access-token)" >&2
  echo >&2
  echo "  Or set OLMO_SKIP_GCS_CHECK=1 to skip and let natural_qa be filtered out" >&2
  echo "  as a dataset-access failure (a recipe/environment blocker, not a" >&2
  echo "  reproducibility failure)." >&2
  exit 1
fi

who="$(gcloud config get-value account 2>/dev/null || true)"
echo "OK: gcloud auth resolves an access token${who:+ (account: $who)}."

# Stage each missing shard with an authenticated GCS read. Prefer gsutil when
# present (parallel + resumable for the large shards); otherwise fall back to an
# authenticated curl against the XML API using the resolved bearer token.
mkdir -p "$NQ_CACHE_DATA_DIR"
base_url="https://storage.googleapis.com/$NQ_GCS_BUCKET/$NQ_GCS_PREFIX"
have_gsutil=0
command -v gsutil >/dev/null 2>&1 && have_gsutil=1

staged=0
for f in "${NQ_FILES[@]}"; do
  dst="$NQ_CACHE_DATA_DIR/$f"
  if [[ -s "$dst" ]]; then
    echo "  - $f already present; skipping."
    continue
  fi
  echo "  - fetching $f ..."
  tmp="$dst.tmp"
  rm -f "$tmp"
  if [[ "$have_gsutil" == "1" ]]; then
    if ! gsutil -q cp "gs://$NQ_GCS_BUCKET/$NQ_GCS_PREFIX/$f" "$tmp"; then
      rm -f "$tmp"
      echo "FAIL: 'gsutil cp' of $f failed." >&2
      echo "  Your identity may lack read access to gs://$NQ_GCS_BUCKET (the bucket" >&2
      echo "  dropped allAuthenticatedUsers too). Use a credentialed account/project" >&2
      echo "  with access, or set OLMO_SKIP_GCS_CHECK=1 to filter natural_qa out." >&2
      exit 1
    fi
  else
    code="$(curl -fsSL --retry 3 -w '%{http_code}' -o "$tmp" \
      -H "Authorization: Bearer $TOKEN" "$base_url/$f" || true)"
    if [[ "$code" != "200" || ! -s "$tmp" ]]; then
      rm -f "$tmp"
      echo "FAIL: authenticated GET of $f returned HTTP ${code:-error}." >&2
      if [[ "$code" == "403" ]]; then
        echo "  Your identity authenticated but lacks read access to gs://$NQ_GCS_BUCKET" >&2
        echo "  (the bucket dropped allAuthenticatedUsers too). Use a credentialed" >&2
        echo "  account/project with access, or set OLMO_SKIP_GCS_CHECK=1 to filter" >&2
        echo "  natural_qa out as a dataset-access failure." >&2
      fi
      exit 1
    fi
  fi
  # Integrity: the shards are gzip; a 200 that isn't gzip means a bad object
  # (e.g. an HTML/XML error body written with a 200). Catch it before the run.
  if ! gzip -t "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    echo "FAIL: $f downloaded but is not a valid gzip stream." >&2
    exit 1
  fi
  mv "$tmp" "$dst"
  staged=$((staged + 1))
done

echo "OK: staged $staged new shard(s); natural_questions cache ready at:"
echo "    $NQ_CACHE_DATA_DIR"
echo "Note: the helm-run shim ($ROOT/reproduce/olmo_models/bin/helm-run, first on"
echo "      PATH via _lib.sh) symlinks these into each run's"
echo "      benchmark_output/scenarios/natural_qa/data, so HELM finds the staged"
echo "      shards instead of attempting an anonymous fetch."
