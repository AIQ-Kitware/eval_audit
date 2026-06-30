#!/usr/bin/env python3
"""Compare OLMo-7B deployment variants on a fixed prompt set.

This is the analysis half of the OLMo-7B deployment-matrix MWE (see README.md +
catalog.yaml). It has two subcommands:

  query   Hit ONE running endpoint (OpenAI-compatible /v1/completions or
          /v1/chat/completions) with every prompt and write a per-endpoint
          result JSON. run_matrix.sh calls this once per endpoint, between an
          `infer-stack acquire` and `release`, because the variants will not
          co-host (one GPU each).

  report  Read all the per-endpoint result JSONs and print a side-by-side
          comparison: which variants COLLAPSED to prompt-independent boilerplate
          (the bug) vs which stayed HEALTHY, and — if a reference is given —
          how closely each variant agrees with it per prompt.

Design notes
------------
* Pure standard library (urllib/json/difflib): runs in any python3, no torch,
  no openai client, no GPU. Only the model server it talks to needs a GPU.
* The discriminating signal is exactly the reported symptom: "repeats nonsense
  regardless of prompt". A healthy base model conditions on each distinct prompt
  and produces distinct, on-topic continuations; the fp16-collapsed model emits
  (near-)identical pretraining boilerplate for every prompt. So the core metric
  is *prompt independence* — how few distinct completions a variant produces
  across distinct prompts — backed up by known-boilerplate and degenerate-repeat
  detectors.
* `--selftest` exercises the analysis on synthetic healthy/collapsed data so the
  logic is verifiable without a live server.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Substrings seen in OLMo-1 7B's fp16-collapsed output (from the bug report and
# the known pretraining-boilerplate attractor). Case-insensitive substring hits.
BOILERPLATE_MARKERS = (
    "the first thing you need to do",
    "make sure that you have a",
    "before you start playing",
    "online casino",
    "rules of the game",
)


# --------------------------------------------------------------------------- #
# HTTP client (OpenAI-compatible)
# --------------------------------------------------------------------------- #
def _post_json(url: str, payload: dict[str, Any], api_key: str | None,
               timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def query_one(base_url: str, model: str, prompt: str, *, protocol: str,
              max_tokens: int, api_key: str | None, timeout: float) -> dict[str, Any]:
    """Send one prompt, return a normalized result dict (never raises)."""
    base = base_url.rstrip("/")
    t0 = time.time()
    try:
        if protocol == "chat":
            body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
            }
            resp = _post_json(f"{base}/chat/completions", body, api_key, timeout)
            choice = resp["choices"][0]
            text = choice["message"]["content"]
            tokens: list[str] = []
        else:
            body = {
                "model": model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": 0,
                "logprobs": 1,
                "echo": False,
            }
            resp = _post_json(f"{base}/completions", body, api_key, timeout)
            choice = resp["choices"][0]
            text = choice.get("text", "")
            lp = choice.get("logprobs") or {}
            tokens = list(lp.get("tokens") or [])
        first_token = tokens[0] if tokens else (text.strip().split(" ")[:1] or [""])[0]
        return {
            "completion": text,
            "finish_reason": choice.get("finish_reason"),
            "first_token": first_token,
            "n_tokens": len(tokens) if tokens else None,
            "latency_s": round(time.time() - t0, 3),
            "error": None,
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500] if exc.fp else str(exc)
        return _err(f"HTTP {exc.code}: {detail}", t0)
    except Exception as exc:  # noqa: BLE001 - a probe failure must not abort the grid
        return _err(f"{type(exc).__name__}: {exc}", t0)


def _err(msg: str, t0: float) -> dict[str, Any]:
    return {"completion": None, "finish_reason": None, "first_token": None,
            "n_tokens": None, "latency_s": round(time.time() - t0, 3), "error": msg}


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def normalize(text: str | None) -> str:
    """Lowercase + collapse whitespace, for prompt-independence comparison."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip().lower()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _is_degenerate_repeat(text: str | None) -> bool:
    """True if the completion is one short token repeated (e.g. 'The The The')."""
    norm = normalize(text)
    words = norm.split()
    if len(words) < 4:
        return False
    return len(set(words)) <= 2


def analyze_label(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-variant verdict from its completions across all prompts."""
    ok = [r for r in results if r.get("error") is None and r.get("completion") is not None]
    errored = [r for r in results if r.get("error") is not None]
    n_prompts = len(results)
    completions = [r["completion"] for r in ok]
    norms = [normalize(c) for c in completions]

    n_unique = len(set(n for n in norms if n))
    # max similarity between completions of DIFFERENT prompts (high => collapsed)
    cross_sims: list[float] = []
    for i in range(len(norms)):
        for j in range(i + 1, len(norms)):
            if norms[i] and norms[j]:
                cross_sims.append(_similarity(norms[i], norms[j]))
    max_cross = max(cross_sims) if cross_sims else 0.0
    mean_cross = sum(cross_sims) / len(cross_sims) if cross_sims else 0.0

    boiler = sum(any(m in n for m in BOILERPLATE_MARKERS) for n in norms)
    degen = sum(_is_degenerate_repeat(c) for c in completions)
    empty = sum(1 for n in norms if not n)

    verdict, reasons = _verdict(
        n_prompts=n_prompts, n_ok=len(ok), n_unique=n_unique,
        mean_cross=mean_cross, boiler=boiler, degen=degen, empty=empty,
    )
    return {
        "n_prompts": n_prompts,
        "n_ok": len(ok),
        "n_errored": len(errored),
        "n_unique_completions": n_unique,
        "max_cross_prompt_similarity": round(max_cross, 3),
        "mean_cross_prompt_similarity": round(mean_cross, 3),
        "n_boilerplate_hits": boiler,
        "n_degenerate_repeats": degen,
        "n_empty": empty,
        "verdict": verdict,
        "reasons": reasons,
        "errors": [r["error"] for r in errored],
    }


def _verdict(*, n_prompts: int, n_ok: int, n_unique: int, mean_cross: float,
             boiler: int, degen: int, empty: int) -> tuple[str, list[str]]:
    if n_ok == 0:
        return "NO_DATA", ["all prompts errored / empty"]
    reasons: list[str] = []
    collapsed = False
    if n_prompts > 1 and n_unique <= 1:
        collapsed = True
        reasons.append(f"only {n_unique} unique completion across {n_prompts} prompts")
    if mean_cross >= 0.8 and n_prompts > 1:
        collapsed = True
        reasons.append(f"mean cross-prompt similarity {mean_cross:.2f} >= 0.80")
    if boiler >= max(1, n_ok // 2):
        collapsed = True
        reasons.append(f"{boiler}/{n_ok} completions match known fp16 boilerplate")
    if degen >= max(1, n_ok // 2):
        collapsed = True
        reasons.append(f"{degen}/{n_ok} completions are degenerate token repeats")
    if collapsed:
        return "COLLAPSED", reasons
    # softer suspicion
    if 0.6 <= mean_cross < 0.8 or boiler or degen or empty:
        bits = []
        if 0.6 <= mean_cross < 0.8:
            bits.append(f"elevated cross-prompt similarity {mean_cross:.2f}")
        if boiler:
            bits.append(f"{boiler} boilerplate hit(s)")
        if degen:
            bits.append(f"{degen} degenerate repeat(s)")
        if empty:
            bits.append(f"{empty} empty completion(s)")
        return "SUSPECT", bits
    return "HEALTHY", [f"{n_unique} distinct completions, mean cross-sim {mean_cross:.2f}"]


def agreement_vs_reference(label_results: dict[str, list[dict[str, Any]]],
                           reference: str) -> dict[str, dict[str, Any]]:
    """Per-prompt completion similarity of each label vs the reference label."""
    ref = {r["id"]: r for r in label_results.get(reference, [])}
    out: dict[str, dict[str, Any]] = {}
    for label, results in label_results.items():
        if label == reference:
            continue
        sims: list[float] = []
        per_prompt: dict[str, float] = {}
        for r in results:
            rr = ref.get(r["id"])
            if not rr:
                continue
            a, b = normalize(r.get("completion")), normalize(rr.get("completion"))
            if a or b:
                s = _similarity(a, b)
                sims.append(s)
                per_prompt[r["id"]] = round(s, 3)
        out[label] = {
            "mean_similarity_vs_ref": round(sum(sims) / len(sims), 3) if sims else None,
            "per_prompt": per_prompt,
        }
    return out


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #
def cmd_query(args: argparse.Namespace) -> int:
    prompts = [json.loads(line) for line in Path(args.prompts).read_text().splitlines() if line.strip()]
    label = args.label or args.model
    print(f"[query] {label}  ({args.protocol})  -> {args.base_url}  model={args.model}",
          file=sys.stderr)
    results = []
    for p in prompts:
        r = query_one(args.base_url, args.model, p["prompt"], protocol=args.protocol,
                      max_tokens=args.max_tokens, api_key=args.api_key, timeout=args.timeout)
        r["id"] = p["id"]
        r["prompt"] = p["prompt"]
        results.append(r)
        snippet = (r["completion"] or r["error"] or "")[:70].replace("\n", "\\n")
        print(f"    {p['id']:<24} {snippet}", file=sys.stderr)
    out = {
        "label": label, "model": args.model, "base_url": args.base_url,
        "protocol": args.protocol, "max_tokens": args.max_tokens,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": results,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"[query] wrote {args.out}", file=sys.stderr)
    return 0


def _load_results(paths: list[str]) -> dict[str, list[dict[str, Any]]]:
    files: list[Path] = []
    for pth in paths:
        p = Path(pth)
        files.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    label_results: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        doc = json.loads(f.read_text())
        label_results[doc["label"]] = doc["results"]
    return label_results


def cmd_report(args: argparse.Namespace) -> int:
    label_results = _load_results(args.inputs)
    if not label_results:
        print("no result JSONs found", file=sys.stderr)
        return 1
    return _render_report(label_results, reference=args.reference,
                          out_json=args.out, snippet_len=args.snippet_len)


def _render_report(label_results: dict[str, list[dict[str, Any]]], *,
                   reference: str | None, out_json: str | None,
                   snippet_len: int) -> int:
    analyses = {label: analyze_label(rs) for label, rs in label_results.items()}
    agreement = (agreement_vs_reference(label_results, reference)
                 if reference and reference in label_results else {})

    labels = list(label_results)
    print("\n=== OLMo-7B deployment matrix — per-variant verdict ===\n")
    header = f"{'variant':<22} {'verdict':<10} {'uniq/n':>7} {'meanXsim':>9} {'boiler':>7} {'degen':>6}"
    if agreement:
        header += f" {'~ref':>6}"
    print(header)
    print("-" * len(header))
    order = {"COLLAPSED": 0, "NO_DATA": 1, "SUSPECT": 2, "HEALTHY": 3}
    for label in sorted(labels, key=lambda x: (order.get(analyses[x]["verdict"], 9), x)):
        a = analyses[label]
        row = (f"{label:<22} {a['verdict']:<10} "
               f"{str(a['n_unique_completions']) + '/' + str(a['n_prompts']):>7} "
               f"{a['mean_cross_prompt_similarity']:>9.3f} "
               f"{a['n_boilerplate_hits']:>7} {a['n_degenerate_repeats']:>6}")
        if agreement:
            m = agreement.get(label, {}).get("mean_similarity_vs_ref")
            row += f" {('—' if m is None else f'{m:.2f}'):>6}"
        print(row)
        for reason in a["reasons"]:
            print(f"    - {reason}")
        for e in a["errors"][:2]:
            print(f"    ! {e[:100]}")
    if agreement:
        print(f"\n(~ref = mean per-prompt completion similarity vs reference '{reference}')")

    # Per-prompt completion snippets, so a human can SEE the collapse.
    print("\n=== per-prompt completion snippets ===")
    all_ids: list[str] = []
    for rs in label_results.values():
        for r in rs:
            if r["id"] not in all_ids:
                all_ids.append(r["id"])
    for pid in all_ids:
        print(f"\n# {pid}")
        for label in labels:
            r = next((x for x in label_results[label] if x["id"] == pid), None)
            if not r:
                continue
            text = r.get("completion")
            shown = (text or f"<error: {r.get('error')}>")[:snippet_len].replace("\n", "\\n")
            print(f"    {label:<22} {shown}")

    if out_json:
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(json.dumps(
            {"analyses": analyses, "agreement_vs_reference": agreement,
             "reference": reference}, indent=2))
        print(f"\n[report] wrote {out_json}", file=sys.stderr)
    # Exit nonzero if the *intended-healthy* set has a collapse, so CI/driver can
    # flag a regression. Endpoints whose NAME marks them as expected-bad
    # (auto/fp16/chat) are excluded from that gate.
    expected_bad = ("auto", "fp16", "chat")
    regressions = [l for l, a in analyses.items()
                   if a["verdict"] in ("COLLAPSED", "NO_DATA")
                   and not any(tag in l for tag in expected_bad)]
    if regressions:
        print(f"\nFAIL: unexpected collapse/no-data in: {', '.join(regressions)}",
              file=sys.stderr)
        return 2
    return 0


def cmd_selftest(_args: argparse.Namespace) -> int:
    """Validate the analysis on synthetic data (no server needed)."""
    healthy = [
        {"id": "a", "completion": " At Sebastian's funeral.", "error": None},
        {"id": "b", "completion": " The terrible system of the law.", "error": None},
        {"id": "c", "completion": " Paris.", "error": None},
        {"id": "d", "completion": " 4", "error": None},
    ]
    boiler = ("The first thing you need to do is to make sure that you have a "
              "good understanding of the rules of the game before you start playing.")
    collapsed = [{"id": x, "completion": boiler, "error": None} for x in "abcd"]
    degen = [{"id": x, "completion": "The The The The The", "error": None} for x in "abcd"]

    ha, ca, da = analyze_label(healthy), analyze_label(collapsed), analyze_label(degen)
    print("healthy  ->", ha["verdict"], ha["reasons"])
    print("collapsed->", ca["verdict"], ca["reasons"])
    print("degen    ->", da["verdict"], da["reasons"])
    ok = (ha["verdict"] == "HEALTHY"
          and ca["verdict"] == "COLLAPSED"
          and da["verdict"] == "COLLAPSED")

    # agreement: a near-copy of the healthy set should score ~1.0 vs reference.
    near = [dict(r, completion=(r["completion"] or "") + ".") for r in healthy]
    lr = {"ref": healthy, "cand": near, "bad": collapsed}
    agr = agreement_vs_reference(lr, "ref")
    print("agree cand vs ref ->", agr["cand"]["mean_similarity_vs_ref"])
    print("agree bad  vs ref ->", agr["bad"]["mean_similarity_vs_ref"])
    ok = ok and agr["cand"]["mean_similarity_vs_ref"] >= 0.8
    ok = ok and agr["bad"]["mean_similarity_vs_ref"] < 0.5
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="query one running endpoint")
    q.add_argument("--base-url", required=True, help="e.g. http://localhost:14042/v1")
    q.add_argument("--model", required=True, help="served model name == endpoint name")
    q.add_argument("--prompts", default=str(Path(__file__).with_name("prompts.jsonl")))
    q.add_argument("--protocol", choices=["completions", "chat"], default="completions")
    q.add_argument("--max-tokens", type=int, default=60)
    q.add_argument("--api-key", default=None)
    q.add_argument("--timeout", type=float, default=120.0)
    q.add_argument("--label", default=None, help="display label (default: model)")
    q.add_argument("--out", required=True, help="result JSON path")
    q.set_defaults(func=cmd_query)

    r = sub.add_parser("report", help="aggregate result JSONs into a comparison")
    r.add_argument("inputs", nargs="+", help="result JSON files or a directory of them")
    r.add_argument("--reference", default=None,
                   help="label to treat as ground truth (e.g. hf-bf16 or olmo7b-dbg-bf16)")
    r.add_argument("--snippet-len", type=int, default=90)
    r.add_argument("--out", default=None, help="write the analysis JSON here too")
    r.set_defaults(func=cmd_report)

    s = sub.add_parser("selftest", help="run analysis self-test (no server)")
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
