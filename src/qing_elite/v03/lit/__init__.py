"""V0.3 literature pipeline (U04R).

Framework reused from the verified ``ACADEMIC_LITERATURE_PIPELINE_HANDOFF``:
search plan → metadata registry → full-text acquisition → digest → evidence ledger →
historiography → claim delta. Only the framework and field conventions are reused; the
source projects' topic data and conclusions are not.

Modules: :mod:`schema`, :mod:`registry`, :mod:`harvest`, :mod:`acquire`.
"""

from __future__ import annotations

PIPELINE_VERSION = "0.3-lit-1"
