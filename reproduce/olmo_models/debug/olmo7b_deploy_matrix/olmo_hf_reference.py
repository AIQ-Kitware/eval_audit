#!/usr/bin/env python3
"""Ground-truth OLMo-7B reference via HuggingFace transformers (the oracle row).

This is the non-vLLM engine axis of the deployment matrix. It loads the OLMo-7B
weights directly with transformers and greedy-decodes the SAME prompts, writing a
result JSON in the exact schema compare_deployments.py emits. Feed it into
`compare_deployments.py report --reference hf-<dtype>` so the vLLM variants are
scored against "what OLMo actually says".

Why this is the reference:
  * It is the same code path HELM's huggingface_client would use, so it is the
    most faithful local stand-in for the official together/olmo-7b behavior that
    we can run without the Together API.
  * It removes the vLLM engine from the equation, so a disagreement between a
    healthy vLLM variant and this reference isolates engine/kernel effects from
    the dtype/recipe effects the catalog already separates.

Run it once per dtype you care about, e.g. on a GPU host:

    python olmo_hf_reference.py --dtype bfloat16 --out results/hf-bf16.json
    python olmo_hf_reference.py --dtype float16  --out results/hf-fp16.json   # show the bug off-vLLM too
    python olmo_hf_reference.py --dtype float32  --device cpu \
        --out results/hf-fp32-cpu.json   # slow but engine- and GPU-independent

Requires torch + transformers (+ ai2-olmo for the non-hf `allenai/OLMo-7B`).
Greedy decoding (do_sample=False, temperature 0) to match the vLLM probes.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="allenai/OLMo-7B-hf",
                    help="HF repo id (allenai/OLMo-7B-hf | allenai/OLMo-7B | "
                         "allenai/OLMo-7B-0724-hf)")
    ap.add_argument("--revision", default=None, help="pin a specific HF revision")
    ap.add_argument("--dtype", choices=["bfloat16", "float16", "float32"],
                    default="bfloat16")
    ap.add_argument("--device", default="cuda", help="cuda | cpu")
    ap.add_argument("--trust-remote-code", action="store_true",
                    help="needed for the native allenai/OLMo-7B checkpoint")
    ap.add_argument("--prompts", default=str(Path(__file__).with_name("prompts.jsonl")))
    ap.add_argument("--max-tokens", type=int, default=60)
    ap.add_argument("--label", default=None, help="default: hf-<dtype>")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import torch  # local import so --help works without torch installed
    from transformers import AutoModelForCausalLM, AutoTokenizer

    dtypes = {"bfloat16": torch.bfloat16, "float16": torch.float16,
              "float32": torch.float32}
    label = args.label or f"hf-{args.dtype}"
    print(f"[hf-ref] loading {args.model} ({args.dtype}) on {args.device} as '{label}'")

    tok = AutoTokenizer.from_pretrained(
        args.model, revision=args.revision, trust_remote_code=args.trust_remote_code)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, revision=args.revision, torch_dtype=dtypes[args.dtype],
        trust_remote_code=args.trust_remote_code)
    model.to(args.device)
    model.eval()

    prompts = [json.loads(line) for line in Path(args.prompts).read_text().splitlines()
               if line.strip()]
    results = []
    for p in prompts:
        t0 = time.time()
        try:
            enc = tok(p["prompt"], return_tensors="pt").to(args.device)
            with torch.no_grad():
                out = model.generate(
                    **enc, max_new_tokens=args.max_tokens, do_sample=False,
                    num_beams=1, pad_token_id=tok.eos_token_id)
            gen_ids = out[0][enc["input_ids"].shape[1]:]
            text = tok.decode(gen_ids, skip_special_tokens=True)
            toks = [tok.decode([t]) for t in gen_ids.tolist()]
            r = {"completion": text, "finish_reason": "length",
                 "first_token": toks[0] if toks else "", "n_tokens": len(toks),
                 "latency_s": round(time.time() - t0, 3), "error": None}
        except Exception as exc:  # noqa: BLE001
            r = {"completion": None, "finish_reason": None, "first_token": None,
                 "n_tokens": None, "latency_s": round(time.time() - t0, 3),
                 "error": f"{type(exc).__name__}: {exc}"}
        r["id"] = p["id"]
        r["prompt"] = p["prompt"]
        results.append(r)
        snippet = (r["completion"] or r["error"] or "")[:70].replace("\n", "\\n")
        print(f"    {p['id']:<24} {snippet}")

    out = {"label": label, "model": args.model, "base_url": f"hf://{args.model}",
           "protocol": "completions", "max_tokens": args.max_tokens,
           "dtype": args.dtype, "device": args.device,
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": results}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"[hf-ref] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
