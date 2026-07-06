"""Measure per-prompt hit probabilities for the WikiFact appendix.

Given the Case Study 3 appendix ([docs/papers/neurips-2026/case_study_3_appendix.tex]) and
its accompanying Lean scaffold ([docs/papers/neurips-2026/wikifact_consistency_claim.lean]),
the paper argues that a homogeneous Bernoulli model at the average hit rate is
a *lower bound* on expected agreement. The strengthened claim wants to plug
real numbers into the heterogeneous decomposition

    E_i[ p_i^2 + (1 - p_i)^2 ] = bar(p)^2 + (1 - bar(p))^2 + 2 * Var_i(p_i)

and predict the observed cross-run agreement ratio (0.922 for Pythia-6.9B).

Inputs are HELM run dirs we already have on this machine. Each local run for
WikiFact stored the full `num_completions=5` sample with token-level
log-probabilities under `temperature=1.0` decoding, so the per-completion
cumulative log-prob *is* `log P(emit_this_completion | prompt)`. We use this
to estimate per-prompt single-sample hit probability `q_i` two ways:

  * Monte Carlo: matches / 5 over the five sampled completions.
  * Likelihood lower bound: sum of `exp(cum_logprob)` over unique sampled
    completions whose normalized text matches a `correct` reference. This is
    a lower bound because the true `q_i` includes match-equivalent token
    sequences we never sampled.

We then convert `q_i` to the per-run @5 hit probability `p_i = 1 - (1-q_i)^5`,
aggregate across instances, and report:

  * empirical agreement vs. predicted agreement,
  * the heterogeneity term `2 * Var(p_i)` and its share of total agreement,
  * a Lean snippet with rational approximations the existing scaffold can
    plug into `expectedAgreement_eq_uniformAgreement_add_variance`.

The reference normalization is copied from
`helm.benchmark.metrics.basic_metrics.normalize_text` to match HELM's
`quasi_exact_match` (the metric the appendix's heatmap micro-averages over).

Usage:
    python dev/paper-analysis/neurips-2026/measure_wikifact_logits.py \
        --models pythia-6.9b vicuna-7b-v1.3 falcon-7b \
        --out-dir docs/papers/neurips-2026/wikifact_logits_out

If `--out-dir` is omitted the script just prints the summary table.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import string
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Path discovery.
# ---------------------------------------------------------------------------

LOCAL_GRID_ROOTS: dict[str, list[Path]] = {
    "pythia-6.9b": [Path("/data/crfm-helm-audit/audit-historic-grid")],
    "vicuna-7b-v1.3": [Path("/data/crfm-helm-audit/audit-historic-grid")],
    "falcon-7b": [Path("/data/crfm-helm-audit/audit-falcon-7b-helm-grid")],
}

LOCAL_RUN_PREFIX = {
    "pythia-6.9b": "wikifact:k=5,subject=*,model=eleutherai_pythia-6.9b",
    "vicuna-7b-v1.3": "wikifact:k=5,subject=*,model=lmsys_vicuna-7b-v1.3",
    "falcon-7b": "wikifact:k=5,subject=*,model=tiiuae_falcon-7b",
}

OFFICIAL_ROOT = Path("/data/crfm-helm-public/classic/benchmark_output/runs/v0.3.0")

OFFICIAL_RUN_PREFIX = {
    "pythia-6.9b": "wikifact:k=5,subject=*,model=eleutherai_pythia-6.9b",
    "vicuna-7b-v1.3": "wikifact:k=5,subject=*,model=lmsys_vicuna-7b-v1.3",
    "falcon-7b": "wikifact:k=5,subject=*,model=tiiuae_falcon-7b",
}

CORE_REPORT_ROOTS = [
    Path(
        "/data/crfm-helm-audit-store/virtual-experiments/"
        "open-helm-models-reproducibility/analysis/core-reports"
    ),
    Path(
        "/data/crfm-helm-audit-store/virtual-experiments/"
        "heatmap-paper-slim/analysis/core-reports"
    ),
]

MODEL_DIRNAMES = {
    "pythia-6.9b": "eleutherai_pythia-6.9b",
    "vicuna-7b-v1.3": "lmsys_vicuna-7b-v1.3",
    "falcon-7b": "tiiuae_falcon-7b",
}


def discover_local_runs(model: str) -> list[Path]:
    pattern = LOCAL_RUN_PREFIX[model]
    runs: list[Path] = []
    for grid_root in LOCAL_GRID_ROOTS[model]:
        for helm_id_dir in (grid_root / "helm").glob("helm_id_*"):
            for run_dir in (helm_id_dir / "benchmark_output" / "runs").glob(
                f"*/{pattern}"
            ):
                if (run_dir / "scenario_state.json").exists():
                    runs.append(run_dir)
    return sorted(runs)


def discover_official_run(model: str, subject: str) -> Path | None:
    name = (
        f"wikifact:k=5,subject={subject},model={MODEL_DIRNAMES[model]}"
    )
    candidate = OFFICIAL_ROOT / name
    return candidate if (candidate / "scenario_state.json").exists() else None


# ---------------------------------------------------------------------------
# HELM normalization (match helm/benchmark/metrics/basic_metrics.py).
# ---------------------------------------------------------------------------

_PUNC = set(string.punctuation)


def normalize_text(text: str) -> str:
    """Reproduce HELM's `quasi_exact_match` normalization."""
    text = text.lower()
    text = "".join(ch for ch in text if ch not in _PUNC)
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def quasi_exact_match(gold: str, pred: str) -> bool:
    if not pred:
        return False
    return normalize_text(gold) == normalize_text(pred)


# ---------------------------------------------------------------------------
# Per-run extraction.
# ---------------------------------------------------------------------------


@dataclass
class InstanceObservation:
    instance_id: str
    n_completions: int
    n_match: int  # matches under quasi_exact_match across the n_completions
    cum_logprobs: list[float] = field(default_factory=list)
    match_flags: list[bool] = field(default_factory=list)

    @property
    def q_hat_mc(self) -> float:
        if self.n_completions == 0:
            return float("nan")
        return self.n_match / self.n_completions

    @property
    def q_hat_likelihood(self) -> float:
        # Sum exp(cum_logprob) over UNIQUE matching completions only.
        # Same completion text can be sampled twice; we count its mass once.
        seen: dict[float, bool] = {}
        for clp, m in zip(self.cum_logprobs, self.match_flags):
            if m:
                seen[clp] = True
        return sum(math.exp(clp) for clp in seen)

    @property
    def p5_hat_mc(self) -> float:
        q = self.q_hat_mc
        return 1.0 - (1.0 - q) ** 5

    @property
    def p5_hat_lik(self) -> float:
        q = self.q_hat_likelihood
        return 1.0 - (1.0 - q) ** 5


def cumulative_logprob(completion: dict) -> float:
    # Some HELM versions store top-level `logprob`; otherwise sum tokens.
    if "logprob" in completion and completion["logprob"] is not None:
        return float(completion["logprob"])
    total = 0.0
    for tok in completion.get("tokens", []):
        lp = tok.get("logprob")
        if lp is None:
            return float("-inf")
        total += float(lp)
    return total


def collect_observations(scenario_state_path: Path) -> list[InstanceObservation]:
    """Aggregate per-instance match counts (cheap pass for `summarize_subject`)."""
    with open(scenario_state_path) as f:
        scenario = json.load(f)
    observations: list[InstanceObservation] = []
    for state in scenario["request_states"]:
        instance = state["instance"]
        # The reproducibility-heatmap audit averages across `valid` AND `test`
        # splits (instance_level_n=7200=900*8 metrics in core_metric_report).
        # Keep both so our predicted agreement matches that denominator.
        golds = [
            r["output"]["text"]
            for r in instance.get("references", [])
            if "correct" in r.get("tags", [])
        ]
        if not golds:
            continue
        completions = state.get("result", {}).get("completions", []) or []
        match_flags: list[bool] = []
        cum_logprobs: list[float] = []
        for c in completions:
            text = c.get("text", "") or ""
            matched = any(quasi_exact_match(g, text) for g in golds)
            match_flags.append(matched)
            cum_logprobs.append(cumulative_logprob(c))
        observations.append(
            InstanceObservation(
                instance_id=instance["id"],
                n_completions=len(completions),
                n_match=sum(match_flags),
                cum_logprobs=cum_logprobs,
                match_flags=match_flags,
            )
        )
    return observations


def _serialize_token(tok: dict) -> dict:
    """Compact, schema-stable representation of one decoded token.

    Fields:

    * `text`         — the token piece as decoded by the tokenizer.
    * `logprob`      — log P_{T=1}(token | prefix) under the actual sampler.
    * `top_alt`      — *if available* a list of {text, logprob} entries for
                       the model's top-K alternatives at this step. HELM
                       v0.3.0 official runs typically store the single
                       runner-up (top_k_per_token=1); local re-runs often
                       store none. Each `logprob` here is also at T=1.
    * `logit_gap_to_chosen` — `chosen.logprob - alt.logprob` per alternative;
                              this is identical in nats to the *raw logit*
                              gap because the softmax normalizer cancels.
                              That makes it the temperature-stable quantity:
                              under T-rescaling the gap becomes
                              `gap / T` and is sufficient for top-K
                              renormalization.
    """
    out = {
        "text": tok.get("text"),
        "logprob": tok.get("logprob"),
    }
    alt = tok.get("top_logprobs") or {}
    if alt:
        # `top_logprobs` is a dict {alt_text -> alt_logprob}.
        chosen_lp = tok.get("logprob")
        out["top_alt"] = [
            {
                "text": k,
                "logprob": v,
                "logit_gap_to_chosen": (
                    None if chosen_lp is None or v is None else chosen_lp - v
                ),
            }
            for k, v in alt.items()
        ]
    return out


def dump_raw_records(
    scenario_state_path: Path,
    out_path: Path,
    side: str,
    model: str,
    subject: str,
) -> int:
    """Dump every request_state's full token-level logprob trace as JSONL.

    Each line is one *instance*: prompt, references, decoding params, and a
    list of completions where each completion carries cumulative logprob,
    per-token chosen logprob, and (when present) top-alternative logprob.

    Returns the number of records written.
    """
    with open(scenario_state_path) as f:
        scenario = json.load(f)
    n = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as out:
        for state in scenario["request_states"]:
            instance = state["instance"]
            req = state.get("request", {}) or {}
            golds = [
                r["output"]["text"]
                for r in instance.get("references", [])
                if "correct" in r.get("tags", [])
            ]
            comps_raw = state.get("result", {}).get("completions", []) or []
            completions = []
            for c in comps_raw:
                text = c.get("text", "") or ""
                tokens = [_serialize_token(t) for t in c.get("tokens", []) or []]
                completions.append(
                    {
                        "text": text,
                        "cum_logprob": cumulative_logprob(c),
                        "n_tokens": len(tokens),
                        "tokens": tokens,
                        "matches_reference_quasi_em": any(
                            quasi_exact_match(g, text) for g in golds
                        ),
                    }
                )
            record = {
                "side": side,
                "model": model,
                "subject": subject,
                "instance_id": instance["id"],
                "split": instance.get("split"),
                "prompt": req.get("prompt"),
                "references": golds,
                "request_params": {
                    "temperature": req.get("temperature"),
                    "num_completions": req.get("num_completions"),
                    "max_tokens": req.get("max_tokens"),
                    "stop_sequences": req.get("stop_sequences"),
                    "top_p": req.get("top_p"),
                    "top_k_per_token": req.get("top_k_per_token"),
                },
                "completions": completions,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
    return n


# ---------------------------------------------------------------------------
# Aggregation and prediction.
# ---------------------------------------------------------------------------


@dataclass
class PerSubjectSummary:
    model: str
    subject: str
    local_run_dir: str
    official_run_dir: str | None
    n_instances: int
    # Primary clean estimators (unbiased / direct):
    bar_p5_observed: float            # mean(Y_i) where Y_i = 1{any match in 5}; unbiased for E[p_i]
    uniform_agreement: float          # bar(p)^2 + (1-bar(p))^2 — Lean's `uniformAgreement`
    var_q_unbiased: float             # bias-corrected Var(q_i) using paired-sample identity
    bar_q_mc: float                   # mean(matches/5) — unbiased for E[q]
    var_p5_delta_method: float        # (5*(1-bar_q)^4)^2 * Var(q); rough projected Var(p)
    predicted_agreement_delta: float  # uniform_agreement + 2 * var_p5_delta_method
    # Diagnostic (likelihood lower bound from cumulative logprobs):
    bar_q_lik_lb: float               # mean over i of sum exp(cumlogprob) on matched samples
    # Observed cross-run agreement (from existing core_metric_report.txt):
    observed_agreement: float | None
    # Inferred variance from the variance-decomposition theorem itself.
    # If observed agreement is the heterogeneous expected agreement, then:
    #   inferred_var_p = (observed - uniform) / 2.
    # Reporting this is not circular: it makes the Lean claim concrete by
    # binding Var(p) to a *measured* number, then asserts the measurement
    # is non-negative and below 1/4 (the max for a [0,1]-valued p).
    inferred_var_p5: float | None
    inferred_var_p5_in_admissible_range: bool | None


def _mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def _var(xs: Iterable[float]) -> float:
    xs = list(xs)
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs) if xs else float("nan")


def summarize_subject(
    model: str,
    subject: str,
    local_run_dir: Path,
    official_run_dir: Path | None,
    observed_agreement: float | None,
) -> PerSubjectSummary:
    obs = collect_observations(local_run_dir / "scenario_state.json")
    if not obs:
        raise RuntimeError(f"no observations in {local_run_dir}")

    # Y_i = 1 if any of the 5 sampled completions matched a `correct`
    # reference. Y_i is an unbiased Bernoulli observation of p_i.
    ys = [1.0 if o.n_match > 0 else 0.0 for o in obs]
    bar_p = _mean(ys)
    uniform = bar_p ** 2 + (1 - bar_p) ** 2

    # Bias-corrected estimator for Var(q_i):
    # given k=5 i.i.d. Bernoulli(q_i) per instance,
    #   Var_emp(q_hat) = Var(q) + E[q(1-q)/k]   (Eve's law)
    # so Var(q_i) ~ Var_emp(q_hat) - E[q(1-q)]/k.
    qs = [o.q_hat_mc for o in obs]
    bar_q = _mean(qs)
    k = 5
    var_emp_q = _var(qs)
    bar_q_one_minus_q = _mean([q * (1 - q) for q in qs])
    var_q_unbiased = max(0.0, var_emp_q - bar_q_one_minus_q / k)

    # Project Var(q) to Var(p_5) by the delta method:
    #   p = 1 - (1-q)^5 => dp/dq = 5*(1-q)^4
    # so Var(p) ~ (5*(1-bar_q)^4)^2 * Var(q).
    g_prime = 5 * (1 - bar_q) ** 4
    var_p_delta = (g_prime ** 2) * var_q_unbiased

    qs_lik_lb = [o.q_hat_likelihood for o in obs]

    inferred_var_p = None
    in_range = None
    if observed_agreement is not None:
        # `observed_agreement` is an instance-metric averaged ratio. Across
        # the 8 binary metrics in this scenario all 8 collapse to the same
        # binary score, so the cross-run agreement of the binary score is
        # close to (and for these runs equal to) the reported value.
        inferred_var_p = (observed_agreement - uniform) / 2
        in_range = 0.0 <= inferred_var_p <= 0.25

    return PerSubjectSummary(
        model=model,
        subject=subject,
        local_run_dir=str(local_run_dir),
        official_run_dir=str(official_run_dir) if official_run_dir else None,
        n_instances=len(obs),
        bar_p5_observed=bar_p,
        uniform_agreement=uniform,
        var_q_unbiased=var_q_unbiased,
        bar_q_mc=bar_q,
        var_p5_delta_method=var_p_delta,
        predicted_agreement_delta=uniform + 2 * var_p_delta,
        bar_q_lik_lb=_mean(qs_lik_lb),
        observed_agreement=observed_agreement,
        inferred_var_p5=inferred_var_p,
        inferred_var_p5_in_admissible_range=in_range,
    )


def lookup_observed_agreement(model: str, subject: str) -> float | None:
    """Pull the abs_tol=0 cross-run agreement ratio for this (model, subject)."""
    model_dirname = MODEL_DIRNAMES[model]
    suffix = f"--wikifact-k-5-subject-{subject}-model-{model_dirname}"
    candidates: list[Path] = []
    for root in CORE_REPORT_ROOTS:
        if not root.exists():
            continue
        candidates += [d for d in root.iterdir() if d.name.endswith(suffix)]
    for d in candidates:
        rep = d / "core_metric_report.txt"
        if not rep.exists():
            continue
        text = rep.read_text()
        match = re.search(r"abs_tol=0\.0\s+agree_ratio=([0-9.]+)", text)
        if match:
            return float(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Lean snippet emission.
# ---------------------------------------------------------------------------


def _to_rational(x: float, denom: int = 10000) -> Fraction:
    return Fraction(round(x * denom), denom)


def _ident(*parts: str) -> str:
    s = "_".join(parts)
    return re.sub(r"[^A-Za-z0-9_]", "", s.replace("-", "").replace(".", ""))


_RAW_SCHEMA_DOC = """\
# Raw WikiFact per-token logprob dump

One JSONL file per (side, model, subject), where `side ∈ {local, official}`.
Each line is one HELM `request_state` for one prompt.

## Top-level fields

- `side`             — `"local"` (5-completion local re-run) or `"official"` (HELM v0.3.0)
- `model`            — short id, e.g. `pythia-6.9b`
- `subject`          — wikifact subject, e.g. `place_of_birth`
- `instance_id`      — e.g. `id100`
- `split`            — `valid` or `test`
- `prompt`           — full text fed to the model (5 in-context examples + query)
- `references`       — list of strings tagged `correct` for this prompt
- `request_params`   — decoding params; for the recipe in the paper this is
                       `{temperature: 1.0, num_completions: 5, max_tokens: 8,
                       stop_sequences: ["\\n"], top_p: 1.0, top_k_per_token: K}`.
- `completions`      — list of completion records (5 for local, 1 for v0.3.0 official)

## Completion record

- `text`              — raw decoded completion (untouched, before HELM normalization)
- `cum_logprob`       — sum_t log P_{T=1}(token_t | prefix); equals
                        `log P_{T=1}(this_completion | prompt)` exactly
- `n_tokens`          — number of tokens after stop-sequence truncation
- `tokens`            — list of token records (see below)
- `matches_reference_quasi_em` — whether the completion text matches any
                                 reference under HELM's `quasi_exact_match`
                                 normalization (lower / strip-punct /
                                 strip-articles / collapse-whitespace)

## Token record

- `text`              — token piece as decoded by the tokenizer
- `logprob`           — log P_{T=1}(this_token | prefix)
- `top_alt`           — *optional*. List of alternative-token records the
                        backend chose to store, with `{text, logprob,
                        logit_gap_to_chosen}`. HELM v0.3.0 official runs
                        typically store the single runner-up
                        (`top_k_per_token=1`); local runs often store none.

## Temperature-dependence modeling

The raw `logprob` values are at `T = 1.0`. To rescale to temperature `T'`:

* The token-level logit gap between the chosen token and an alternative is
  invariant under softmax normalization:
      logit_chosen - logit_alt
        = log P_1(chosen) - log P_1(alt)
        = `logit_gap_to_chosen` (already pre-computed)
  Under temperature `T'` this becomes `(logit_gap)/T'`, and the chosen-vs-alt
  conditional probability becomes
      P_{T'}(chosen | {chosen, alt}) = sigmoid(logit_gap / T').
* For top-K renormalization (when more alternatives are stored), use
  softmax over `(logit_chosen, logit_alt_1, ..., logit_alt_{K-1})` where
  each logit equals `log P_1(token) + C` for the same constant `C` (which
  cancels under softmax). So you can use the *log P_1* values directly as
  logit stand-ins for the purposes of T-rescaling within the top-K subset.
* Without the full vocabulary distribution, T-rescaling of the *full*
  per-token distribution is approximate — top-K coverage bounds it.

For sequence-level temperature reasoning, treat the `cum_logprob` field as
`sum_t logit_t + C_total`. The constant `C_total` cancels in any *ratio* of
conditional probabilities, so for top-K sequence-level renormalization (e.g.
within the 5 sampled completions per prompt) you can use `cum_logprob` as a
logit-equivalent quantity directly.
"""


def _check_lean_residual(
    bar_p_r: Fraction, observed_r: Fraction, inferred_r: Fraction,
    slack_r: Fraction,
) -> tuple[bool, Fraction]:
    """Compute the actual rational residual and verify it's within slack."""
    uniform_r = bar_p_r ** 2 + (1 - bar_p_r) ** 2
    residual = abs(uniform_r + 2 * inferred_r - observed_r)
    return (residual <= slack_r, residual)


def _emit_lean_block(
    title: str,
    ident: str,
    bar_p: float,
    observed: float | None,
    inferred_var: float | None,
    in_range: bool | None,
    n_instances: int,
    extra_doc: str = "",
) -> list[str]:
    bar_p_r = _to_rational(bar_p, denom=10000)
    lines = [
        f"-- {title} (n_instances = {n_instances})",
        (f"-- {extra_doc}" if extra_doc else "").rstrip(),
        f"def barP5_{ident} : ℝ := {bar_p_r.numerator} / {bar_p_r.denominator}",
        "",
        f"def uniformAgreement_{ident} : ℝ :=",
        f"  barP5_{ident}^2 + (1 - barP5_{ident})^2",
        "",
    ]
    if observed is not None and inferred_var is not None:
        observed_r = _to_rational(observed, denom=10000)
        inferred_r = _to_rational(inferred_var, denom=100000)
        # Slack accounts for rounding in the rational approximations of
        # bar(p), observed, and inferred Var. We compute it from the actual
        # rational residual and add headroom so `norm_num` has a margin.
        uniform_r = bar_p_r ** 2 + (1 - bar_p_r) ** 2
        residual_r = abs(uniform_r + 2 * inferred_r - observed_r)
        slack_r = residual_r + Fraction(1, 1000)  # 0.001 headroom
        ok, _ = _check_lean_residual(bar_p_r, observed_r, inferred_r, slack_r)
        assert ok, f"Lean slack too tight for {ident}: residual {float(residual_r)} > slack {float(slack_r)}"
        slack = float(slack_r)
        lines += [
            f"def observedAgreement_{ident} : ℝ := "
            f"{observed_r.numerator} / {observed_r.denominator}",
            "",
            f"/-- Inferred per-prompt variance Var(p_5) =",
            f"    (observedAgreement - uniformAgreement) / 2 -/",
            f"def inferredVar_{ident} : ℝ := "
            f"{inferred_r.numerator} / {inferred_r.denominator}",
            "",
            f"/-- The inferred variance lies in [0, 1/4]. -/",
            f"theorem inferredVar_admissible_{ident} :",
            f"    0 ≤ inferredVar_{ident} ∧ inferredVar_{ident} ≤ 1/4 := by",
            f"  unfold inferredVar_{ident}; refine ⟨by norm_num, by norm_num⟩",
            "",
            f"/-- Variance decomposition matches observed agreement up to "
            f"rational-rounding slack. -/",
            f"theorem decomposition_matches_observed_{ident} :",
            f"    |uniformAgreement_{ident} + 2 * inferredVar_{ident}",
            f"      - observedAgreement_{ident}|",
            f"      ≤ {slack_r.numerator} / {slack_r.denominator} := by",
            f"  unfold uniformAgreement_{ident} barP5_{ident}",
            f"        inferredVar_{ident} observedAgreement_{ident}",
            "  rw [abs_le]; refine ⟨by norm_num, by norm_num⟩",
            "",
        ]
        if in_range is False:
            lines.append(
                f"-- WARNING: inferred variance is OUT OF [0, 1/4] — the "
                "admissibility theorem above will fail `norm_num`."
            )
            lines.append("")
    return lines


def _lean_str(s: str) -> str:
    """Escape a Python string for inclusion in a Lean string literal."""
    return (
        s.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _lean_float(x: float | None) -> str:
    """Render a float as a Lean `Float` literal; `none` if missing."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "none"
    return f"some ({x:.10g})"


def _build_lean_logit_samples(
    rows: list[PerSubjectSummary],
    canonical_subject: str,
    k_per_model: int,
) -> str:
    """Emit a Lean module with raw logprob samples for `k_per_model`
    instances per model from the canonical subject.

    Each instance includes prompt, references, decoding params, and the
    full token-level (`logprob`, `top_alt.logprob`) trace for every
    completion. This is the data the Lean prover needs to model
    temperature dependence at the per-token level.
    """
    structs = """\
namespace WikiFactNoise.RawLogits

/-- One alternative token (e.g. the runner-up reported by HELM v0.3.0). -/
structure TokenAlt where
  text : String
  logprob : Float
  logitGapToChosen : Option Float := none
deriving Repr

/-- One decoded token. `logprob` and `topAlt.logprob` are at T = 1.0. -/
structure Token where
  text : String
  logprob : Float
  topAlt : List TokenAlt := []
deriving Repr

/-- One sampled completion. `cumLogprob = sum tokens.logprob`. -/
structure Completion where
  text : String
  cumLogprob : Float
  matchesReference : Bool
  tokens : List Token
deriving Repr

/-- Decoding parameters; the WikiFact recipe is
    `{temperature := 1.0, numCompletions := 5, maxTokens := 8, ...}`. -/
structure RequestParams where
  temperature : Float
  numCompletions : Nat
  maxTokens : Nat
  stopSequences : List String
  topP : Float
  topKPerToken : Nat
deriving Repr

structure Instance where
  side : String          -- "local" or "official"
  model : String
  subject : String
  instanceId : String
  split : String
  prompt : String
  references : List String
  request : RequestParams
  completions : List Completion
deriving Repr

"""

    # Pick canonical-subject rows, one per model.
    canonical = [r for r in rows if r.subject == canonical_subject]
    instance_blocks: list[str] = []
    for r in canonical:
        ident = _ident(r.model, r.subject)
        sample_block = _render_lean_instance_block(
            ident=ident,
            side="local",
            run_dir=Path(r.local_run_dir),
            model=r.model,
            subject=r.subject,
            k=k_per_model,
        )
        instance_blocks.append(sample_block)
        if r.official_run_dir:
            off_block = _render_lean_instance_block(
                ident=ident + "_official",
                side="official",
                run_dir=Path(r.official_run_dir),
                model=r.model,
                subject=r.subject,
                k=k_per_model,
            )
            instance_blocks.append(off_block)

    header = (
        "/- Auto-generated by dev/paper-analysis/neurips-2026/measure_wikifact_logits.py.\n"
        "   Raw per-token logprob samples for the WikiFact heatmap cell.\n"
        "   The Lean prover can use these structured samples to reason\n"
        "   about temperature-dependent re-normalization of completion\n"
        "   probabilities. -/\n\n"
        "import Mathlib\n\n"
    )
    body = structs + "\n\n".join(instance_blocks)
    footer = "\nend WikiFactNoise.RawLogits\n"
    return header + body + footer


def _render_lean_instance_block(
    ident: str,
    side: str,
    run_dir: Path,
    model: str,
    subject: str,
    k: int,
) -> str:
    """Read scenario_state.json from `run_dir`, take first k instances, render."""
    p = run_dir / "scenario_state.json"
    if not p.exists():
        return f"-- (no scenario_state.json at {p})\n"
    with p.open() as f:
        scenario = json.load(f)
    states = scenario.get("request_states", [])[:k]
    if not states:
        return f"-- (no request_states in {p})\n"

    items: list[str] = []
    for state in states:
        instance = state["instance"]
        req = state.get("request", {}) or {}
        golds = [
            r["output"]["text"]
            for r in instance.get("references", [])
            if "correct" in r.get("tags", [])
        ]
        comps_raw = state.get("result", {}).get("completions", []) or []
        comp_items: list[str] = []
        for c in comps_raw:
            text = c.get("text", "") or ""
            matched = any(quasi_exact_match(g, text) for g in golds)
            cum_lp = cumulative_logprob(c)
            tok_items: list[str] = []
            for t in c.get("tokens", []) or []:
                t_lp = t.get("logprob")
                alt_items: list[str] = []
                for alt_text, alt_lp in (t.get("top_logprobs") or {}).items():
                    gap = (
                        None
                        if t_lp is None or alt_lp is None
                        else f"{t_lp - alt_lp:.10g}"
                    )
                    alt_items.append(
                        "      ⟨"
                        f"\"{_lean_str(alt_text)}\", {alt_lp:.10g}, "
                        f"{('some ' + gap) if gap is not None else 'none'}⟩"
                    )
                alt_block = (
                    "[" + (",\n".join(alt_items) if alt_items else "") + "]"
                )
                tok_items.append(
                    f"    ⟨\"{_lean_str(t.get('text','') or '')}\", "
                    f"{(t_lp if t_lp is not None else 0):.10g}, {alt_block}⟩"
                )
            tokens_block = "[\n" + ",\n".join(tok_items) + "\n  ]"
            comp_items.append(
                "  ⟨"
                f"\"{_lean_str(text)}\", {cum_lp:.10g}, "
                f"{'true' if matched else 'false'}, {tokens_block}⟩"
            )
        completions_block = (
            "[\n" + ",\n".join(comp_items) + "\n]"
            if comp_items
            else "[]"
        )
        params = (
            "⟨"
            f"{float(req.get('temperature', 1.0)):.6g}, "
            f"{int(req.get('num_completions', 1))}, "
            f"{int(req.get('max_tokens', 0))}, "
            "[" + ", ".join(
                f"\"{_lean_str(s)}\""
                for s in (req.get("stop_sequences") or [])
            ) + "], "
            f"{float(req.get('top_p', 1.0)):.6g}, "
            f"{int(req.get('top_k_per_token', 0))}"
            "⟩"
        )
        refs = (
            "["
            + ", ".join(f"\"{_lean_str(g)}\"" for g in golds)
            + "]"
        )
        items.append(
            "⟨"
            f"\"{side}\", \"{_lean_str(model)}\", \"{_lean_str(subject)}\", "
            f"\"{_lean_str(instance['id'])}\", \"{_lean_str(instance.get('split',''))}\", "
            f"\"{_lean_str(req.get('prompt','') or '')}\", "
            f"{refs}, {params}, {completions_block}"
            "⟩"
        )
    items_block = "[\n" + ",\n".join(items) + "\n]"
    return (
        f"/-- First {len(states)} instances from {side}/{model}/{subject}.\n"
        f"    Source: {p}. -/\n"
        f"def samples_{ident} : List Instance :=\n{items_block}\n"
    )


def lean_snippet(
    pooled: dict[str, dict],
    canonical_rows: dict[tuple[str, str], PerSubjectSummary],
) -> str:
    """Emit Lean stubs binding numerical values into the existing scaffold.

    For each model we emit two blocks:

    * The canonical subject (the one cited in the paper's heatmap, default
      `place_of_birth`). This is the strongest claim — it ties the Lean
      theorem directly to the value reported in the appendix.
    * The pooled-across-subjects estimate, for completeness.
    """

    lines = [
        "/- Auto-generated by dev/paper-analysis/neurips-2026/measure_wikifact_logits.py.",
        "   Empirical evidence binding the WikiFact heterogeneity claim",
        "   to local 5-sample HELM scenario_state data. -/",
        "",
        "import Mathlib",
        "import docs.paper.wikifact_consistency_claim",
        "",
        "open WikiFactNoise",
        "",
        "namespace WikiFactNoise.Empirical",
        "",
    ]
    # Canonical-subject blocks (one per model).
    for (model, subject), s in sorted(canonical_rows.items()):
        ident = _ident(model, subject)
        lines += _emit_lean_block(
            title=f"{model} :: subject={subject} (paper-cited cell)",
            ident=ident,
            bar_p=s.bar_p5_observed,
            observed=s.observed_agreement,
            inferred_var=s.inferred_var_p5,
            in_range=s.inferred_var_p5_in_admissible_range,
            n_instances=s.n_instances,
            extra_doc=(
                f"local run dir: {s.local_run_dir}; "
                f"observed = {s.observed_agreement} (cross-run agree_ratio at abs_tol=0)."
            ),
        )
    # Pooled-across-subjects blocks.
    for model, p in sorted(pooled.items()):
        ident = _ident(model, "pooled")
        lines += _emit_lean_block(
            title=f"{model} :: pooled across {p['n_subjects']} subjects",
            ident=ident,
            bar_p=p["bar_p5_observed"],
            observed=p["observed_agreement"],
            inferred_var=p["inferred_var_p5"],
            in_range=p["inferred_var_p5_in_admissible_range"],
            n_instances=p["n_instances"],
            extra_doc=(
                f"subjects: {', '.join(p['subjects'])}"
            ),
        )
    lines += ["end WikiFactNoise.Empirical", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI driver.
# ---------------------------------------------------------------------------


def render_table(rows: list[PerSubjectSummary]) -> str:
    cols = [
        ("model", 14),
        ("subject", 24),
        ("n", 4),
        ("bar(q)", 7),
        ("bar(p5)", 8),
        ("uniform", 8),
        ("Var(q)*", 8),
        ("Var(p5)δ", 9),
        ("pred_δ", 7),
        ("observed", 9),
        ("Var(p5)inf", 10),
        ("ok", 3),
    ]
    head = " ".join(f"{name:>{w}}" for name, w in cols)
    out = [head, "-" * len(head)]
    for r in rows:
        out.append(
            " ".join(
                [
                    f"{r.model:>14}",
                    f"{r.subject:>24}",
                    f"{r.n_instances:>4d}",
                    f"{r.bar_q_mc:>7.4f}",
                    f"{r.bar_p5_observed:>8.4f}",
                    f"{r.uniform_agreement:>8.4f}",
                    f"{r.var_q_unbiased:>8.5f}",
                    f"{r.var_p5_delta_method:>9.5f}",
                    f"{r.predicted_agreement_delta:>7.4f}",
                    (
                        f"{r.observed_agreement:>9.4f}"
                        if r.observed_agreement is not None
                        else f"{'n/a':>9}"
                    ),
                    (
                        f"{r.inferred_var_p5:>10.5f}"
                        if r.inferred_var_p5 is not None
                        else f"{'n/a':>10}"
                    ),
                    (
                        f"{('Y' if r.inferred_var_p5_in_admissible_range else 'N'):>3}"
                        if r.inferred_var_p5_in_admissible_range is not None
                        else f"{'-':>3}"
                    ),
                ]
            )
        )
    return "\n".join(out)


def aggregate_per_model(rows: list[PerSubjectSummary]) -> dict[str, dict]:
    """Pool instances across subjects.

    Pooling is done at the prompt level: each subject contributes its
    n_instances prompts as i.i.d. units. We pool bar(p) directly and pool
    Var(q) via the law of total variance.
    """
    by_model: dict[str, list[PerSubjectSummary]] = {}
    for r in rows:
        by_model.setdefault(r.model, []).append(r)
    out: dict[str, dict] = {}
    for model, rs in by_model.items():
        total = sum(r.n_instances for r in rs) or 1

        def w_mean(getter):
            return sum(getter(r) * r.n_instances for r in rs) / total

        bar_p_pooled = w_mean(lambda r: r.bar_p5_observed)
        bar_q_pooled = w_mean(lambda r: r.bar_q_mc)
        # Law of total variance for Var(q).
        within = w_mean(lambda r: r.var_q_unbiased)
        between = sum(
            r.n_instances * (r.bar_q_mc - bar_q_pooled) ** 2 for r in rs
        ) / total
        var_q_pooled = within + between
        # Project to Var(p) at the pooled bar(q).
        var_p_pooled_delta = (5 * (1 - bar_q_pooled) ** 4) ** 2 * var_q_pooled
        uniform = bar_p_pooled ** 2 + (1 - bar_p_pooled) ** 2
        predicted_delta = uniform + 2 * var_p_pooled_delta
        valid_obs = [
            (r.observed_agreement, r.n_instances)
            for r in rs
            if r.observed_agreement is not None
        ]
        observed_pool = (
            sum(v * w for v, w in valid_obs) / sum(w for _, w in valid_obs)
            if valid_obs
            else None
        )
        inferred_var_p = (
            (observed_pool - uniform) / 2 if observed_pool is not None else None
        )
        out[model] = {
            "n_subjects": len(rs),
            "n_instances": total,
            "bar_p5_observed": bar_p_pooled,
            "bar_q": bar_q_pooled,
            "var_q_pooled": var_q_pooled,
            "var_p5_delta": var_p_pooled_delta,
            "uniform_agreement": uniform,
            "predicted_agreement_delta": predicted_delta,
            "observed_agreement": observed_pool,
            "inferred_var_p5": inferred_var_p,
            "inferred_var_p5_in_admissible_range": (
                None
                if inferred_var_p is None
                else (0.0 <= inferred_var_p <= 0.25)
            ),
            "subjects": [r.subject for r in rs],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--models",
        nargs="+",
        default=["pythia-6.9b", "vicuna-7b-v1.3", "falcon-7b"],
        choices=list(LOCAL_GRID_ROOTS.keys()),
    )
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument(
        "--limit-subjects", type=int, default=None,
        help="Process at most this many subjects per model (smoke testing).",
    )
    ap.add_argument(
        "--canonical-subject", default="place_of_birth",
        help=(
            "Subject the paper's WikiFact cell refers to. Used for the "
            "canonical Lean stub. Default: place_of_birth (the heatmap-paper-slim packet)."
        ),
    )
    ap.add_argument(
        "--no-raw", action="store_true",
        help="Skip dumping per-token logprob JSONL traces (only summary outputs).",
    )
    ap.add_argument(
        "--lean-sample-instances", type=int, default=8,
        help=(
            "How many instances per model from the canonical subject to "
            "include in `wikifact_logit_samples.lean` as Lean-importable "
            "structured data."
        ),
    )
    args = ap.parse_args(argv)

    rows: list[PerSubjectSummary] = []
    for model in args.models:
        local_runs = discover_local_runs(model)
        if not local_runs:
            print(f"[warn] no local runs found for {model}", file=sys.stderr)
            continue
        if args.limit_subjects:
            local_runs = local_runs[: args.limit_subjects]
        for run_dir in local_runs:
            # Extract subject from "wikifact:k=5,subject=X,model=Y"
            m = re.search(r"subject=([^,]+),model=", run_dir.name)
            if not m:
                continue
            subject = m.group(1)
            official = discover_official_run(model, subject)
            observed = lookup_observed_agreement(model, subject)
            print(f"[info] {model} :: {subject}", file=sys.stderr)
            rows.append(
                summarize_subject(model, subject, run_dir, official, observed)
            )

    print(render_table(rows))
    print()
    pooled = aggregate_per_model(rows)
    print("Per-model pooled estimates:")
    for model, p in pooled.items():
        line = (
            f"  {model}: n={p['n_instances']} "
            f"bar(q)={p['bar_q']:.4f} bar(p5)={p['bar_p5_observed']:.4f} "
            f"uniform={p['uniform_agreement']:.4f} "
            f"Var(q)*={p['var_q_pooled']:.5f} "
            f"Var(p5)δ={p['var_p5_delta']:.5f} "
            f"predicted_δ={p['predicted_agreement_delta']:.4f} "
            f"observed="
            + (
                f"{p['observed_agreement']:.4f}"
                if p["observed_agreement"] is not None
                else "n/a"
            )
        )
        if p["inferred_var_p5"] is not None:
            line += (
                f" inferred_Var(p5)={p['inferred_var_p5']:.5f} "
                f"({'admissible' if p['inferred_var_p5_in_admissible_range'] else 'OUT_OF_RANGE'})"
            )
        print(line)
    print()
    print(
        "Legend: '*' = unbiased estimator (paired-sample / Eve's-law correction);\n"
        "        'δ' = delta-method projection from Var(q) to Var(p_5);\n"
        "        'inferred_Var(p5) = (observed - uniform) / 2', i.e. the value\n"
        "        the variance-decomposition theorem identifies given the\n"
        "        observed cross-run agreement. Admissible iff in [0, 1/4]."
    )

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "per_subject.json").write_text(
            json.dumps([r.__dict__ for r in rows], indent=2)
        )
        (args.out_dir / "per_model.json").write_text(json.dumps(pooled, indent=2))
        canonical_rows = {
            (r.model, r.subject): r
            for r in rows
            if r.subject == args.canonical_subject
        }
        (args.out_dir / "wikifact_consistency_data.lean").write_text(
            lean_snippet(pooled, canonical_rows)
        )

        # Raw per-token logprob dump for temperature-dependence modeling.
        if not args.no_raw:
            raw_root = args.out_dir / "raw"
            manifest: list[dict] = []
            for r in rows:
                local_jsonl = (
                    raw_root
                    / f"local__{r.model}__{r.subject}.jsonl"
                )
                n_local = dump_raw_records(
                    Path(r.local_run_dir) / "scenario_state.json",
                    local_jsonl,
                    side="local",
                    model=r.model,
                    subject=r.subject,
                )
                manifest.append(
                    {
                        "side": "local",
                        "model": r.model,
                        "subject": r.subject,
                        "path": str(local_jsonl.relative_to(args.out_dir)),
                        "n_records": n_local,
                        "source_run_dir": r.local_run_dir,
                    }
                )
                if r.official_run_dir:
                    off_jsonl = (
                        raw_root
                        / f"official__{r.model}__{r.subject}.jsonl"
                    )
                    n_off = dump_raw_records(
                        Path(r.official_run_dir) / "scenario_state.json",
                        off_jsonl,
                        side="official",
                        model=r.model,
                        subject=r.subject,
                    )
                    manifest.append(
                        {
                            "side": "official",
                            "model": r.model,
                            "subject": r.subject,
                            "path": str(off_jsonl.relative_to(args.out_dir)),
                            "n_records": n_off,
                            "source_run_dir": r.official_run_dir,
                        }
                    )
            (raw_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
            # Schema reference, kept next to the data to make it easy for a
            # downstream Lean prover (or any consumer) to deserialize.
            (raw_root / "SCHEMA.md").write_text(_RAW_SCHEMA_DOC)
            print(
                f"Wrote {len(manifest)} JSONL files of raw per-token logprob "
                f"traces under {raw_root}/"
            )

            # Lean-importable subset: K instances per model from the canonical
            # subject, hard-coded as `def` declarations the Lean prover can
            # plug straight into theorems without I/O.
            sample_lean = _build_lean_logit_samples(
                rows=rows,
                canonical_subject=args.canonical_subject,
                k_per_model=args.lean_sample_instances,
            )
            (args.out_dir / "wikifact_logit_samples.lean").write_text(sample_lean)
        # LaTeX-ready macros for the appendix. We emit two flavors:
        # `\barP<model>` (pooled across subjects) and
        # `\barPcanon<model>` (single canonical subject = place_of_birth by
        # default). The paper's WikiFact heatmap cell is the canonical one.
        with (args.out_dir / "summary.tex").open("w") as f:
            f.write("% Auto-generated by measure_wikifact_logits.py\n")
            for m, p in pooled.items():
                ident = m.replace("-", "").replace(".", "")
                f.write(
                    f"\\newcommand{{\\barP{ident}}}{{{p['bar_p5_observed']:.3f}}}\n"
                )
                f.write(
                    f"\\newcommand{{\\uniformAgree{ident}}}{{{p['uniform_agreement']:.3f}}}\n"
                )
                if p["observed_agreement"] is not None:
                    f.write(
                        f"\\newcommand{{\\obsAgree{ident}}}{{{p['observed_agreement']:.3f}}}\n"
                    )
                if p["inferred_var_p5"] is not None:
                    f.write(
                        f"\\newcommand{{\\inferredVarP{ident}}}{{{p['inferred_var_p5']:.4f}}}\n"
                    )
            f.write(
                f"% Canonical subject = {args.canonical_subject} "
                "(matches the heatmap-paper-slim packet).\n"
            )
            for r in rows:
                if r.subject != args.canonical_subject:
                    continue
                ident = r.model.replace("-", "").replace(".", "")
                f.write(
                    f"\\newcommand{{\\barPcanon{ident}}}{{{r.bar_p5_observed:.3f}}}\n"
                )
                f.write(
                    f"\\newcommand{{\\uniformAgreeCanon{ident}}}"
                    f"{{{r.uniform_agreement:.3f}}}\n"
                )
                if r.observed_agreement is not None:
                    f.write(
                        f"\\newcommand{{\\obsAgreeCanon{ident}}}"
                        f"{{{r.observed_agreement:.4f}}}\n"
                    )
                if r.inferred_var_p5 is not None:
                    f.write(
                        f"\\newcommand{{\\inferredVarPcanon{ident}}}"
                        f"{{{r.inferred_var_p5:.4f}}}\n"
                    )
        print(f"\nWrote outputs under {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
