"""Host-side instance-identity comparator for ladder rung 2.

The ladder's fidelity criterion is that the era harness selects and constructs
BYTE-FOR-BYTE the same evaluated instances the official run did. We compare the
identity key ``(instance_id, train_trial_index, prompt)`` for every evaluated
request:

  * official side  — the classic public artifact ``display_requests.json``
    (a list; the corpus does NOT ship ``scenario_state.json``, so this is the
    only published per-instance record). Each entry has ``instance_id``,
    ``train_trial_index`` and ``request.prompt``.
  * produced side  — the era-image dry-run ``scenario_state.json`` (a dict with
    ``request_states``); each request state has ``instance.id``,
    ``train_trial_index`` and ``request.prompt``.

Why the prompt (not input+references)? The public corpus does not publish
instance input/reference payloads anywhere (``scenario.json`` carries only
metadata). The request PROMPT is a strict superset: it embeds the instance
input, the selected in-context (few-shot) examples, and the exact formatting,
after model-specific window truncation — so prompt equality proves instance
selection AND prompt construction fidelity at once. Result/response fields are
ignored (the dry-run has none).

Shape-detecting so either file may be passed on either side (a list => display
records; a dict with ``request_states`` => scenario state).

Runs on the HOST with stdlib only (no repo venv needed on the GPU machine).

Usage:
    python3 instance_diff.py <official_display_requests.json> <produced_scenario_state.json>

Exit 0 = identical instance identity (prints INSTANCES_MATCH <n>).
Exit 1 = divergence (prints a bounded unified summary of what differs).
"""
from __future__ import annotations

import json
import sys


def _identity_keys(path: str) -> list[str]:
    """Sorted, de-duplicated ``(instance_id, train_trial_index, prompt)`` keys.

    Detects the file shape: a JSON list is a ``display_requests.json`` record
    set; a JSON object with ``request_states`` is a ``scenario_state.json``.
    """
    with open(path, encoding="utf-8") as fp:
        doc = json.load(fp)

    records = []
    if isinstance(doc, list):  # display_requests.json
        for entry in doc:
            records.append(
                (
                    entry.get("instance_id"),
                    entry.get("train_trial_index"),
                    (entry.get("request") or {}).get("prompt"),
                )
            )
    elif isinstance(doc, dict) and "request_states" in doc:  # scenario_state.json
        for rs in doc.get("request_states", []):
            records.append(
                (
                    (rs.get("instance") or {}).get("id"),
                    rs.get("train_trial_index"),
                    (rs.get("request") or {}).get("prompt"),
                )
            )
    else:
        raise ValueError(
            f"{path}: unrecognized shape (expected a display_requests list or a "
            "scenario_state object with 'request_states')"
        )

    keys = {
        json.dumps(
            {"id": inst_id, "train_trial_index": trial, "prompt": prompt},
            sort_keys=True,
            ensure_ascii=False,
        )
        for inst_id, trial, prompt in records
    }
    return sorted(keys)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    official, produced = _identity_keys(argv[0]), _identity_keys(argv[1])
    if official == produced:
        print(f"INSTANCES_MATCH {len(official)}")
        return 0

    produced_set, official_set = set(produced), set(official)
    only_official = [k for k in official if k not in produced_set]
    only_produced = [k for k in produced if k not in official_set]
    print(
        f"INSTANCES_DIVERGE official={len(official)} produced={len(produced)} "
        f"only_official={len(only_official)} only_produced={len(only_produced)}"
    )
    for label, items in (("<official-only>", only_official), ("<produced-only>", only_produced)):
        for key in items[:5]:
            print(f"  {label} {key[:240]}")
        if len(items) > 5:
            print(f"  {label} ... and {len(items) - 5} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
