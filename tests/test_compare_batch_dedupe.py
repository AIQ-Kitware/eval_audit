"""Unit tests for compare_batch re-attempt deduplication (audit item 10a)."""
from __future__ import annotations

from eval_audit.workflows.compare_batch import _dedupe_kwdg_rows


def _row(name: str, dpath: str, mtime: float) -> dict:
    return {"run_spec_name": name, "dpath": dpath, "done_mtime": mtime}


def test_dedupe_prefers_newest_done_mtime():
    rows = [
        _row("boolq:model=x", "/jobs/a", 100.0),
        _row("boolq:model=x", "/jobs/b", 300.0),  # newest
        _row("boolq:model=x", "/jobs/c", 200.0),
        _row("mmlu:model=y", "/jobs/d", 50.0),
    ]
    lut, dup_count, dup_keys = _dedupe_kwdg_rows(rows)
    assert lut["boolq:model=x"]["dpath"] == "/jobs/b"
    assert lut["mmlu:model=y"]["dpath"] == "/jobs/d"
    # Two of the three boolq attempts are shadowed; one key had duplicates.
    assert dup_count == 2
    assert dup_keys == 1


def test_dedupe_tiebreak_is_deterministic_on_dpath():
    # Same mtime -> deterministic max on dpath (independent of input order).
    rows_a = [
        _row("k", "/jobs/aaa", 100.0),
        _row("k", "/jobs/zzz", 100.0),
    ]
    rows_b = list(reversed(rows_a))
    lut_a, _, _ = _dedupe_kwdg_rows(rows_a)
    lut_b, _, _ = _dedupe_kwdg_rows(rows_b)
    assert lut_a["k"]["dpath"] == lut_b["k"]["dpath"] == "/jobs/zzz"


def test_dedupe_no_duplicates():
    rows = [_row("a", "/j/1", 1.0), _row("b", "/j/2", 2.0)]
    lut, dup_count, dup_keys = _dedupe_kwdg_rows(rows)
    assert set(lut) == {"a", "b"}
    assert dup_count == 0
    assert dup_keys == 0
