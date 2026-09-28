"""V0.3 U07R: evidence-aware Qing kinship graph.

Modules:
  ``graph``      canonical tables + NetworkX view + structural validators
  ``indicators`` family-capital indicators with numerator/denominator/observability/lineage
  ``coverage``   source-specific and cohort-specific coverage reports
  ``run``        orchestration and outputs

NetworkX is a *compute* layer only: the canonical truth stays in Parquet tables, and no
graph object is ever persisted as the source of record.
"""

from __future__ import annotations

ONTOLOGY_VERSION = "relations-v2"
