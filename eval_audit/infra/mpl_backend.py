"""Force matplotlib's headless ``Agg`` backend.

Importing this module (before ``matplotlib.pyplot``) pins the non-interactive
``Agg`` backend, which has no GUI/Tk dependency.

Why this is needed: the aggregate-report path renders figures on headless run
hosts (and not always from the main thread). matplotlib's default *interactive*
backend (``TkAgg``) then tears Tk objects down on the wrong thread and crashes
the whole process with::

    RuntimeError: main thread is not in main loop
    Tcl_AsyncDelete: async handler deleted by the wrong thread
    Illegal instruction (core dumped)

``Agg`` renders straight to PNG/file with no event loop, so none of that
applies. An explicit ``MPLBACKEND`` env override is respected (set it to ``Agg``
or any non-interactive backend); otherwise we force ``Agg``.

Usage: ``import eval_audit.infra.mpl_backend  # noqa: F401`` at the top of any
module that imports ``matplotlib.pyplot``, before the pyplot import.
"""
from __future__ import annotations

import os

import matplotlib

# Respect an explicit operator override (e.g. someone exported MPLBACKEND=Agg or
# a non-interactive variant); otherwise force Agg. force=True switches even if
# pyplot was already imported elsewhere, as long as no figure has been realized.
if not os.environ.get("MPLBACKEND"):
    matplotlib.use("Agg", force=True)
