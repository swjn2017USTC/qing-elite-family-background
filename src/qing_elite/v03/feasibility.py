"""Source feasibility probes and plan selection (U04R).

Two layers, kept strictly apart:

* **machine facts** — ``python -m qing_elite.v03.feasibility --probe`` issues the requests
  declared in ``config/v03/source_probes.yaml`` and records status, content type, size and
  the first characters of the body in ``data/interim_v03/probe_results.json``. Nothing is
  hand-written and nothing is bulk-downloaded.
* **curated metadata** — ``config/v03/source_catalog.yaml`` holds what a probe cannot
  answer (coverage years, kin scope, terms, estimated size). It is labelled as curation and
  must cite the probe or the literature registry for anything machine-verifiable.

``build_feasibility`` merges both into ``data/processed_v03/source_feasibility.parquet``;
``select_default_plan`` then applies the rule table in ``source_plan.yaml`` order and
returns the first matching PLAN A/B/C with the sources that justify it.
"""

from __future__ import annotations

import argparse
import functools
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import yaml

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.lit.registry import (
    FEASIBILITY_PARQUET,
    PROCESSED_V03_DIR,
    SOURCE_PLAN_JSON,
    validate_feasibility,
)
from qing_elite.v03.lit.schema import SOURCE_ACCESS_TYPES, SOURCE_FEASIBILITY_SCHEMA

CONFIG_V03 = PROJECT_ROOT / "config" / "v03"
PROBES_YAML = CONFIG_V03 / "source_probes.yaml"
CATALOG_YAML = CONFIG_V03 / "source_catalog.yaml"
PLAN_YAML = CONFIG_V03 / "source_plan.yaml"
PROBE_RESULTS_JSON = PROJECT_ROOT / "data" / "interim_v03" / "probe_results.json"

MAX_SNIPPET = 240


class FeasibilityError(ValueError):
    """Raised when probe or catalog configuration breaks the feasibility contract."""


@functools.lru_cache(maxsize=None)
def load_probe_config(path: Path | None = None) -> dict[str, Any]:
    with (path or PROBES_YAML).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not config.get("probes"):
        raise FeasibilityError("source_probes.yaml must declare probes")
    for entry in config["probes"]:
        for key in ("id", "source", "url", "method"):
            if key not in entry:
                raise FeasibilityError(f"probe {entry.get('id')!r} is missing {key}")
    return config


@functools.lru_cache(maxsize=None)
def load_catalog(path: Path | None = None) -> dict[str, Any]:
    with (path or CATALOG_YAML).open(encoding="utf-8") as handle:
        catalog = yaml.safe_load(handle)
    sources = catalog.get("sources") or []
    if not sources:
        raise FeasibilityError("source_catalog.yaml must declare sources")
    names = [entry["source_name"] for entry in sources]
    if len(names) != len(set(names)):
        raise FeasibilityError("source_catalog.yaml has duplicate source_name entries")
    for entry in sources:
        if entry.get("access_type") not in SOURCE_ACCESS_TYPES:
            raise FeasibilityError(
                f"{entry['source_name']}: access_type must be one of {SOURCE_ACCESS_TYPES}"
            )
    return catalog


@functools.lru_cache(maxsize=None)
def load_plan_rules(path: Path | None = None) -> dict[str, Any]:
    with (path or PLAN_YAML).open(encoding="utf-8") as handle:
        plan = yaml.safe_load(handle)
    rules = (plan.get("plan_selection") or {}).get("rules") or []
    if not rules:
        raise FeasibilityError("source_plan.yaml must declare plan_selection.rules")
    declared = set(plan.get("plans") or {})
    for rule in rules:
        if rule.get("plan") not in declared:
            raise FeasibilityError(f"rule {rule.get('id')} points at an undeclared plan")
    return plan


# --------------------------------------------------------------------------- probing


def probe_one(entry: dict[str, Any], *, client: httpx.Client) -> dict[str, Any]:
    """Issue one probe and record exactly what came back."""
    url = entry["url"]
    method = entry["method"]
    record: dict[str, Any] = {
        "probe_id": entry["id"],
        "source": entry["source"],
        "url": url,
        "method": method,
        "checked_at": date.today().isoformat(),
    }
    try:
        if method == "POST":
            response = client.post(
                url,
                data=entry.get("data") or {},
                headers=entry.get("headers") or {},
            )
        else:
            response = client.request(method, url)
        body = response.text if method != "HEAD" else ""
        record.update(
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            bytes=int(response.headers.get("content-length") or len(response.content)),
            snippet=" ".join(body[: MAX_SNIPPET * 3].split())[:MAX_SNIPPET],
            outcome="ok",
        )
    except Exception as error:
        record.update(
            status_code=None,
            content_type=None,
            bytes=None,
            snippet=None,
            outcome=f"{type(error).__name__}: {error}",
        )
    return record


def run_probes(*, offline: bool = False) -> list[dict[str, Any]]:
    """Probe every declared endpoint; ``--offline`` re-uses the recorded results."""
    if offline and PROBE_RESULTS_JSON.exists():
        return json.loads(PROBE_RESULTS_JSON.read_text(encoding="utf-8"))
    config = load_probe_config()
    records: list[dict[str, Any]] = []
    with httpx.Client(
        timeout=float(config.get("timeout_seconds", 30)),
        follow_redirects=True,
        headers={
            "User-Agent": "qing-elite-family-background/0.3 (source feasibility probe; contact via repo)"
        },
    ) as client:
        for entry in config["probes"]:
            records.append(probe_one(entry, client=client))
    PROBE_RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROBE_RESULTS_JSON.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return records


def probe_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Best (most informative) probe record per source."""
    best: dict[str, dict[str, Any]] = {}
    for record in records:
        current = best.get(record["source"])
        if current is None or _probe_rank(record) > _probe_rank(current):
            best[record["source"]] = record
    return best


def _probe_rank(record: dict[str, Any]) -> int:
    """Prefer reachable, content-bearing responses when summarising a source."""
    status = record.get("status_code") or 0
    rank = 0
    if record.get("outcome") == "ok":
        rank += 1
    if status == 200:
        rank += 2
    if status in (200, 404, 403, 503) and record.get("bytes"):
        rank += 1
    return rank


# --------------------------------------------------------------------------- building


def build_feasibility(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Merge curated catalog rows with the probed evidence into the feasibility table."""
    catalog = load_catalog()
    index = probe_index(records)
    rows: list[dict[str, Any]] = []
    for entry in catalog["sources"]:
        probe = index.get(entry["source_name"])
        row = dict(entry)
        row["probe_url"] = probe["url"] if probe else None
        row["probe_http_status"] = probe.get("status_code") if probe else None
        if probe:
            row["probe_observed"] = (
                f"{probe.get('method')} {probe['url']} -> {probe.get('status_code')} "
                f"[{probe.get('content_type')}] {probe.get('snippet') or probe.get('outcome')}"
            )
            row["probed_at"] = probe["checked_at"]
        else:
            row["probe_observed"] = "no probe declared for this source"
            row["probed_at"] = date.today().isoformat()
        rows.append(row)
    frame = pd.DataFrame(rows)
    frame["probe_http_status"] = frame["probe_http_status"].astype("Int64")
    for column, spec in SOURCE_FEASIBILITY_SCHEMA.columns.items():
        if str(spec.dtype) == "boolean" and column in frame.columns:
            frame[column] = frame[column].astype("boolean")
    frame = validate_feasibility(frame)
    PROCESSED_V03_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(FEASIBILITY_PARQUET, index=False)
    return frame


# --------------------------------------------------------------------------- planning


def _matches(row: pd.Series, requires: dict[str, Any]) -> bool:
    if "plan_role" in requires and row["plan_role"] not in _as_list(requires["plan_role"]):
        return False
    if "tier" in requires and row["tier"] not in _as_list(requires["tier"]):
        return False
    if "access_type" in requires and row["access_type"] not in _as_list(requires["access_type"]):
        return False
    for key in ("kin_scope_grade", "kin_population_coverage"):
        if key in requires and row[key] not in _as_list(requires[key]):
            return False
    for key in requires.get("all_of", []) or []:
        if not bool(row.get(key)):
            return False
    any_of = requires.get("any_of") or []
    if any_of and not any(bool(row.get(key)) for key in any_of):
        return False
    return True


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def select_default_plan(feasibility: pd.DataFrame) -> dict[str, Any]:
    """Apply the declared rules in order; first rule with supporting sources wins."""
    plan_config = load_plan_rules()
    rules = plan_config["plan_selection"]["rules"]
    for rule in rules:
        supporting = [
            row["source_name"]
            for _, row in feasibility.iterrows()
            if _matches(row, rule.get("requires") or {})
        ]
        if supporting:
            return {
                "selected_plan": rule["plan"],
                "rule_id": rule["id"],
                "rule_description": rule["description"],
                "supporting_sources": supporting,
                "evaluated_rules": [entry["id"] for entry in rules],
                "plans": plan_config["plans"],
            }
    fallback = plan_config["plan_selection"].get("fallback_plan")
    if not fallback:
        raise FeasibilityError("no rule matched and no fallback_plan is declared")
    return {
        "selected_plan": fallback,
        "rule_id": "fallback",
        "rule_description": plan_config["plan_selection"].get("fallback_reason", ""),
        "supporting_sources": [],
        "evaluated_rules": [entry["id"] for entry in rules],
        "plans": plan_config["plans"],
    }


def write_plan(selection: dict[str, Any]) -> Path:
    PROCESSED_V03_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_PLAN_JSON.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return SOURCE_PLAN_JSON


def render_report(
    feasibility: pd.DataFrame, selection: dict[str, Any], *, path: Path | None = None
) -> Path:
    """Render ``source_feasibility.md`` from the table and the plan selection.

    Numbers are read from the parquet, never typed by hand, so the prose cannot drift from
    the machine-checked evidence.
    """
    target = path or (
        PROJECT_ROOT / "reports" / "upgrade_v03" / "source_feasibility.md"
    )
    lines: list[str] = []
    lines.append("# V0.3 U04R — 候选史料来源可行性（machine-readable: `data/processed_v03/source_feasibility.parquet`）")
    lines.append("")
    lines.append(
        f"- 探测日期：{feasibility['probed_at'].max()}；来源数：{len(feasibility)}；"
        f"access_type 分布：" + ", ".join(
            f"{name}={count}"
            for name, count in feasibility["access_type"].value_counts().items()
        )
    )
    lines.append(
        "- 判定枚举：`PUBLIC_STRUCTURED`（结构化可机读）、`PUBLIC_SCAN`（公开扫描件）、"
        "`PUBLIC_UI_ONLY`（只有在线检索界面）、`ACCESS_REQUEST_REQUIRED`（须申请/订阅）、"
        "`PAPER_TABLE_ONLY`（只存在于论文附表）、`UNAVAILABLE`（找不到）。"
    )
    lines.append("")
    lines.append("## 1. 逐来源结论")
    lines.append("")
    lines.append(
        "| 来源 | 层 | 角色 | 年代 | 亲属覆盖(范围/人群) | access_type | bulk | api | 条款 | 置信 |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in feasibility.sort_values(["plan_role", "source_name"]).itertuples(index=False):
        lines.append(
            f"| `{row.source_name}` | {row.tier} | {row.plan_role} | {row.coverage_years} | "
            f"{row.kin_scope_grade}/{row.kin_population_coverage} | {row.access_type} | "
            f"{row.bulk_available} | {row.api_available} | {row.terms[:60]}… | {row.evidence_confidence} |"
        )
    lines.append("")
    lines.append("## 2. 探测证据（每行一条实际观测）")
    lines.append("")
    for row in feasibility.sort_values("source_name").itertuples(index=False):
        lines.append(f"### `{row.source_name}` — {row.zh_name}")
        lines.append("")
        lines.append(f"- access_type: **{row.access_type}**（bulk={row.bulk_available}, api={row.api_available}, scan={row.scan_available}, machine_readable={row.machine_readable}）")
        lines.append(f"- 覆盖：{row.coverage_years}；人群：{row.population}")
        lines.append(f"- 亲属：{row.kin_scope}（范围档 {row.kin_scope_grade}，人群覆盖 {row.kin_population_coverage}）")
        lines.append(f"- 职业：{row.career_scope}")
        lines.append(f"- 体量估计：{row.estimated_n}；OCR 页数估计：{row.estimated_ocr_pages}")
        lines.append(f"- 条款：{row.terms}")
        lines.append(f"- 再分发：{row.redistribution}；允许本地缓存：{row.local_cache_allowed}")
        lines.append(f"- 自动化/研究价值：{row.automation_score} / {row.research_value}")
        lines.append(f"- 探测观测：{row.probe_observed}")
        lines.append(f"- 备注：{row.notes}")
        lines.append("")
    lines.append("## 3. PLAN A / B / C 与默认方案")
    lines.append("")
    lines.append(f"**默认方案：{selection['selected_plan']}**（规则 `{selection['rule_id']}`）")
    lines.append("")
    lines.append(f"- 规则说明：{str(selection['rule_description']).strip()}")
    lines.append(f"- 支撑来源：{', '.join(selection['supporting_sources']) or '（无，走 fallback）'}")
    lines.append(f"- 求值顺序：{' → '.join(selection['evaluated_rules'])} → fallback")
    lines.append("")
    lines.append("| 方案 | 定义 | 对后续阶段的影响 |")
    lines.append("| --- | --- | --- |")
    for name, spec in selection["plans"].items():
        implications = "；".join(str(item).strip() for item in spec.get("implications", []))
        lines.append(
            f"| **{name}** {spec['label']} | {str(spec['definition']).strip()} | {implications} |"
        )
    lines.append("")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="source feasibility probe and plan")
    parser.add_argument("--probe", action="store_true", help="run the declared probes now")
    parser.add_argument("--offline", action="store_true", help="re-use recorded probe results")
    parser.add_argument("--plan", action="store_true", help="print the plan selection only")
    args = parser.parse_args(argv)

    records = run_probes(offline=args.offline or not args.probe)
    feasibility = build_feasibility(records)
    selection = select_default_plan(feasibility)
    write_plan(selection)
    report = render_report(feasibility, selection)
    print(f"feasibility rows: {len(feasibility)} -> {FEASIBILITY_PARQUET}")
    print(f"report: {report}")
    print(f"default plan: {selection['selected_plan']} (rule {selection['rule_id']})")
    print(f"supporting sources: {selection['supporting_sources']}")
    if args.plan:
        print(json.dumps(selection, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
