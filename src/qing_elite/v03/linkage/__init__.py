"""V0.3 U06R: entity resolution for Chinese historical records.

Modules:
  ``cgedq``      frozen release access, official ``person_id`` validation, legacy crosswalk
  ``features``   pair features (name, pinyin, shape proxy, place, degree, chronology, career)
  ``matchers``   deterministic / ML / Splink scorers and their agreement pattern
  ``active``     uncertainty-based active learning with a hard small budget
  ``cluster``    union-find clustering with temporal-connectivity validation
  ``validators`` chronology / geography / career-transition validators
  ``evaluate``   held-out precision-recall, auto-accept region, abstention
  ``run``        orchestration and outputs
"""

from __future__ import annotations

UPSTREAM = {
    "repo": "bruceyyu/ML-Chinese-record-linkage",
    "commit": "79fec7e5b7c16a9bb852b00c19dd4f969881d849",
    "license": "CC BY-NC 4.0 (LICENSE.txt); README badge says CC BY-SA 4.0 — treat the stricter",
    "use": "ideas only (blocking / supervised matching / active learning / graph clustering / temporal connectivity); no code or binaries vendored",
}
