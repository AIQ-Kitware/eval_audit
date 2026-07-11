"""Host-side instance-identity comparator for ladder rung 2.

Compares the produced (era-image dry-run) ``scenario_state.json`` against the
official one: the ladder's fidelity criterion is that the era harness selects
BYTE-FOR-BYTE the same instances the official run did (instance ids + input
payloads + reference payloads). Request/result fields are deliberately ignored
— the official file carries live results, the dry-run does not; only *instance
identity* is the rung-2 claim.

Runs on the HOST with stdlib only (no repo venv needed on the GPU machine).

Usage:
    python3 instance_diff.py <official_scenario_state.json> <produced_scenario_state.json>

Exit 0 = identical instance identity (prints INSTANCES_MATCH <n>).
Exit 1 = divergence (prints a bounded unified summary of what differs).
"""
from __future__ import annotations

import json
import sys


def _instance_identity(scenario_state_path: str) -> list[str]:
    """Sorted, de-duplicated identity keys for every instance in the file.

    One key per distinct instance: id + train/eval split + canonical-JSON input
    + canonical-JSON references. Both sides are era-format files (official
    classic artifact vs era-image dry-run), so shapes agree; canonical JSON
    dumps keep the comparison byte-stable without assuming the era's exact
    Instance schema.
    """
    with open(scenario_state_path, encoding="utf-8") as fp:
        state = json.load(fp)
    keys = set()
    for rs in state.get("request_states", []):
        inst = rs.get("instance", {})
        keys.add(
            json.dumps(
                {
                    "id": inst.get("id"),
                    "split": inst.get("split"),
                    "input": inst.get("input"),
                    "references": inst.get("references"),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )
    return sorted(keys)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    official, produced = _instance_identity(argv[0]), _instance_identity(argv[1])
    if official == produced:
        print(f"INSTANCES_MATCH {len(official)}")
        return 0

    only_official = [k for k in official if k not in set(produced)]
    only_produced = [k for k in produced if k not in set(official)]
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
