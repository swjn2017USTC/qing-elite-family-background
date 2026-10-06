"""A-layer verification against an authoritative外部 source (P07, item 3).

The plan requires 100% verification of the A layers (A1 大學士 / A2 南書房 / A3 軍機大臣)
against an authoritative source. P01 established that Academia Sinica's 人名權威—人物傳記
資料庫 is scriptable (no captcha, no login), so every A-layer person is looked up there and
compared with CBDB on native place; discrepancies are listed for human review.

Requests are paced and cached; nothing is bulk-crawled.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx
import pandas as pd

from qing_elite.utils.config import PROCESSED_DIR

IHP_ENDPOINT = "https://newarchive.ihp.sinica.edu.tw/sncaccgi/sncacFtp"
USER_AGENT = "qing-elite-family-background/0.1 (P07 A-layer verification; contact: repo owner)"
CACHE_DIR = Path("data/interim/ihp")
OUTPUT = Path("audit/a_layer_verification.csv")

_HITS = re.compile(r"共<font color=red>(\d+)</font>筆")
_FIELD = re.compile(r"<th>([^<]+)</th><td>(.*?)</td>", re.DOTALL)
_PROVINCES = (
    "江蘇", "浙江", "安徽", "江西", "福建", "湖北", "湖南", "河南", "山東", "山西",
    "陝西", "甘肅", "四川", "廣東", "廣西", "雲南", "貴州", "直隸", "奉天", "盛京",
    "吉林", "黑龍江", "新疆", "內蒙古", "蒙古", "滿洲",
)


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True)


def lookup(name: str, *, sleep_seconds: float = 1.5, attempts: int = 3) -> dict[str, Any]:
    """Look one name up in the IHP authority database (cached on disk)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{name}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    delay = 2.0
    result: dict[str, Any] = {"name": name, "found": False, "hits": 0, "error": None}
    with _client() as client:
        for attempt in range(attempts):
            try:
                response = client.get(
                    IHP_ENDPOINT,
                    params={"ACTION": f"TQ,sncacFtpqf,({name})@TM,1st,search_simple"},
                )
                response.raise_for_status()
                html = response.text
                hits = _HITS.search(html)
                hit_count = int(hits.group(1)) if hits else (1 if "權威號" in html else 0)
                fields = {key.strip(): re.sub(r"<[^>]+>", " ", value).strip() for key, value in _FIELD.findall(html)}
                result.update(
                    {
                        "found": hit_count > 0,
                        "hits": hit_count,
                        "authority_id": _clean(fields.get("權威號")),
                        "life_dates": _clean(fields.get("中曆生卒")),
                        "native_place": _clean(fields.get("籍貫")),
                        "province": _province_of(fields.get("籍貫", "")),
                        "name_on_record": _clean(fields.get("姓名")),
                        "note": _clean(fields.get("異名"))[:120] if fields.get("異名") else None,
                    }
                )
                break
            except Exception as error:  # noqa: BLE001 - the audit records the failure verbatim
                result["error"] = f"{type(error).__name__}: {error}"
                time.sleep(delay)
                delay *= 2
            finally:
                time.sleep(sleep_seconds)
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


# IHP writes the native place as a hierarchy table ("京師 - 真定府 - 柏鄉縣"); the first
# element is the top-level unit, whose Qing names do not always equal CBDB's province.
_PROVINCE_ALIASES: dict[str, str] = {
    "京師": "直隸",
    "京師省": "直隸",
    "南京": "江蘇",
    "南京省": "江蘇",
    "江南省": "江蘇",
    "遼東都指揮使司": "奉天",
    "遼東": "奉天",
    "盛京": "奉天",
}


def _province_of(text: str) -> str | None:
    """Extract the top-level administrative unit from an IHP native-place string."""
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = re.sub(r"原名|今名|經度|緯度", " ", text)
    head = re.split(r"[-–—]|\s{2,}", cleaned.strip())[0].strip()
    head = re.sub(r"^(京師|南京|湖廣|江南|直隸|奉天|盛京|遼東)\s*", lambda m: m.group(1), head)
    candidates = [head, head.replace("省", ""), cleaned]
    for candidate in candidates:
        if candidate in _PROVINCE_ALIASES:
            return _PROVINCE_ALIASES[candidate]
        for province in _PROVINCES:
            if province in candidate:
                return province
        if "湖廣" in candidate or "湖广" in candidate:
            return "湖廣"
        if "江南" in candidate:
            return "江南"
    return None


def verify_a_layer(
    master: pd.DataFrame | None = None,
    *,
    sleep_seconds: float = 1.5,
    limit: int | None = None,
) -> pd.DataFrame:
    """Verify every A-layer person against IHP and compare native place with CBDB."""
    if master is None:
        master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    a_layer = master[master["highest_tier"].isin(["A1", "A2", "A3"])].copy()
    a_layer = a_layer.sort_values(["highest_tier", "person_uid"])
    if limit:
        a_layer = a_layer.head(limit)
    rows: list[dict[str, Any]] = []
    for record in a_layer.to_dict("records"):
        name = str(record["c_name_chn"])
        ihp = lookup(name, sleep_seconds=sleep_seconds)
        cbdb_province = record.get("native_province_effective")
        # Recompute from the stored native-place text: older cache entries predate the
        # hierarchy parser, and re-fetching would be wasteful.
        ihp_province = ihp.get("province") or _province_of(ihp.get("native_place") or "")
        province_match = _province_consistent(ihp_province, cbdb_province)
        rows.append(
            {
                "person_uid": record["person_uid"],
                "name": name,
                "tier": record["highest_tier"],
                "cbdb_birthyear": record.get("c_birthyear"),
                "cbdb_deathyear": record.get("c_deathyear"),
                "cbdb_province": cbdb_province,
                "cbdb_banner": record.get("banner_effective"),
                "ihp_found": ihp.get("found"),
                "ihp_hits": ihp.get("hits"),
                "ihp_authority_id": ihp.get("authority_id"),
                "ihp_life_dates": ihp.get("life_dates"),
                "ihp_native_place": ihp.get("native_place"),
                "ihp_province": ihp_province,
                "province_consistent": province_match,
                "verification": (
                    "consistent" if province_match is True
                    else ("no_ihp_record" if not ihp.get("found")
                          else ("not_comparable" if province_match is None else "inconsistent"))
                ),
                "error": ihp.get("error"),
            }
        )
    frame = pd.DataFrame.from_records(rows)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUTPUT, index=False)
    return frame


def _province_consistent(ihp_province: object, cbdb_province: object) -> bool | None:
    if not isinstance(ihp_province, str) or not isinstance(cbdb_province, str):
        return None
    if ihp_province == cbdb_province:
        return True
    # 江南/湖廣 were split during the Qing; treat the umbrella names as compatible.
    umbrella = {
        "江南": {"江蘇", "安徽"},
        "湖廣": {"湖北", "湖南"},
        "直隸": {"直隸", "京師"},
        "奉天": {"奉天", "盛京", "吉林"},
    }
    for base, parts in umbrella.items():
        if {ihp_province, cbdb_province} <= (parts | {base}):
            return True
    return False


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="verify A-layer persons against IHP")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=1.5)
    args = parser.parse_args(argv)
    frame = verify_a_layer(sleep_seconds=args.sleep, limit=args.limit or None)
    print(frame["verification"].value_counts().to_string())
    print(f"rows: {len(frame)} -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
