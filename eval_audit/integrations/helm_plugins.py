"""HELM entry-point plugins for eval_audit local-execution runs.

``helm-run`` calls ``load_entry_point_plugins(group="helm")`` *after* it
registers its built-in and ``prod_env`` configs (see ``helm.benchmark.run.main``),
so anything registered here overrides the matching built-in entries —
``register_tokenizer_config`` overwrites by name (a dict assignment). Because
``eval_audit`` is installed in the same environment as ``helm-run`` (it provides
the ``eval-audit-*`` console scripts), declaring a ``[project.entry-points.helm]``
entry pointing at this module is enough: HELM imports it eagerly, triggering the
registration side effects below. No edits to the vendored HELM submodule are
needed.

After changing this file or its entry-point declaration, reinstall the package
(``uv pip install -e .``) so the entry-point metadata is refreshed.
"""

from __future__ import annotations

from helm.benchmark.tokenizer_config_registry import (
    TokenizerConfig,
    TokenizerSpec,
    register_tokenizer_config,
)


def register_tokenizer_overrides() -> None:
    """Override HELM tokenizer aliases that don't load under local execution."""
    # HELM's built-in tokenizer config for the ``allenai/olmo-7b`` alias points at
    # the original allenai/OLMo-7B repo with ``trust_remote_code`` (the custom
    # hf_olmo tokenizer, which needs the ``ai2-olmo`` package). Under HELM's
    # ``local_files_only`` load the remote tokenizer code is unavailable, so
    # transformers falls back to a base fast class with no ``tokenizer.json`` and
    # raises "Couldn't instantiate the backend tokenizer". Repoint the alias at the
    # transformers-native allenai/OLMo-7B-hf tokenizer (same vocab, real
    # ``tokenizer.json``, no remote code) — the repo vLLM already serves.
    register_tokenizer_config(
        TokenizerConfig(
            name="allenai/olmo-7b",
            tokenizer_spec=TokenizerSpec(
                class_name="helm.tokenizers.huggingface_tokenizer.HuggingFaceTokenizer",
                args={"pretrained_model_name_or_path": "allenai/OLMo-7B-hf"},
            ),
            end_of_text_token="<|endoftext|>",
            prefix_token="",
        )
    )


# Import-time side effect: HELM only imports the entry point, it does not call it.
register_tokenizer_overrides()
