"""CBDB structured kinship -> the three ancestral slots (P03).

Everything here is resolved from explicit CBDB relations (``KIN_DATA`` plus
``KINSHIP_CODES``). Nothing is inferred from names, and no LLM is involved: a slot
is either backed by a kin record or reported as missing.

CBDB stores kinship in one direction ("person A has B as kin B's relation"), so a
father is the row where ``c_personid`` is the study person and ``c_kin_code`` is
the code for 父.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping

import pandas as pd

# Slot -> kin codes, most specific first. The first available code wins; the rest
# are kept in the audit columns so a different convention can be re-run later.
SLOT_KIN_CODES: dict[str, tuple[int, ...]] = {
    "father": (75, 82, 98, 107, 363, 571),  # 父, 嗣父, 繼父, 養父, 本生父, 非親生父
    "grandfather": (62, 440, 563),  # 祖父, 嗣祖父, 本生祖
    "great_grandfather": (48,),  # 曾祖
}
SLOTS: tuple[str, ...] = ("father", "grandfather", "great_grandfather")
PRIMARY_KIN_CODES: dict[str, int] = {slot: codes[0] for slot, codes in SLOT_KIN_CODES.items()}


@dataclass(frozen=True, slots=True)
class SlotRelation:
    """One resolved ancestral slot for one study person."""

    cbdb_personid: int
    slot: str
    kin_code: int | None
    kin_relation_chn: str | None
    ancestor_personid: int | None
    ancestor_personid_raw: int | None
    remapped_from_merged_id: bool
    evidence_origin: str
    source_textid: int | None
    source_title: str | None
    source_pages: str | None
    source_notes: str | None
    n_candidate_codes: int


def load_merged_person_map(conn: sqlite3.Connection) -> dict[int, int]:
    """``MERGED_PERSON_DATA``: duplicate person id -> surviving person id.

    CBDB removes merged duplicates from ``BIOG_MAIN``; the surviving id is
    ``c_personid``. Any reference to a merged-away id must be rewritten, otherwise
    the same ancestor appears under two ids.
    """
    merged: dict[int, int] = {}
    for survivor, merged_from in conn.execute(
        "SELECT c_personid, c_merged_from_personid FROM MERGED_PERSON_DATA"
    ):
        if survivor is not None and merged_from is not None and survivor != merged_from:
            merged[int(merged_from)] = int(survivor)
    return merged


def load_slot_relations(
    conn: sqlite3.Connection,
    person_ids: Iterable[int],
    merged_map: Mapping[int, int] | None = None,
) -> pd.DataFrame:
    """One row per (study person, slot); ``ancestor_personid`` is NULL when unknown."""
    merged_map = merged_map or {}
    ids = sorted({int(value) for value in person_ids})
    relations: dict[tuple[int, str], SlotRelation] = {}

    for chunk in _chunks(ids, 800):
        rows = pd.read_sql_query(
            f"""
            SELECT k.c_personid, k.c_kin_id, k.c_kin_code, kc.c_kinrel_chn, kc.c_kinrel,
                   k.c_source, k.c_pages, k.c_notes, k.c_autogen_notes,
                   t.c_title_chn
            FROM KIN_DATA k
            LEFT JOIN KINSHIP_CODES kc ON kc.c_kincode = k.c_kin_code
            LEFT JOIN TEXT_CODES t ON t.c_textid = k.c_source
            WHERE k.c_personid IN ({_placeholders(chunk)})
            """,
            conn,
        )
        for row in rows.itertuples(index=False):
            slot = _slot_for_code(row.c_kin_code)
            if slot is None:
                continue
            candidate = SlotRelation(
                cbdb_personid=int(row.c_personid),
                slot=slot,
                kin_code=int(row.c_kin_code),
                kin_relation_chn=row.c_kinrel_chn,
                ancestor_personid=_rewrite_id(row.c_kin_id, merged_map),
                ancestor_personid_raw=None if row.c_kin_id is None else int(row.c_kin_id),
                remapped_from_merged_id=bool(
                    row.c_kin_id is not None and int(row.c_kin_id) in merged_map
                ),
                evidence_origin=_evidence_origin(row.c_source, row.c_autogen_notes),
                source_textid=None if row.c_source in (None, 0) else int(row.c_source),
                source_title=row.c_title_chn,
                source_pages=None if not isinstance(row.c_pages, str) or not row.c_pages.strip() else row.c_pages,
                source_notes=None
                if not isinstance(row.c_notes, str) or not row.c_notes.strip()
                else row.c_notes,
                n_candidate_codes=1,
            )
            key = (candidate.cbdb_personid, slot)
            current = relations.get(key)
            if current is None or _code_rank(slot, candidate.kin_code) < _code_rank(
                slot, current.kin_code
            ):
                relations[key] = candidate
            else:
                relations[key] = replace(
                    current, n_candidate_codes=current.n_candidate_codes + 1
                )

    frame = pd.DataFrame(
        [
            {
                **asdict(relation),
                "kin_code_primary": relation.kin_code == PRIMARY_KIN_CODES[relation.slot],
            }
            for relation in relations.values()
        ]
    )
    if frame.empty:
        frame = pd.DataFrame(columns=["cbdb_personid", "slot", "ancestor_personid"])
    return frame


def _slot_for_code(kin_code: object) -> str | None:
    try:
        code = int(kin_code)
    except (TypeError, ValueError):
        return None
    for slot, codes in SLOT_KIN_CODES.items():
        if code in codes:
            return slot
    return None


def _code_rank(slot: str, kin_code: int | None) -> int:
    codes = SLOT_KIN_CODES[slot]
    try:
        return codes.index(int(kin_code)) if kin_code is not None else len(codes)
    except ValueError:
        return len(codes)


def _rewrite_id(person_id: object, merged_map: Mapping[int, int]) -> int | None:
    if person_id is None:
        return None
    try:
        value = int(person_id)
    except (TypeError, ValueError):
        return None
    return int(merged_map.get(value, value))


def _evidence_origin(source: object, autogen_notes: object) -> str:
    """How much evidence backs the kin record itself.

    CBDB auto-generates the inverse direction of explicit relations, and a kin row
    without any bibliographic source is weaker evidence than a cited one.
    """
    has_source = source not in (None, 0, "0")
    has_autogen = isinstance(autogen_notes, str) and bool(autogen_notes.strip())
    if has_source and not has_autogen:
        return "source_cited"
    if has_source and has_autogen:
        return "source_cited_autogenerated"
    if has_autogen:
        return "autogenerated_only"
    return "unattributed"


def load_ancestor_attributes(
    conn: sqlite3.Connection,
    ancestor_ids: Iterable[int],
    office_tiers: Mapping[int, tuple[str, str]] | None = None,
) -> pd.DataFrame:
    """Names, degrees, offices and native place for ancestor person ids."""
    ids = sorted({int(value) for value in ancestor_ids})
    if not ids:
        return pd.DataFrame(columns=["ancestor_personid"])
    office_tiers = office_tiers or {}

    people = _query_chunked(
        conn,
        """
        SELECT c_personid, c_name_chn, c_surname_chn, c_mingzi_chn, c_index_year,
               c_birthyear, c_deathyear, c_dy, c_index_addr_id
        FROM BIOG_MAIN WHERE c_personid IN ({ids})
        """,
        ids,
    )
    # Ancestor columns carry an ``ancestor_`` prefix so they can never collide with
    # the study person's own columns when the two frames are joined.
    people = people.rename(
        columns={
            "c_personid": "ancestor_personid",
            "c_name_chn": "ancestor_name",
            "c_surname_chn": "ancestor_surname",
            "c_mingzi_chn": "ancestor_mingzi",
            "c_index_year": "ancestor_index_year",
            "c_birthyear": "ancestor_birth_year",
            "c_deathyear": "ancestor_death_year",
            "c_dy": "ancestor_dy",
            "c_index_addr_id": "ancestor_index_addr_id",
            "c_ethnicity_code": "ancestor_ethnicity_code",
        }
    )

    alt_names = _query_chunked(
        conn,
        """
        SELECT c_personid AS ancestor_personid, c_alt_name_chn
        FROM ALTNAME_DATA WHERE c_personid IN ({ids})
          AND c_alt_name_chn IS NOT NULL AND c_alt_name_chn <> ''
        """,
        ids,
    )
    alt_joined = (
        alt_names.groupby("ancestor_personid")["c_alt_name_chn"]
        .agg(lambda values: "|".join(sorted(set(values))))
        .rename("ancestor_alt_names")
        .reset_index()
    )

    entries = _query_chunked(
        conn,
        """
        SELECT e.c_personid AS ancestor_personid, c.c_entry_desc_chn, e.c_year
        FROM ENTRY_DATA e JOIN ENTRY_CODES c ON c.c_entry_code = e.c_entry_code
        WHERE e.c_personid IN ({ids})
        """,
        ids,
    )
    from qing_elite.cbdb.degrees import classify_entry_desc

    if not entries.empty:
        classified = entries["c_entry_desc_chn"].map(classify_entry_desc)
        entries["degree_category"] = [value[0] for value in classified]
        entries["degree_rank"] = [value[1] for value in classified]
        best = (
            entries.sort_values(["degree_rank", "c_year"], ascending=[False, True])
            .drop_duplicates("ancestor_personid")
            .rename(
                columns={
                    "c_entry_desc_chn": "ancestor_degree_chn",
                    "c_year": "ancestor_degree_year",
                }
            )
        )
        degree_frame = best[
            ["ancestor_personid", "ancestor_degree_chn", "ancestor_degree_year", "degree_category", "degree_rank"]
        ].rename(columns={"degree_category": "ancestor_degree", "degree_rank": "ancestor_degree_rank"})
    else:
        degree_frame = pd.DataFrame(
            columns=["ancestor_personid", "ancestor_degree_chn", "ancestor_degree_year", "ancestor_degree", "ancestor_degree_rank"]
        )

    offices = _query_chunked(
        conn,
        """
        SELECT p.c_personid AS ancestor_personid, p.c_office_id, o.c_office_chn,
               p.c_firstyear, p.c_lastyear, p.c_dy
        FROM POSTED_TO_OFFICE_DATA p JOIN OFFICE_CODES o ON o.c_office_id = p.c_office_id
        WHERE p.c_personid IN ({ids})
        """,
        ids,
    )
    if not offices.empty:
        offices["tier"] = offices["c_office_id"].map(
            lambda oid: office_tiers.get(int(oid), (None, None))[0]
        )
        tier_rank = {"A1": 1, "A2": 2, "A3": 3, "B": 4, "C": 5, "D": 6}
        office_summary = offices.groupby("ancestor_personid").agg(
            ancestor_n_postings=("c_office_id", "size"),
            ancestor_n_distinct_offices=("c_office_id", "nunique"),
            ancestor_office_first_year=("c_firstyear", "min"),
            ancestor_office_last_year=("c_lastyear", "max"),
            ancestor_office_sample=(
                "c_office_chn",
                lambda values: "|".join(sorted(set(values))[:8]),
            ),
        )
        best_tier = (
            offices.dropna(subset=["tier"])
            .assign(rank=lambda frame: frame["tier"].map(tier_rank))
            .sort_values("rank")
            .drop_duplicates("ancestor_personid")
            .set_index("ancestor_personid")["tier"]
            .rename("ancestor_highest_tier")
        )
    else:
        office_summary = pd.DataFrame(
            columns=[
                "ancestor_n_postings",
                "ancestor_n_distinct_offices",
                "ancestor_office_first_year",
                "ancestor_office_last_year",
                "ancestor_office_sample",
            ]
        )
        best_tier = pd.Series(dtype="object", name="ancestor_highest_tier")

    addresses = _query_chunked(
        conn,
        """
        SELECT a.c_personid AS ancestor_personid, a.c_addr_id, a.c_addr_type,
               ac.c_name_chn AS addr_chn
        FROM BIOG_ADDR_DATA a JOIN ADDR_CODES ac ON ac.c_addr_id = a.c_addr_id
        WHERE a.c_personid IN ({ids}) AND a.c_addr_type IN (1, 13)
        """,
        ids,
    )
    if not addresses.empty:
        native = (
            addresses[addresses["c_addr_type"] == 1]
            .drop_duplicates("ancestor_personid")
            .set_index("ancestor_personid")["addr_chn"]
            .rename("ancestor_native_addr")
        )
        banner_addr = (
            addresses[addresses["c_addr_type"] == 13]
            .drop_duplicates("ancestor_personid")
            .set_index("ancestor_personid")["addr_chn"]
            .rename("ancestor_banner_addr")
        )
    else:
        native = pd.Series(dtype="object", name="ancestor_native_addr")
        banner_addr = pd.Series(dtype="object", name="ancestor_banner_addr")

    frame = (
        people.merge(alt_joined, on="ancestor_personid", how="left")
        .merge(degree_frame, on="ancestor_personid", how="left")
        .merge(office_summary, on="ancestor_personid", how="left")
        .merge(best_tier, on="ancestor_personid", how="left")
        .merge(native, on="ancestor_personid", how="left")
        .merge(banner_addr, on="ancestor_personid", how="left")
    )
    return frame


def _placeholders(values: Iterable[int]) -> str:
    return ",".join(str(int(value)) for value in values)


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _query_chunked(conn: sqlite3.Connection, query: str, ids: list[int]) -> pd.DataFrame:
    """Run a query with an ``{ids}`` placeholder across id chunks."""
    frames = [
        pd.read_sql_query(query.format(ids=_placeholders(chunk)), conn)
        for chunk in _chunks(ids, 800)
    ]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
