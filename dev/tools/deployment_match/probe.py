"""OpenAI-compatible probe for one grid cell.

Sends the oracle's prompts to a running endpoint, replaying the official recipe
(max_tokens / temperature / stop) verbatim and applying the cell's request-time
knobs. The important non-standard knob is ``add_special_tokens``: vLLM honors it
on ``/v1/completions`` and the LiteLLM gateway forwards it, but HELM's own client
does NOT set it — so this direct probe is how we can actually A/B it. Stdlib only.

Result schema (consumed by ``score`` / ``report``)::

    {"cell_id", "endpoint", "request": {add_special_tokens, protocol},
     "results": [{instance_id, completion, first_token, finish_reason,
                  n_tokens, latency_s, error}, ...]}
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


def _post(url: str, payload: dict[str, Any], api_key: str | None, timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _recipe_body(recipe: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if recipe.get("max_tokens") is not None:
        body["max_tokens"] = recipe["max_tokens"]
    body["temperature"] = recipe.get("temperature", 0) or 0
    stop = recipe.get("stop_sequences")
    if stop:
        body["stop"] = stop
    if recipe.get("top_p") is not None:
        body["top_p"] = recipe["top_p"]
    return body


def query_one(base_url: str, model: str, prompt: str, *, protocol: str,
              recipe: dict[str, Any], add_special_tokens: bool | None,
              api_key: str | None, timeout: float,
              add_generation_prompt: bool | None = None) -> dict[str, Any]:
    base = base_url.rstrip("/")
    t0 = time.time()
    try:
        if protocol == "chat":
            body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                    **_recipe_body(recipe)}
            if add_special_tokens is not None:
                body["add_special_tokens"] = add_special_tokens
            # add_generation_prompt=False reproduces an old chat template that
            # didn't append the assistant turn (vLLM defaults it True).
            if add_generation_prompt is not None:
                body["add_generation_prompt"] = add_generation_prompt
            resp = _post(f"{base}/chat/completions", body, api_key, timeout)
            choice = resp["choices"][0]
            text = choice["message"]["content"]
            tokens: list[str] = []
        else:
            body = {"model": model, "prompt": prompt, "logprobs": 1, "echo": False,
                    **_recipe_body(recipe)}
            if add_special_tokens is not None:
                body["add_special_tokens"] = add_special_tokens
            resp = _post(f"{base}/completions", body, api_key, timeout)
            choice = resp["choices"][0]
            text = choice.get("text", "")
            tokens = list((choice.get("logprobs") or {}).get("tokens") or [])
        first = tokens[0] if tokens else (text.strip().split(" ")[:1] or [""])[0]
        return {"completion": text, "finish_reason": choice.get("finish_reason"),
                "first_token": first, "n_tokens": len(tokens) if tokens else None,
                "latency_s": round(time.time() - t0, 3), "error": None}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400] if exc.fp else str(exc)
        return _err(f"HTTP {exc.code}: {detail}", t0)
    except Exception as exc:  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}", t0)


def _err(msg: str, t0: float) -> dict[str, Any]:
    return {"completion": None, "finish_reason": None, "first_token": None,
            "n_tokens": None, "latency_s": round(time.time() - t0, 3), "error": msg}


def query_cell(base_url: str, cell: dict[str, Any], sample: list[dict[str, Any]],
               recipe: dict[str, Any], *, api_key: str | None = None,
               timeout: float = 120.0, progress: bool = True) -> dict[str, Any]:
    """Run every sampled prompt through one cell; return its result doc."""
    endpoint = cell["endpoint"]
    rq = cell["request"]
    results = []
    for s in sample:
        r = query_one(base_url, endpoint, s["prompt"], protocol=rq["protocol"],
                      recipe=recipe, add_special_tokens=rq.get("add_special_tokens"),
                      add_generation_prompt=rq.get("add_generation_prompt"),
                      api_key=api_key, timeout=timeout)
        r["instance_id"] = s["instance_id"]
        results.append(r)
        if progress:
            import sys
            snip = (r["completion"] or r["error"] or "")[:60].replace("\n", "\\n")
            print(f"    {s['instance_id']:<20} {snip}", file=sys.stderr)
    return {"cell_id": cell["cell_id"], "endpoint": endpoint, "request": rq,
            "results": results}
