"""Static era-import checker for the helm_era_shim package (ladder tier 0).

The shim runs inside era images against OLD helm APIs (v0.2.4 / v0.3.0), so no
host test can import it against the real target. This checker closes that gap
WITHOUT docker: it AST-parses every ``helm.*`` import in the shim source and
verifies, via ``git show <era_ref>:<module path>`` against the helm submodule,
that the imported module exists and defines each imported symbol AT EVERY ERA
in docker/eras.yaml.

Imports wrapped in ``try: ... except ImportError:`` are skipped — that is the
sanctioned pattern for symbols that moved between eras (each alternative is
allowed to fail at one era by construction).

KNOWN_BAD entries are review findings
(docs/planning/era-pinned-review-findings-2026-07-10.md) awaiting their fix:
they xfail with the finding number so the suite stays green while any NEW
era-incompatible import fails loudly. Remove each entry with its fix (the fix
wraps the import in try/except, so the parameter disappears from collection).
"""
from __future__ import annotations

import ast
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from eval_audit.eras import load_era_registry
from eval_audit.infra.paths import repo_root

_SHIM_DIR = Path(__file__).resolve().parent.parent / "docker" / "era_shim" / "helm_era_shim"
_HELM_SUBMODULE = Path(__file__).resolve().parent.parent / "submodules" / "helm"

#: (module, symbol, era_key) -> reason. Review findings awaiting fixes.
KNOWN_BAD: dict[tuple[str, str, str], str] = {
    ("helm.common.request", "wrap_request_time", "helm-v0.2.4"): (
        "Finding 3: wrap_request_time lives in helm.proxy.clients.client at "
        "v0.2.4; needs a try/except import fallback"
    ),
    ("helm.benchmark.huggingface_registration", "register_huggingface_hub_model_from_flag_value", "helm-v0.2.4"): (
        "Finding 8: module absent at v0.2.4 (helm.proxy.clients."
        "huggingface_model_registry there); needs version dispatch"
    ),
    ("helm.benchmark.huggingface_registration", "register_huggingface_local_model_from_flag_value", "helm-v0.2.4"): (
        "Finding 8: module absent at v0.2.4; needs version dispatch"
    ),
}


def _guarded_line_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    """Line ranges of try-bodies whose handlers catch ImportError-ish types."""
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            names: set[str] = set()
            t = handler.type
            if t is None:
                names.add("Exception")  # bare except guards imports too
            elif isinstance(t, ast.Name):
                names.add(t.id)
            elif isinstance(t, ast.Tuple):
                names.update(e.id for e in t.elts if isinstance(e, ast.Name))
            if names & {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}:
                end = max(getattr(stmt, "end_lineno", stmt.lineno) for stmt in node.body)
                ranges.append((node.lineno, end))
                break
    return ranges


def _collect_helm_imports() -> list[tuple[str, str, str, str]]:
    """Every unguarded ``helm.*`` import in the shim: (file, module, symbol, loc)."""
    out: list[tuple[str, str, str, str]] = []
    for py in sorted(_SHIM_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        guarded = _guarded_line_ranges(tree)

        def _is_guarded(lineno: int) -> bool:
            return any(lo <= lineno <= hi for lo, hi in guarded)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and (
                node.module == "helm" or node.module.startswith("helm.")
            ):
                if _is_guarded(node.lineno):
                    continue
                for alias in node.names:
                    out.append((py.name, node.module, alias.name, f"{py.name}:{node.lineno}"))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "helm" or alias.name.startswith("helm."):
                        if not _is_guarded(node.lineno):
                            out.append((py.name, alias.name, "", f"{py.name}:{node.lineno}"))
    return out


@lru_cache(maxsize=None)
def _era_module_source(ref: str, module: str) -> str | None:
    """Source of ``module`` at era commit ``ref``, or None if it doesn't exist."""
    rel = "src/" + module.replace(".", "/")
    for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
        proc = subprocess.run(
            ["git", "-C", str(_HELM_SUBMODULE), "show", f"{ref}:{candidate}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout
    return None


def _symbol_defined(source: str, symbol: str) -> bool:
    sym = re.escape(symbol)
    patterns = (
        rf"^(?:async\s+)?def\s+{sym}\b",
        rf"^class\s+{sym}\b",
        rf"^{sym}\s*[:=]",
        rf"^from\s+\S+\s+import\s+.*\b{sym}\b",  # re-export
        rf"^import\s+.*\b{sym}\b",
        rf"^\s{{4}}{sym},?\s*$",  # name on its own line inside a paren import
    )
    return any(re.search(p, source, re.MULTILINE) for p in patterns)


def _params():
    registry = load_era_registry(repo_root() / "docker" / "eras.yaml")
    imports = _collect_helm_imports()
    assert imports, "no helm imports found in the shim — checker misconfigured?"
    for era in registry.values():
        for fname, module, symbol, loc in imports:
            yield pytest.param(
                era.key,
                era.helm_git_ref,
                module,
                symbol,
                id=f"{era.key}:{module}.{symbol or '<module>'}@{loc}",
            )


@pytest.mark.parametrize("era_key, era_ref, module, symbol", list(_params()))
def test_shim_import_resolves_at_era(era_key: str, era_ref: str, module: str, symbol: str):
    if (module, symbol, era_key) in KNOWN_BAD:
        pytest.xfail(KNOWN_BAD[(module, symbol, era_key)])

    source = _era_module_source(era_ref, module)
    assert source is not None, (
        f"shim imports {module!r}, which does not exist at era {era_key} "
        f"({era_ref}). Wrap the import in try/except ImportError with the era "
        "alternative, or drop it."
    )
    if symbol and symbol != "*":
        assert _symbol_defined(source, symbol), (
            f"shim imports {symbol!r} from {module!r}, which does not define it "
            f"at era {era_key} ({era_ref}). It may live elsewhere at this era — "
            "use a try/except import fallback."
        )
