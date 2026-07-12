#!/usr/bin/env python3
"""Tiny query tool over ``docker/eras.yaml`` for ``build.sh``.

``build.sh`` is a POSIX-shell script and cannot parse YAML natively; this helper
exposes exactly the fields the era build path needs, and nothing more. It
depends only on the stdlib + PyYAML (present in the repo venv and most dev
Pythons). Keep it dependency-light: it must NOT import ``eval_audit`` (the era
build stages a clean context and does not install the superproject).

Usage:
    read_eras.py <eras.yaml> --list
    read_eras.py <eras.yaml> <era_key> <field>

Fields: helm_git_ref, python_version, constraints, helm_extras, image_name,
capability. Exit non-zero (with a message on stderr) on an unknown era/field so
``build.sh`` can ``die`` cleanly.
"""
from __future__ import annotations

import sys

_FIELDS = {
    "helm_git_ref",
    "python_version",
    "constraints",
    "helm_extras",
    "image_name",
    "capability",
}


def _load(path: str) -> dict:
    import yaml

    with open(path, "r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp)
    if not isinstance(data, dict) or not isinstance(data.get("eras"), dict):
        raise SystemExit(f"{path}: not a mapping with an 'eras' key")
    return data["eras"]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    eras = _load(argv[0])

    if argv[1] == "--list":
        for key in eras:
            print(key)
        return 0

    if len(argv) < 3:
        print("usage: read_eras.py <eras.yaml> <era_key> <field>", file=sys.stderr)
        return 2
    era_key, field = argv[1], argv[2]
    if era_key not in eras:
        print(
            f"unknown era {era_key!r}; known: {', '.join(eras) or '<none>'}",
            file=sys.stderr,
        )
        return 3
    if field not in _FIELDS:
        print(f"unknown field {field!r}; known: {', '.join(sorted(_FIELDS))}", file=sys.stderr)
        return 4
    spec = eras[era_key]
    if field == "helm_extras":
        value = spec.get(field, "all")
    elif field == "capability":
        value = spec.get(field, "era-shim-from-spec")
    else:
        value = spec.get(field)
    if value is None:
        print(f"era {era_key!r} has no field {field!r}", file=sys.stderr)
        return 5
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
