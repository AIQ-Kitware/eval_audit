#!/usr/bin/env python3
"""Catalog core-report packets by decoding recipe: chain-of-thought and temperature.

Each ``core-metrics-*`` packet under an experiment's ``analysis/core-reports/``
directory compares an official HELM run against its local reproduction. The two
recipe facts we catalog here — whether the scenario is chain-of-thought (CoT)
and its decoding temperature — are *not* stored in the packet itself; they live
in the per-run ``run_spec.json`` (``adapter_spec.method`` / ``adapter_spec.temperature``).
This tool resolves both for every packet by reading that ``run_spec.json`` via
the ``run_path`` recorded in each packet's ``components_manifest.json``.

  * CoT: ``adapter_spec.method`` containing ``chain_of_thought`` (e.g.
    ``multiple_choice_joint_chain_of_thought``) -> CoT; any other method -> no-CoT.
  * temperature: ``adapter_spec.temperature`` (0 vs 1).

Resolution order (first hit wins), so the answer is provenance-honest:

  1. official component ``run_path/run_spec.json``
  2. local    component ``run_path/run_spec.json``
  3. any other component run_spec.json
  4. an explicit ``use_chain_of_thought=`` / ``method=`` / ``temperature=`` token
     in the run-spec name (or packet dir name)
  5. otherwise ``unknown`` (run dirs not mounted here / local-only slice)

Because local reproductions replay the official ``run_spec.json`` verbatim
(from-spec recipe), the two sides agree by construction; either is sufficient.
The headline output documents each *benchmark* (deduplicated across models and
across official/local), e.g. ``mmlu_pro: CoT, temperature=1``.

Outputs a per-benchmark recipe summary and a per-packet CSV to stdout/disk, and
— with ``--link-dir`` — a ``<recipe>/`` tree of symlinks so the catalog is
browsable on disk.

Usage:
  python dev/scripts/catalog_core_reports_by_temperature.py \
      /data/crfm-helm-audit-store/virtual-experiments/olmo-models/analysis/core-reports \
      --csv /tmp/recipe_catalog.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# Order in which a component's run_spec is trusted for recipe facts.
_SOURCE_PRIORITY = ("official", "local")

# Accept both the ``key=value`` form (run-spec names) and the ``key-value``
# form (slugified packet dir names) as token separators.
_TEMP_IN_NAME = re.compile(r"(?:^|[,:_-])temperature[=-]([0-9]*\.?[0-9]+)")
_COT_FLAG_IN_NAME = re.compile(r"use_chain_of_thought[=-](true|false)")
_METHOD_IN_NAME = re.compile(r"method[=-]([a-z_]+)")


def _read_adapter_spec(run_path: str | None) -> dict[str, Any] | None:
    """Return ``adapter_spec`` from ``<run_path>/run_spec.json``, or None."""
    if not run_path:
        return None
    spec_fpath = Path(run_path) / "run_spec.json"
    if not spec_fpath.is_file():
        return None
    try:
        spec = json.loads(spec_fpath.read_text(encoding="utf-8"))
    except Exception:
        return None
    adapter = spec.get("adapter_spec") if isinstance(spec, dict) else None
    return adapter if isinstance(adapter, dict) else None


def _temperature_from_adapter(adapter: dict[str, Any]) -> float | None:
    temp = adapter.get("temperature")
    try:
        return float(temp) if temp is not None else None
    except (TypeError, ValueError):
        return None


def _cot_from_adapter(adapter: dict[str, Any]) -> bool | None:
    """CoT-ness from ``adapter_spec.method`` (the authoritative signal)."""
    method = adapter.get("method")
    if not isinstance(method, str) or not method:
        return None
    return "chain_of_thought" in method


def _temperature_from_name(name: str | None) -> float | None:
    if not name:
        return None
    match = _TEMP_IN_NAME.search(name)
    return float(match.group(1)) if match else None


def _cot_from_name(name: str | None) -> bool | None:
    if not name:
        return None
    flag = _COT_FLAG_IN_NAME.search(name)
    if flag:
        return flag.group(1) == "true"
    method = _METHOD_IN_NAME.search(name)
    if method:
        return "chain_of_thought" in method.group(1)
    return None


def _run_identity(report_fpath: Path, manifest: dict[str, Any]) -> str | None:
    """Best available human-readable run-spec name for a packet."""
    if report_fpath.is_file():
        try:
            report = json.loads(report_fpath.read_text(encoding="utf-8"))
            if report.get("run_spec_name"):
                return report["run_spec_name"]
        except Exception:
            pass
    if manifest.get("run_entry"):
        return manifest["run_entry"]
    for component in manifest.get("components", []):
        for key in ("run_entry", "run_name", "run_spec_name"):
            if component.get(key):
                return component[key]
    return None


def _cot_label(cot: bool | None) -> str:
    if cot is True:
        return "CoT"
    if cot is False:
        return "no-CoT"
    return "unknown"


def _temperature_display(temperature: float | None) -> str:
    return "" if temperature is None else f"{temperature:g}"


def _bucket(temperature: float | None) -> str:
    if temperature is None:
        return "temperature=unknown"
    return f"temperature={temperature:g}"


def _recipe_label(cot: bool | None, temperature: float | None) -> str:
    temp = _temperature_display(temperature) or "unknown"
    return f"{_cot_label(cot)}, temperature={temp}"


def resolve_packet(packet_dpath: Path) -> dict[str, Any] | None:
    """Resolve one packet to a catalog row, or None if it isn't a packet."""
    manifest_fpath = packet_dpath / "components_manifest.json"
    if not manifest_fpath.is_file():
        return None
    try:
        manifest = json.loads(manifest_fpath.read_text(encoding="utf-8"))
    except Exception:
        manifest = {}

    components = {
        component.get("source_kind"): component
        for component in manifest.get("components", [])
    }

    temperature: float | None = None
    temperature_source = "unknown"
    cot: bool | None = None
    cot_source = "unknown"

    # 1-3: prefer official, then local, then any component with a readable spec.
    # CoT and temperature are read from the same run_spec.json, but resolved
    # independently so a spec missing one field can still supply the other.
    ordered_kinds = list(_SOURCE_PRIORITY) + [
        kind for kind in components if kind not in _SOURCE_PRIORITY
    ]
    for kind in ordered_kinds:
        component = components.get(kind)
        if component is None:
            continue
        adapter = _read_adapter_spec(component.get("run_path"))
        if adapter is None:
            continue
        if temperature is None:
            temp = _temperature_from_adapter(adapter)
            if temp is not None:
                temperature, temperature_source = temp, f"run_spec.json:{kind}"
        if cot is None:
            flag = _cot_from_adapter(adapter)
            if flag is not None:
                cot, cot_source = flag, f"run_spec.json:{kind}"
        if temperature is not None and cot is not None:
            break

    run_spec_name = _run_identity(packet_dpath / "core_metric_report.json", manifest)

    # 4: fall back to tokens in the run name / packet dir name.
    name_candidates = [run_spec_name, packet_dpath.name]
    if temperature is None:
        for candidate in name_candidates:
            temp = _temperature_from_name(candidate)
            if temp is not None:
                temperature, temperature_source = temp, "run_name"
                break
    if cot is None:
        for candidate in name_candidates:
            flag = _cot_from_name(candidate)
            if flag is not None:
                cot, cot_source = flag, "run_name"
                break

    official = components.get("official", {})
    local = components.get("local", {})
    return {
        "packet_dir": packet_dpath.name,
        "run_spec_name": run_spec_name or "",
        "benchmark_family": (run_spec_name or packet_dpath.name).split(":", 1)[0],
        "model": local.get("model") or official.get("model") or "",
        "cot": _cot_label(cot),
        "temperature": _temperature_display(temperature),
        "recipe": _recipe_label(cot, temperature),
        "bucket": _bucket(temperature),
        "cot_source": cot_source,
        "temperature_source": temperature_source,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "core_reports_dir",
        type=Path,
        help="Path to an experiment's analysis/core-reports/ directory.",
    )
    parser.add_argument("--csv", type=Path, default=None, help="Write the per-packet catalog CSV here.")
    parser.add_argument(
        "--link-dir",
        type=Path,
        default=None,
        help="Create <link-dir>/<recipe>/ trees of symlinks to each packet.",
    )
    args = parser.parse_args(argv)

    root = args.core_reports_dir.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    rows = []
    for packet_dpath in sorted(p for p in root.iterdir() if p.is_dir()):
        row = resolve_packet(packet_dpath)
        if row is not None:
            rows.append(row)

    if not rows:
        print(f"No core-report packets found under {root}", file=sys.stderr)
        return 1

    print(f"Cataloged {len(rows)} packets under {root}\n")

    # --- headline: per-benchmark recipe ----------------------------------
    # Deduplicated across models and across official/local: one line per
    # benchmark family. A family with mixed recipes (e.g. some packets whose
    # run dirs aren't mounted, so temperature is unknown) lists each variant.
    by_family: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        temp = row["temperature"] or "unknown"
        by_family[row["benchmark_family"]][(row["cot"], temp)] += 1
    print("Per-benchmark recipe (deduplicated across models and official/local):")
    for family in sorted(by_family):
        variants = by_family[family]
        if len(variants) == 1:
            (cot, temp), _ = next(iter(variants.items()))
            print(f"  {family}: {cot}, temperature={temp}")
        else:
            parts = "; ".join(
                f"{cot}, temperature={temp} ({count})"
                for (cot, temp), count in sorted(variants.items())
            )
            print(f"  {family}: {parts}")

    # --- counts ----------------------------------------------------------
    recipe_counts = Counter(row["recipe"] for row in rows)
    print("\nRecipe buckets (packets):")
    for recipe in sorted(recipe_counts):
        print(f"  {recipe:32} {recipe_counts[recipe]:>4}")

    sources = Counter(f"cot={row['cot_source']} temp={row['temperature_source']}" for row in rows)
    print("\nResolution provenance (packets):")
    for source in sorted(sources):
        print(f"  {source:48} {sources[source]:>4}")

    # --- CSV -------------------------------------------------------------
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "packet_dir", "run_spec_name", "benchmark_family", "model",
            "cot", "temperature", "recipe", "bucket",
            "cot_source", "temperature_source",
        ]
        with args.csv.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote CSV: {args.csv}")

    # --- symlink tree ----------------------------------------------------
    if args.link_dir:
        link_root = args.link_dir.expanduser().resolve()
        for row in rows:
            slug = row["recipe"].replace(", ", "__").replace(" ", "_")
            bucket_dpath = link_root / slug
            bucket_dpath.mkdir(parents=True, exist_ok=True)
            link = bucket_dpath / row["packet_dir"]
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(root / row["packet_dir"])
        print(f"\nWrote symlink catalog: {link_root}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
