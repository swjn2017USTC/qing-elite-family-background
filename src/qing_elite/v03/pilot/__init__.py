"""V0.3 U05R pilot: frame sampling, deterministic extraction, verification and scoring.

Modules:
  ``frame``      stratified, reproducible pilot frame (source × cohort × region × credential)
  ``rules``      deterministic roster parser (rules before models)
  ``structured`` CBDB structured records → V0.3 fact rows with evidence
  ``ocr``        PaddleOCR-VL runner wrapper (external venv, page cache)
  ``verifier``   automatic evidence checks and risk scoring
  ``evaluate``   field-level precision/recall/abstention against a small gold set
"""

from __future__ import annotations

PILOT_VERSION = "0.3-u05r-1"
