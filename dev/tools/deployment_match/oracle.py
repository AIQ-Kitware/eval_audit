"""Read a public HELM run into the comparison oracle.

The oracle is everything the search needs from the official side:

* the **recipe** (adapter params) to replay verbatim — we vary only deployment
  knobs, never the recipe (from-spec fidelity);
* a small **sample** of instances, each carrying the official prompt + the
  official completion (+ per-token text/logprobs when present) — the ground truth
  the candidate deployments are scored against;
* the official **model / deployment** names, so ``registry`` can look up the
  official tokenizer + max_sequence_length to seed grid defaults.

On-disk shape (verified against ``/data/crfm-helm-public``): a run dir has
``run_spec.json`` (``adapter_spec`` with model/model_deployment/max_tokens/…) and
``scenario_state.json`` (``request_states[i].request.prompt`` +
``.result.completions[0].text``/``.tokens``). Stdlib only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# Adapter params that define the recipe. Replayed fixed; never swept.
RECIPE_KEYS = (
    "method",
    "max_tokens",
    "temperature",
    "stop_sequences",
    "num_outputs",
    "top_p",
    "top_k_per_token",
    "presence_penalty",
    "frequency_penalty",
)


@dataclass
class InstanceSample:
    instance_id: str
    prompt: str
    official_completion: str
    official_logprob: float | None = None
    official_tokens: list[dict[str, Any]] = field(default_factory=list)
    prompt_len_chars: int = 0
    # Per-request generation params (should match the recipe; captured for audit).
    request_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Oracle:
    run_dir: str
    run_name: str
    model: str
    model_deployment: str
    recipe: dict[str, Any]
    sample: list[InstanceSample]
    n_available: int

    def to_json(self) -> dict[str, Any]:
        return {
            "run_dir": self.run_dir,
            "run_name": self.run_name,
            "model": self.model,
            "model_deployment": self.model_deployment,
            "recipe": self.recipe,
            "n_available": self.n_available,
            "sample": [asdict(s) for s in self.sample],
        }

    @classmethod
    def from_json(cls, doc: dict[str, Any]) -> "Oracle":
        return cls(
            run_dir=doc["run_dir"],
            run_name=doc["run_name"],
            model=doc["model"],
            model_deployment=doc["model_deployment"],
            recipe=doc["recipe"],
            n_available=doc.get("n_available", len(doc.get("sample", []))),
            sample=[InstanceSample(**s) for s in doc.get("sample", [])],
        )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _request_states(run_dir: Path) -> list[dict[str, Any]]:
    """Per-instance records, preferring scenario_state.json (has completions)."""
    ss = run_dir / "scenario_state.json"
    if ss.exists():
        return _load_json(ss).get("request_states") or []
    # Fallback: display_requests (prompts only) — no official completions, so the
    # oracle would be prompt-only. Loud caller-side check below handles this.
    dr = run_dir / "display_requests.json"
    if dr.exists():
        return list(_load_json(dr))
    raise FileNotFoundError(
        f"{run_dir} has neither scenario_state.json nor display_requests.json"
    )


def _instance_id(rs: dict[str, Any], idx: int) -> str:
    inst = rs.get("instance") or {}
    return str(inst.get("id") or rs.get("instance_id") or f"idx{idx}")


def _first_completion(rs: dict[str, Any]) -> dict[str, Any]:
    comps = (rs.get("result") or {}).get("completions") or []
    return comps[0] if comps else {}


def _select_indices(states: list[dict[str, Any]], n: int, strategy: str) -> list[int]:
    """Choose which instances to sample.

    ``spread-by-length`` (default) sorts by prompt length and takes ``n``
    evenly-spaced picks, so the sample spans short (MC-like) and long prompts —
    the OLMo EOS bug surfaced differently on each (``"The"`` vs the long
    boilerplate), so a length-diverse sample is what makes a small ``n`` still
    discriminating. ``head`` takes the first ``n`` in file order; ``random`` uses
    a fixed seed for reproducibility.
    """
    total = len(states)
    if n >= total:
        return list(range(total))
    if strategy == "head":
        return list(range(n))
    if strategy == "random":
        import random

        rng = random.Random(1234)
        return sorted(rng.sample(range(total), n))
    # spread-by-length (default)
    order = sorted(
        range(total),
        key=lambda i: len(((states[i].get("request") or {}).get("prompt")) or ""),
    )
    picks = {round(k * (total - 1) / (n - 1)) for k in range(n)} if n > 1 else {0}
    # map evenly-spaced ranks in the length ordering back to state indices
    return sorted(order[p] for p in sorted(picks))


def load_oracle(run_dir: str | Path, *, n: int = 16,
                strategy: str = "spread-by-length") -> Oracle:
    """Build the :class:`Oracle` from a public HELM run directory."""
    run_dir = Path(run_dir)
    spec = _load_json(run_dir / "run_spec.json")
    adapter = spec.get("adapter_spec") or {}
    recipe = {k: adapter.get(k) for k in RECIPE_KEYS if adapter.get(k) is not None}

    states = _request_states(run_dir)
    if not states:
        raise ValueError(f"{run_dir} has no request_states / instances")
    idxs = _select_indices(states, n, strategy)

    sample: list[InstanceSample] = []
    for i in idxs:
        rs = states[i]
        req = rs.get("request") or {}
        prompt = req.get("prompt") or (rs.get("instance") or {}).get("input", {}).get("text", "")
        comp = _first_completion(rs)
        sample.append(InstanceSample(
            instance_id=_instance_id(rs, i),
            prompt=prompt,
            official_completion=comp.get("text", ""),
            official_logprob=comp.get("logprob"),
            official_tokens=comp.get("tokens") or [],
            prompt_len_chars=len(prompt),
            request_params={k: req.get(k) for k in (
                "max_tokens", "temperature", "stop_sequences",
                "num_completions", "echo_prompt", "top_p", "top_k_per_token",
            ) if k in req},
        ))

    return Oracle(
        run_dir=str(run_dir),
        run_name=spec.get("name") or run_dir.name,
        model=adapter.get("model") or "",
        model_deployment=adapter.get("model_deployment") or "",
        recipe=recipe,
        sample=sample,
        n_available=len(states),
    )


def has_official_completions(oracle: Oracle) -> bool:
    """True if the sample carries official completion text (not prompt-only)."""
    return any(s.official_completion for s in oracle.sample)
