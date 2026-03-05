#!/usr/bin/env python3
"""
health_data_sync.py - 外部健康数据同步工具
功能: set-location, fetch
"""
import argparse
import json
import os
import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse


def load_json(fp, default=None):
    if default is None:
        default = {}
    if fp.exists():
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(fp, data):
    tmp = fp.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(fp)


def _is_remote_location(location):
    # Windows drive letter like C:\... should not be treated as URL.
    if len(location) >= 2 and location[1] == ":":
        return False
    parsed = urlparse(location)
    return parsed.scheme in {"http", "https", "ftp", "s3", "gs"}


def _parse_target_date(target_date):
    try:
        return datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_range(period, target_date):
    end = _parse_target_date(target_date)
    if end is None:
        return None, None

    if period == "day":
        start = end
    elif period == "week":
        start = end - timedelta(days=6)
    else:
        start = end.replace(day=1)
    return start, end


def set_location(location, data_dir):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / "external_data_config.json"
    cfg = load_json(cfg_path)
    cfg["health_data_location"] = location
    cfg["updated_at"] = datetime.now().isoformat()
    save_json(cfg_path, cfg)
    print(json.dumps({"status": "success", "message": f"成功保存外部数据位置: {location}"}, ensure_ascii=False))
    return 0


def _find_health_data_root(root_dir, max_depth=3):
    """
    从 root_dir 开始，向下搜索包含“健康同步 *”关键子目录的层级。
    """
    if not os.path.isdir(root_dir):
        return None

    health_markers = ["健康同步 心率", "健康同步 睡眠", "健康同步 体重", "健康同步 步数"]

    def _has_health_subdirs(d):
        if not os.path.isdir(d):
            return False
        found = sum(1 for marker in health_markers if os.path.isdir(os.path.join(d, marker)))
        return found >= 2

    queue = deque([(root_dir, 0)])
    while queue:
        current, depth = queue.popleft()
        if _has_health_subdirs(current):
            return current
        if depth >= max_depth:
            continue
        try:
            for entry in os.scandir(current):
                if entry.is_dir():
                    queue.append((entry.path, depth + 1))
        except OSError:
            # 忽略无法访问目录，继续扫描其他路径。
            continue
    return None


def _error_result(period, target_date, message):
    return {
        "status": "error",
        "period": period,
        "target_date": target_date,
        "metrics": {},
        "message": message,
    }


def fetch_data(period, target_date, data_dir):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / "external_data_config.json"
    cfg = load_json(cfg_path)

    if "health_data_location" not in cfg:
        result = _error_result(period, target_date, "未配置外部数据位置，请先执行 set-location")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    start_date, end_date = _resolve_range(period, target_date)
    if start_date is None:
        result = _error_result(period, target_date, "target-date 格式错误，必须是 YYYY-MM-DD")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    loc = cfg["health_data_location"]
    if _is_remote_location(loc):
        result = _error_result(period, target_date, f"当前仅支持本地目录，不支持远程 URL: {loc}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    if not Path(loc).is_dir():
        result = _error_result(period, target_date, f"数据目录不存在或不可访问: {loc}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    extracted_dir = _find_health_data_root(loc)
    if extracted_dir is None:
        result = _error_result(period, target_date, f"在 {loc} 及其子目录中未找到包含“健康同步 *”数据文件夹的目录")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    try:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if scripts_dir not in sys.path:
            sys.path.append(scripts_dir)

        from health_metrics_engine import generate_health_report

        comprehensive_report = generate_health_report(
            extracted_dir,
            data_dir=data_dir,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

        output_data = {
            "status": "success",
            "period": period,
            "target_date": target_date,
            "metrics": comprehensive_report.get("metrics", {}),
            "message": f"成功从 {loc} 抓取并计算了 {start_date} ~ {end_date} 的健康数据",
        }
        exit_code = 0
    except Exception as e:
        output_data = _error_result(period, target_date, f"健康数据引擎运行失败: {e}")
        exit_code = 1

    cache_path = dd / f"health_cache_{period}_{target_date}.json"
    save_json(cache_path, output_data)
    print(json.dumps(output_data, ensure_ascii=False, indent=2))
    return exit_code


def main():
    pa = argparse.ArgumentParser(description="外部健康数据同步工具")
    sp = pa.add_subparsers(dest="command")

    p1 = sp.add_parser("set-location")
    p1.add_argument("--location", required=True)
    p1.add_argument("--data-dir", required=True)

    p2 = sp.add_parser("fetch")
    p2.add_argument("--period", required=True, choices=["day", "week", "month"])
    p2.add_argument("--target-date", required=True)
    p2.add_argument("--data-dir", required=True)

    args = pa.parse_args()
    if args.command == "set-location":
        return set_location(args.location, args.data_dir)
    if args.command == "fetch":
        return fetch_data(args.period, args.target_date, args.data_dir)
    pa.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
