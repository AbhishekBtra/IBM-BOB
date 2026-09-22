"""SQL anti-pattern rule definitions and Finding dataclass.

Each rule function accepts the full SQL text of a stored procedure and returns
a (possibly empty) list of Finding objects describing issues found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List


# ─────────────────────────────────────────────────────────────────────────────
# Finding dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """Represents a single data quality code issue identified in a sproc."""

    rule_id: str
    severity: str          # "HIGH" or "MEDIUM"
    title: str
    description: str
    recommendation: str
    evidence: str = ""     # Short snippet or pattern that triggered the rule


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def _strip_comments(sql: str) -> str:
    """Remove single-line (--) and block (/* */) SQL comments."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def _normalise(sql: str) -> str:
    """Uppercase and collapse whitespace for reliable pattern matching."""
    return re.sub(r"\s+", " ", _strip_comments(sql)).upper().strip()


# ─────────────────────────────────────────────────────────────────────────────
# DQ-001 — SELECT * Usage
# ─────────────────────────────────────────────────────────────────────────────

def check_select_star(sql: str) -> List[Finding]:
    """DQ-001: Detect unqualified SELECT * which hides schema changes."""
    norm = _normalise(sql)
    # Match SELECT * or SELECT <alias>.* but not COUNT(*)
    matches = re.findall(r"SELECT\s+(?:\w+\.)?\*(?!\s*\))", norm)
    if not matches:
        return []
    return [Finding(
        rule_id="DQ-001",
        severity="HIGH",
        title="SELECT * Usage",
        description=(
            "The stored procedure uses SELECT *, which selects all columns "
            "without an explicit list. If the source table schema changes "
            "(column added, removed, or reordered) the sproc will silently "
            "produce incorrect or misaligned data in the target table."
        ),
        recommendation=(
            "Replace SELECT * with an explicit column list. This makes the "
            "sproc resilient to upstream schema changes and documents the "
            "intended data contract clearly."
        ),
        evidence=matches[0],
    )]


# ─────────────────────────────────────────────────────────────────────────────
# DQ-002 — Missing NULL Handling
# ─────────────────────────────────────────────────────────────────────────────

def check_null_handling(sql: str) -> List[Finding]:
    """DQ-002: LEFT/RIGHT/FULL JOIN present but no NULL-guard expressions."""
    norm = _normalise(sql)
    has_outer_join = bool(re.search(r"\b(LEFT|RIGHT|FULL)\s+(OUTER\s+)?JOIN\b", norm))
    if not has_outer_join:
        return []

    null_guards = ["COALESCE", "IFNULL", "IS NULL", "IS NOT NULL", "NULLIF", "IF("]
    has_guard = any(g in norm for g in null_guards)
    if has_guard:
        return []

    return [Finding(
        rule_id="DQ-002",
        severity="HIGH",
        title="Missing NULL Handling on Outer Join",
        description=(
            "The sproc performs a LEFT, RIGHT, or FULL OUTER JOIN but contains "
            "no NULL-guard expressions (COALESCE, IFNULL, IS NULL / IS NOT NULL, "
            "NULLIF). Unmatched rows from outer joins produce NULLs in all "
            "right-hand columns. Without guards these NULLs propagate silently "
            "into the target table, causing incorrect aggregations and broken "
            "downstream logic."
        ),
        recommendation=(
            "Wrap nullable columns from the non-driving side of the join with "
            "COALESCE(col, default) or IFNULL(col, default). Add explicit "
            "IS NULL / IS NOT NULL filters in WHERE or HAVING clauses where "
            "NULL rows must be excluded."
        ),
        evidence="Outer JOIN detected without any NULL-guard expression.",
    )]


# ─────────────────────────────────────────────────────────────────────────────
# DQ-003 — Implicit Type Casting
# ─────────────────────────────────────────────────────────────────────────────

def check_implicit_cast(sql: str) -> List[Finding]:
    """DQ-003: String literals compared to likely numeric/date columns without CAST."""
    norm = _normalise(sql)
    findings: List[Finding] = []

    # Pattern: a string literal on either side of = / <> / IN against a column
    # that looks numeric/date by name (ID, CODE, DATE, NUM, AMOUNT, etc.)
    implicit_patterns = [
        # e.g.  customer_id = '12345'
        (r"\b\w*(?:_ID|_CODE|_NUM|_KEY|_DATE|_TS|_TIME)\s*(?:=|<>|!=)\s*'[^']+'"  ,
         "Numeric/date-like column compared to a string literal without CAST"),
        # e.g.  '12345' = customer_id
        (r"'[^']+'\s*(?:=|<>|!=)\s*\w*(?:_ID|_CODE|_NUM|_KEY|_DATE|_TS|_TIME)\b",
         "String literal compared to a numeric/date-like column without CAST"),
    ]

    for pattern, evidence_msg in implicit_patterns:
        if re.search(pattern, norm):
            findings.append(Finding(
                rule_id="DQ-003",
                severity="MEDIUM",
                title="Implicit Type Casting Risk",
                description=(
                    "The sproc compares a column whose name suggests a numeric "
                    "or date type (_ID, _CODE, _NUM, _KEY, _DATE, _TS) directly "
                    "to a string literal without an explicit CAST. BigQuery may "
                    "silently coerce types, but a mismatch can cause incorrect "
                    "JOIN matches, wrong filter results, or runtime errors if the "
                    "actual column type changes."
                ),
                recommendation=(
                    "Use explicit CAST or SAFE_CAST: e.g. CAST(column AS STRING) "
                    "or CAST('123' AS INT64). This makes the intent unambiguous "
                    "and prevents silent data loss from failed coercions."
                ),
                evidence=evidence_msg,
            ))
            break  # one finding per sproc is sufficient

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# DQ-004 — Missing Deduplication
# ─────────────────────────────────────────────────────────────────────────────

def check_missing_dedup(sql: str) -> List[Finding]:
    """DQ-004: INSERT/MERGE target without deduplication in the SELECT."""
    norm = _normalise(sql)
    has_write = bool(re.search(r"\b(INSERT\s+INTO|MERGE\b)", norm))
    if not has_write:
        return []

    dedup_signals = [
        r"\bDISTINCT\b",
        r"\bGROUP\s+BY\b",
        r"\bQUALIFY\b",
        r"\bROW_NUMBER\s*\(",
        r"\bRANK\s*\(",
        r"\bDENSE_RANK\s*\(",
    ]
    has_dedup = any(re.search(p, norm) for p in dedup_signals)
    if has_dedup:
        return []

    return [Finding(
        rule_id="DQ-004",
        severity="HIGH",
        title="Missing Deduplication Before Write",
        description=(
            "The sproc performs an INSERT INTO or MERGE but the SELECT feeding "
            "it contains no deduplication logic (no DISTINCT, GROUP BY, QUALIFY "
            "ROW_NUMBER(), QUALIFY RANK()). If the source data or join fanout "
            "produces duplicate rows, they will be written to the target table "
            "silently, inflating row counts and corrupting aggregations."
        ),
        recommendation=(
            "Add a deduplication step before writing: use SELECT DISTINCT for "
            "simple cases, GROUP BY with explicit aggregations, or a QUALIFY "
            "ROW_NUMBER() OVER (PARTITION BY <key> ORDER BY <tie-breaker>) = 1 "
            "window filter to keep exactly one row per business key."
        ),
        evidence="INSERT INTO or MERGE detected without DISTINCT / GROUP BY / QUALIFY.",
    )]


# ─────────────────────────────────────────────────────────────────────────────
# DQ-005 — Unfiltered Full-Table Scan
# ─────────────────────────────────────────────────────────────────────────────

def check_unfiltered_scan(sql: str) -> List[Finding]:
    """DQ-005: SELECT from a table with no WHERE / partition filter."""
    norm = _normalise(sql)

    # Only flag if there is at least one FROM <table> reference
    has_from = bool(re.search(r"\bFROM\s+`?\w", norm))
    if not has_from:
        return []

    has_where = bool(re.search(r"\bWHERE\b", norm))
    partition_signals = [
        "_PARTITIONTIME", "_PARTITIONDATE",
        "DATE(", "TIMESTAMP(", "DATETIME(",
        "CURRENT_DATE", "CURRENT_TIMESTAMP",
        "DATE_SUB", "DATE_ADD", "TIMESTAMP_SUB", "TIMESTAMP_ADD",
    ]
    has_partition_filter = any(s in norm for s in partition_signals)

    if has_where or has_partition_filter:
        return []

    return [Finding(
        rule_id="DQ-005",
        severity="HIGH",
        title="Unfiltered Full-Table Scan",
        description=(
            "The sproc reads from a table without any WHERE clause or partition "
            "filter. On large or partitioned BigQuery tables this reads ALL "
            "historical data on every execution, risking stale data inclusion, "
            "excessive cost, and incorrect results when the sproc is intended "
            "to process only recent or incremental data."
        ),
        recommendation=(
            "Add a WHERE clause that filters on the partition column "
            "(_PARTITIONDATE, _PARTITIONTIME, or a date/timestamp column). "
            "For incremental loads, parameterise the date range using DECLARE "
            "variables so the same sproc handles both full and incremental runs."
        ),
        evidence="FROM clause detected without WHERE or partition filter.",
    )]


# ─────────────────────────────────────────────────────────────────────────────
# DQ-006 — CROSS JOIN / Cartesian Product Risk
# ─────────────────────────────────────────────────────────────────────────────

def check_cross_join(sql: str) -> List[Finding]:
    """DQ-006: Explicit CROSS JOIN or JOIN without an ON/USING clause."""
    norm = _normalise(sql)
    findings: List[Finding] = []

    # Explicit CROSS JOIN
    if re.search(r"\bCROSS\s+JOIN\b", norm):
        findings.append(Finding(
            rule_id="DQ-006",
            severity="HIGH",
            title="Explicit CROSS JOIN Detected",
            description=(
                "The sproc contains an explicit CROSS JOIN. Unless the joined "
                "table is guaranteed to always return exactly one row (e.g. a "
                "single-row config/lookup table), a CROSS JOIN will multiply "
                "every row in the left table by every row in the right table, "
                "producing an explosive row count and silently duplicating data "
                "in the target table."
            ),
            recommendation=(
                "Replace CROSS JOIN with an INNER JOIN or LEFT JOIN with an "
                "explicit ON clause. If the intent is a single-value broadcast "
                "(e.g. a scalar config lookup), use a scalar subquery or a CTE "
                "with LIMIT 1 instead."
            ),
            evidence="CROSS JOIN keyword found.",
        ))

    # JOIN without ON or USING — scan each JOIN token
    join_pattern = re.compile(
        r"\b(?:INNER|LEFT(?:\s+OUTER)?|RIGHT(?:\s+OUTER)?|FULL(?:\s+OUTER)?)\s+JOIN\b"
    )
    on_using_pattern = re.compile(r"\b(ON|USING)\b")

    # Split on each JOIN occurrence and check if ON/USING follows before next JOIN/WHERE
    segments = re.split(r"\b(?:INNER|LEFT|RIGHT|FULL|CROSS)\s+(?:OUTER\s+)?JOIN\b", norm)
    # segments[0] is before first JOIN; segments[1..] are after each JOIN keyword
    for seg in segments[1:]:
        # Take text up to the next JOIN keyword or end of statement
        next_join = re.search(r"\b(?:INNER|LEFT|RIGHT|FULL|CROSS)\s+(?:OUTER\s+)?JOIN\b", seg)
        chunk = seg[:next_join.start()] if next_join else seg
        if not on_using_pattern.search(chunk):
            findings.append(Finding(
                rule_id="DQ-006",
                severity="HIGH",
                title="JOIN Without ON or USING Clause",
                description=(
                    "A JOIN in the sproc has no ON or USING clause. BigQuery "
                    "will treat this as a cartesian product (like CROSS JOIN), "
                    "multiplying rows and silently producing inflated or "
                    "duplicated data in the target table."
                ),
                recommendation=(
                    "Add an explicit ON <left_key> = <right_key> or "
                    "USING (<shared_key_column>) clause to every JOIN. "
                    "Verify the join keys are of matching types and are never NULL "
                    "unless a NULL-safe comparison (IS NOT DISTINCT FROM) is intended."
                ),
                evidence="JOIN keyword found without a following ON or USING clause.",
            ))
            break  # one finding per sproc is sufficient

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# DQ-007 — Hardcoded Date / Magic Number Literals
# ─────────────────────────────────────────────────────────────────────────────

def check_hardcoded_literals(sql: str) -> List[Finding]:
    """DQ-007: Hardcoded date strings or large magic number literals."""
    norm = _normalise(sql)
    findings: List[Finding] = []

    # Hardcoded date literals: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'
    date_matches = re.findall(r"'\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?'", norm)
    if date_matches:
        findings.append(Finding(
            rule_id="DQ-007",
            severity="MEDIUM",
            title="Hardcoded Date Literal",
            description=(
                "The sproc contains one or more hardcoded date or datetime "
                "string literals (e.g. '2023-01-01'). Hardcoded dates make the "
                "sproc brittle: it will silently reprocess stale data or miss "
                "recent data if the literal is not updated when the sproc is "
                "reused in a new period."
            ),
            recommendation=(
                "Replace hardcoded dates with DECLARE parameters at the top of "
                "the sproc (e.g. DECLARE start_date DATE DEFAULT CURRENT_DATE - 1) "
                "or accept them as procedure input parameters. This makes the "
                "processing window explicit and reusable."
            ),
            evidence=f"Hardcoded date literals found: {', '.join(set(date_matches[:3]))}",
        ))

    # Magic number literals > 999 that are not inside a LIMIT clause
    # Strip LIMIT <n> occurrences first to avoid false positives
    norm_no_limit = re.sub(r"\bLIMIT\s+\d+", " ", norm)
    magic_matches = re.findall(r"\b([1-9]\d{3,})\b", norm_no_limit)
    # Filter out year-like values (1900–2099) which may appear in date arithmetic
    magic_matches = [m for m in magic_matches if not (1900 <= int(m) <= 2099)]
    if magic_matches:
        findings.append(Finding(
            rule_id="DQ-007",
            severity="MEDIUM",
            title="Magic Number Literal",
            description=(
                "The sproc contains numeric literals with no obvious semantic "
                "meaning (values > 999 excluding years). Magic numbers make the "
                "sproc hard to maintain: their purpose is undocumented and "
                "changing business rules (thresholds, bucket sizes) requires "
                "a code edit rather than a config change."
            ),
            recommendation=(
                "Replace magic numbers with DECLARE variables at the top of the "
                "sproc with descriptive names, or pass them as input parameters. "
                "Add a comment explaining the business meaning of each threshold."
            ),
            evidence=f"Magic number literals found: {', '.join(set(str(m) for m in magic_matches[:3]))}",
        ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# DQ-008 — Missing Idempotency Guard
# ─────────────────────────────────────────────────────────────────────────────

def check_idempotency(sql: str) -> List[Finding]:
    """DQ-008: INSERT INTO or MERGE without a prior DELETE/TRUNCATE or WHERE NOT EXISTS."""
    norm = _normalise(sql)
    has_insert = bool(re.search(r"\bINSERT\s+INTO\b", norm))
    has_merge = bool(re.search(r"\bMERGE\b", norm))

    if not (has_insert or has_merge):
        return []

    idempotency_signals = [
        r"\bDELETE\s+FROM\b",
        r"\bTRUNCATE\s+TABLE\b",
        r"\bWHERE\s+NOT\s+EXISTS\b",
        r"\bWHEN\s+NOT\s+MATCHED\b",   # MERGE idempotency branch
        r"\bWHEN\s+MATCHED\b",          # MERGE update branch
        r"\bCREATE\s+OR\s+REPLACE\b",
        r"\bWRITE_TRUNCATE\b",
    ]
    has_guard = any(re.search(p, norm) for p in idempotency_signals)
    if has_guard:
        return []

    return [Finding(
        rule_id="DQ-008",
        severity="HIGH",
        title="Missing Idempotency Guard",
        description=(
            "The sproc performs an INSERT INTO or MERGE but has no idempotency "
            "guard: no preceding DELETE/TRUNCATE, no WHERE NOT EXISTS filter, "
            "and no WHEN NOT MATCHED / WHEN MATCHED MERGE branches. "
            "If the sproc is re-run (due to a pipeline retry, manual re-trigger, "
            "or scheduling overlap) it will silently insert duplicate rows into "
            "the target table without error."
        ),
        recommendation=(
            "Choose one of: (a) add a DELETE FROM target WHERE <date_key> = @run_date "
            "before the INSERT to clear the partition first; "
            "(b) use CREATE OR REPLACE TABLE AS SELECT for full refreshes; "
            "(c) replace INSERT with a full MERGE statement that handles both "
            "WHEN MATCHED THEN UPDATE and WHEN NOT MATCHED THEN INSERT; "
            "(d) add a WHERE NOT EXISTS (SELECT 1 FROM target WHERE key = src.key) "
            "guard on the INSERT."
        ),
        evidence="INSERT INTO or MERGE found without DELETE, TRUNCATE, WHERE NOT EXISTS, or MERGE guard branches.",
    )]


# ─────────────────────────────────────────────────────────────────────────────
# Rule registry — all rules are run in this order
# ─────────────────────────────────────────────────────────────────────────────

ALL_RULES: List[Callable[[str], List[Finding]]] = [
    check_select_star,
    check_null_handling,
    check_implicit_cast,
    check_missing_dedup,
    check_unfiltered_scan,
    check_cross_join,
    check_hardcoded_literals,
    check_idempotency,
]
