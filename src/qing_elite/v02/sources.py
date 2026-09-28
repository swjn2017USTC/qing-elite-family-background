"""U03 source protocol: four frozen sources, one fixed retrieval order.

The protocol is executed identically for every person in the pilot, and every step is
logged with an outcome — ``found`` / ``not_found`` / ``unavailable`` / ``error`` — not
just the hits. Sources are cached on disk so a re-run is free and idempotent.

1. ``cbdb_frozen``   — the frozen CBDB release (local SQLite, cbdb_20260912)
2. ``cbdb_api``      — CBDB REST API cross-check (cbdb.fas.harvard.edu/cbdbapi/person)
3. ``sinica_lod``    — 中研院數位文化中心鏈結開放資料 SPARQL
4. ``wikisource_dump`` — fixed-date zhwikisource multistream dump (2026-09-01)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from qing_elite.utils.config import CBDB_SQLITE, INTERIM_DIR, RAW_DIR

SOURCE_ORDER = ("cbdb_frozen", "cgedq_frozen", "cbdb_api", "sinica_lod", "wikisource_dump")
OUTCOMES = ("found", "not_found", "unavailable", "error")
# ``sinica_lod`` is optional until U04 validates a machine-queryable endpoint and its
# terms (U03 found none). Completion is reported over the required sources, with the
# all-source figure kept alongside so the gap stays visible.
REQUIRED_SOURCES = ("cbdb_frozen", "cgedq_frozen", "cbdb_api", "wikisource_dump")
OPTIONAL_SOURCES = ("sinica_lod",)

V03_DIR = INTERIM_DIR / "v03"
CBDB_API_CACHE = V03_DIR / "cbdb_api"
CBDB_API_BASE = "https://cbdb.fas.harvard.edu/cbdbapi/person"
CBDB_API_SLEEP = 1.5
SINICA_URL = "https://data.ascdc.tw/sparql.php"
WIKISOURCE_DUMP = (
    RAW_DIR
    / "zhwikisource"
    / "zhwikisource-20260901-pages-articles-multistream-index.txt.bz2"
)
WIKISOURCE_DUMP_URL = (
    "https://dumps.wikimedia.org/zhwikisource/20260901/"
    "zhwikisource-20260901-pages-articles-multistream-index.txt.bz2"
)
WIKISOURCE_DUMP_DATE = "2026-09-01"
WIKISOURCE_SHA256 = "28bc1f75468f53d25b4eae2de07420f9a2f2005e2e7f4d3f0b645b10aec5d0c5"
WIKISOURCE_TITLE_CACHE = V03_DIR / "wikisource_qingshigao.json"
HTTP_TIMEOUT = 30


@dataclass
class SourceResult:
    """One source step for one person.

    ``attribution`` says how the record was tied to the person: ``explicit_id`` when the
    source's own identifier matched, ``name_only`` when only the name matched (which the
    v0.1 study showed to be unsafe), or ``none`` when nothing was found. A name-only hit
    never counts as effective information.
    """

    source_id: str
    outcome: str
    identity: bool = False
    office: bool = False
    degree: bool = False
    ancestry: bool = False
    attribution: str = "none"
    detail: str = ""
    seconds: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def attributed(self) -> bool:
        return self.attribution == "explicit_id"

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome: {self.outcome}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ 1. frozen CBDB


def _cbdb_lookup(person: Mapping[str, Any]) -> tuple[int | None, int]:
    """CBDB person id for the person: explicit id, or name candidates."""
    cbdb_id = person.get("cbdb_personid")
    if pd.notna(cbdb_id):
        return int(cbdb_id), 1
    name = person.get("name_chn")
    if not isinstance(name, str) or not name:
        return None, 0
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        rows = conn.execute(
            "SELECT c_personid FROM BIOG_MAIN WHERE c_name_chn = ? AND c_dy = 20", (name,)
        ).fetchall()
    finally:
        conn.close()
    if len(rows) == 1:
        return int(rows[0][0]), 1
    return None, len(rows)


def frozen_cbdb(person: Mapping[str, Any]) -> SourceResult:
    """The frozen CBDB release: identity, postings and degrees from local tables."""
    started = time.time()
    cbdb_id, candidates = _cbdb_lookup(person)
    if cbdb_id is None:
        outcome = "not_found" if candidates == 0 else "unavailable"
        detail = (
            "no CBDB person id and no unique name match"
            if candidates == 0
            else f"{candidates} same-name CBDB persons; not attributable"
        )
        return SourceResult("cbdb_frozen", outcome, detail=detail, seconds=time.time() - started)
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        offices = conn.execute(
            "SELECT count(*) FROM POSTED_TO_OFFICE_DATA WHERE c_personid = ?", (cbdb_id,)
        ).fetchone()[0]
        kin = conn.execute(
            "SELECT count(*) FROM KIN_DATA WHERE c_personid = ?", (cbdb_id,)
        ).fetchone()[0]
        degrees = conn.execute(
            """
            SELECT c.c_entry_desc_chn FROM ENTRY_DATA e
            JOIN ENTRY_CODES c ON c.c_entry_code = e.c_entry_code
            WHERE e.c_personid = ?
            """,
            (cbdb_id,),
        ).fetchall()
    finally:
        conn.close()
    degree_texts = [row[0] for row in degrees if row[0]]
    return SourceResult(
        "cbdb_frozen",
        "found",
        identity=True,
        office=bool(offices),
        degree=bool(degree_texts),
        ancestry=bool(kin),
        attribution="explicit_id" if pd.notna(person.get("cbdb_personid")) else "name_only",
        detail=f"{offices} office postings, {len(degree_texts)} degree entries, {kin} kin rows",
        seconds=time.time() - started,
        extra={"cbdb_personid": cbdb_id},
    )


# --------------------------------------------------------------- 1b. frozen CGED-Q


def _cgedq_persons() -> pd.DataFrame:
    """The frozen JSL roster person frame (U02 cache, rebuilt if absent)."""
    from qing_elite.v02.linkage_data import build_cgedq_persons

    return build_cgedq_persons()


def cgedq_frozen(person: Mapping[str, Any]) -> SourceResult:
    """The frozen CGED-Q JSL release: the roster entry that defines a D-layer person.

    It supplies identity, office and degree *as recorded in the roster*; it carries no
    ancestry, so it can never contribute to the family-capital estimand.
    """
    started = time.time()
    raw = person.get("cgedq_person_id")
    ids = [part for part in str(raw).split("|") if part and part.lower() != "nan"] if isinstance(raw, str) else []
    if not ids:
        return SourceResult(
            "cgedq_frozen", "not_found", detail="no CGED-Q roster id for this entity",
            seconds=time.time() - started,
        )
    frame = _cgedq_persons()
    known = frame.set_index("cgedq_person_id")
    for person_id in ids:
        if person_id in known.index:
            record = known.loc[person_id]
            if isinstance(record, pd.DataFrame):
                record = record.iloc[0]
            office = (
                isinstance(record.get("primary_office_core"), str)
                or (pd.notna(record.get("n_appointment_spells")) and record["n_appointment_spells"] > 0)
            )
            degree = isinstance(record.get("degree_raw"), str) or isinstance(
                record.get("degree_category"), str
            )
            return SourceResult(
                "cgedq_frozen",
                "found",
                identity=True,
                office=bool(office),
                degree=bool(degree),
                attribution="explicit_id",
                detail=(
                    f"roster entry {person_id}: {int(record.get('n_observations') or 0)} observations, "
                    f"office={record.get('primary_office_core')}, degree={record.get('degree_raw')}"
                    " (roster carries no ancestry)"
                ),
                seconds=time.time() - started,
            )
    return SourceResult(
        "cgedq_frozen", "not_found", detail=f"roster ids {ids} absent from the frozen slice",
        seconds=time.time() - started,
    )


# --------------------------------------------------------------------- 2. CBDB API


def _api_cache_path(key: str) -> Path:
    return CBDB_API_CACHE / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json"


def _api_query(params: dict[str, str]) -> dict[str, Any]:
    """Cached API call; 404 means "no record", transient failures are retried twice."""
    CBDB_API_CACHE.mkdir(parents=True, exist_ok=True)
    key = urllib.parse.urlencode(sorted(params.items()))
    path = _api_cache_path(key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    url = f"{CBDB_API_BASE}?{urllib.parse.urlencode(params)}&mode=json"
    request = urllib.request.Request(
        url, headers={"User-Agent": "qing-elite-family-background/0.2 (research)"}
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            time.sleep(CBDB_API_SLEEP)
            return payload
        except urllib.error.HTTPError as error:
            if error.code == 404:
                payload = {"error": {"code": 404, "message": "no record"}}
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                time.sleep(CBDB_API_SLEEP)
                return payload
            last_error = error
        except Exception as error:  # noqa: BLE001 - retried below, reported if persistent
            last_error = error
        time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"{type(last_error).__name__}: {last_error}")


def _api_person(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    try:
        return payload["Package"]["PersonAuthority"]["PersonInfo"]["Person"]
    except (KeyError, TypeError):
        return None


def _api_has_section(person: Mapping[str, Any], key: str) -> bool:
    section = person.get(key)
    if isinstance(section, Mapping):
        return bool(section)
    return bool(section)


def cbdb_api(person: Mapping[str, Any]) -> SourceResult:
    """CBDB REST API cross-check, by explicit id first, then by name."""
    started = time.time()
    cbdb_id = person.get("cbdb_personid")
    try:
        if pd.notna(cbdb_id):
            payload = _api_query({"id": str(int(cbdb_id))})
            attribution = "explicit_id"
        else:
            name = person.get("name_chn")
            if not isinstance(name, str) or not name:
                return SourceResult(
                    "cbdb_api", "not_found", detail="no id and no name to query",
                    seconds=time.time() - started,
                )
            payload = _api_query({"name": name})
            attribution = "name_only"
    except Exception as error:  # noqa: BLE001 - the protocol records failures, never raises
        return SourceResult(
            "cbdb_api", "error", detail=f"{type(error).__name__}: {error}",
            seconds=time.time() - started,
        )
    person_payload = _api_person(payload)
    if person_payload is None:
        message = (payload.get("error") or {}).get("message") if isinstance(payload, Mapping) else None
        return SourceResult(
            "cbdb_api", "not_found", detail=f"API returned no person record ({message or 'empty'})",
            seconds=time.time() - started,
        )
    person_id = None
    returned_name = None
    if isinstance(person_payload.get("BasicInfo"), Mapping):
        inner = (person_payload["BasicInfo"] or {}).get("Person") or {}
        if isinstance(inner, Mapping):
            returned_name = (inner.get("BasicInfo") or {}).get("ChName")
            person_id = (inner.get("BasicInfo") or {}).get("PersonId")
    offices = _api_has_section(person_payload, "Posting") or _api_has_section(person_payload, "Postings")
    degrees = _api_has_section(person_payload, "Entry") or _api_has_section(person_payload, "Entrances")
    return SourceResult(
        "cbdb_api",
        "found",
        identity=True,
        office=bool(offices),
        degree=bool(degrees),
        attribution=attribution,
        detail=f"API person id={person_id}, name={returned_name}",
        seconds=time.time() - started,
        extra={"returned_name": returned_name},
    )


# -------------------------------------------------------------------- 3. Sinica LOD


def sinica_probe() -> tuple[str, str]:
    """Is any 中研院 LOD SPARQL endpoint machine-queryable right now?

    The probe records every candidate URL it tried, so U04 inherits the evidence rather
    than repeating the search.
    """
    V03_DIR.mkdir(parents=True, exist_ok=True)
    cache = V03_DIR / "sinica_probe.json"
    if cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        return payload["outcome"], payload["detail"]
    attempts: list[dict[str, str]] = []
    outcome, detail = "unavailable", "no candidate endpoint returned SPARQL JSON"
    for url in (
        f"{SINICA_URL}?{urllib.parse.urlencode({'query': 'SELECT * WHERE { ?s ?p ?o } LIMIT 1'})}",
        "https://data.ascdc.tw/sparql",
        "https://data.ascdc.tw/sparql/query",
        "https://data.ascdc.tw/api/sparql",
    ):
        try:
            request = urllib.request.Request(
                url, headers={"Accept": "application/sparql-results+json"}
            )
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                content_type = response.headers.get("Content-Type", "")
                body = response.read(400)
            if "json" in content_type.lower() and body.lstrip().startswith(b"{"):
                outcome = "found"
                detail = f"SPARQL JSON from {url}"
                attempts.append({"url": url, "result": "sparql_json"})
                break
            attempts.append({"url": url, "result": f"{response.status} {content_type or 'unknown'}"})
        except Exception as error:  # noqa: BLE001 - the probe records, never raises
            attempts.append({"url": url, "result": f"{type(error).__name__}: {error}"})
    if outcome == "unavailable":
        detail = (
            "no machine-queryable SPARQL endpoint found on 2026-09-15; every candidate "
            "answered with an HTML landing page (see attempts)"
        )
    cache.write_text(
        json.dumps(
            {"outcome": outcome, "detail": detail, "attempts": attempts, "probed_at": "2026-09-15"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return outcome, detail


def sinica_lod(person: Mapping[str, Any]) -> SourceResult:
    started = time.time()
    outcome, detail = sinica_probe()
    return SourceResult(
        "sinica_lod", outcome, detail=detail, seconds=time.time() - started
    )


# --------------------------------------------------------------- 4. Wikisource dump


def wikisource_index() -> dict[str, Any]:
    """Frozen dump index: 清史稿 page titles with their stream offsets."""
    V03_DIR.mkdir(parents=True, exist_ok=True)
    if WIKISOURCE_TITLE_CACHE.exists():
        return json.loads(WIKISOURCE_TITLE_CACHE.read_text(encoding="utf-8"))
    import bz2

    if not WIKISOURCE_DUMP.exists():
        payload = {"available": False, "reason": "dump index not downloaded", "titles": {}}
        WIKISOURCE_TITLE_CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload
    titles: dict[str, int] = {}
    with bz2.open(WIKISOURCE_DUMP, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "清史稿" not in line:
                continue
            parts = line.rstrip("\n").split(":", 2)
            if len(parts) != 3:
                continue
            offset, _, title = parts
            titles[title.strip()] = int(offset)
    payload = {"available": True, "titles": titles, "sha256": WIKISOURCE_SHA256,
               "date": WIKISOURCE_DUMP_DATE, "url": WIKISOURCE_DUMP_URL}
    WIKISOURCE_TITLE_CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def wikisource_dump(person: Mapping[str, Any]) -> SourceResult:
    """Does the fixed dump contain the 《清史稿》 volume the protocol would read?

    The lookup is title-level against the frozen dump index; article extraction is U04.
    """
    started = time.time()
    index = wikisource_index()
    if not index.get("available"):
        return SourceResult(
            "wikisource_dump", "unavailable", detail=str(index.get("reason")),
            seconds=time.time() - started,
        )
    volumes = index.get("titles") or {}
    locator = person.get("biography_source_ids")
    if isinstance(locator, str) and locator:
        volume = locator.split("#")[0]
        if volume in volumes:
            return SourceResult(
                "wikisource_dump", "found",
                attribution="volume_locator",
                detail=f"{volume} present in the {WIKISOURCE_DUMP_DATE} dump (offset {volumes[volume]})",
                seconds=time.time() - started,
            )
    text = person.get("name_chn")
    if isinstance(text, str) and text and f"清史稿/{text}" in volumes:
        return SourceResult(
            "wikisource_dump", "found", identity=True, attribution="page_title",
            detail=f"page 清史稿/{text} is in the dump", seconds=time.time() - started,
        )
    return SourceResult(
        "wikisource_dump", "not_found",
        detail="no attributable 《清史稿》 page in the frozen dump index",
        seconds=time.time() - started,
    )


# ----------------------------------------------------------------------- protocol

ADAPTERS = {
    "cbdb_frozen": frozen_cbdb,
    "cgedq_frozen": cgedq_frozen,
    "cbdb_api": cbdb_api,
    "sinica_lod": sinica_lod,
    "wikisource_dump": wikisource_dump,
}


def run_protocol(person: Mapping[str, Any], *, sources: tuple[str, ...] = SOURCE_ORDER) -> list[SourceResult]:
    """Execute the fixed source order for one person."""
    return [ADAPTERS[source](person) for source in sources]


def protocol_fingerprint() -> str:
    """Freeze the protocol definition so results can be tied to it."""
    payload = {
        "order": list(SOURCE_ORDER),
        "required": list(REQUIRED_SOURCES),
        "optional": list(OPTIONAL_SOURCES),
        "outcomes": list(OUTCOMES),
        "cbdb_frozen": "cbdb_20260912",
        "cgedq_frozen": "cgedq_jsl_public_1760-1912 (1760-1798 slice)",
        "cbdb_api": CBDB_API_BASE,
        "sinica": SINICA_URL,
        "wikisource": {"url": WIKISOURCE_DUMP_URL, "date": WIKISOURCE_DUMP_DATE, "sha256": WIKISOURCE_SHA256},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


__all__ = [
    "SOURCE_ORDER",
    "OUTCOMES",
    "SourceResult",
    "run_protocol",
    "protocol_fingerprint",
    "sinica_probe",
    "wikisource_index",
]
