"""Orchestrates anti-pattern rules against a fetched sproc DDL."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from analyzer.bq_client import BigQueryClient
from analyzer.rules import ALL_RULES, Finding


def analyze_story(story_config: Dict[str, str], bq_client: BigQueryClient) -> Dict[str, Any]:
    """
    Fetch the sproc DDL for a single story and run all anti-pattern rules.

    Args:
        story_config: Dict with keys ``story_id``, ``table``, ``sproc``.
        bq_client:    Authenticated BigQueryClient instance.

    Returns:
        A result dict with keys:
          - story_id
          - table
          - sproc
          - sproc_ddl_fetched  (bool)
          - error              (str | None) — set if DDL fetch failed
          - findings           (list of finding dicts)
          - summary            ({"HIGH": n, "MEDIUM": n, "total": n})
    """
    story_id = story_config["story_id"]
    table = story_config["table"]
    sproc = story_config["sproc"]

    result: Dict[str, Any] = {
        "story_id": story_id,
        "table": table,
        "sproc": sproc,
        "sproc_ddl_fetched": False,
        "error": None,
        "findings": [],
        "summary": {"HIGH": 0, "MEDIUM": 0, "total": 0},
    }

    # ── Fetch DDL ────────────────────────────────────────────────────────────
    try:
        ddl = bq_client.get_sproc_ddl(sproc)
        result["sproc_ddl_fetched"] = True
    except ValueError as exc:
        result["error"] = str(exc)
        return result

    # ── Run all rules ────────────────────────────────────────────────────────
    findings: list[Finding] = []
    for rule_fn in ALL_RULES:
        findings.extend(rule_fn(ddl))

    # ── Build output ─────────────────────────────────────────────────────────
    result["findings"] = [asdict(f) for f in findings]

    high_count = sum(1 for f in findings if f.severity == "HIGH")
    medium_count = sum(1 for f in findings if f.severity == "MEDIUM")
    result["summary"] = {
        "HIGH": high_count,
        "MEDIUM": medium_count,
        "total": high_count + medium_count,
    }

    return result
