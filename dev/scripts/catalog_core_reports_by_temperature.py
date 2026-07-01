#!/usr/bin/env python3
"""Catalog core-report packets by decoding temperature (0 vs 1).

Each ``core-metrics-*`` packet under an experiment's ``analysis/core-reports/``
directory compares an official HELM run against its local reproduction. The
decoding temperature is *not* stored in the packet itself — it lives in the
per-run ``run_spec.json`` (``adapter_spec.temperature``). This tool resolves the
temperature for every packet by reading that ``run_spec.json`` via the
``run_path`` recorded in each packet's ``components_manifest.json``.

Resolution order (first hit wins), so the answer is honest about provenance:

  1. official component ``run_path/run_spec.json`` -> adapter_spec.temperature
  2. local    component ``run_path/run_spec.json`` -> adapter_spec.temperature
  3. any other component run_spec.json
  4. an explicit ``temperature=<x>`` token in the run-spec name
  5. otherwise ``unknown`` (run dirs not mounted here / local-only slice)

Because local reproductions replay the official ``run_spec.json`` verbatim
(from-spec recipe), the two sides agree by construction; either is sufficient.

Outputs a CSV (one row per packet), a grouped text summary to stdout, and
— with ``--link-dir`` — a ``temperature=0/`` / ``temperature=1/`` tree of
symlinks so the catalog is browsable on disk.

Usage:
  python dev/scripts/catalog_core_reports_by_temperature.py \
      /data/crfm-helm-audit-store/virtual-experiments/olmo-models/analysis/core-reports \
      --csv /tmp/temperature_catalog.csv
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

# Order in which a component's run_spec is trusted for the temperature fact.
_SOURCE_PRIORITY = ("official", "local")

_TEMP_IN_NAME = re.compile(r"(?:^|[,:])temperature=([0-9]*\.?[0-9]+)")


def _adapter_temperature(run_path: str | None) -> float | None:
    """Read ``adapter_spec.temperature`` from ``<run_path>/run_spec.json``."""
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
    if not isinstance(adapter, dict):
        return None
    temp = adapter.get("temperature")
    try:
        return float(temp) if temp is not None else None
    except (TypeError, ValueError):
        return None


def _temperature_from_name(name: str | None) -> float | None:
    if not name:
        return None
    match = _TEMP_IN_NAME.search(name)
    return float(match.group(1)) if match else None


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


def _bucket(temperature: float | None) -> str:
    if temperature is None:
        return "unknown"
    if temperature == 0:
        return "temperature=0"
    if temperature == 1:
        return "temperature=1"
    return f"temperature={temperature:g}"


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
    source = "unknown"
    # 1-3: prefer official, then local, then any component with a readable spec.
    ordered_kinds = list(_SOURCE_PRIORITY) + [
        kind for kind in components if kind not in _SOURCE_PRIORITY
    ]
    for kind in ordered_kinds:
        component = components.get(kind)
        if component is None:
            continue
        temp = _adapter_temperature(component.get("run_path"))
        if temp is not None:
            temperature, source = temp, f"run_spec.json:{kind}"
            break

    run_spec_name = _run_identity(packet_dpath / "core_metric_report.json", manifest)

    # 4: explicit temperature token in the run name (rare — override runs only).
    if temperature is None:
        temp = _temperature_from_name(run_spec_name)
        if temp is not None:
            temperature, source = temp, "run_spec_name"

    official = components.get("official", {})
    local = components.get("local", {})
    return {
        "packet_dir": packet_dpath.name,
        "run_spec_name": run_spec_name or "",
        "benchmark_family": (run_spec_name or packet_dpath.name).split(":", 1)[0],
        "model": local.get("model") or official.get("model") or "",
        "temperature": "" if temperature is None else f"{temperature:g}",
        "bucket": _bucket(temperature),
        "temperature_source": source,
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
        help="Create <link-dir>/<bucket>/ trees of symlinks to each packet.",
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

    # --- grouped summary -------------------------------------------------
    buckets = Counter(row["bucket"] for row in rows)
    print(f"Cataloged {len(rows)} packets under {root}\n")
    print("Temperature buckets:")
    for bucket in sorted(buckets):
        print(f"  {bucket:16} {buckets[bucket]:>4}")

    by_bucket_family: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_bucket_family[row["bucket"]][row["benchmark_family"]] += 1
    print("\nBenchmark families per bucket:")
    for bucket in sorted(by_bucket_family):
        families = ", ".join(f"{fam}×{n}" for fam, n in sorted(by_bucket_family[bucket].items()))
        print(f"  {bucket}: {families}")

    sources = Counter(row["temperature_source"] for row in rows)
    print("\nResolution provenance:")
    for source in sorted(sources):
        print(f"  {source:22} {sources[source]:>4}")

    # --- CSV -------------------------------------------------------------
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "packet_dir", "run_spec_name", "benchmark_family",
            "model", "temperature", "bucket", "temperature_source",
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
            bucket_dpath = link_root / row["bucket"]
            bucket_dpath.mkdir(parents=True, exist_ok=True)
            link = bucket_dpath / row["packet_dir"]
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(root / row["packet_dir"])
        print(f"\nWrote symlink catalog: {link_root}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
