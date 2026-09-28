"""P02 driver: build the study universe (appointments + officials_master).

    uv run python -m qing_elite.build_universe

No LLM is involved. Outputs:

    data/processed/appointments.parquet
    data/processed/officials_master.parquet
    output/tables/p02_*.csv

Tier definitions and office standardization live in ``config/offices.yaml``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from qing_elite.cbdb.appointments import (
    TIER_RANK,
    attach_places,
    build_province_lookup,
    load_alt_names,
    load_postings,
)
from qing_elite.cbdb.degrees import classify_entry_desc, entry_code_mapping
from qing_elite.cbdb.offices import OfficeTier, included_office_ids, resolve_cbdb_offices
from qing_elite.cgedq.offices import CgedqOfficeClassifier
from qing_elite.cgedq.officials import (
    d_layer_appointments,
    degree_lookup,
    load_chushen_recodes,
    load_d_layer,
    person_level,
)
from qing_elite.linkage.names import (
    build_cbdb_candidates,
    link_persons,
    normalize_province,
)
from qing_elite.utils.config import (
    CBDB_SQLITE,
    CGEDQ_TAB,
    OUTPUT_DIR,
    PROCESSED_DIR,
    RAW_DIR,
    load_offices,
)

BANNER_ETHNICITY = {
    "滿洲": "manchu_banner",
    "蒙古": "mongol_banner",
    "漢軍": "han_bannerman",
    "内务府": "booi_bannerman",
    "內務府": "booi_bannerman",
}


@dataclass(frozen=True, slots=True)
class Universe:
    appointments: pd.DataFrame
    officials: pd.DataFrame
    audit: dict[str, pd.DataFrame]


def main() -> int:
    cfg = load_offices()
    study = cfg["study_window"]
    year_min, year_max = int(study["start"]), int(study["end"])

    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        universe, summary = build(cfg, conn, year_min, year_max)
    finally:
        conn.close()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "tables").mkdir(parents=True, exist_ok=True)
    appointments_path = PROCESSED_DIR / "appointments.parquet"
    officials_path = PROCESSED_DIR / "officials_master.parquet"
    universe.appointments.to_parquet(appointments_path, index=False)
    universe.officials.to_parquet(officials_path, index=False)
    for name, table in universe.audit.items():
        table.to_csv(OUTPUT_DIR / "tables" / f"p02_{name}.csv", index=False)

    print(f"appointments.parquet : {len(universe.appointments):,} rows -> {appointments_path}")
    print(f"officials_master.parquet : {len(universe.officials):,} rows -> {officials_path}")
    for line in summary:
        print(line)
    return 0


def build(
    cfg: Mapping[str, Any],
    conn: sqlite3.Connection,
    year_min: int,
    year_max: int,
) -> tuple[Universe, list[str]]:
    summary: list[str] = []
    positions = conn.execute(
        "SELECT c_office_id, c_office_chn, c_dy FROM OFFICE_CODES"
    ).fetchall()
    resolved = resolve_cbdb_offices(positions, cfg)
    tiers = included_office_ids(resolved)
    province_lookup = build_province_lookup(conn)

    # ---------------------------------------------------------------- CBDB side
    postings = load_postings(conn, tiers.keys(), tiers)
    postings = attach_places(conn, postings)
    postings["year_min"] = year_min
    postings["year_max"] = year_max
    in_window = _window_flag(postings, year_min, year_max)
    postings["in_window"] = in_window
    postings["posting_dy_inferred"] = postings["posting_dy"].isin([0, -1]) | postings[
        "posting_dy"
    ].isna()
    postings["person_key"] = "cbdb:" + postings["c_personid"].astype(str)
    postings["appointment_id"] = _cbdb_appointment_ids(postings)
    postings["is_concurrent_overlap"] = _overlap_flags(postings)
    postings["source"] = "CBDB"

    cbdb_appointments = postings[
        [
            "appointment_id",
            "source",
            "person_key",
            "c_personid",
            "tier",
            "tier_rank",
            "c_office_chn",
            "office_std",
            "institution",
            "c_office_id",
            "office_variant",
            "ambiguous_institution",
            "appointment_type",
            "is_acting",
            "is_concurrent",
            "is_concurrent_overlap",
            "c_appt_code",
            "c_assume_office_code",
            "date_start",
            "date_end",
            "date_source",
            "in_window",
            "year_discarded",
            "posting_dy",
            "posting_dy_inferred",
            "c_addr_id",
            "place_chn",
            "province",
            "c_name_chn",
            "c_index_year",
            "c_birthyear",
            "c_deathyear",
            "c_notes",
        ]
    ].rename(
        columns={
            "c_office_chn": "office_raw",
            "c_office_id": "source_office_id",
            "c_appt_code": "appointment_code",
            "c_assume_office_code": "assume_code",
            "c_addr_id": "place_id",
            "c_name_chn": "person_name",
            "c_index_year": "person_index_year",
            "c_birthyear": "person_birth_year",
            "c_deathyear": "person_death_year",
            "c_notes": "source_notes",
        }
    )

    # ------------------------------------------------------- CBDB person detail
    persons = _cbdb_person_frame(conn, province_lookup)
    alt_names = load_alt_names(conn, persons["cbdb_personid"].tolist())
    degrees = _cbdb_degrees(conn)

    cbdb_master = (
        persons.merge(degrees, on="cbdb_personid", how="left")
        .merge(
            alt_names.groupby("person_key")["c_alt_name_chn"]
            .agg(lambda values: "|".join(sorted(set(values))))
            .rename("alt_names")
            .reset_index(),
            on="person_key",
            how="left",
        )
    )

    # ------------------------------------------------------------- CGED-Q side
    classifier = CgedqOfficeClassifier(cfg)
    jsl = load_d_layer(CGEDQ_TAB, classifier, year_min=1760, year_max=1798)
    spells = d_layer_appointments(jsl)
    jsl_persons = person_level(jsl, spells)
    recodes = load_chushen_recodes(RAW_DIR / "cgeq" / "cgedq_jsl_chushen_recodes.tab")
    chushen = degree_lookup(recodes, cfg["cgedq"]["degree_category_rank"])
    jsl_persons["degree_jsl"] = jsl_persons["degree_raw"].map(
        lambda value: chushen.get(str(value).strip(), (None, 0))[0] if value else None
    )
    jsl_persons["degree_jsl_rank"] = jsl_persons["degree_raw"].map(
        lambda value: chushen.get(str(value).strip(), (None, 0))[1] if value else 0
    )

    cgedq_appointments = spells.assign(source="CGEDQ")
    # A person may hold the same office in more than one tenure; the spell ordinal
    # keeps the id unique without merging those tenures.
    cgedq_appointments["appointment_id"] = (
        "cgedq:"
        + cgedq_appointments["person_id"].astype(str)
        + ":"
        + cgedq_appointments["office_core"].astype(str)
        + ":"
        + cgedq_appointments["tenure_ordinal"].astype(str)
    )
    cgedq_appointments["person_key"] = "cgedq:" + cgedq_appointments["person_id"].astype(str)
    cgedq_appointments["tier"] = "D"
    cgedq_appointments["tier_rank"] = TIER_RANK["D"]
    cgedq_appointments["office_std"] = cgedq_appointments["office_core"]
    cgedq_appointments["date_start"] = cgedq_appointments["first_year"]
    cgedq_appointments["date_end"] = cgedq_appointments["last_year"]
    cgedq_appointments["date_source"] = "jsl_observation"
    cgedq_appointments["in_window"] = cgedq_appointments["first_year"].between(year_min, year_max)
    # ---------------------------------------------------------------- linkage
    candidates = build_cbdb_candidates(conn, province_lookup)
    linkage = link_persons(
        jsl_persons, candidates, cfg["cgedq"]["simplify_map"], cfg["cgedq"]["char_variants"]
    )
    jsl_persons = jsl_persons.merge(linkage, on="cgedq_person_id", how="left")

    # JSL appointments adopt the CBDB person id when a link was found, so both
    # halves of the universe share one person key.
    uid_map = {
        str(row.cgedq_person_id): (
            f"cbdb:{int(row.cbdb_personid)}" if pd.notna(row.cbdb_personid) else None
        )
        for row in linkage.itertuples(index=False)
    }
    cgedq_appointments["person_uid"] = [
        uid_map.get(str(pid)) or key
        for pid, key in zip(cgedq_appointments["person_id"], cgedq_appointments["person_key"])
    ]
    cgedq_appointments["linkage_confidence"] = cgedq_appointments["person_id"].map(
        dict(zip(linkage["cgedq_person_id"].astype(str), linkage["linkage_confidence"]))
    )
    cbdb_appointments["person_uid"] = cbdb_appointments["person_key"]
    cbdb_appointments["linkage_confidence"] = None

    # ------------------------------------------------------- officials_master
    appointments = pd.concat(
        [cbdb_appointments, cgedq_appointments], ignore_index=True, sort=False
    )
    officials = _assemble_master(cbdb_master, jsl_persons, appointments, year_min, year_max)

    audit = _audit_tables(
        cfg,
        resolved,
        postings,
        jsl,
        spells,
        candidates,
        linkage,
        officials,
        persons,
        appointments,
        conn,
    )
    summary.append(_summary_line(appointments, officials, linkage))
    return Universe(appointments=appointments, officials=officials, audit=audit), summary


# --------------------------------------------------------------------- helpers


def _cbdb_appointment_ids(postings: pd.DataFrame) -> pd.Series:
    """Stable per-posting ids; a few CBDB rows lack posting id / sequence, so a
    duplicate ordinal is appended only where the natural key repeats."""
    def as_text(column: str) -> pd.Series:
        return postings[column].astype("string").fillna("NA").astype(str)

    base = (
        "cbdb:" + as_text("c_personid") + ":" + as_text("c_office_id")
        + ":" + as_text("c_posting_id") + ":" + as_text("c_sequence")
    )
    ordinal = base.groupby(base).cumcount()
    return base.where(ordinal.eq(0), base + "#" + ordinal.astype(str))


def _window_flag(frame: pd.DataFrame, year_min: int, year_max: int) -> pd.Series:
    start, end = frame["date_start"], frame["date_end"]
    inside = ((start.between(year_min, year_max)) | (end.between(year_min, year_max))).fillna(False)
    unknown = start.isna() & end.isna()
    return pd.Series(
        [True if flag else (None if unknown else False) for flag, unknown in zip(inside, unknown)],
        index=frame.index,
        dtype="object",
    )


def _overlap_flags(frame: pd.DataFrame) -> pd.Series:
    """True when another posting of the same person overlaps this one in time."""
    flags = pd.Series(False, index=frame.index)
    dated = frame[frame["date_start"].notna()].copy()
    dated["date_end_filled"] = dated["date_end"].fillna(dated["date_start"])
    for _person, rows in dated.groupby("c_personid"):
        if len(rows) < 2:
            continue
        for index, row in rows.iterrows():
            others = rows.drop(index)
            overlapping = (others["date_start"] <= row["date_end_filled"]) & (
                others["date_end_filled"] >= row["date_start"]
            )
            if overlapping.any():
                flags.at[index] = True
    return flags


def _cbdb_person_frame(conn: sqlite3.Connection, province_lookup: dict[int, str]) -> pd.DataFrame:
    frame = pd.read_sql_query(
        """
        SELECT b.c_personid AS cbdb_personid, b.c_name_chn, b.c_surname_chn, b.c_mingzi_chn,
               b.c_birthyear, b.c_deathyear, b.c_index_year, b.c_index_year_type_code,
               b.c_dy, b.c_ethnicity_code, b.c_index_addr_id, b.c_fl_earliest_year,
               b.c_fl_latest_year
        FROM BIOG_MAIN b
        WHERE b.c_dy = 20
        """,
        conn,
    )
    banners = pd.read_sql_query(
        """
        SELECT DISTINCT a.c_personid, ac.c_name_chn AS banner_addr
        FROM BIOG_ADDR_DATA a
        JOIN ADDR_CODES ac ON ac.c_addr_id = a.c_addr_id
        WHERE a.c_addr_type = 13
        """,
        conn,
    )
    banners["banner_ethnicity"] = banners["banner_addr"].map(_banner_ethnicity)
    banner_agg = banners.groupby("c_personid").agg(
        banner_addr=("banner_addr", lambda values: "|".join(sorted(set(values)))),
        cbdb_banner_status=("banner_ethnicity", lambda values: "|".join(sorted(set(values)))),
    )
    frame["person_key"] = "cbdb:" + frame["cbdb_personid"].astype(str)
    frame["native_province"] = frame["c_index_addr_id"].map(province_lookup).map(normalize_province)
    frame["native_county"] = None
    frame = frame.merge(banner_agg, left_on="cbdb_personid", right_index=True, how="left")
    frame["cbdb_banner_status"] = frame["cbdb_banner_status"].fillna("unknown")
    return frame


def _banner_ethnicity(banner_addr: str | None) -> str:
    if not banner_addr:
        return "banner_unspecified"
    for keyword, label in BANNER_ETHNICITY.items():
        if banner_addr.startswith(keyword):
            return label
    return "banner_unspecified"


def _effective_banner(cbdb_value: object, jsl_value: object) -> str:
    """Banner evidence, never asserting non-banner status from missing data.

    CBDB can distinguish 滿洲/蒙古/漢軍 when an 八旗 address exists; JSL's 旗分
    column only records the eight banners. An absent value means *unknown*:
    missing flag data must not be read as "non-banner Han" (missing != commoner).
    """
    if isinstance(cbdb_value, str) and cbdb_value and cbdb_value != "unknown":
        if "manchu_banner" in cbdb_value:
            return "manchu_banner"
        if "mongol_banner" in cbdb_value:
            return "mongol_banner"
        if "han_bannerman" in cbdb_value:
            return "han_bannerman"
        if "booi_bannerman" in cbdb_value:
            return "booi_bannerman"
        return "banner_unspecified"
    if isinstance(jsl_value, str) and jsl_value.strip():
        return "bannerman_ethnicity_unknown"
    return "unknown"


def _cbdb_degrees(conn: sqlite3.Connection) -> pd.DataFrame:
    frame = pd.read_sql_query(
        """
        SELECT e.c_personid AS cbdb_personid, c.c_entry_desc_chn
        FROM ENTRY_DATA e
        JOIN ENTRY_CODES c ON c.c_entry_code = e.c_entry_code
        JOIN BIOG_MAIN b ON b.c_personid = e.c_personid
        WHERE b.c_dy = 20
        """,
        conn,
    )
    classified = frame["c_entry_desc_chn"].map(classify_entry_desc)
    frame["degree_cbdb"] = [value[0] for value in classified]
    frame["degree_cbdb_rank"] = [value[1] for value in classified]
    frame["person_key"] = "cbdb:" + frame["cbdb_personid"].astype(str)
    best = frame.sort_values("degree_cbdb_rank", ascending=False).drop_duplicates("cbdb_personid")
    return best[["cbdb_personid", "degree_cbdb", "degree_cbdb_rank"]]


def _assemble_master(
    cbdb_master: pd.DataFrame,
    jsl_persons: pd.DataFrame,
    appointments: pd.DataFrame,
    year_min: int,
    year_max: int,
) -> pd.DataFrame:
    """Build one row per resolved person entity.

    The master is the *study universe*: CBDB persons holding at least one tier
    A/B/C appointment, plus every CGED-Q D-layer person. CBDB's remaining Qing
    population stays out of the master and is summarised in the audit tables
    (p02_cbdb_qing_population.csv) so nothing disappears silently.
    """
    tiers = appointments[["person_uid", "tier"]].drop_duplicates()
    tier_flags = (
        tiers.assign(value=True)
        .pivot_table(index="person_uid", columns="tier", values="value", aggfunc="any")
        .reindex(columns=list(TIER_RANK), fill_value=False)
    )
    tier_flags.index.name = "person_uid"
    tier_lists = (
        tiers.groupby("person_uid")["tier"]
        .agg(lambda values: "|".join(sorted(set(values), key=lambda t: TIER_RANK[t])))
        .rename("tiers_present")
    )
    counts = appointments.groupby("person_uid").agg(
        n_appointments=("appointment_id", "size"),
        career_first_year=("date_start", "min"),
        career_last_year=("date_start", "max"),
        n_unknown_year=("date_source", lambda values: int((values == "none").sum())),
        n_appointments_in_window=("in_window", lambda values: int(values.eq(True).sum())),
        n_appointments_unknown_window=("in_window", lambda values: int(values.isna().sum())),
    )

    members = set(tiers["person_uid"])
    cbdb = cbdb_master[cbdb_master["person_key"].isin(members)].copy()
    cbdb["source"] = "CBDB"
    cbdb["person_uid"] = cbdb["person_key"]

    jsl = jsl_persons.copy()
    jsl["source_person_key"] = "cgedq:" + jsl["cgedq_person_id"].astype(str)
    jsl["person_uid"] = [
        f"cbdb:{int(pid)}" if pd.notna(pid) else key
        for pid, key in zip(jsl["cbdb_personid"], jsl["source_person_key"])
    ]
    linked = jsl[jsl["cbdb_personid"].notna()]
    unlinked = jsl[jsl["cbdb_personid"].isna()]

    linked_agg = pd.DataFrame(
        {
            "cgedq_person_id": linked.groupby("person_uid")["cgedq_person_id"]
            .agg(lambda values: "|".join(sorted(set(values)))),
            "n_jsl_links": linked.groupby("person_uid").size(),
            "jsl_province": linked.groupby("person_uid")["native_province"].first(),
            "jsl_county": linked.groupby("person_uid")["native_county"].first(),
            "jsl_banner": linked.groupby("person_uid")["banner_std"].first(),
            "degree_jsl": linked.groupby("person_uid")["degree_jsl"].first(),
            "degree_jsl_rank": linked.groupby("person_uid")["degree_jsl_rank"].first(),
            "n_observations": linked.groupby("person_uid")["n_observations"].sum(),
            "first_year": linked.groupby("person_uid")["first_year"].min(),
            "last_year": linked.groupby("person_uid")["last_year"].max(),
            "linkage_confidence": linked.groupby("person_uid")["linkage_confidence"].agg(
                lambda values: sorted(set(values), key=_confidence_rank)[0]
            ),
            "linkage_evidence": linked.groupby("person_uid")["linkage_evidence"].first(),
            "n_name_candidates": linked.groupby("person_uid")["n_name_candidates"].max(),
        }
    ).reset_index()

    merged = cbdb.merge(linked_agg, on="person_uid", how="left")
    merged["source"] = ["CBDB+CGEDQ" if isinstance(v, str) else "CBDB" for v in merged["cgedq_person_id"]]
    merged["n_jsl_links"] = merged["n_jsl_links"].fillna(0).astype("int64")

    unlinked_master = pd.DataFrame(
        {
            "person_uid": unlinked["source_person_key"],
            "person_key": unlinked["source_person_key"],
            "cbdb_personid": pd.NA,
            "c_name_chn": unlinked["name_chn"],
            "c_surname_chn": unlinked["surname"],
            "c_mingzi_chn": unlinked["given_name"],
            "c_birthyear": pd.NA,
            "c_deathyear": pd.NA,
            "c_index_year": pd.NA,
            "c_dy": pd.NA,
            "c_index_addr_id": pd.NA,
            "native_province": pd.NA,
            "native_county": pd.NA,
            "cbdb_banner_status": "unknown",
            "banner_addr": pd.NA,
            "degree_cbdb": pd.NA,
            "degree_cbdb_rank": pd.NA,
            "alt_names": pd.NA,
            "source": "CGEDQ",
            "cgedq_person_id": unlinked["cgedq_person_id"],
            "n_jsl_links": 1,
            "jsl_province": unlinked["native_province"],
            "jsl_county": unlinked["native_county"],
            "jsl_banner": unlinked["banner_std"],
            "degree_jsl": unlinked["degree_jsl"],
            "degree_jsl_rank": unlinked["degree_jsl_rank"],
            "n_observations": unlinked["n_observations"],
            "first_year": unlinked["first_year"],
            "last_year": unlinked["last_year"],
            "linkage_confidence": unlinked["linkage_confidence"],
            "linkage_evidence": unlinked["linkage_evidence"],
            "n_name_candidates": unlinked["n_name_candidates"],
        }
    )
    master = pd.concat([merged, unlinked_master], ignore_index=True, sort=False)

    master = master.merge(tier_flags, left_on="person_uid", right_index=True, how="left")
    for tier in TIER_RANK:
        master[tier] = master[tier].fillna(False).astype(bool)
    master = master.merge(tier_lists, left_on="person_uid", right_index=True, how="left")
    master = master.merge(counts, left_on="person_uid", right_index=True, how="left")
    master["n_appointments"] = master["n_appointments"].fillna(0).astype("int64")
    master["n_unknown_year"] = master["n_unknown_year"].fillna(0).astype("int64")

    # Effective attributes: CBDB first, JSL as fallback, always with provenance.
    master["native_province_effective"] = master["native_province"].fillna(master["jsl_province"])
    master["native_county_effective"] = master["native_county"].fillna(master["jsl_county"])
    master["banner_effective"] = [
        _effective_banner(cbdb_value, jsl_value)
        for cbdb_value, jsl_value in zip(master["cbdb_banner_status"], master["jsl_banner"])
    ]
    master["degree_effective"] = master["degree_cbdb"].where(
        master["degree_cbdb"].notna(), master["degree_jsl"]
    )
    master["degree_effective_rank"] = (
        master["degree_cbdb_rank"]
        .where(master["degree_cbdb_rank"].notna(), master["degree_jsl_rank"])
        .fillna(0)
        .astype("int64")
    )
    master["degree_source"] = [
        "CBDB" if isinstance(cbdb_value, str) and cbdb_value
        else ("CGEDQ" if isinstance(jsl_value, str) and jsl_value else "none")
        for cbdb_value, jsl_value in zip(master["degree_cbdb"], master["degree_jsl"])
    ]
    master["name_effective"] = master["c_name_chn"].fillna(
        pd.Series(
            [_join_name(surname, given) for surname, given in zip(master["c_surname_chn"], master["c_mingzi_chn"])],
            index=master.index,
        )
    )
    master["n_appointments_in_window"] = master["n_appointments_in_window"].fillna(0).astype("int64")
    master["n_appointments_unknown_window"] = (
        master["n_appointments_unknown_window"].fillna(0).astype("int64")
    )
    # A person is in the study window when at least one appointment dated inside
    # 1644-1820 exists; appointments without any year evidence stay "unknown".
    master["in_study_window"] = [
        True if dated > 0 else (None if unknown > 0 else False)
        for dated, unknown in zip(
            master["n_appointments_in_window"], master["n_appointments_unknown_window"]
        )
    ]
    master["tiers_present"] = master["tiers_present"].fillna("")
    master["highest_tier"] = master["tiers_present"].map(
        lambda value: value.split("|")[0] if value else None
    )
    # The master is the study universe: a person must hold at least one tier
    # appointment. CGED-Q persons whose only posts fall outside tier D are recorded
    # in the audit tables instead of being carried as universe members.
    members = set(appointments["person_uid"])
    master = master[master["person_uid"].isin(members)].reset_index(drop=True)
    return master


def _confidence_rank(value: object) -> int:
    order = {"high": 0, "medium": 1, "ambiguous": 2, "unmatched": 3, "unlinkable_no_surname": 4}
    return order.get(str(value), 9)


def _join_name(surname: object, given: object) -> str | None:
    """Join surname and given name, tolerating missing pieces."""
    parts = [
        str(value).strip()
        for value in (surname, given)
        if isinstance(value, str) and value.strip() and value.strip().lower() != "nan"
    ]
    return "".join(parts) or None

def _audit_tables(
    cfg: Mapping[str, Any],
    resolved: list[OfficeTier],
    postings: pd.DataFrame,
    jsl: pd.DataFrame,
    spells: pd.DataFrame,
    candidates: pd.DataFrame,
    linkage: pd.DataFrame,
    master: pd.DataFrame,
    cbdb_population: pd.DataFrame,
    appointments: pd.DataFrame,
    conn: sqlite3.Connection,
) -> dict[str, pd.DataFrame]:
    del cfg  # tier rules are already resolved; kept in the signature for symmetry
    tier_map = pd.DataFrame(
        [
            {
                "office_id": row.office_id,
                "office_chn": row.office_chn,
                "office_dy": row.office_dy,
                "tier": row.tier,
                "canonical": row.canonical,
                "institution": row.institution,
                "source": row.source,
            }
            for row in resolved
        ]
    )
    degree_map = pd.DataFrame(
        [
            {
                "entry_code": mapping.entry_code,
                "entry_desc_chn": mapping.entry_desc_chn,
                "degree": mapping.degree,
                "rank": mapping.rank,
            }
            for mapping in entry_code_mapping(conn)
        ]
    )
    jsl_office = (
        jsl.groupby(["office_raw", "office_core", "office_category"], dropna=False)
        .size()
        .rename("n_rows")
        .reset_index()
        .sort_values("n_rows", ascending=False)
    )
    spell_audit = (
        spells.groupby("n_observations")
        .size()
        .rename("n_spells")
        .reset_index()
        .assign(share_of_spells=lambda frame: (frame["n_spells"] / frame["n_spells"].sum()).round(4))
    )
    tier_counts = (
        pd.concat(
            [
                postings.assign(source="CBDB")[["source", "tier", "date_start", "in_window"]],
                spells.assign(
                    source="CGEDQ", tier="D", date_start=spells["first_year"], in_window=True
                )[["source", "tier", "date_start", "in_window"]],
            ],
            ignore_index=True,
        )
        .groupby(["source", "tier"])
        .agg(
            n_appointments=("date_start", "size"),
            n_unknown_year=("date_start", lambda values: int(values.isna().sum())),
            first_year=("date_start", "min"),
            last_year=("date_start", "max"),
        )
        .reset_index()
    )
    year_quality = pd.DataFrame(
        [
            {
                "cbdb_tier_postings": int(len(postings)),
                "with_posting_years": int(postings["date_source"].eq("posting").sum()),
                "with_index_year_fallback": int(postings["date_source"].eq("index_year").sum()),
                "without_any_year": int(postings["date_source"].eq("none").sum()),
                "dropped_invalid_posting_year": int(postings["year_discarded"].sum()),
                "in_study_window_true": int(postings["in_window"].eq(True).sum()),
                "in_study_window_false": int(postings["in_window"].eq(False).sum()),
                "in_study_window_unknown": int(postings["in_window"].isna().sum()),
            }
        ]
    )
    d_summary = _cgedq_d_summary(jsl, spells)
    coverage = _coverage_by_tier(master, appointments)
    linkage_table = _linkage_table(linkage)
    same_name = _same_name_audit(master, cbdb_population)
    population = _cbdb_population_summary(cbdb_population, master)
    excluded_postings = _posting_exclusions(resolved, conn)
    candidate_stats = pd.DataFrame(
        [
            {
                "cbdb_qing_persons": int(len(candidates)),
                "distinct_names": int(candidates["c_name_chn"].nunique()),
            }
        ]
    )
    return {
        "office_tier_map": tier_map,
        "cbdb_degree_map": degree_map,
        "cgedq_office_classification": jsl_office,
        "cgedq_spell_audit": spell_audit,
        "year_quality": year_quality,
        "tier_counts": tier_counts,
        "coverage_by_tier": coverage,
        "cgedq_d_summary": d_summary,
        "tier_posting_exclusions": excluded_postings,
        "linkage_cbdb_cgedq": linkage_table,
        "same_name_audit": same_name,
        "cbdb_qing_population": population,
        "cbdb_candidate_stats": candidate_stats,
    }


def _posting_exclusions(resolved: list[OfficeTier], conn: sqlite3.Connection) -> pd.DataFrame:
    """Tier-classified offices whose postings belong to another dynasty.

    The study window is Qing (1644-1820): a Ming-flagged posting of an office that
    is also used in the Qing is excluded, and the count is reported here so the
    exclusion is visible rather than silent.
    """
    included = [row for row in resolved if row.source == "include"]
    ids = ",".join(str(row.office_id) for row in included)
    frame = pd.read_sql_query(
        f"""
        SELECT p.c_dy AS posting_dy, (b.c_dy = 20) AS person_is_qing,
               count(*) AS n_postings, count(DISTINCT p.c_personid) AS n_persons
        FROM POSTED_TO_OFFICE_DATA p
        JOIN BIOG_MAIN b ON b.c_personid = p.c_personid
        WHERE p.c_office_id IN ({ids})
        GROUP BY 1, 2 ORDER BY 3 DESC
        """,
        conn,
    )
    frame["included_in_universe"] = (
        frame["posting_dy"].eq(20)
        | (frame["posting_dy"].isin([0, -1]) | frame["posting_dy"].isna()) & frame["person_is_qing"]
    )
    frame["note"] = [
        "kept: posting is Qing, or posting dynasty unknown for a Qing person"
        if kept
        else "excluded: neither the posting nor a usable posting dynasty marks it Qing"
        for kept in frame["included_in_universe"]
    ]
    return frame


def _cgedq_d_summary(jsl: pd.DataFrame, spells: pd.DataFrame) -> pd.DataFrame:
    """How much of the JSL slice becomes the D layer, and how spells were formed."""
    d_rows = jsl[jsl["office_tier"] == "D"]
    return pd.DataFrame(
        [
            {
                "jsl_rows_in_slice": int(len(jsl)),
                "jsl_persons_in_slice": int(jsl["person_id"].nunique()),
                "rows_classified_d": int(len(d_rows)),
                "rows_classified_not_d": int(len(jsl) - len(d_rows)),
                "persons_with_at_least_one_d_post": int(spells["person_id"].nunique()),
                "d_appointment_spells": int(len(spells)),
                "spells_split_by_edition_gap": int(spells["spell_break"].nunique()) - 1
                if "spell_break" in spells
                else 0,
                "mean_observations_per_spell": round(float(spells["n_observations"].mean()), 2),
                "median_observations_per_spell": float(spells["n_observations"].median()),
                "acting_spells": int(spells["is_acting"].sum()),
                "concurrent_spells": int(spells["is_concurrent"].sum()),
            }
        ]
    )


def _coverage_by_tier(master: pd.DataFrame, appointments: pd.DataFrame) -> pd.DataFrame:
    """Per-tier person counts and attribute coverage (P02 acceptance item)."""
    rows: list[dict[str, object]] = []
    for tier in list(TIER_RANK):
        members = master[master[tier]]
        if members.empty:
            continue
        tier_appointments = appointments[appointments["tier"] == tier]
        rows.append(
            {
                "tier": tier,
                "n_persons": len(members),
                "n_appointments": int(len(tier_appointments)),
                "n_with_appointment_year": int(tier_appointments["date_start"].notna().sum()),
                "n_in_window": int(tier_appointments["in_window"].eq(True).sum()),
                "n_out_of_window": int(tier_appointments["in_window"].eq(False).sum()),
                "n_window_unknown": int(tier_appointments["in_window"].isna().sum()),
                "n_persons_in_window": int(members["in_study_window"].eq(True).sum()),
                "first_year": tier_appointments["date_start"].min(),
                "last_year": tier_appointments["date_start"].max(),
                "native_province_pct": round(100 * members["native_province_effective"].notna().mean(), 1),
                "banner_known_pct": round(
                    100 * members["banner_effective"].ne("unknown").mean(), 1
                ),
                "degree_known_pct": round(100 * members["degree_effective"].notna().mean(), 1),
                "birth_year_pct": round(100 * members["c_birthyear"].notna().mean(), 1),
                "death_year_pct": round(100 * members["c_deathyear"].notna().mean(), 1),
                "index_year_pct": round(100 * members["c_index_year"].notna().mean(), 1),
                "alt_name_pct": round(100 * members["alt_names"].notna().mean(), 1),
            }
        )
    return pd.DataFrame.from_records(rows)


def _linkage_table(linkage: pd.DataFrame) -> pd.DataFrame:
    counts = linkage["linkage_confidence"].value_counts()
    total = int(len(linkage))
    table = pd.DataFrame(
        {
            "linkage_confidence": counts.index,
            "n_jsl_persons": counts.to_numpy(),
        }
    )
    table["share_of_jsl_persons_pct"] = (100 * table["n_jsl_persons"] / total).round(2)
    reliable = int(counts.get("high", 0))
    with_medium = reliable + int(counts.get("medium", 0))
    table.loc[len(table)] = ["reliable_high_only", reliable, round(100 * reliable / total, 2)]
    table.loc[len(table)] = [
        "reliable_high_plus_medium",
        with_medium,
        round(100 * with_medium / total, 2),
    ]
    table.loc[len(table)] = ["total_jsl_persons", total, 100.0]
    return table


def _same_name_audit(master: pd.DataFrame, cbdb_population: pd.DataFrame) -> pd.DataFrame:
    """同名異人 counts: same name, different person ids (both sources)."""
    cbdb_universe = master[master["cbdb_personid"].notna()]
    cbdb_dupes = (
        cbdb_universe.groupby("c_name_chn")["cbdb_personid"].nunique().rename("n_persons")
    )
    jsl_universe = master[master["cgedq_person_id"].notna()]
    jsl_dupes = jsl_universe.groupby("name_effective")["person_uid"].nunique().rename("n_persons")
    population_dupes = (
        cbdb_population.groupby("c_name_chn")["cbdb_personid"].nunique().rename("n_persons")
    )
    return pd.DataFrame(
        [
            {
                "scope": "cbdb_universe",
                "distinct_names": int(cbdb_dupes.size),
                "names_shared_by_multiple_persons": int((cbdb_dupes > 1).sum()),
                "max_persons_per_name": int(cbdb_dupes.max()),
                "persons_with_shared_name": int(cbdb_dupes[cbdb_dupes > 1].sum()),
            },
            {
                "scope": "cgedq_universe",
                "distinct_names": int(jsl_dupes.size),
                "names_shared_by_multiple_persons": int((jsl_dupes > 1).sum()),
                "max_persons_per_name": int(jsl_dupes.max()),
                "persons_with_shared_name": int(jsl_dupes[jsl_dupes > 1].sum()),
            },
            {
                "scope": "cbdb_qing_population",
                "distinct_names": int(population_dupes.size),
                "names_shared_by_multiple_persons": int((population_dupes > 1).sum()),
                "max_persons_per_name": int(population_dupes.max()),
                "persons_with_shared_name": int(population_dupes[population_dupes > 1].sum()),
            },
        ]
    )


def _cbdb_population_summary(
    cbdb_population: pd.DataFrame, master: pd.DataFrame
) -> pd.DataFrame:
    """CBDB Qing persons outside the tier universe: how many, holding what."""
    universe_ids = set(master["cbdb_personid"].dropna().astype(int))
    outside = cbdb_population[~cbdb_population["cbdb_personid"].isin(universe_ids)]
    return pd.DataFrame(
        [
            {
                "cbdb_qing_persons_total": int(len(cbdb_population)),
                "in_study_universe": int(len(cbdb_population) - len(outside)),
                "outside_universe": int(len(outside)),
                "outside_with_province_pct": round(
                    100 * outside["native_province"].notna().mean(), 1
                ),
                "outside_with_birth_year_pct": round(100 * outside["c_birthyear"].notna().mean(), 1),
                "note": "CBDB 其餘清代人物不屬 A1/A2/A3/B/C 任何一層，亦不在 CGED-Q D 層；"
                "需基層對照時可另做 benchmark，不得當作本研究樣本。",
            }
        ]
    )

def _summary_line(appointments: pd.DataFrame, officials: pd.DataFrame, linkage: pd.DataFrame) -> str:
    counts = linkage["linkage_confidence"].value_counts()
    total = len(linkage)
    reliable = int(counts.get("high", 0) + counts.get("medium", 0))
    return (
        f"appointments by tier: "
        + ", ".join(
            f"{tier}={int(n)}" for tier, n in appointments["tier"].value_counts().sort_index().items()
        )
        + f" | officials={len(officials):,} | CGED-Q reliable linkage="
        + f"{reliable}/{total} ({100 * reliable / max(total, 1):.1f}%)"
    )


if __name__ == "__main__":
    raise SystemExit(main())
