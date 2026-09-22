"""CLI entry point for the BigQuery Data Quality Analyzer.

Usage:
    python main.py --story STORY-001
    python main.py --all
    python main.py --all --config path/to/other_config.yaml
"""

from __future__ import annotations

import sys
from typing import List

import click
import yaml

from analyzer.analyzer import analyze_story
from analyzer.bq_client import BigQueryClient
from analyzer.reporter import write_report


# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_config(config_path: str) -> dict:
    """Load and minimally validate config.yaml."""
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
    except FileNotFoundError:
        raise click.ClickException(
            f"Config file not found: '{config_path}'. "
            "Copy config.yaml and populate it with your GCP settings."
        )

    if not isinstance(cfg, dict):
        raise click.ClickException(f"Config file '{config_path}' is not valid YAML.")

    if "bigquery" not in cfg:
        raise click.ClickException(
            "Missing required 'bigquery' section in config.yaml. "
            "See config.yaml for the expected schema."
        )

    bq = cfg["bigquery"]
    for key in ("project_id", "dataset_id"):
        if not bq.get(key) or bq[key].startswith("YOUR_"):
            raise click.ClickException(
                f"config.yaml bigquery.{key} is not set. "
                "Replace the placeholder value with your real GCP setting."
            )

    if "stories" not in cfg or not isinstance(cfg["stories"], list):
        raise click.ClickException(
            "Missing or empty 'stories' list in config.yaml."
        )

    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--story", "-s",
    default=None,
    help="Analyze a single STORY by its ID (e.g. STORY-001).",
)
@click.option(
    "--all", "run_all",
    is_flag=True,
    default=False,
    help="Analyze all stories defined in config.yaml.",
)
@click.option(
    "--config", "-c",
    default="config.yaml",
    show_default=True,
    help="Path to the configuration file.",
)
@click.option(
    "--output-dir", "-o",
    default="reports",
    show_default=True,
    help="Directory to write YAML report files into.",
)
def main(story: str | None, run_all: bool, config: str, output_dir: str) -> None:
    """BigQuery Data Quality Analyzer — detect SQL anti-patterns in sprocs."""

    if not story and not run_all:
        raise click.UsageError(
            "Specify --story <ID> to analyze one story, or --all to analyze all stories."
        )

    cfg = _load_config(config)
    bq_cfg = cfg["bigquery"]
    all_stories: List[dict] = cfg["stories"]

    # ── Select target stories ─────────────────────────────────────────────
    if run_all:
        targets = all_stories
    else:
        targets = [s for s in all_stories if s["story_id"] == story]
        if not targets:
            raise click.ClickException(
                f"Story '{story}' not found in {config}. "
                "Check the story_id value or add it to the stories list."
            )

    # ── Build BQ client ───────────────────────────────────────────────────
    bq_client = BigQueryClient(
        project_id=bq_cfg["project_id"],
        dataset_id=bq_cfg["dataset_id"],
        location=bq_cfg.get("location", "US"),
    )

    # ── Analyze & report ──────────────────────────────────────────────────
    any_high = False

    for story_cfg in targets:
        sid = story_cfg["story_id"]
        click.echo(f"\n🔍  Analyzing {sid} (table: {story_cfg['table']}, sproc: {story_cfg['sproc']}) ...")

        result = analyze_story(story_cfg, bq_client)

        if not result["sproc_ddl_fetched"]:
            click.secho(
                f"  [ERROR] {sid} — could not fetch sproc DDL: {result['error']}",
                fg="red",
            )
            continue

        report_path = write_report(result, output_dir=output_dir)

        summary = result["summary"]
        if summary["total"] == 0:
            status_line = click.style("✓  No issues found", fg="green")
        else:
            parts = []
            if summary["HIGH"]:
                parts.append(click.style(f"HIGH: {summary['HIGH']}", fg="red", bold=True))
            if summary["MEDIUM"]:
                parts.append(click.style(f"MEDIUM: {summary['MEDIUM']}", fg="yellow"))
            status_line = "⚠  " + ", ".join(parts)

        click.echo(
            f"  [{sid}] {status_line} | "
            f"{summary['total']} finding(s) → {report_path}"
        )

        if summary["HIGH"] > 0:
            any_high = True

    click.echo("")

    # ── Exit code ─────────────────────────────────────────────────────────
    if any_high:
        sys.exit(1)


if __name__ == "__main__":
    main()
