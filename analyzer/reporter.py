"""Writes per-STORY YAML diagnostic report files."""

from __future__ import annotations

import os
from typing import Any, Dict

import yaml


def write_report(result: Dict[str, Any], output_dir: str = "reports") -> str:
    """
    Write a YAML diagnostic report for a single story analysis result.

    Args:
        result:     The dict returned by ``analyze_story``.
        output_dir: Directory to write the report into (created if absent).

    Returns:
        The absolute path to the written YAML file.
    """
    os.makedirs(output_dir, exist_ok=True)

    story_id = result["story_id"]
    filename = os.path.join(output_dir, f"{story_id}.yaml")

    with open(filename, "w", encoding="utf-8") as fh:
        yaml.dump(
            result,
            fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )

    return os.path.abspath(filename)
