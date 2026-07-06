"""Benchmark-bundle preset catalog (PRESET_CONFIGS) + combined-OLMo composition.

Extracted verbatim from ``integrations/infer_stack/adapter.py`` (R-3, pure
relocation). Kept as Python (not YAML) because the inline comments are
load-bearing. ``adapter`` re-exports these names so existing
``from ...adapter import PRESET_CONFIGS`` call sites keep working.
"""
from __future__ import annotations

from typing import Any


PRESET_CONFIGS: dict[str, dict[str, Any]] = {
    "gpt_oss_20b_core_grid": {
        "profile": "gpt-oss-20b-completions-dp4",
        "bundle_name": "gpt_oss_20b_core_grid",
        "access_kind": "openai-compatible",
        # G2: the old `-completions` profile name encoded this; now explicit.
        # (Frozen/archival preset — no catalog entry ships for it.)
        "protocol_mode": "completions",
        "model_deployment_name": "litellm/gpt-oss-20b-local",
        "smoke_manifest": {
            "experiment_name": "audit-gpt-oss-20b-core-grid-smoke",
            "description": "Smoke-test for gpt-oss-20b on the core 14 reproducibility benchmarks.",
            "run_entries": [
                "boolq:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-gpt-oss-20b-core-grid-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-gpt-oss-20b-core-grid",
            "description": "gpt-oss-20b on the 14 core benchmarks shared with pythia-6.9b and vicuna-7b-v1.3, enabling cross-model comparison beyond the classic HELM corpus.",
            "run_entries": [
                "boolq:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=LGBTQ,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=all,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=black,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=christian,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=female,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=male,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=muslim,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=other_religions,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "civil_comments:demographic=white,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "entity_data_imputation:dataset=Buy,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "entity_data_imputation:dataset=Restaurant,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "entity_matching:dataset=Abt_Buy,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "entity_matching:dataset=Beer,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "entity_matching:dataset=Dirty_iTunes_Amazon,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "gsm:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "imdb:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "lsat_qa:task=all,method=multiple_choice_joint,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=college_chemistry,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=computer_security,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=econometrics,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "narrative_qa:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "quac:model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning:mode=induction,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning:mode=pattern_match,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning:mode=variable_substitution,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning_natural:difficulty=easy,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "synthetic_reasoning_natural:difficulty=hard,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "truthful_qa:task=mc_single,method=multiple_choice_joint,model=openai/gpt-oss-20b,data_augmentation=canonical,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=author,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=currency,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=discoverer_or_inventor,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=instance_of,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=medical_condition_treated,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=part_of,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=place_of_birth,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=plaintiff,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=position_held,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "wikifact:k=5,subject=symptoms_and_signs,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-gpt-oss-20b-core-grid",
            "max_eval_instances": 1000,
            "devices": "0,1,2,3",
            "tmux_workers": 4,
        },
    },
    "gpt_oss_20b_vllm": {
        "profile": "gpt-oss-20b-completions",
        "bundle_name": "gpt_oss_20b_vllm",
        "access_kind": "openai-compatible",
        # G2: the old `-completions` profile name encoded this; now explicit.
        # (Frozen/archival preset — no catalog entry ships for it.)
        "protocol_mode": "completions",
        "model_deployment_name": "litellm/gpt-oss-20b-local",
        "smoke_manifest": {
            "experiment_name": "audit-gpt-oss-20b-vllm-smoke",
            "description": "Smoke-test HELM batch for openai/gpt-oss-20b through the local LiteLLM-backed vLLM service.",
            "run_entries": [
                "ifeval:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-gpt-oss-20b-vllm-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-historic-grid-gpt-oss-20b-vllm-trimmed",
            "description": "Targeted in-scope historic-grid extension for openai/gpt-oss-20b using the local LiteLLM-backed vLLM service.",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "ifeval:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-historic-grid-gpt-oss-20b-vllm-trimmed",
            "max_eval_instances": 1000,
        },
    },
    "qwen2_72b_vllm": {
        "profile": "qwen2-72b-instruct-tp2-balanced",
        "bundle_name": "qwen2_72b_vllm",
        "backend": "compose",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/qwen2-72b-instruct-local",
        # Instruct/chat model (official together/qwen2-72b-instruct is a
        # TogetherChatClient) → chat protocol.
        "protocol_mode": "chat",
        "smoke_manifest": {
            "experiment_name": "audit-qwen2-72b-vllm-smoke",
            "description": "Smoke-test HELM batch for qwen/qwen2-72b-instruct through a local vLLM server.",
            "run_entries": [
                "ewok:domain=agent_properties,model=qwen/qwen2-72b-instruct",
            ],
            "suite": "audit-qwen2-72b-vllm-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-historic-grid-qwen2-72b-vllm",
            "description": "Historic-grid reproduction batch for qwen/qwen2-72b-instruct through a local vLLM server.",
            "run_entries": [
                "ewok:domain=agent_properties,model=qwen/qwen2-72b-instruct",
                "ewok:domain=material_dynamics,model=qwen/qwen2-72b-instruct",
                "ewok:domain=material_properties,model=qwen/qwen2-72b-instruct",
                "ewok:domain=physical_dynamics,model=qwen/qwen2-72b-instruct",
                "ewok:domain=physical_interactions,model=qwen/qwen2-72b-instruct",
                "ewok:domain=physical_relations,model=qwen/qwen2-72b-instruct",
                "ewok:domain=quantitative_properties,model=qwen/qwen2-72b-instruct",
                "ewok:domain=social_interactions,model=qwen/qwen2-72b-instruct",
                "ewok:domain=social_properties,model=qwen/qwen2-72b-instruct",
                "ewok:domain=social_relations,model=qwen/qwen2-72b-instruct",
                "ewok:domain=spatial_relations,model=qwen/qwen2-72b-instruct",
            ],
            "suite": "audit-historic-grid-qwen2-72b-vllm",
            "max_eval_instances": 1000,
        },
    },
    "small_models_kubeai_overnight": {
        "bundle_name": "small_models_kubeai_overnight",
        "backend": "kubeai",
        "profiles": [
            {
                "profile": "qwen2-5-7b-instruct-turbo-default",
                "model_deployment_name": "kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "helm_model_name": "qwen/qwen2.5-7b-instruct-turbo",
                "helm_tokenizer_name": "qwen/qwen2.5-7b-instruct",
                # Instruct/chat model (official together/qwen2.5-7b-instruct-turbo
                # is a TogetherChatClient) → chat protocol.
                "protocol_mode": "chat",
            },
            {
                "profile": "vicuna-7b-v1-3-no-chat-template",
                "model_deployment_name": "kubeai/vicuna-7b-v1-3-no-chat-template-local",
                "helm_model_name": "lmsys/vicuna-7b-v1.3",
                "helm_tokenizer_name": "hf-internal-testing/llama-tokenizer",
                # Deliberately served WITHOUT a chat template (the deployment and
                # profile name say so; the official huggingface/vicuna-7b-v1.3 uses
                # the llama tokenizer, which has no chat template → completion
                # behavior) → completions protocol.
                "protocol_mode": "completions",
                # Keep a small headroom margin for the live vLLM/Vicuna path, which
                # appears to need a few reserved tokens beyond HELM's nominal budget.
                "helm_max_sequence_and_generated_tokens_length": 2040,
            },
        ],
        "smoke_manifest": {
            "experiment_name": "audit-small-models-kubeai-smoke",
            "description": "Smoke-test batch for the small KubeAI-served Qwen 2.5 7B and Vicuna 7B profiles.",
            "run_entries": [
                "ifeval:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "boolq:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical,model_deployment=kubeai/vicuna-7b-v1-3-no-chat-template-local",
            ],
            "suite": "audit-small-models-kubeai-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-small-models-kubeai-overnight",
            "description": "Targeted overnight batch for the KubeAI-served Qwen 2.5 7B and Vicuna 7B profiles.",
            "run_entries": [
                "commonsense:dataset=openbookqa,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "gsm:model=qwen/qwen2.5-7b-instruct-turbo,stop=none,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "med_qa:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "narrative_qa:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=kubeai/qwen2-5-7b-instruct-turbo-default-local",
                "boolq:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical,model_deployment=kubeai/vicuna-7b-v1-3-no-chat-template-local",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical,model_deployment=kubeai/vicuna-7b-v1-3-no-chat-template-local",
                "narrative_qa:model=lmsys/vicuna-7b-v1.3,data_augmentation=canonical,model_deployment=kubeai/vicuna-7b-v1-3-no-chat-template-local",
            ],
            "suite": "audit-small-models-kubeai-overnight",
            "max_eval_instances": 1000,
        },
    },
    "finish_qwen25_gptoss": {
        # Closes the Qwen-2.5 + gpt-oss gaps surfaced by the Case Study 3
        # audit (see ``paper_draft/case_study_3_appendix.tex``):
        #   - Qwen 2.5 7B Instruct: 9 unique HELM run_entries from
        #     lite/v1.9.0 with no local repro at all (math × 7 subjects
        #     + natural_qa × 2 modes), plus reruns of the 6
        #     execution-spec-drifted benchmark families with the public
        #     adapter_spec.instructions intact.
        #   - gpt-oss 20B: 8 capabilities/v1.12.0 + safety/v1.14.0
        #     run_entries with no local repro.
        # Driven by the ``pythia-qwen25-gptoss-mixed-4x96`` profile in
        # the infer_stack submodule, which co-resides Qwen 2.5 + gpt-oss
        # alongside the two Pythia services another experiment uses on
        # the same host.
        "bundle_name": "finish_qwen25_gptoss",
        "backend": "compose",
        "infer_stack_profile": "pythia-qwen25-gptoss-mixed-4x96",
        "profiles": [
            {
                "profile": "qwen2-5-7b-instruct-turbo-default",
                "model_deployment_name": "vllm/qwen2-5-7b-instruct-turbo-local",
                "helm_model_name": "qwen/qwen2.5-7b-instruct-turbo",
                "helm_tokenizer_name": "qwen/qwen2.5-7b-instruct",
                # Instruct/chat model (official together/qwen2.5-7b-instruct-turbo
                # is a TogetherChatClient) → chat protocol.
                "protocol_mode": "chat",
            },
            {
                "profile": "gpt-oss-20b-chat",
                "model_deployment_name": "litellm/gpt-oss-20b-local",
                "helm_model_name": "openai/gpt-oss-20b",
                "helm_tokenizer_name": "openai/o200k_harmony",
                # gpt-oss needs the harmony format (applied via the chat template);
                # the official together/gpt-oss-20b is a TogetherChatClient → chat
                # protocol. (The frozen gpt_oss_20b_* presets pin completions; this
                # active preset matches the official deployment instead.)
                "protocol_mode": "chat",
            },
        ],
        "smoke_manifest": {
            "experiment_name": "audit-finish-qwen25-gptoss-smoke",
            "description": (
                "Smoke-test batch covering one Qwen 2.5 + one gpt-oss "
                "run_entry from the finish_qwen25_gptoss target list."
            ),
            "run_entries": [
                # One quick run from each model, both 5 instances.
                # Qwen smoke uses MMLU instead of MATH because the
                # MATH benchmark depends on the ``hendrycks/competition_math``
                # HF dataset which has been disabled in this preset
                # (see ``full_manifest.run_entries`` below for the
                # explanation).
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                "ifeval:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
            ],
            "suite": "audit-finish-qwen25-gptoss-smoke",
            "max_eval_instances": 5,
        },
        "full_manifest": {
            "experiment_name": "audit-finish-qwen25-gptoss",
            "description": (
                "Closes the Qwen 2.5 + gpt-oss gaps in the open-weight "
                "HELM audit (Case Study 3). Qwen rows replay the "
                "execution-spec-drifted public run_specs (with the "
                "matching adapter_spec.instructions prefix); MATH and "
                "natural_qa are disabled (data-access blockers — see "
                "notes); gpt-oss rows cover the 8 capabilities/safety "
                "entries from suite v1.12.0 / v1.14.0 with no local "
                "repro yet."
            ),
            "run_entries": [
                # ── Qwen 2.5 7B: missing benchmarks (no local repro yet)
                #
                # Two benchmark families are **disabled** in this preset
                # because their underlying datasets aren't reachable
                # cleanly from aiq-gpu today:
                #
                # 1. ``math:`` × 7 subjects (algebra,
                #    counting_and_probability, geometry,
                #    intermediate_algebra, number_theory, prealgebra,
                #    precalculus) at level=1, CoT=True. Loads the
                #    ``hendrycks/competition_math`` HuggingFace dataset.
                # 2. ``natural_qa:`` × 2 modes (closedbook,
                #    openbook_longans). HELM fetches the natural_questions
                #    dataset from a Google Storage URL that returns
                #    HTTP 403 from aiq-gpu (gated / pulled / blocked
                #    egress — observed 2026-04-30).
                #
                # Re-enable each by un-commenting its run_entries below
                # AND restoring the matching dataset name in
                # ``02_warmup_data.sh``.

                # ── Qwen 2.5 7B: rerun execution-spec-drifted families
                # The local audit previously ran these without the
                # public adapter_spec.instructions prefix that the
                # public HELM Qwen runs use; rerunning here pulls the
                # public run_spec via eval-audit-run, which carries the
                # prefix through to the locally-served model.
                # mmlu — one representative subject (like wmt_14 below;
                # HELM run naming uses subject as the only varying knob
                # for mmlu's recipe-canonical packets). Expanding to the
                # full 10-subject sweep is a deliberate coverage call for
                # the operator, not baked in here (audit D-2).
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # legalbench — one representative subset (same note as mmlu)
                "legalbench:subset=abercrombie,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # commonsense × 2
                "commonsense:dataset=openbookqa,method=multiple_choice_joint,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # gsm — was completion_content_drift; rerun with public
                # adapter_spec to verify the stop-sequence handling
                # matches now.
                "gsm:model=qwen/qwen2.5-7b-instruct-turbo,stop=none,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # med_qa
                "med_qa:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # narrative_qa
                "narrative_qa:model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",
                # wmt_14 × 10 language pairs (one representative; the
                # rest follow the same pattern and can be added as
                # HELM_EXTRA_RUN_ENTRIES if desired)
                "wmt_14:language_pair=fr-en,model=qwen/qwen2.5-7b-instruct-turbo,model_deployment=vllm/qwen2-5-7b-instruct-turbo-local",

                # ── gpt-oss 20B: missing capabilities/v1.12.0 entries
                # gpqa is a gated HF dataset (Idavidrein/gpqa) and
                # cannot be downloaded from aiq-gpu without HF
                # credentials with access to the gate. Disabled
                # 2026-04-30; re-enable by restoring this line and
                # adding ``Idavidrein/gpqa`` back to
                # 02_warmup_data.sh once credentials are in place.
                # "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",
                # Disabled 2026-04-30: external-validator scenarios.
                # These benchmarks ship HELM annotators that hardcode
                # third-party graders (openai/gpt-4o, together/llama-...)
                # via ``Annotator.auto_client.make_request(...)``. The
                # graders pull credentials from HELM's
                # ``prod_env/credentials.conf`` (or ``$HELM_CREDENTIALS``)
                # — *not* from our bundle's ``model_deployments.yaml``.
                # Since this is a *local* reproducibility audit, we don't
                # send queries to external paid APIs. Re-enable by
                # uncommenting and either dropping a ``credentials.conf``
                # at the helm-run base-path or exporting
                # ``HELM_CREDENTIALS='openaiApiKey: "sk-..."'``.
                # "omni_math:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # OmniMATHAnnotator (LLM-as-jury)
                # "wildbench:subset=v2,model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # WildBenchAnnotator → openai/gpt-4o + together/llama-3.1-405b
                # "anthropic_red_team:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # AnthropicRedTeamAnnotator
                # "harm_bench:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # HarmBenchAnnotator (LLM-as-jury)
                # "simple_safety_tests:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # SimpleSafetyTestsAnnotator
                # "xstest:model=openai/gpt-oss-20b,model_deployment=litellm/gpt-oss-20b-local",  # XSTestAnnotator
            ],
            "suite": "audit-finish-qwen25-gptoss",
            "max_eval_instances": 1000,
        },
    },
    "e2e-phi_2-vllm-philosophy": {
        "profile": "phi2-single",
        "bundle_name": "e2e-phi_2-vllm",
        "access_kind": "openai-compatible",
        "model_deployment_name": "vllm/phi-2-local",
        "profiles": [
            {
                "profile": "phi2-single",
                # The bundle keeps its NATIVE local deployment name on both paths.
                # Under --from-spec the generated manifest's `model_deployment`
                # field carries this same name (set by the exporter), and the
                # from-spec CLI rewrites the replayed run_spec.json's
                # adapter_spec.model_deployment to it — so the produced run records
                # the LOCAL endpoint and the audit reports same_deployment=no.
                # Rewrite target == registered name by construction (no drift).
                # Supersedes the by-name `from_spec_model_deployment_name` rekey;
                # see docs/historical/planning/from-spec-deployment-rewrite-plan.md Change 5.
                "model_deployment_name": "vllm/phi-2-local",
                "helm_model_name": "microsoft/phi-2",
                "helm_tokenizer_name": "microsoft/phi-2",
                "protocol_mode": "completions",
                "helm_max_sequence_and_generated_tokens_length": 2048,
            }
        ],
        "smoke_manifest": {
            "experiment_name": "e2e-phi_2-vllm-philosophy-smoke",
            "description": "Smoke-test for phi-2 on a small HELM batch.",
            "run_entries": [
                "mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft/phi-2,eval_split=test"
            ],
            "suite": "e2e-phi_2-vllm-philosophy-smoke",
            "max_eval_instances": 5,
            # From-spec recipe SOURCE (migration plan Change 2a). Only emitted into
            # the generated manifest when export-benchmark-bundle --from-spec is
            # passed; narrowed to the mmlu subtree so discovery is fast/unambiguous.
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            # Containerized HELM is mandatory (the docker pipeline pins the
            # software env). network=host so the in-container HELM client reaches
            # the host-served model (vLLM behind LiteLLM); gpus=none keeps the
            # client off the serving GPUs (it's an HTTP caller; the model's GPU is
            # leased separately). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "e2e-phi_2-vllm-philosophy-full",
            "description": "Full HELM batch for phi-2.",
            "run_entries": [
                "mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft/phi-2,eval_split=test"
            ],
            "suite": "e2e-phi_2-vllm-philosophy-full",
            "max_eval_instances": 1000,
            # From-spec recipe SOURCE (migration plan Change 2a). The official
            # adapter_spec.max_eval_instances is 10000, so this 1000 cap compares on
            # HELM's deterministic instance PREFIX, not official parity (plan §7).
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    # NOTE: the incomparable negative control deliberately carries NO from-spec
    # fields. From-spec replays the official recipe verbatim, which would erase the
    # temperature=1 deviation this scenario exists to flag, so it stays on the
    # run-entry path (migration plan Change 4 / §7). The grid's e2e_fromspec_enabled
    # also excludes it, so --from-spec is never passed for it.
    "e2e-phi_2-vllm-philosophy-incomparable": {
        "profile": "phi2-single",
        "bundle_name": "e2e-phi_2-vllm-philosophy-incomparable",
        "access_kind": "openai-compatible",
        "model_deployment_name": "vllm/phi-2-local",
        "profiles": [
            {
                "profile": "phi2-single",
                "model_deployment_name": "vllm/phi-2-local",
                "helm_model_name": "microsoft/phi-2",
                "helm_tokenizer_name": "microsoft/phi-2",
                "protocol_mode": "completions",
                "helm_max_sequence_and_generated_tokens_length": 2048,
            }
        ],
        "smoke_manifest": {
            "experiment_name": "e2e-phi_2-vllm-philosophy-incomparable-smoke",
            "description": "Smoke-test for phi-2 with temperature changed",
            "run_entries": [
                "mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft/phi-2,eval_split=test,temperature=1"
            ],
            "suite": "e2e-phi_2-vllm-philosophy-incomparable-smoke",
            "max_eval_instances": 5,
            # Containerized HELM (mandatory); see e2e-phi_2-vllm-philosophy.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "e2e-phi_2-vllm-philosophy-incomparable-full",
            "description": "Full HELM batch for phi-2 with temperature changed",
            "run_entries": [
                'mmlu:subject=philosophy,method=multiple_choice_joint,model=microsoft/phi-2,eval_split=test,temperature=1'
            ],
            "suite": "e2e-phi_2-vllm-philosophy-incomparable-full",
            "max_eval_instances": 1000,
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    # NOTE: the former ``e2e-phi_2-vllm-philosophy-container`` preset was removed
    # once containerization became mandatory for every run — it had become an
    # exact duplicate of ``e2e-phi_2-vllm-philosophy`` (whose smoke/full manifests
    # now carry container_network: host + hf_cache_dir, so it runs in-container
    # too). The image is supplied per-run via ``eval-audit-run --container-image``.
    # allenai/olmo-7b is reproduced under TWO official HELM suites, split into
    # two experiments so each from-spec discovery key resolves to exactly one
    # official run (migration plan §4.1 + the olmo-7b suite-split decision):
    #   -mmlu: the full MMLU suite (eval_split=test, /mmlu v1.1.0)
    #   -lite: HELM-Lite (commonsense/gsm/legalbench/med_qa/narrative_qa/wmt_14
    #          + the 5 curated HELM-Lite MMLU subjects, /lite v1.2.0).
    # The per-subject mmlu runs exist in BOTH suites (lite's dir name is a
    # token-subset of mmlu's), so a shared root would be ambiguous; per-suite
    # precomputed_root is what disambiguates.
    "allenai-olmo-7b-mmlu": {
        "profile": "allenai-olmo-7b-single",
        "bundle_name": "allenai-olmo-7b-mmlu",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-7b",
        # G1: HELM model/tokenizer aliases (moved out of the deleted infer_stack
        # models.yaml). Base model → completions protocol (G2).
        "helm_model_name": "allenai/olmo-7b",
        "helm_tokenizer_name": "allenai/olmo-7b",
        "protocol_mode": "completions",
        # Reserve 32 tokens below max-model-len (2048) so the live vLLM path
        # never trips its hard prompt+generation budget: HELM truncates the
        # *raw* prompt to this budget, but the served client adds a few tokens
        # (BOS / chat-template wrapper) HELM can't see. Without the reserve,
        # num_output_tokens-heavy run_entries overflow by ~1-13 tokens and vLLM
        # returns ContextWindowExceededError. Keep <= models.yaml max_model_len.
        "helm_max_sequence_and_generated_tokens_length": 2016,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-7b-mmlu-smoke",
            "description": "Smoke batch for allenai/olmo-7b (mmlu suite); from-spec replay of the official run_spec.json.",
            "run_entries": [
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
            ],
            "suite": "audit-allenai-olmo-7b-mmlu-smoke",
            "max_eval_instances": 5,
            # From-spec recipe SOURCE (migration plan Change 1): the official
            # run_spec.json is discovered under this root. mmlu
            # suite only, so the per-subject mmlu runs that exist in BOTH suites
            # resolve unambiguously (the other suite's dir is not under this root).
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container HELM client reaches the
            # host-served model (vLLM behind LiteLLM); gpus=none keeps HELM off
            # the serving GPUs (MC/exact-match entries, no local-HF judge).
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-7b-mmlu-full",
            "description": "Full batch for allenai/olmo-7b (mmlu suite); from-spec replay of the official run_spec.json.",
            "run_entries": [
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=anatomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=astronomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=business_ethics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=clinical_knowledge,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_biology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_chemistry,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_computer_science,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_medicine,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=college_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=computer_security,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=conceptual_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=econometrics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=electrical_engineering,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=elementary_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=formal_logic,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=global_facts,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_biology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_chemistry,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_computer_science,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_european_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_geography,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_government_and_politics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_macroeconomics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_microeconomics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_psychology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_statistics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_us_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=high_school_world_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=human_aging,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=human_sexuality,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=international_law,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=jurisprudence,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=logical_fallacies,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=machine_learning,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=management,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=marketing,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=medical_genetics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=miscellaneous,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=moral_disputes,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=moral_scenarios,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=nutrition,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=philosophy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=prehistory,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=professional_accounting,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=professional_law,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=professional_medicine,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=professional_psychology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=public_relations,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=security_studies,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=sociology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=virology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
                "mmlu:subject=world_religions,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-7b",
            ],
            "suite": "audit-allenai-olmo-7b-mmlu-full",
            "max_eval_instances": 1000,
            # From-spec recipe SOURCE (migration plan Change 1): the official
            # run_spec.json is discovered under this root. mmlu
            # suite only, so the per-subject mmlu runs that exist in BOTH suites
            # resolve unambiguously (the other suite's dir is not under this root).
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container HELM client reaches the
            # host-served model (vLLM behind LiteLLM); gpus=none keeps HELM off
            # the serving GPUs (MC/exact-match entries, no local-HF judge).
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-7b-lite": {
        "profile": "allenai-olmo-7b-single",
        "bundle_name": "allenai-olmo-7b-lite",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-7b",
        # G1: HELM model/tokenizer aliases (moved out of the deleted infer_stack
        # models.yaml). Base model → completions protocol (G2).
        "helm_model_name": "allenai/olmo-7b",
        "helm_tokenizer_name": "allenai/olmo-7b",
        "protocol_mode": "completions",
        # Reserve 32 tokens below max-model-len (2048) so the live vLLM path
        # never trips its hard prompt+generation budget: HELM truncates the
        # *raw* prompt to this budget, but the served client adds a few tokens
        # (BOS / chat-template wrapper) HELM can't see. Without the reserve,
        # num_output_tokens-heavy run_entries overflow by ~1-13 tokens and vLLM
        # returns ContextWindowExceededError. Keep <= models.yaml max_model_len.
        "helm_max_sequence_and_generated_tokens_length": 2016,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-7b-lite-smoke",
            "description": "Smoke batch for allenai/olmo-7b (lite suite); from-spec replay of the official run_spec.json.",
            "run_entries": [
                "commonsense:method=multiple_choice_joint,dataset=openbookqa,model=allenai/olmo-7b",
                # Canary (promoted from full_manifest): wmt_14 loads a HF dataset
                # whose id resolution is huggingface_hub-version sensitive (the
                # "wmt-14 isn't a valid HF dataset" failure) and scores via sacrebleu
                # from crfm-helm[metrics]. Keeping it in the SMOKE set makes a stale
                # or mis-built ([heim]-only / floated-hub) runner image fail this
                # cheap preflight instead of deep in the full grid.
                "wmt_14:language_pair=fr-en,model=allenai/olmo-7b",
            ],
            "suite": "audit-allenai-olmo-7b-lite-smoke",
            "max_eval_instances": 5,
            # From-spec recipe SOURCE (migration plan Change 1): the official
            # run_spec.json is discovered under this root. lite
            # suite only, so the per-subject mmlu runs that exist in BOTH suites
            # resolve unambiguously (the other suite's dir is not under this root).
            "precomputed_root": "/data/crfm-helm-public/lite",
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container HELM client reaches the
            # host-served model (vLLM behind LiteLLM); gpus=none keeps HELM off
            # the serving GPUs (MC/exact-match entries, no local-HF judge).
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-7b-lite-full",
            "description": "Full batch for allenai/olmo-7b (lite suite); from-spec replay of the official run_spec.json.",
            "run_entries": [
                "commonsense:method=multiple_choice_joint,dataset=openbookqa,model=allenai/olmo-7b",
                "gsm:model=allenai/olmo-7b",
                "legalbench:subset=abercrombie,model=allenai/olmo-7b",
                "legalbench:subset=corporate_lobbying,model=allenai/olmo-7b",
                "legalbench:subset=function_of_decision_section,model=allenai/olmo-7b",
                "legalbench:subset=international_citizenship_questions,model=allenai/olmo-7b",
                "legalbench:subset=proa,model=allenai/olmo-7b",
                "med_qa:model=allenai/olmo-7b",
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,model=allenai/olmo-7b",
                "mmlu:subject=college_chemistry,method=multiple_choice_joint,model=allenai/olmo-7b",
                "mmlu:subject=computer_security,method=multiple_choice_joint,model=allenai/olmo-7b",
                "mmlu:subject=econometrics,method=multiple_choice_joint,model=allenai/olmo-7b",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,model=allenai/olmo-7b",
                "narrative_qa:model=allenai/olmo-7b",
                "wmt_14:language_pair=cs-en,model=allenai/olmo-7b",
                "wmt_14:language_pair=de-en,model=allenai/olmo-7b",
                "wmt_14:language_pair=fr-en,model=allenai/olmo-7b",
                "wmt_14:language_pair=hi-en,model=allenai/olmo-7b",
                "wmt_14:language_pair=ru-en,model=allenai/olmo-7b",
            ],
            "suite": "audit-allenai-olmo-7b-lite-full",
            "max_eval_instances": 1000,
            # From-spec recipe SOURCE (migration plan Change 1): the official
            # run_spec.json is discovered under this root. lite
            # suite only, so the per-subject mmlu runs that exist in BOTH suites
            # resolve unambiguously (the other suite's dir is not under this root).
            "precomputed_root": "/data/crfm-helm-public/lite",
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container HELM client reaches the
            # host-served model (vLLM behind LiteLLM); gpus=none keeps HELM off
            # the serving GPUs (MC/exact-match entries, no local-HF judge).
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-2-0325-32b-instruct": {
        "profile": "allenai-olmo-2-0325-32b-instruct-single",
        "bundle_name": "allenai-olmo-2-0325-32b-instruct",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-2-0325-32b-instruct",
        # G1: HELM aliases. Instruct model → chat protocol (G2, explicit).
        "helm_model_name": "allenai/olmo-2-0325-32b-instruct",
        "helm_tokenizer_name": "allenai/olmo-2-0325-32b-instruct",
        "protocol_mode": "chat",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        # The chat-template wrapper adds ~12 tokens HELM doesn't count, so the
        # num_output_tokens=2048 run_entries (gpqa/mmlu_pro/ifeval) overflow
        # 4096 without it.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-2-0325-32b-instruct-smoke",
            "description": "Smoke-test batch for allenai/olmo-2-0325-32b-instruct; run_entries from candidate_runs.json.",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-0325-32b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-0325-32b-instruct-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-2-0325-32b-instruct-full",
            "description": "Full local-reproduction batch for allenai/olmo-2-0325-32b-instruct; run_entries sourced from candidate_runs.json. Includes 1 gated-dataset run(s) (require HuggingFace auth).",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-0325-32b-instruct",
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-0325-32b-instruct",
                "ifeval:num_output_tokens=2048,model=allenai/olmo-2-0325-32b-instruct",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-0325-32b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-0325-32b-instruct-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-1-7-7b": {
        "profile": "allenai-olmo-1-7-7b-single",
        "bundle_name": "allenai-olmo-1-7-7b",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-1-7-7b",
        # G1: tokenizer alias is case-sensitive and non-obvious (the "-hf"
        # conversion repo), distinct from the lowercase HELM model alias.
        # Base model → completions protocol (G2).
        "helm_model_name": "allenai/olmo-1.7-7b",
        "helm_tokenizer_name": "allenai/OLMo-1.7-7B-hf",
        "protocol_mode": "completions",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-1-7-7b-smoke",
            "description": "Smoke-test batch for allenai/olmo-1.7-7b; run_entries from candidate_runs.json.",
            "run_entries": [
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
            ],
            "suite": "audit-allenai-olmo-1-7-7b-smoke",
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-1-7-7b-full",
            "description": "Full local-reproduction batch for allenai/olmo-1.7-7b; run_entries sourced from candidate_runs.json.",
            "run_entries": [
                "mmlu:subject=abstract_algebra,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=anatomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=astronomy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=business_ethics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=clinical_knowledge,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_biology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_chemistry,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_computer_science,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_medicine,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=college_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=computer_security,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=conceptual_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=econometrics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=electrical_engineering,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=elementary_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=formal_logic,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=global_facts,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_biology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_chemistry,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_computer_science,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_european_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_geography,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_government_and_politics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_macroeconomics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_mathematics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_microeconomics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_physics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_psychology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_statistics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_us_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=high_school_world_history,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=human_aging,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=human_sexuality,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=international_law,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=jurisprudence,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=logical_fallacies,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=machine_learning,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=management,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=marketing,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=medical_genetics,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=miscellaneous,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=moral_disputes,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=moral_scenarios,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=nutrition,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=philosophy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=prehistory,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=professional_accounting,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=professional_law,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=professional_medicine,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=professional_psychology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=public_relations,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=security_studies,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=sociology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=us_foreign_policy,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=virology,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
                "mmlu:subject=world_religions,method=multiple_choice_joint,eval_split=test,model=allenai/olmo-1.7-7b",
            ],
            "suite": "audit-allenai-olmo-1-7-7b-full",
            "precomputed_root": "/data/crfm-helm-public/mmlu",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-2-1124-13b-instruct": {
        "profile": "allenai-olmo-2-1124-13b-instruct-single",
        "bundle_name": "allenai-olmo-2-1124-13b-instruct",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-2-1124-13b-instruct",
        # G1: the 13B reuses the 7B tokenizer alias (intentional, not a typo).
        # Instruct model → chat protocol (G2, explicit).
        "helm_model_name": "allenai/olmo-2-1124-13b-instruct",
        "helm_tokenizer_name": "allenai/olmo-2-1124-7b-instruct",
        "protocol_mode": "chat",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-2-1124-13b-instruct-smoke",
            "description": "Smoke-test batch for allenai/olmo-2-1124-13b-instruct; run_entries from candidate_runs.json.",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-1124-13b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-1124-13b-instruct-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-2-1124-13b-instruct-full",
            "description": "Full local-reproduction batch for allenai/olmo-2-1124-13b-instruct; run_entries sourced from candidate_runs.json. Includes 1 gated-dataset run(s) (require HuggingFace auth).",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-1124-13b-instruct",
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-13b-instruct",
                "ifeval:num_output_tokens=2048,model=allenai/olmo-2-1124-13b-instruct",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-13b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-1124-13b-instruct-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmo-2-1124-7b-instruct": {
        "profile": "allenai-olmo-2-1124-7b-instruct-single",
        "bundle_name": "allenai-olmo-2-1124-7b-instruct",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmo-2-1124-7b-instruct",
        # G1: HELM aliases. Instruct model → chat protocol (G2, explicit).
        "helm_model_name": "allenai/olmo-2-1124-7b-instruct",
        "helm_tokenizer_name": "allenai/olmo-2-1124-7b-instruct",
        "protocol_mode": "chat",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmo-2-1124-7b-instruct-smoke",
            "description": "Smoke-test batch for allenai/olmo-2-1124-7b-instruct; run_entries from candidate_runs.json.",
            "run_entries": [
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
                # Canary (promoted from full_manifest): ifeval's metric imports
                # langdetect, which lives only in crfm-helm[metrics]/[cleva] — a
                # [heim]-built runner image dies here with "ModuleNotFoundError:
                # langdetect". One ifeval entry in the SMOKE set catches a
                # mis-built/stale image on this cheap preflight, not the full grid.
                # (The container env is shared across models, so a single instruct
                # canary suffices for the whole grid.)
                "ifeval:num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-1124-7b-instruct-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmo-2-1124-7b-instruct-full",
            "description": "Full local-reproduction batch for allenai/olmo-2-1124-7b-instruct; run_entries sourced from candidate_runs.json. Includes 1 gated-dataset run(s) (require HuggingFace auth).",
            "run_entries": [
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmo-2-1124-7b-instruct",
                "ifeval:num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmo-2-1124-7b-instruct",
            ],
            "suite": "audit-allenai-olmo-2-1124-7b-instruct-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
    "allenai-olmoe-1b-7b-0125-instruct": {
        "profile": "allenai-olmoe-1b-7b-0125-instruct-single",
        "bundle_name": "allenai-olmoe-1b-7b-0125-instruct",
        "access_kind": "vllm-direct",
        "model_deployment_name": "vllm/allenai-olmoe-1b-7b-0125-instruct",
        # G1: HELM aliases. Instruct model → chat protocol (G2, explicit).
        "helm_model_name": "allenai/olmoe-1b-7b-0125-instruct",
        "helm_tokenizer_name": "allenai/olmoe-1b-7b-0125-instruct",
        "protocol_mode": "chat",
        # 32-token reserve below max-model-len (4096); see allenai-olmo-7b.
        # OLMoE's chat template adds ~13 tokens HELM doesn't count.
        "helm_max_sequence_and_generated_tokens_length": 4064,
        "smoke_manifest": {
            "experiment_name": "audit-allenai-olmoe-1b-7b-0125-instruct-smoke",
            "description": "Smoke-test batch for allenai/olmoe-1b-7b-0125-instruct; run_entries from candidate_runs.json.",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmoe-1b-7b-0125-instruct",
            ],
            "suite": "audit-allenai-olmoe-1b-7b-0125-instruct-smoke",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 5,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
        "full_manifest": {
            "experiment_name": "audit-allenai-olmoe-1b-7b-0125-instruct-full",
            "description": "Full local-reproduction batch for allenai/olmoe-1b-7b-0125-instruct; run_entries sourced from candidate_runs.json. Includes 1 gated-dataset run(s) (require HuggingFace auth).",
            "run_entries": [
                "bbq:subject=all,method=multiple_choice_joint,max_train_instances=0,model=allenai/olmoe-1b-7b-0125-instruct",
                "gpqa:subset=gpqa_main,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmoe-1b-7b-0125-instruct",
                "ifeval:num_output_tokens=2048,model=allenai/olmoe-1b-7b-0125-instruct",
                "mmlu_pro:subject=all,use_chain_of_thought=true,use_few_shot=false,num_output_tokens=2048,model=allenai/olmoe-1b-7b-0125-instruct",
            ],
            "suite": "audit-allenai-olmoe-1b-7b-0125-instruct-full",
            "precomputed_root": "/data/crfm-helm-public",
            "max_eval_instances": 1000,
            # Containerized HELM ("docker pipeline") is mandatory; the grid passes
            # `eval-audit-run --container-image "$OLMO_CONTAINER_IMAGE"`.
            # network=host so the in-container
            # HELM client reaches the host-served model (vLLM behind LiteLLM);
            # gpus=none keeps HELM off the serving GPUs (these MC/exact-match
            # entries have no local-HF judge model). See docs/container-execution.md.
            "container_network": "host",
            "hf_cache_dir": "~/.cache/eval-audit-hf",
            "container_gpus": "none",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Combined multi-model OLMo preset (parent-root fan-out)
# ─────────────────────────────────────────────────────────────────────────────
# A single MULTI-deployment bundle spanning the five OLMo models whose official
# runs all resolve under the shared parent root ``/data/crfm-helm-public``
# (olmo-1.7-7b + the four OLMo-2 / OLMoE instruct models). It exists so the OLMo
# grid can fan out across GPUs *under one schedule*: export it with
# ``--freeze-rel-paths`` and run ``eval-audit-run --tmux-workers N`` — cmd_queue
# then issues N concurrent per-run leases and infer-stack co-hosts what fits /
# serializes the rest across the pool (olmo-multi-model-from-spec-plan.md §4.4,
# §4.7). The seven single-model presets above are LEFT INTACT; this one REUSES
# their profile facts and run_entries verbatim (one source of truth — no
# transcription drift) and only appends the inline ``model_deployment=<local>``
# token each frozen source needs as its per-run rewrite target + lease key.
#
# Why these five and not olmo-7b: the olmo-7b ``-mmlu`` / ``-lite`` presets share
# their per-subject MMLU run dirs across the ``/mmlu`` and ``/lite`` suites, so the
# broad parent root is AMBIGUOUS for them — they keep the narrow per-suite roots
# and stay single-model. olmo-1.7-7b's MMLU resolves 57/57 with 0 AMBIGUOUS under
# the parent root, so the five share one root cleanly (plan §4.4).
#
# REQUIRES the exact-path exporter (``--freeze-rel-paths``): a multi-deployment
# bundle has no single manifest-level rewrite target, so the plain ``--from-spec``
# *discovery* path can't place it. The freeze path strips each local token for
# discovery (``_strip_local_deployment`` — local-only) and reuses it as that run's
# rewrite target (``_freeze_run_spec_sources``). Grid wiring (a runbook target +
# ``--tmux-workers``) and the host preflight's matching local-strip are the
# remaining steps (plan §4.3, §4.7) — this preset is the exporter half.
_OLMO_COMBINED_PRESET_KEYS = (
    "allenai-olmo-1-7-7b",
    "allenai-olmo-2-1124-7b-instruct",
    "allenai-olmo-2-1124-13b-instruct",
    "allenai-olmoe-1b-7b-0125-instruct",
    "allenai-olmo-2-0325-32b-instruct",
)


def _inline_local_deployment(run_entries: list[str], deployment: str) -> list[str]:
    """Suffix each from-spec run-entry with an inline ``model_deployment=<local>``.

    A multi-deployment from-spec bundle can't infer which local server a run uses
    from a bare discovery key, so each entry names its LOCAL rewrite target inline
    (the run-entry multi-model convention). ``_freeze_run_spec_sources`` strips the
    token for discovery (``_strip_local_deployment`` — local-only) and reuses it as
    that source's per-run rewrite target and ``lease_endpoints`` key. See
    docs/historical/planning/olmo-multi-model-from-spec-plan.md §4.4. (R-8: delegates to the
    shared append helper.)
    """
    from eval_audit.helm.run_entries import append_model_deployment

    return [append_model_deployment(entry, deployment) for entry in run_entries]


def _olmo_combined_run_entries(mode: str) -> list[str]:
    """Union of the five parent-root presets' ``{mode}_manifest`` run_entries, each
    suffixed with its own inline local deployment token (same model order as the
    ``profiles`` list, so the frozen sources and ``lease_endpoints`` map line up)."""
    entries: list[str] = []
    for key in _OLMO_COMBINED_PRESET_KEYS:
        cfg = PRESET_CONFIGS[key]
        entries.extend(
            _inline_local_deployment(
                cfg[f"{mode}_manifest"]["run_entries"], cfg["model_deployment_name"]
            )
        )
    return entries


PRESET_CONFIGS["allenai-olmo-combined"] = {
    "bundle_name": "allenai-olmo-combined",
    # Native access is vLLM-direct like the singles; the grid overrides with
    # --access-kind openai-compatible (the LiteLLM gateway) at export time.
    "access_kind": "vllm-direct",
    # One profile per model, pulled from each single-model preset so the HELM
    # aliases / protocol mode / token-budget reserve stay defined in exactly one
    # place. Order is load-bearing: _lease_facts zips profiles <-> serving facts to
    # build the lease_endpoints {deployment: endpoint} map.
    "profiles": [
        {
            "profile": PRESET_CONFIGS[key]["profile"],
            "access_kind": PRESET_CONFIGS[key]["access_kind"],
            "model_deployment_name": PRESET_CONFIGS[key]["model_deployment_name"],
            "helm_model_name": PRESET_CONFIGS[key]["helm_model_name"],
            "helm_tokenizer_name": PRESET_CONFIGS[key]["helm_tokenizer_name"],
            "protocol_mode": PRESET_CONFIGS[key]["protocol_mode"],
            "helm_max_sequence_and_generated_tokens_length": PRESET_CONFIGS[key][
                "helm_max_sequence_and_generated_tokens_length"
            ],
        }
        for key in _OLMO_COMBINED_PRESET_KEYS
    ],
    "smoke_manifest": {
        "experiment_name": "audit-allenai-olmo-combined-smoke",
        "description": (
            "Smoke batch for the combined multi-model OLMo bundle: each model's "
            "own smoke run_entries (all five endpoints exercised, incl. the "
            "ifeval/langdetect container canary); from-spec exact-path replay under "
            "the shared parent root. Run with --tmux-workers N to fan the models "
            "across GPUs under one schedule."
        ),
        "run_entries": _olmo_combined_run_entries("smoke"),
        "suite": "audit-allenai-olmo-combined-smoke",
        # Shared parent root — every model's runs resolve here (0 AMBIGUOUS).
        "precomputed_root": "/data/crfm-helm-public",
        "max_eval_instances": 5,
        # Containerized HELM is mandatory (the grid passes --container-image); host
        # network so the in-container client reaches the host-served vLLM, gpus=none
        # keeps HELM off the serving GPUs (each model's GPU is leased separately).
        "container_network": "host",
        "hf_cache_dir": "~/.cache/eval-audit-hf",
        "container_gpus": "none",
    },
    "full_manifest": {
        "experiment_name": "audit-allenai-olmo-combined-full",
        "description": (
            "Full local-reproduction batch for the combined multi-model OLMo "
            "bundle: the union of the five parent-root presets' full run_entries "
            "(olmo-1.7-7b MMLU x 57 + the four instruct models' "
            "bbq/gpqa/ifeval/mmlu_pro), each replayed from its official "
            "run_spec.json via exact-path freeze. Includes gated-dataset runs "
            "(gpqa requires HuggingFace auth). Run with --tmux-workers N to fan "
            "across GPUs."
        ),
        "run_entries": _olmo_combined_run_entries("full"),
        "suite": "audit-allenai-olmo-combined-full",
        "precomputed_root": "/data/crfm-helm-public",
        "max_eval_instances": 1000,
        "container_network": "host",
        "hf_cache_dir": "~/.cache/eval-audit-hf",
        "container_gpus": "none",
    },
}
