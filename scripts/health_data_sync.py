#!/usr/bin/env python3
"""
health_data_sync.py - 外部健康数据同步与切片抓取 (V2)
功能: set-location, fetch
"""
import argparse, json
import os
import sys
from pathlib import Path
from datetime import datetime

def load_json(fp, default=None):
    if default is None: default = {}
    if fp.exists():
        try:
            with open(fp, 'r', encoding='utf-8') as f: return json.load(f)
        except: return default
    return default

def save_json(fp, data):
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def set_location(location, data_dir):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / "external_data_config.json"
    cfg = load_json(cfg_path)
    cfg["health_data_location"] = location
    cfg["updated_at"] = datetime.now().isoformat()
    save_json(cfg_path, cfg)
    print(json.dumps({"status": "success", "message": f"成功保存外部数据位置: {location}"}, ensure_ascii=False))

def fetch_data(period, target_date, data_dir):
    dd = Path(data_dir)
    cfg_path = dd / "external_data_config.json"
    cfg = load_json(cfg_path)
    
    if "health_data_location" not in cfg:
        print(json.dumps({"status": "error", "message": "未配置外部数据位置，请先执行 set-location"}, ensure_ascii=False))
        return

    loc = cfg["health_data_location"]
    
    # 尝试找到提取目录
    extracted_dir = os.path.join(loc, "健康信息例子", "extracted") if "健康信息例子" in loc else os.path.join(loc, "extracted")
    if not os.path.exists(extracted_dir):
        extracted_dir = loc
        
    try:
        # 动态导入引擎
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if scripts_dir not in sys.path:
            sys.path.append(scripts_dir)
            
        from health_metrics_engine import generate_health_report
        comprehensive_report = generate_health_report(extracted_dir)
        
        output_data = {
            "status": "success",
            "period": period,
            "target_date": target_date,
            "metrics": comprehensive_report.get("metrics", {}),
            "message": f"成功从 {loc} 引擎抓取并计算了全维健康数据"
        }
        
    except Exception as e:
        output_data = {
            "status": "error",
            "period": period,
            "target_date": target_date,
            "metrics": {},
            "message": f"健康数据引擎运行失败: {str(e)}"
        }

    cache_path = dd / f"health_cache_{period}_{target_date}.json"
    save_json(cache_path, output_data)
    
    print(json.dumps(output_data, ensure_ascii=False, indent=2))

def main():
    pa = argparse.ArgumentParser(description="外部健康数据同步工具 (V2)")
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
        set_location(args.location, args.data_dir)
    elif args.command == "fetch":
        fetch_data(args.period, args.target_date, args.data_dir)
    else:
        pa.print_help()

if __name__ == "__main__":
    main()
