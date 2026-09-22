# BigQuery Data Quality Analyzer

An automated Python CLI tool that helps the support team debug **data quality issues rooted in SQL code practices** inside BigQuery stored procedures (sprocs).

Given a STORY/DEMAND ID, the tool:

1. Looks up the associated target table and stored procedure from `config.yaml`
2. Fetches the sproc DDL from `INFORMATION_SCHEMA.ROUTINES` via the BigQuery API
3. Runs **8 SQL anti-pattern rule checks** against the sproc SQL text
4. Emits a structured `reports/<STORY-ID>.yaml` file with findings, severity ratings, and fix recommendations

> **Scope:** This tool targets *code quality issues* in sprocs — not bad source data. The premise is:
> `1 STORY → 1 target table → 1 stored procedure`

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.9+ |
| pip | 21+ |
| Google Cloud SDK (`gcloud`) | any recent version |

---

## Installation

```bash
# 1. Clone or download this project, then enter the directory
cd bq-dq-analyzer

# 2. (Recommended) Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Authentication

The tool uses **Application Default Credentials (ADC)**. No service account key files are needed.

```bash
gcloud auth application-default login
```

Ensure the authenticated account has the following BigQuery IAM permissions on the target project:

| Permission | Why |
|---|---|
| `bigquery.routines.get` | Read sproc DDL from INFORMATION_SCHEMA |
| `bigquery.jobs.create` | Execute the INFORMATION_SCHEMA query |

The minimum predefined role that covers both is **`roles/bigquery.metadataViewer`** plus **`roles/bigquery.jobUser`**.

---

## Configuration

Edit `config.yaml` and replace all placeholder values (`YOUR_GCP_PROJECT_ID`, etc.) with real values:

```yaml
bigquery:
  project_id: "my-gcp-project"       # GCP project owning the dataset
  dataset_id: "my_dataset"            # Dataset containing sprocs and tables
  location: "US"                      # BigQuery region: US, EU, us-central1, etc.

stories:
  - story_id: "STORY-101"
    table: "sales_daily_summary"      # Target table this story populates
    sproc: "sp_load_sales_daily"      # Stored procedure that populates it

  - story_id: "STORY-202"
    table: "customer_cohorts"
    sproc: "sp_build_customer_cohorts"
```

---

## Usage

### Analyze a single story

```bash
python main.py --story STORY-101
```

### Analyze all configured stories

```bash
python main.py --all
```

### Use a different config file or output directory

```bash
python main.py --all --config path/to/other_config.yaml --output-dir /tmp/dq-reports
```

### Full help

```bash
python main.py --help
```

### Example console output

```
🔍  Analyzing STORY-101 (table: sales_daily_summary, sproc: sp_load_sales_daily) ...
  [STORY-101] ⚠  HIGH: 3, MEDIUM: 1 | 4 finding(s) → /path/to/reports/STORY-101.yaml

🔍  Analyzing STORY-202 (table: customer_cohorts, sproc: sp_build_customer_cohorts) ...
  [STORY-202] ✓  No issues found | 0 finding(s) → /path/to/reports/STORY-202.yaml
```

**Exit codes:**
- `0` — analysis completed, no HIGH severity findings
- `1` — analysis completed, at least one HIGH severity finding was found

---

## Report Format

Each story produces a `reports/<STORY-ID>.yaml` file:

```yaml
story_id: STORY-101
table: sales_daily_summary
sproc: sp_load_sales_daily
sproc_ddl_fetched: true
error: null
summary:
  HIGH: 2
  MEDIUM: 1
  total: 3
findings:
  - rule_id: DQ-001
    severity: HIGH
    title: SELECT * Usage
    description: >
      The stored procedure uses SELECT *, which selects all columns without an
      explicit list. If the source table schema changes the sproc will silently
      produce incorrect or misaligned data in the target table.
    recommendation: >
      Replace SELECT * with an explicit column list. This makes the sproc
      resilient to upstream schema changes and documents the intended data
      contract clearly.
    evidence: SELECT *

  - rule_id: DQ-008
    severity: HIGH
    title: Missing Idempotency Guard
    description: >
      ...
    recommendation: >
      ...
    evidence: INSERT INTO or MERGE found without DELETE, TRUNCATE, WHERE NOT EXISTS, or MERGE guard branches.
```

If the sproc DDL cannot be fetched (e.g. wrong name, missing permissions), the report contains:

```yaml
sproc_ddl_fetched: false
error: "Stored procedure 'sp_xyz' not found in dataset 'project.dataset'."
findings: []
summary:
  HIGH: 0
  MEDIUM: 0
  total: 0
```

---

## Rule Reference

| Rule ID | Title | Severity | What it detects |
|---------|-------|----------|-----------------|
| **DQ-001** | SELECT * Usage | HIGH | `SELECT *` or `SELECT alias.*` — schema changes will silently misalign data |
| **DQ-002** | Missing NULL Handling | HIGH | Outer JOIN (LEFT/RIGHT/FULL) with no COALESCE / IFNULL / IS NULL guard — NULLs propagate silently |
| **DQ-003** | Implicit Type Casting | MEDIUM | Column named `*_ID`, `*_CODE`, `*_DATE`, etc. compared to a string literal without CAST |
| **DQ-004** | Missing Deduplication | HIGH | INSERT INTO or MERGE without DISTINCT / GROUP BY / QUALIFY ROW_NUMBER() — duplicates written silently |
| **DQ-005** | Unfiltered Full-Table Scan | HIGH | No WHERE clause or partition filter on any SELECT — reads all historical data on every run |
| **DQ-006** | CROSS JOIN / Cartesian Risk | HIGH | Explicit CROSS JOIN, or a JOIN with no ON/USING clause — row-count explosion |
| **DQ-007** | Hardcoded Date/Magic Literals | MEDIUM | Date strings `'YYYY-MM-DD'` or integers > 999 (excluding year values) hardcoded in SQL |
| **DQ-008** | Missing Idempotency Guard | HIGH | INSERT INTO or MERGE without DELETE / TRUNCATE / WHERE NOT EXISTS / WHEN NOT MATCHED — duplicate rows on reruns |

---

## Project Structure

```
bq-dq-analyzer/
├── config.yaml              # STORY → table → sproc mapping + BigQuery settings
├── requirements.txt         # Python dependencies
├── main.py                  # CLI entry point
├── analyzer/
│   ├── __init__.py
│   ├── bq_client.py         # Fetches sproc DDL from BigQuery INFORMATION_SCHEMA
│   ├── rules.py             # 8 anti-pattern rule functions + Finding dataclass
│   ├── analyzer.py          # Orchestrates rules, builds result dict
│   └── reporter.py          # Writes YAML report files
└── reports/                 # Output directory (gitignored)
```

---

## Adding a New Rule

1. Open [`analyzer/rules.py`](analyzer/rules.py)
2. Write a new function with the signature `def check_my_rule(sql: str) -> List[Finding]:`
3. Return a `Finding(rule_id="DQ-00X", severity="HIGH"|"MEDIUM", title=..., description=..., recommendation=..., evidence=...)` when the anti-pattern is detected
4. Append the function to the `ALL_RULES` list at the bottom of the file

The function will automatically be picked up by the analyzer on the next run — no other changes needed.
