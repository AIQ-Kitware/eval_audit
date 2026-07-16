from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        from openai import OpenAI
    except ImportError:
        print("The 'openai' package is required for this smoke check.", file=sys.stderr)
        return 2

    model_name = os.environ.get("MODEL_NAME", "Qwen/Qwen3.5-9B-Base")
    client = OpenAI(
        base_url="http://localhost:8000/v1",
        api_key="EMPTY",
    )
    # Base (pre-trained) model: exercise the legacy completions API, the same
    # surface HELM's VLLMClient uses. A chat request would exercise the chat
    # template, which a base model does not have.
    response = client.completions.create(
        model=model_name,
        prompt="The capital of France is",
        max_tokens=8,
        temperature=0.0,
    )
    text = response.choices[0].text
    print(repr(text))
    if not text.strip():
        print("Empty completion from the server.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
