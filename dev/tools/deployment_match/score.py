"""Score candidate cells against the official completions and rank them.

The objective is **agreement with the official outputs** (Together stores no
usable logprobs, so completion *text* is the ground-truth signal). Per instance
we compute, candidate vs official:

* ``exact_match``  — stripped strings equal
* ``quasi_match``  — SQuAD-normalized equal (lower, strip punct + articles)
* ``similarity``   — normalized SequenceMatcher ratio
* ``first_token``  — normalized first token equal (the ` Diana` vs `The`
  discriminator that localized the OLMo EOS bug)

Aggregated per cell into a composite score, plus a model-agnostic
prompt-independence *collapse* diagnostic (few unique completions / high
cross-prompt similarity across distinct prompts = the "ignores the prompt"
failure). When ``eval_audit`` is importable, each instance also carries the
``request_state_diff`` (``_walker_diff``) for parity with the audit reports.

Stdlib-only core; ``eval_audit`` import is optional enrichment.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

_ARTICLES = {"a", "an", "the"}
_PUNCT = re.compile(r"[^\w\s]")

# Composite weights. Quasi-match and first-token dominate (they track the audit's
# quasi_exact_match metric and the token-level discriminator); similarity breaks ties.
W_QUASI, W_FIRST, W_SIM = 0.45, 0.35, 0.20


def _strip(t: str | None) -> str:
    return (t or "").strip()


def _squad_norm(t: str | None) -> str:
    s = _PUNCT.sub(" ", (t or "").lower())
    return " ".join(w for w in s.split() if w not in _ARTICLES)


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _first_word(normalized: str) -> str:
    parts = normalized.split()
    return parts[0] if parts else ""


try:  # optional: the audit's request_state_diff for parity
    from eval_audit.helm.diff_primitives import _walker_diff as _audit_walker_diff  # type: ignore
except Exception:  # noqa: BLE001
    _audit_walker_diff = None


def score_instance(candidate: dict[str, Any], official: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("error") or candidate.get("completion") is None:
        return {"error": candidate.get("error") or "no completion", "ok": False}
    cand_txt, off_txt = candidate["completion"], official.get("official_completion", "")
    cq, oq = _squad_norm(cand_txt), _squad_norm(off_txt)
    # First WORD, not the API's first token: Together stores the whole answer as
    # one "token", so word-level is source-agnostic (and still the ` Diana` vs
    # `The` discriminator that localized the EOS bug).
    cand_first, off_first = _first_word(cq), _first_word(oq)
    row = {
        "ok": True,
        "exact_match": _strip(cand_txt) == _strip(off_txt),
        "quasi_match": cq == oq,
        "similarity": round(_similarity(cq, oq), 3),
        "first_token_match": bool(off_first) and cand_first == off_first,
        "candidate": cand_txt,
        "official": off_txt,
    }
    if _audit_walker_diff is not None:
        try:
            row["request_state_diff"] = _audit_walker_diff(
                {"text": off_txt}, {"text": cand_txt})
        except Exception:  # noqa: BLE001
            pass
    return row


def _collapse(candidate_completions: list[str]) -> dict[str, Any]:
    """Prompt-independence diagnostic (model-agnostic)."""
    norms = [_squad_norm(c) for c in candidate_completions if c]
    n = len(norms)
    uniq = len(set(norms))
    sims = [_similarity(norms[i], norms[j]) for i in range(n) for j in range(i + 1, n)]
    mean_cross = sum(sims) / len(sims) if sims else 0.0
    collapsed = n > 1 and (uniq <= 1 or mean_cross >= 0.8)
    return {"n": n, "unique": uniq, "mean_cross_prompt_similarity": round(mean_cross, 3),
            "collapsed": collapsed}


def score_cell(cell_doc: dict[str, Any], oracle_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    cand_completions = []
    for r in cell_doc.get("results", []):
        off = oracle_by_id.get(r.get("instance_id"))
        if off is None:
            continue
        row = score_instance(r, off)
        row["instance_id"] = r.get("instance_id")
        rows.append(row)
        if row.get("ok"):
            cand_completions.append(r.get("completion") or "")
    ok = [r for r in rows if r.get("ok")]
    n_ok = len(ok)

    def rate(key: str) -> float:
        return round(sum(1 for r in ok if r.get(key)) / n_ok, 3) if n_ok else 0.0

    exact = rate("exact_match")
    quasi = rate("quasi_match")
    first = rate("first_token_match")
    sim = round(sum(r["similarity"] for r in ok) / n_ok, 3) if n_ok else 0.0
    composite = round(W_QUASI * quasi + W_FIRST * first + W_SIM * sim, 4) if n_ok else 0.0
    collapse = _collapse(cand_completions)

    return {
        "cell_id": cell_doc.get("cell_id"),
        "endpoint": cell_doc.get("endpoint"),
        "request": cell_doc.get("request"),
        "n_scored": len(rows),
        "n_ok": n_ok,
        "n_errored": len(rows) - n_ok,
        "exact_match_rate": exact,
        "quasi_match_rate": quasi,
        "first_token_match_rate": first,
        "mean_similarity": sim,
        "composite": composite,
        "collapse": collapse,
        "verdict": ("NO_DATA" if n_ok == 0
                    else "COLLAPSED" if collapse["collapsed"]
                    else "MATCH" if quasi >= 0.8
                    else "PARTIAL"),
        "rows": rows,
        "errors": [r.get("error") for r in rows if not r.get("ok")][:3],
    }


def rank(cell_docs: list[dict[str, Any]], oracle_sample: list[dict[str, Any]]) -> list[dict[str, Any]]:
    oracle_by_id = {s["instance_id"]: s for s in oracle_sample}
    scored = [score_cell(c, oracle_by_id) for c in cell_docs]
    scored.sort(key=lambda s: (s["composite"], s["first_token_match_rate"],
                               s["mean_similarity"]), reverse=True)
    return scored


# --------------------------------------------------------------------------- #
def selftest() -> int:
    oracle = [
        {"instance_id": "a", "official_completion": " Diana", "official_tokens": [{"text": " Diana"}]},
        {"instance_id": "b", "official_completion": " The terrible system", "official_tokens": [{"text": " The"}]},
        {"instance_id": "c", "official_completion": " Paris", "official_tokens": [{"text": " Paris"}]},
    ]

    def cell(cid, fn):
        return {"cell_id": cid, "endpoint": cid, "request": {},
                "results": [{"instance_id": o["instance_id"], "completion": fn(o),
                             "first_token": fn(o).split(" ")[1] if len(fn(o).split(" ")) > 1 else fn(o),
                             "error": None} for o in oracle]}

    perfect = cell("perfect", lambda o: o["official_completion"])
    boiler = "The first thing you need to do is to make sure you have a good setup"
    collapsed = cell("collapsed", lambda o: " " + boiler)
    partial = cell("partial", lambda o: o["official_completion"] if o["instance_id"] == "a" else " " + boiler)

    ranked = rank([collapsed, partial, perfect], oracle)
    order = [r["cell_id"] for r in ranked]
    verdicts = {r["cell_id"]: r["verdict"] for r in ranked}
    print("ranking:", order)
    for r in ranked:
        print(f"  {r['cell_id']:<10} {r['verdict']:<9} composite={r['composite']} "
              f"quasi={r['quasi_match_rate']} first={r['first_token_match_rate']} "
              f"collapsed={r['collapse']['collapsed']}")
    ok = (order[0] == "perfect"
          and verdicts["perfect"] == "MATCH"
          and verdicts["collapsed"] == "COLLAPSED"
          and ranked[0]["composite"] > ranked[-1]["composite"])
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
