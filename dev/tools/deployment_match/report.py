"""Render the deployment-match comparison and emit the winning recipe.

Three outputs:

* a ranked table (composite score vs official, plus the sub-metrics + collapse
  verdict per cell);
* a per-instance snippet matrix (official vs the top cells) so a human can *see*
  where a losing cell diverges;
* ``best_deployment`` — the winning serve + request knobs, annotated with which
  are **HELM-path-native** (serve-time: reached by a normal HELM run) vs
  **probe-only** (request-time ``add_special_tokens``: only sent by this tool /
  the gateway, so landing it in production needs a client change or the
  serve-time tokenizer override — the route production actually took).
"""

from __future__ import annotations

from typing import Any

# Request-time knobs a normal HELM run does NOT send (openai_client omits them),
# mapped to their default value. A winner only "needs landing" when its value is
# NON-default (e.g. add_special_tokens=False); the default (True) is a no-op.
PROBE_ONLY_DEFAULTS = {"add_special_tokens": True}


def render_ranking(scored: list[dict[str, Any]]) -> str:
    lines = ["", "=== deployment-match ranking (vs official) ===", ""]
    header = (f"{'#':>2} {'cell':<34} {'verdict':<9} {'score':>6} "
              f"{'quasi':>6} {'first':>6} {'sim':>6} {'collapse':>8}")
    lines += [header, "-" * len(header)]
    for i, s in enumerate(scored, 1):
        lines.append(
            f"{i:>2} {s['cell_id']:<34} {s['verdict']:<9} {s['composite']:>6.3f} "
            f"{s['quasi_match_rate']:>6.2f} {s['first_token_match_rate']:>6.2f} "
            f"{s['mean_similarity']:>6.2f} "
            f"{('yes' if s['collapse']['collapsed'] else 'no'):>8}")
        for e in s.get("errors", [])[:1]:
            lines.append(f"     ! {str(e)[:90]}")
    return "\n".join(lines)


def render_snippets(scored: list[dict[str, Any]], oracle_sample: list[dict[str, Any]],
                    *, top_k: int = 4, snippet_len: int = 70) -> str:
    top = scored[:top_k]
    lines = ["", f"=== per-instance completions (official vs top {len(top)} cells) ==="]
    rows_by_cell = {s["cell_id"]: {r.get("instance_id"): r for r in s.get("rows", [])}
                    for s in top}
    for s in oracle_sample:
        iid = s["instance_id"]
        off = (s.get("official_completion") or "")[:snippet_len].replace("\n", "\\n")
        lines.append(f"\n# {iid}")
        lines.append(f"    {'OFFICIAL':<34} {off}")
        for cell in top:
            r = rows_by_cell[cell["cell_id"]].get(iid) or {}
            txt = r.get("candidate")
            shown = ((txt if txt is not None else f"<{r.get('error', 'n/a')}>")
                     [:snippet_len].replace("\n", "\\n"))
            mark = "=" if r.get("quasi_match") else ("~" if r.get("first_token_match") else " ")
            lines.append(f"  {mark} {cell['cell_id']:<34} {shown}")
    return "\n".join(lines)


def best_deployment(scored: list[dict[str, Any]], cells_by_id: dict[str, dict[str, Any]],
                    resolution: Any) -> dict[str, Any]:
    if not scored:
        return {"error": "no scored cells"}
    winner = scored[0]
    cell = cells_by_id.get(winner["cell_id"], {})
    serve = cell.get("serve", {})
    request = cell.get("request", winner.get("request", {}))

    # Only non-default request-time values "need landing"; a default value is
    # what a normal HELM run already sends.
    probe_only = sorted(k for k in request if k in PROBE_ONLY_DEFAULTS
                        and request[k] != PROBE_ONLY_DEFAULTS[k])
    native_request = sorted(k for k in request if k not in probe_only)

    notes: list[str] = []
    if probe_only:
        notes.append(
            "winning request-time knob(s) " + ", ".join(f"{k}={request[k]}" for k in probe_only)
            + " are probe-only and NON-default: a normal HELM run won't send them. "
            "Land them via a VLLMClient change or an equivalent serve-time fix "
            "(e.g. a --tokenizer override, as OLMo did in 74ba33d).")
    if serve.get("tokenizer"):
        notes.append(f"winner uses serve-time tokenizer override --tokenizer {serve['tokenizer']} "
                     "(HELM-path-native and gateway-proof).")

    return {
        "winner_cell": winner["cell_id"],
        "composite": winner["composite"],
        "verdict": winner["verdict"],
        "metrics": {k: winner[k] for k in
                    ("quasi_match_rate", "first_token_match_rate",
                     "exact_match_rate", "mean_similarity", "n_ok")},
        "serve_time_knobs": {  # HELM-path-native
            "hf_source": resolution.hf_source,
            "dtype": serve.get("dtype"),
            "tokenizer": serve.get("tokenizer"),
            "max_model_len": serve.get("max_model_len"),
            "trust_remote_code": serve.get("trust_remote_code"),
            "attention_backend": serve.get("attention_backend"),
            # Carried so the confirm catalog re-serves fp32 with the same GPU count
            # (TP>1 is what let fp32 MoE serve at all — a TP=1 confirm would OOM).
            "tensor_parallel_size": serve.get("tensor_parallel_size"),
            # Serving runtime numbers so the confirm catalog reproduces the
            # winner faithfully (max_num_seqs is the batch-invariance knob).
            "max_num_seqs": serve.get("max_num_seqs"),
            "gpu_memory_utilization": serve.get("gpu_memory_utilization"),
            "max_num_batched_tokens": serve.get("max_num_batched_tokens"),
            "extra_args": serve.get("extra_args"),
        },
        "request_time_knobs": {
            "native": {k: request[k] for k in native_request},
            "probe_only": {k: request[k] for k in probe_only},
        },
        "notes": notes,
    }
