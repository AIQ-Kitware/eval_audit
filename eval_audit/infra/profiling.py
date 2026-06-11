"""Shared ``@profile`` decorator shim for opt-in line profiling.

``@profile`` is a zero-overhead no-op unless ``LINE_PROFILE=1`` is set
in the environment, in which case ``line_profiler`` swaps in a real
profiler. That lets us leave decorators on hot functions in production
without any runtime cost. Falls back to an identity wrapper when
line_profiler isn't installed at all (so fresh checkouts don't break
before someone runs ``uv pip install line_profiler``).

Usage::

    from eval_audit.infra.profiling import profile

    @profile
    def hot_function(...):
        ...

This module replaced fourteen identical inline copies of the
try/except shim (2026-06-11); import from here instead of redefining.
"""
from __future__ import annotations

try:
    from line_profiler import profile  # type: ignore[import-not-found]
except ImportError:
    def profile(func):  # type: ignore[no-redef]
        return func


__all__ = ["profile"]
