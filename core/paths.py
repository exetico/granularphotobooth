"""Helpers for locating output folders and turning file paths into URLs."""

import os


def output_dirs(config: dict) -> tuple[str, str]:
    disk_cfg = config.get("storage", {}).get("local_disk", {})
    raw_dir = disk_cfg.get("raw_dir", disk_cfg.get("output_dir", "output/raw/"))
    composite_dir = disk_cfg.get("composite_dir", disk_cfg.get("output_dir", "output/composites/"))
    return os.path.abspath(raw_dir), os.path.abspath(composite_dir)


def output_root(config: dict) -> str:
    """Common parent of the raw + composite folders (served at /output)."""
    raw_dir, composite_dir = output_dirs(config)
    return os.path.commonpath([raw_dir, composite_dir])
