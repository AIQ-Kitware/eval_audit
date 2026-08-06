"""Raw HELM filesystem readers.

Discovery-only: these classes walk a HELM ``benchmark_output/`` tree and
yield run directories. They are intentionally limited to raw filesystem
ingestion for the official/local index builders; comparison and report
code paths consume :mod:`eval_audit.normalized` instead.

Do not add comparison logic here. If you need to read HELM JSONs in a
comparison context, route through :func:`eval_audit.normalized.load_run`
so source_kind / artifact_format / Origin are populated.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class _SuiteRun:
    path: str

    @property
    def name(self) -> str:
        return Path(self.path).name


class HelmSuite:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.name = self.path.name

    def runs(self, pattern: str = "*") -> Iterable[_SuiteRun]:
        for run_dpath in sorted(self.path.iterdir()):
            if not run_dpath.is_dir():
                continue
            if not fnmatch.fnmatch(run_dpath.name, pattern):
                continue
            if not (run_dpath / "run_spec.json").exists():
                continue
            yield _SuiteRun(path=str(run_dpath))


class HelmOutputs:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)

    @classmethod
    def coerce(cls, data: Any) -> "HelmOutputs":
        if isinstance(data, cls):
            return data
        return cls(data)

    def suites(self, pattern: str = "*") -> Iterable[HelmSuite]:
        runs_dpath = self.root_dir / "runs"
        if not runs_dpath.exists():
            return []
        suites = []
        for suite_dpath in sorted(runs_dpath.iterdir()):
            if not suite_dpath.is_dir():
                continue
            if not fnmatch.fnmatch(suite_dpath.name, pattern):
                continue
            suites.append(HelmSuite(suite_dpath))
        return suites
