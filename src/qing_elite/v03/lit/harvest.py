"""Metadata harvest: OpenAlex + Crossref → candidate pool → registry (U04R).

Deterministic and cache-backed: every query response is stored under
``data/interim_v03/lit/raw/`` before being parsed, so the candidate pool can be rebuilt
without hitting the network again. Relevance scoring and core selection follow the
weights and thresholds frozen in ``config/v03/literature.yaml`` — no model decides them.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import yaml

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.lit.registry import (
    CANDIDATES_JSONL,
    RAW_DIR,
    REGISTRY_JSONL,
    load_registry,
    next_literature_ids,
    validate_registry,
    write_jsonl,
)

LITERATURE_YAML = PROJECT_ROOT / "config" / "v03" / "literature.yaml"
LITERATURE_MANUAL_YAML = PROJECT_ROOT / "config" / "v03" / "literature_manual.yaml"
S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,abstract,year,venue,externalIds,openAccessPdf,publicationTypes,citationCount,language"
CROSSREF_URL = "https://api.crossref.org/works"

_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


class LiteraturePlanError(ValueError):
    """Raised when ``config/v03/literature.yaml`` breaks the search-plan contract."""


@functools.lru_cache(maxsize=None)
def load_plan(path: Path | None = None) -> dict[str, Any]:
    with (path or LITERATURE_YAML).open(encoding="utf-8") as handle:
        plan = yaml.safe_load(handle)
    validate_plan(plan)
    return plan


def validate_plan(plan: dict[str, Any]) -> None:
    clusters = plan.get("query_clusters") or {}
    required = [f"Q{i}_" for i in range(1, 13)]
    missing = [
        prefix for prefix in required if not any(key.startswith(prefix) for key in clusters)
    ]
    if missing:
        raise LiteraturePlanError(f"search plan must cover clusters {missing}")
    for key, value in clusters.items():
        if not value.get("queries"):
            raise LiteraturePlanError(f"cluster {key} has no queries")
    core = plan.get("core_selection") or {}
    for key in ("weights", "min_score_for_core", "core_limit"):
        if key not in core:
            raise LiteraturePlanError(f"core_selection.{key} is required")
    if core["core_limit"] <= 0:
        raise LiteraturePlanError("core_selection.core_limit must be positive")
    if not (plan.get("digest_requirements") or {}).get("required_sections"):
        raise LiteraturePlanError("digest_requirements.required_sections is required")
    mapping = plan.get("claim_mapping") or {}
    if not mapping.get("testability_levels"):
        raise LiteraturePlanError("claim_mapping.testability_levels is required")


# --------------------------------------------------------------------------- providers


def _cache_path(provider: str, query: str) -> Path:
    digest = hashlib.sha256(f"{provider}:{query}".encode()).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:40].strip("-") or "query"
    return RAW_DIR / f"{provider}-{slug}-{digest}.json"


def _fetch_json(
    url: str,
    params: dict[str, Any],
    cache: Path,
    *,
    client: httpx.Client,
    attempts: int = 5,
    sleep_seconds: float = 3.0,
) -> dict:
    """Fetch with a disk cache and backoff on the providers' transient 429/5xx."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    last_error: Exception | None = None
    for attempt in range(attempts):
        response = client.get(url, params=params)
        if response.status_code in (429, 500, 502, 503, 504):
            last_error = httpx.HTTPStatusError(
                f"{response.status_code}", request=response.request, response=response
            )
            time.sleep(sleep_seconds * (attempt + 1))
            continue
        response.raise_for_status()
        payload = response.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload
    raise last_error if last_error else RuntimeError("unreachable")


#: Provider failures are recorded, never fatal: a partially available search layer must
#: not stop the feasibility stage (the provider log is written next to the raw cache).
PROVIDER_EVENTS: list[dict[str, Any]] = []

#: Per-query retry budget. The public Semantic Scholar pool throttles hard (429), so the
#: harvest spends a bounded amount of time per query and records throttling as an event.
PROVIDER_ATTEMPTS = {"semanticscholar": 2, "crossref": 3}
PROVIDER_SPACING = {"semanticscholar": 3.0, "crossref": 0.5}


def _safe_fetch(
    provider: str,
    query: str,
    url: str,
    params: dict[str, Any],
    cache: Path,
    *,
    client: httpx.Client,
    sleep_seconds: float = 3.0,
) -> dict | None:
    try:
        payload = _fetch_json(
            url,
            params,
            cache,
            client=client,
            attempts=PROVIDER_ATTEMPTS.get(provider, 3),
            sleep_seconds=sleep_seconds,
        )
        PROVIDER_EVENTS.append({"provider": provider, "query": query, "status": "ok"})
        time.sleep(PROVIDER_SPACING.get(provider, 0.5))
        return payload
    except Exception as error:
        PROVIDER_EVENTS.append(
            {
                "provider": provider,
                "query": query,
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return None


def _s2_rows(payload: dict[str, Any], query: str, cluster: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("data", []):
        oa = item.get("openAccessPdf") or {}
        external = item.get("externalIds") or {}
        rows.append(
            {
                "title": (item.get("title") or "").strip(),
                "authors": None,
                "year": item.get("year"),
                "venue": item.get("venue"),
                "doi": (external.get("DOI") or "").lower() or None,
                "url": f"https://www.semanticscholar.org/paper/{item.get('paperId')}"
                if item.get("paperId")
                else None,
                "language": item.get("language"),
                "type": (item.get("publicationTypes") or [None])[0],
                "abstract": item.get("abstract"),
                "is_oa": bool(oa.get("url")),
                "oa_status": "open" if oa.get("url") else None,
                "oa_url": oa.get("url"),
                "oa_is_pdf": bool(oa.get("url")),
                "cited_by": item.get("citationCount"),
                "query_cluster": cluster,
                "discovery_source": f"semanticscholar:{query}",
            }
        )
    return rows


def _crossref_rows(payload: dict[str, Any], query: str, cluster: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("message", {}).get("items", []):
        title = (item.get("title") or [""])[0]
        abstract = item.get("abstract")
        rows.append(
            {
                "title": (title or "").strip(),
                "authors": "; ".join(
                    f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in (item.get("author") or [])[:8]
                ),
                "year": (item.get("issued", {}).get("date-parts") or [[None]])[0][0],
                "venue": (item.get("container-title") or [None])[0],
                "doi": item.get("DOI"),
                "url": item.get("URL"),
                "language": item.get("language"),
                "type": item.get("type"),
                "abstract": re.sub(r"<[^>]+>", " ", abstract).strip() if abstract else None,
                "is_oa": None,
                "oa_status": None,
                "oa_url": None,
                "oa_is_pdf": False,
                "cited_by": item.get("is-referenced-by-count"),
                "query_cluster": cluster,
                "discovery_source": f"crossref:{query}",
            }
        )
    return rows


def harvest_metadata(*, offline: bool = False, rows_per_query: int | None = None) -> list[dict[str, Any]]:
    """Query every cluster against Semantic Scholar and Crossref; return raw rows.

    OpenAlex was probed first and reported ``503 search temporarily unavailable`` for
    anonymous use (see ``config/v03/literature.yaml:providers_unavailable``), so the plan
    runs on the two providers that answered.
    """
    plan = load_plan()
    limit = rows_per_query or int(plan["rows_per_query"])
    user_agent = plan.get("user_agent") or "qing-elite-family-background/0.3"
    collected: list[dict[str, Any]] = []
    with httpx.Client(
        timeout=60.0, headers={"User-Agent": user_agent}, follow_redirects=True
    ) as client:
        for cluster, spec in plan["query_clusters"].items():
            for query in spec["queries"]:
                s2_cache = _cache_path("semanticscholar", query)
                if not (offline and not s2_cache.exists()):
                    payload = _safe_fetch(
                        "semanticscholar",
                        query,
                        S2_URL,
                        {"query": query, "limit": limit, "fields": S2_FIELDS},
                        s2_cache,
                        client=client,
                    )
                    if payload:
                        collected.extend(_s2_rows(payload, query, cluster))

                crossref_cache = _cache_path("crossref", query)
                if offline and not crossref_cache.exists():
                    continue
                payload = _safe_fetch(
                    "crossref",
                    query,
                    CROSSREF_URL,
                    {"query.bibliographic": query, "rows": limit},
                    crossref_cache,
                    client=client,
                    sleep_seconds=1.0,
                )
                if payload:
                    collected.extend(_crossref_rows(payload, query, cluster))
    return collected


# --------------------------------------------------------------------------- normalize


def normalize_title(title: str) -> str:
    return _PUNCT.sub("", (title or "").lower())


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicates by DOI, else by normalized title; keep the richer row."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("title"):
            continue
        key = f"doi:{row['doi'].lower()}" if row.get("doi") else f"title:{normalize_title(row['title'])}"
        current = best.get(key)
        if current is None:
            best[key] = dict(row)
            continue
        # prefer the row that carries an abstract and an OA link
        for field in ("abstract", "oa_url", "venue", "year", "doi"):
            if not current.get(field) and row.get(field):
                current[field] = row[field]
        if row.get("author") or row.get("authors"):
            if len(str(row.get("authors") or "")) > len(str(current.get("authors") or "")):
                current["authors"] = row["authors"]
        if row.get("query_cluster") not in current.get("discovery_source", ""):
            current["discovery_source"] = f"{current['discovery_source']};{row['discovery_source']}"
    return list(best.values())


def relevance_score(row: dict[str, Any], weights: dict[str, int]) -> int:
    haystack = f"{row.get('title', '')} {row.get('abstract') or ''} {row.get('venue') or ''}".lower()
    return sum(int(weight) for pattern, weight in weights.items() if re.search(pattern, haystack))


def _pattern_hits(text: str, pattern: str) -> bool:
    """Match a pattern whose alternatives may be English phrases or Chinese substrings.

    English alternatives need word boundaries — otherwise ``kin`` matches ``Taking`` — while
    Chinese alternatives must stay substring matches (no whitespace to anchor on).
    """
    for alternative in pattern.split("|"):
        alternative = alternative.strip().lower()
        if not alternative:
            continue
        if re.fullmatch(r"[a-z0-9][a-z0-9 _\-&']*", alternative):
            if re.search(rf"\b{re.escape(alternative)}\b", text):
                return True
        elif alternative in text:
            return True
    return False


def is_on_topic(row: dict[str, Any], patterns: list[str]) -> bool:
    """Topical gate on the **title** only.

    Abstracts of off-topic papers routinely mention elites, officials or careers in passing
    (e.g. a paper on officials' suicides), so an abstract-level keyword hit is not evidence
    that the paper is about this project's question.
    """
    haystack = (row.get("title") or "").lower()
    return any(_pattern_hits(haystack, pattern) for pattern in patterns)


def collapse_versions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per paper title; demote other versions/preprints to candidate.

    The same paper often appears several times (journal article, repository copy, preprint)
    with different DOIs, so DOI deduplication cannot catch it. Within a title group the row
    carrying an abstract wins, then the more cited, then the later year; the rest are kept
    as candidates and annotated instead of being dropped.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(normalize_title(row["title"]), []).append(row)
    collapsed: list[dict[str, Any]] = []
    for group in grouped.values():
        ranked = sorted(
            group,
            key=lambda item: (
                bool(item.get("abstract")),
                int(item.get("cited_by") or 0),
                int(item.get("year") or 0),
            ),
            reverse=True,
        )
        keeper, *duplicates = ranked
        for duplicate in duplicates:
            duplicate["duplicate_of"] = keeper["title"]
        collapsed.extend(group)
    return collapsed


def score_and_rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan = load_plan()
    core = plan["core_selection"]
    topic_patterns = core.get("required_any_patterns") or []
    scored: list[dict[str, Any]] = []
    for row in dedupe_rows(rows):
        row = dict(row)
        row["relevance_score"] = relevance_score(row, core["weights"])
        row["topics"] = row.get("query_cluster")
        row["on_topic"] = is_on_topic(row, topic_patterns) if topic_patterns else True
        scored.append(row)
    scored.sort(key=lambda item: (-item["relevance_score"], -(item.get("year") or 0), item["title"]))
    eligible = [
        row
        for row in scored
        if row["on_topic"]
        and row["relevance_score"] >= int(core["min_score_for_core"])
        and (not core.get("require_abstract_or_fulltext") or row.get("abstract") or row.get("oa_url"))
    ]
    core_rows = _one_per_title(eligible)[: int(core["core_limit"])]
    core_ids = {id(row) for row in core_rows}

    collapsed = collapse_versions(scored)
    for row in collapsed:
        row["tier"] = "core" if id(row) in core_ids else "candidate"
    return collapsed


def _one_per_title(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the best version of each paper, preserving the input ordering."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = normalize_title(row["title"])
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        if _version_rank(row) > _version_rank(current):
            best[key] = row
    keep = {id(row) for row in best.values()}
    return [row for row in rows if id(row) in keep]


def _version_rank(row: dict[str, Any]) -> tuple:
    return (
        bool(row.get("abstract")),
        int(row.get("cited_by") or 0),
        int(row.get("year") or 0),
    )


def to_registry_row(row: dict[str, Any], literature_id: str) -> dict[str, Any]:
    if row.get("oa_url"):
        access_status = "OPEN_FULLTEXT_PDF" if row.get("oa_is_pdf") else "OPEN_FULLTEXT_HTML"
        rights = "OPEN_ACCESS"
    elif row.get("abstract"):
        access_status, rights = "ABSTRACT_ONLY", "UNKNOWN"
    else:
        access_status, rights = "METADATA_ONLY", "UNKNOWN"
    if access_status == "OPEN_FULLTEXT_PDF":
        evidence_level = "METADATA"  # only becomes FULLTEXT once parsing succeeds
    elif row.get("abstract"):
        evidence_level = "ABSTRACT"
    else:
        evidence_level = "METADATA"
    return {
        "literature_id": literature_id,
        "title": row["title"],
        "authors": row.get("authors") or None,
        "year": row.get("year"),
        "venue": row.get("venue"),
        "doi": row.get("doi"),
        "url": row.get("url"),
        "language": row.get("language") if row.get("language") in ("en", "zh", "ja") else "other",
        "type": row.get("type"),
        "query_cluster": row["query_cluster"],
        "discovery_source": row["discovery_source"],
        "retrieved_at": date.today().isoformat(),
        "abstract": row.get("abstract"),
        "relevance_score": int(row["relevance_score"]),
        "tier": row["tier"],
        "topics": row.get("topics"),
        "access_status": access_status,
        "rights_status": rights,
        "evidence_level": evidence_level,
        "local_file": None,
        "sha256": None,
        "normalized_text": None,
        "fulltext_verified": False,
        "notes": f"oa_status={row.get('oa_status')}; cited_by={row.get('cited_by')}; oa_url={row.get('oa_url')}",
    }


def load_manual_oa_urls(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Licence-clear open-access locations found by probing, keyed by DOI.

    Search APIs report no OA copy for most of these history journals; where a probe found a
    public author copy (e.g. an OSF preprint of a paywalled article) it is declared here with
    the evidence URL, and only then does the acquisition step fetch it.
    """
    path = path or LITERATURE_MANUAL_YAML
    if not path.exists():
        return {}
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides: dict[str, dict[str, Any]] = {}
    for entry in config.get("oa_urls") or []:
        for key in ("doi", "url", "evidence_url", "verified_at"):
            if not entry.get(key):
                raise LiteraturePlanError(f"oa_urls entry {entry.get('doi')!r} is missing {key}")
        overrides[entry["doi"].lower()] = entry
    return overrides


def apply_oa_overrides(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overrides = load_manual_oa_urls()
    for row in rows:
        doi = (row.get("doi") or "").lower()
        override = overrides.get(doi)
        if not override:
            continue
        row["oa_url"] = override["url"]
        row["oa_is_pdf"] = True
        row["oa_status"] = "probed_open_access"
        row["is_oa"] = True
    return rows


def load_manual_entries(path: Path | None = None) -> list[dict[str, Any]]:
    """Curated entries discovered by source probing, each with an evidence URL.

    The search APIs miss items the source probes surface (e.g. the 2026 Tongguanlu paper),
    so curation is explicit and auditable: no metadata without ``evidence_url``.
    """
    path = path or LITERATURE_MANUAL_YAML
    if not path.exists():
        return []
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = config.get("entries") or []
    for entry in entries:
        for key in ("title", "evidence_url", "retrieved_at"):
            if not entry.get(key):
                raise LiteraturePlanError(f"manual entry {entry.get('title')!r} is missing {key}")
        if not (entry.get("doi") or entry.get("url")):
            raise LiteraturePlanError(f"manual entry {entry['title']!r} needs a doi or url")
    return entries


def manual_rows() -> list[dict[str, Any]]:
    """Convert curated entries into the internal candidate row shape."""
    rows: list[dict[str, Any]] = []
    for entry in load_manual_entries():
        rows.append(
            {
                "title": entry["title"],
                "authors": entry.get("authors"),
                "year": entry.get("year"),
                "venue": entry.get("venue"),
                "doi": (entry.get("doi") or "").lower() or None,
                "url": entry.get("url") or entry["evidence_url"],
                "language": entry.get("language"),
                "type": "curated_entry",
                "abstract": entry.get("abstract"),
                "is_oa": bool(entry.get("oa_url")),
                "oa_status": "probed_open_access" if entry.get("oa_url") else None,
                "oa_url": entry.get("oa_url"),
                "oa_is_pdf": bool(entry.get("oa_url")),
                "cited_by": None,
                "query_cluster": entry.get("query_cluster") or "Q0_manual",
                "discovery_source": f"manual:{entry['evidence_url']}",
                "manual_tier": entry.get("tier_hint"),
                "retrieved_at": entry["retrieved_at"],
            }
        )
    return rows


def build_registry(*, offline: bool = False) -> pd.DataFrame:
    """Harvest, score, dedupe and register the candidate pool."""
    rows = apply_oa_overrides(score_and_rank(harvest_metadata(offline=offline) + manual_rows()))
    write_jsonl(CANDIDATES_JSONL, _serialisable(rows))
    log_path = RAW_DIR / "provider_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(PROVIDER_EVENTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ids = next_literature_ids(len(rows), existing=pd.DataFrame(columns=["literature_id"]))
    registry = pd.DataFrame([to_registry_row(row, lit_id) for row, lit_id in zip(rows, ids)])
    # curated entries keep their declared tier and retrieval date
    for row, literature_id in zip(rows, ids):
        if row.get("manual_tier"):
            registry.loc[registry["literature_id"] == literature_id, "tier"] = row["manual_tier"]
        if row.get("retrieved_at"):
            registry.loc[registry["literature_id"] == literature_id, "retrieved_at"] = row["retrieved_at"]
    registry["year"] = registry["year"].astype("Int64")
    registry["relevance_score"] = registry["relevance_score"].astype("Int64")
    registry = validate_registry(registry)
    write_jsonl(REGISTRY_JSONL, registry.to_dict(orient="records"))
    return registry


def _serialisable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in row.items() if k != "oa_is_pdf"} for row in rows]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="harvest literature metadata")
    parser.add_argument("--plan", action="store_true", help="print the query plan and exit")
    parser.add_argument("--offline", action="store_true", help="use only cached responses")
    parser.add_argument("--rows", type=int, default=None, help="rows per query (default from config)")
    args = parser.parse_args(argv)

    plan = load_plan()
    if args.plan:
        total = sum(len(spec["queries"]) for spec in plan["query_clusters"].values())
        print(json.dumps({k: v["queries"] for k, v in plan["query_clusters"].items()}, ensure_ascii=False, indent=2))
        print(f"queries={total} providers={plan['providers']} rows_per_query={plan['rows_per_query']}")
        return 0

    registry = build_registry(offline=args.offline)
    print(f"registry rows: {len(registry)}")
    print(f"core: {int((registry['tier'] == 'core').sum())}")
    print(f"with abstract: {int(registry['abstract'].notna().sum())}")
    print(f"with OA link: {int(registry['notes'].str.contains('oa_url=https').sum())}")
    print(registry.loc[registry["tier"] == "core", ["literature_id", "year", "relevance_score", "title"]].to_string(index=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
