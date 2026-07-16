#!/usr/bin/env python3
"""Discriminate WHY Qwen3.5-9B-Base answers boolq with a leading "\\n\\n".

Smoke finding (2026-07-16): on boolq the model's FIRST token after a
well-formed 5-shot prompt ending "Answer:" is "\\n\\n" (logprob ~-0.28, i.e.
~75% confident); HELM's canonical stop=['\\n'] truncates the completion to ''
and every instance scores 0. The tokenizer is exonerated (add_special_tokens
changes nothing). Two live hypotheses:

  H1 style     — the model prefers paragraph-style "Answer:\\n\\nYes"; content
                 is fine, the recipe's stop sequence eats it.
  H2 precision — fp16-on-Turing numerics distort the distribution (the GDN
                 state kernels are fp16-sensitive); content after the newline
                 would be junk too.

Run ON YARDRAT while the endpoint is leased (the runbook world):

    cd ~/code/eval_audit/reproduce/qwen35_vllm
    export INFER_STACK_CONFIG_DIR=$PWD/config/infer_stack
    infer-stack acquire qwen3-5-9b-base-single --ttl 30m --yes --queue \
        --timeout 1800 --env-file /tmp/probe-lease.env
    # --timeout is LOAD-BEARING: infer-stack's default readiness budget is
    # 600s and a cold vLLM start (compile pass) can exceed it — the lease
    # then self-releases mid-load and the env file is never written.
    python ../../dev/oneoff/qwen35_boolq_probe.py
    infer-stack release --yes --env-file /tmp/probe-lease.env

Reads LITELLM_MASTER_KEY via `infer-stack env` (same world). Prints the
verdict per case: what the model says with no stop sequence, and whether the
text after the leading newlines is the correct Yes/No.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request

BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:14042")
MODEL = "qwen3-5-9b-base-single"

# Three boolq-style few-shot cases with unambiguous answers.
SHOTS = (
    "Passage: The sky appears blue due to Rayleigh scattering of sunlight.\n"
    "Question: Is the sky blue on a clear day?\nAnswer: Yes\n\n"
    "Passage: Pigs are terrestrial mammals and cannot fly.\n"
    "Question: Can pigs fly?\nAnswer: No\n\n"
)
CASES = [
    ("expects-Yes", SHOTS + "Passage: Water freezes at 0 degrees Celsius at "
     "standard pressure.\nQuestion: Does water freeze at 0 degrees Celsius?\nAnswer:", "Yes"),
    ("expects-No", SHOTS + "Passage: The Great Wall of China is located in "
     "China.\nQuestion: Is the Great Wall of China located in France?\nAnswer:", "No"),
]


def _master_key() -> str:
    return subprocess.check_output(
        ["infer-stack", "env", "LITELLM_MASTER_KEY"], text=True
    ).strip()


def _complete(key: str, prompt: str, *, max_tokens: int, stop=None) -> dict:
    payload = {
        "model": MODEL, "prompt": prompt, "max_tokens": max_tokens,
        "temperature": 0.0, "logprobs": 3,
    }
    if stop is not None:
        payload["stop"] = stop
    req = urllib.request.Request(
        f"{BASE_URL}/v1/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def main() -> int:
    key = _master_key()
    verdicts = []
    for name, prompt, expected in CASES:
        # A: HELM's canonical recipe (stop='\n', 5 tokens) — reproduces the smoke.
        a = _complete(key, prompt, max_tokens=5, stop=["\n"])["choices"][0]
        # B: no stop, room to talk — what does the model REALLY say?
        b = _complete(key, prompt, max_tokens=12)["choices"][0]
        raw = b["text"]
        stripped = raw.strip()
        content_ok = stripped[:3].rstrip(".").rstrip() in (expected, expected.lower())
        verdicts.append(content_ok)
        print(f"--- {name} (expected {expected!r})")
        print(f"    HELM-recipe completion: {a['text']!r} (finish={a.get('finish_reason')})")
        print(f"    unstopped completion  : {raw!r}")
        print(f"    content after strip   : {stripped!r} -> {'CORRECT' if content_ok else 'WRONG'}")
    print()
    if all(verdicts):
        print("VERDICT: H1 (style) — content is correct behind the leading newline;")
        print("the canonical stop=['\\n'] recipe is what zeroes the score.")
    elif not any(verdicts):
        print("VERDICT: leans H2 (precision/serving) — content is wrong even")
        print("unstopped; try the fp32 ablation (catalog dtype: float32) next.")
    else:
        print("VERDICT: mixed — rerun with more cases; consider the fp32 ablation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
