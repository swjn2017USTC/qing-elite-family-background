"""Behaviour tests for the P02 universe build.

These cover the decisions that would silently corrupt the study universe if they
regressed: office standardization, tier exclusion, quarterly spell splitting,
person-id handling and name linkage.
"""

from __future__ import annotations

import pandas as pd
import pytest

from qing_elite.build_universe import _cbdb_appointment_ids, _effective_banner, _window_flag
from qing_elite.cbdb.degrees import classify_entry_desc
from qing_elite.cbdb.offices import TIER_ORDER, resolve_cbdb_offices
from qing_elite.cgedq.offices import CgedqOfficeClassifier
from qing_elite.cgedq.officials import d_layer_appointments, edition_positions
from qing_elite.linkage.names import normalize_province, provinces_compatible
from qing_elite.utils.config import load_offices


@pytest.fixture(scope="module")
def classifier() -> CgedqOfficeClassifier:
    return CgedqOfficeClassifier(load_offices())


@pytest.mark.parametrize(
    ("raw", "expected_core", "expected_tier"),
    [
        ("典史", "典史", "D"),
        ("知县", "知縣", "D"),  # simplified characters fold to traditional
        ("復設訓導", "復設訓導", "D"),  # longest match wins over 訓導
        ("邳州管河州同", "州同", "D"),  # functional prefix must not be cut as a connector
        ("吏目管典史事", "吏目", "D"),  # "A 管 B 事" names the substantive post first
        ("知縣改授教諭", "教諭", "D"),  # "A 改授 B" names the new post last
        ("布政司經歷", None, None),  # provincial office, not a prefecture post
        ("國子監學正", None, None),  # capital institution
        ("筆帖式", "筆帖式", None),  # capital clerk: recognised but kept out of tier D
        ("空白", None, None),  # vacant post placeholder
    ],
)
def test_classifier_core_office(
    classifier: CgedqOfficeClassifier, raw: str, expected_core: str | None, expected_tier: str | None
) -> None:
    result = classifier.classify(raw)
    assert result.core == expected_core
    assert result.tier == expected_tier


def test_classifier_separates_provincial_from_prefecture(classifier: CgedqOfficeClassifier) -> None:
    assert classifier.classify("江蘇巡撫").category == "省級"
    assert classifier.classify("正黃旗").category == "旗籍誤填"
    assert classifier.classify("正黄旗").category == "旗籍誤填"


def test_classifier_marks_concurrent_duty(classifier: CgedqOfficeClassifier) -> None:
    assert classifier.classify("縣丞兼管水利").is_concurrent
    assert not classifier.classify("縣丞").is_concurrent


def test_tier_resolution_covers_every_configured_office() -> None:
    config = load_offices()
    positions: list[tuple[int, str, int]] = []
    office_id = 1000
    for spec in config["cbdb"].values():
        for name in spec["office_names"]:
            positions.append((office_id, name, 20))
            office_id += 1
    for names in (config.get("cbdb_excluded") or {}).values():
        for name in names:
            positions.append((office_id, name, 20))
            office_id += 1
    resolved = resolve_cbdb_offices(positions, config)
    by_name = {row.office_chn: row for row in resolved}
    assert sum(1 for row in resolved if row.source == "include") == len(positions) - sum(
        len(names) for names in config["cbdb_excluded"].values()
    )
    assert by_name["吏部尚書"].tier == "B"
    assert by_name["巡撫"].tier == "C"
    assert by_name["尚書房行走"].tier == "excluded"  # princes' school, not the boards
    assert by_name["軍機章京"].tier == "excluded"  # secretariat, not a Grand Councillor
    assert by_name["盛京刑部侍郎"].tier == "excluded"  # secondary capital


def test_tier_resolution_rejects_unknown_office_names() -> None:
    with pytest.raises(ValueError, match="absent from this CBDB release"):
        resolve_cbdb_offices([(1, "吏部尚書", 20)], load_offices())


def test_missing_office_name_is_reported_not_ignored() -> None:
    """A configured name that the release lacks must surface, not shrink silently."""
    config = load_offices()
    config["cbdb"]["C"]["office_names"] = ["不存在的官名"]
    with pytest.raises(ValueError, match="不存在的官名"):
        resolve_cbdb_offices([(1, "巡撫", 20)], config)


def test_window_flag_distinguishes_false_from_unknown() -> None:
    frame = pd.DataFrame(
        {
            "date_start": [1700, 1600, None],
            "date_end": [1710, 1605, None],
        }
    )
    flags = _window_flag(frame, 1644, 1820)
    assert list(flags) == [True, False, None]


def test_degree_ladder() -> None:
    assert classify_entry_desc("科舉: 進士(籠統)") == ("進士", 6)
    assert classify_entry_desc("武進士") == ("武科", 7)
    assert classify_entry_desc("科舉: 鄉貢舉人") == ("舉人", 5)
    assert classify_entry_desc("貢生: 拔貢") == ("貢生", 4)
    assert classify_entry_desc("監生(籠統)") == ("監生", 3)
    assert classify_entry_desc("學校: 生員(庠生)") == ("生員", 2)
    assert classify_entry_desc("行伍") == ("其他入仕", 1)
    assert classify_entry_desc(None) == ("unknown", 0)


def test_province_normalization_and_aliases() -> None:
    assert normalize_province("江蘇省") == "江蘇"
    assert provinces_compatible("江南", "江蘇")
    assert provinces_compatible("江南", "安徽")
    assert not provinces_compatible("江南", "山東")
    assert not provinces_compatible(None, "山東")


def test_banner_never_asserts_non_banner() -> None:
    assert _effective_banner("unknown", None) == "unknown"
    assert _effective_banner("unknown", "正白旗") == "bannerman_ethnicity_unknown"
    assert _effective_banner("manchu_banner", None) == "manchu_banner"
    assert _effective_banner("han_bannerman|banner_unspecified", None) == "han_bannerman"


EDITIONS = [1760.75, 1761.5, 1765.0, 1765.75, 1768.5, 1773.5, 1777.5, 1786.0, 1788.0, 1788.75]


def _spell_input(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    positions = {value: index for index, value in enumerate(sorted(EDITIONS))}
    return pd.DataFrame(
        [
            {
                "person_id": person,
                "office_core": office,
                "office_tier": "D",
                "year_decimal": year,
                "edition_pos": positions[year],
                "year": int(year),
                "season": 1,
                "record_number": str(index),
                "office_raw": office,
                "office_category": "縣正印",
                "region": "江蘇",
                "post_class": None,
                "post_grade": None,
                "institution_1": None,
                "is_acting": False,
                "is_concurrent": False,
            }
            for index, (person, office, year) in enumerate(rows)
        ]
    )


def test_quarterly_repeats_collapse_but_edition_gaps_split() -> None:
    frame = _spell_input(
        [
            ("N1", "知縣", 1760.75),
            ("N1", "知縣", 1761.5),  # consecutive published editions -> one spell
            ("N1", "知縣", 1788.0),  # long gap -> a second tenure of the same office
            ("N2", "典史", 1760.75),
            ("N2", "巡檢", 1760.75),  # same edition, two posts -> concurrent
        ]
    )
    spells = d_layer_appointments(frame)
    n1 = spells[spells["person_id"] == "N1"]
    assert len(n1) == 2
    assert n1["n_observations"].tolist() == [2, 1]
    assert n1["tenure_ordinal"].tolist() == [1, 2]
    n2 = spells[spells["person_id"] == "N2"]
    assert len(n2) == 2
    assert bool(n2["is_concurrent"].all())


def test_edition_positions_follow_the_release_sequence() -> None:
    positions = edition_positions(pd.Series([1761.5, 1760.75, 1761.5, None]))
    assert positions == {1760.75: 0, 1761.5: 1}


def test_appointment_ids_stay_unique_without_posting_id() -> None:
    postings = pd.DataFrame(
        {
            "c_personid": [1, 1, 2],
            "c_office_id": [10, 10, 10],
            "c_posting_id": [pd.NA, pd.NA, 7],
            "c_sequence": [1, 1, 1],
        }
    )
    ids = _cbdb_appointment_ids(postings)
    assert ids.is_unique
    assert ids.tolist() == ["cbdb:1:10:NA:1", "cbdb:1:10:NA:1#1", "cbdb:2:10:7:1"]


def test_tier_order_is_the_study_hierarchy() -> None:
    assert TIER_ORDER == ["A1", "A2", "A3", "B", "C"]
