"""Commit 13a (open-judge-plan §14.3): judge-prompt length preflight.

The preflight must render exactly the prompts that reach the judge
(excluding the empty-candidate WildBench shortcut) and size
max_model_len from actual data.
"""

from __future__ import annotations

from pathlib import Path

from judging_fixture_lib import build_wildbench_source_run, build_xstest_source_run

from eval_audit.judging import response_snapshot as snap
from eval_audit.judging.prompt_lengths import measure_prompt_lengths, render_judge_prompts


def _snapshot(tmp_path: Path, builder, **kwargs) -> Path:
    builder(tmp_path / "src", **kwargs)
    return snap.build_response_snapshot(tmp_path / "src", tmp_path / "snapshots").snapshot_dpath


def test_xstest_prompt_lengths_estimate(tmp_path: Path):
    snapshot = _snapshot(tmp_path, build_xstest_source_run)
    report = measure_prompt_lengths(snapshot, safety_margin=512)
    assert report.benchmark == "xstest"
    assert report.num_prompts == 3
    assert report.token_estimated is True
    assert report.output_budget == 256  # official safety budget
    # recommended = max prompt tokens + 256 + 512
    assert report.recommended_max_model_len == int(report.token_stats["max"]) + 256 + 512
    assert report.char_stats["max"] > 0


def test_wildbench_excludes_empty_candidate(tmp_path: Path):
    # 2 instances, index 1 is empty -> only 1 prompt reaches the judge.
    snapshot = _snapshot(tmp_path, build_wildbench_source_run, empty_output_index=1)
    benchmark, prompts = render_judge_prompts(snapshot)
    assert benchmark == "wildbench"
    assert len(prompts) == 1
    report = measure_prompt_lengths(snapshot)
    assert report.num_prompts == 1
    assert report.output_budget == 2000  # official WildBench budget
    # The rendered prompt contains the official template's checklist block.
    assert "Checklist item A0" in prompts[0]


def test_explicit_tokenizer_counter_used(tmp_path: Path):
    snapshot = _snapshot(tmp_path, build_xstest_source_run)
    # A trivial whitespace "tokenizer" to prove the counter path is taken.
    report = measure_prompt_lengths(
        snapshot, tokenizer=lambda text: len(text.split()), tokenizer_name="whitespace"
    )
    assert report.token_estimated is False
    assert report.tokenizer == "whitespace"
    # Token max equals the max whitespace-word count over the prompts.
    _, prompts = render_judge_prompts(snapshot)
    assert report.token_stats["max"] == max(len(p.split()) for p in prompts)
