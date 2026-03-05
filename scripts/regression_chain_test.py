#!/usr/bin/env python3
"""
Minimal regression test for the energy uncertainty pipeline:
1) health_metrics_engine output
2) summary_report merged JSON payload
3) notion_health_sync preview blocks
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    raise SystemExit(1)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail(f"Cannot read JSON file: {path} ({exc})")


def _assert_keys(data: dict, keys, label: str):
    missing = [k for k in keys if k not in data]
    if missing:
        _fail(f"{label} missing keys: {missing}")


def _extract_json_from_stdout(stdout: str) -> dict:
    text = (stdout or "").strip()
    if not text:
        _fail("Command returned empty stdout")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            _fail("Could not locate JSON object in command stdout")
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            _fail(f"Invalid JSON payload in stdout: {exc}")


def _iter_text(obj):
    if isinstance(obj, dict):
        if obj.get("type") == "text" and isinstance(obj.get("text"), dict):
            content = obj["text"].get("content")
            if isinstance(content, str):
                yield content
        for v in obj.values():
            yield from _iter_text(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_text(item)


def main():
    parser = argparse.ArgumentParser(description="Run end-to-end regression checks for energy uncertainty fields")
    parser.add_argument("--date", default="2026-03-05", help="Target date for daily report (YYYY-MM-DD)")
    parser.add_argument("--data-dir", default=None, help="Data directory (default: <project>/data)")
    parser.add_argument("--sample-extracted", default=None, help="Sample extracted health folder")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent.parent
    data_dir = Path(args.data_dir) if args.data_dir else (project_dir / "data")
    sample_extracted = Path(args.sample_extracted) if args.sample_extracted else (project_dir.parent / "健康信息例子" / "extracted")

    if not data_dir.exists():
        _fail(f"Data directory not found: {data_dir}")
    if not sample_extracted.exists():
        _fail(f"Sample extracted directory not found: {sample_extracted}")

    scripts_dir = project_dir / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from health_metrics_engine import generate_health_report

    print("[STEP 1] Checking engine output fields...")
    report = generate_health_report(
        str(sample_extracted),
        data_dir=str(data_dir),
        start_date=args.date,
        end_date=args.date,
    )
    energy = report.get("metrics", {}).get("energy_expenditure", {})
    if not energy:
        _fail("Engine returned no energy_expenditure data")

    first_day = sorted(energy.keys())[0]
    day_energy = energy[first_day]
    _assert_keys(day_energy, [
        "active_burn_kcal",
        "active_burn_kcal_low",
        "active_burn_kcal_high",
        "tdee_kcal",
        "tdee_kcal_low",
        "tdee_kcal_high",
        "active_burn_method",
        "active_burn_confidence_score",
        "active_burn_confidence_label",
        "active_burn_assumptions",
    ], "Engine energy payload")

    print("[STEP 2] Checking merged report llm_objective_input fields...")
    cmd_report = [
        sys.executable,
        str(scripts_dir / "summary_report.py"),
        "generate-merged",
        "--type",
        "daily",
        "--end-date",
        args.date,
        "--data-dir",
        str(data_dir),
    ]
    proc_report = subprocess.run(
        cmd_report,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc_report.returncode != 0:
        _fail(f"summary_report.py failed: {proc_report.stderr.strip()}")

    report_payload = _extract_json_from_stdout(proc_report.stdout)
    output_path_raw = report_payload.get("saved_report_json_path")
    if not output_path_raw:
        _fail("summary_report output did not include saved_report_json_path")

    output_path = Path(output_path_raw)
    if not output_path.is_absolute():
        output_path = (project_dir / output_path).resolve()
    if not output_path.exists():
        _fail(f"Generated report JSON not found: {output_path}")

    merged_json = _load_json(output_path)
    energy_obj = merged_json.get("llm_objective_input", {}).get("energy", {})
    _assert_keys(energy_obj, [
        "avg_tdee_kcal_low",
        "avg_tdee_kcal_high",
        "avg_active_burn_kcal_low",
        "avg_active_burn_kcal_high",
        "estimated_from_hr_days",
        "active_burn_confidence_labels",
    ], "Merged report llm_objective_input.energy")

    print("[STEP 3] Checking Notion preview includes uncertainty context...")
    cmd_preview = [
        sys.executable,
        str(scripts_dir / "notion_health_sync.py"),
        "preview",
        "--report-file",
        str(output_path),
        "--data-dir",
        str(data_dir),
    ]
    proc_preview = subprocess.run(
        cmd_preview,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc_preview.returncode != 0:
        _fail(f"notion_health_sync.py preview failed: {proc_preview.stderr.strip()}")

    preview_path = data_dir / "notion_template_preview.json"
    if not preview_path.exists():
        _fail(f"Notion preview file not found: {preview_path}")

    blocks = _load_json(preview_path)
    all_text = "\n".join(_iter_text(blocks))
    if "置信度" not in all_text and "心率回退估算" not in all_text:
        _fail("Notion preview does not contain uncertainty/estimation text")

    print("[PASS] End-to-end regression checks passed")
    print(f"  date: {args.date}")
    print(f"  report_json: {output_path}")
    print(f"  notion_preview: {preview_path}")


if __name__ == "__main__":
    main()
