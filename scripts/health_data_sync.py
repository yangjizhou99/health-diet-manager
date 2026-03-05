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
        except Exception: return default
    return default

def save_json(fp, data):
    tmp = fp.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(fp)

def set_location(location, data_dir):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / "external_data_config.json"
    cfg = load_json(cfg_path)
    cfg["health_data_location"] = location
    cfg["updated_at"] = datetime.now().isoformat()
    save_json(cfg_path, cfg)
    print(json.dumps({"status": "success", "message": f"成功保存外部数据位置: {location}"}, ensure_ascii=False))

def _find_health_data_root(root_dir, max_depth=3):
    """从用户指定的根目录开始，自动搜索包含 '健康同步 *' 子目录的那一层。
    支持任意目录结构，最多向下搜索 max_depth 层。"""
    HEALTH_MARKERS = ["健康同步 心率", "健康同步 睡眠", "健康同步 体重", "健康同步 步数"]
    
    def _has_health_subdirs(d):
        """检查目录 d 下是否存在至少 2 个健康数据标记子目录"""
        if not os.path.isdir(d):
            return False
        found = sum(1 for m in HEALTH_MARKERS if os.path.isdir(os.path.join(d, m)))
        return found >= 2
    
    # BFS 搜索，按层级依次检查
    from collections import deque
    queue = deque([(root_dir, 0)])
    while queue:
        current, depth = queue.popleft()
        if _has_health_subdirs(current):
            return current
        if depth < max_depth:
            try:
                for entry in os.scandir(current):
                    if entry.is_dir():
                        queue.append((entry.path, depth + 1))
            except PermissionError:
                pass
    return None

def fetch_data(period, target_date, data_dir):
    dd = Path(data_dir)
    cfg_path = dd / "external_data_config.json"
    cfg = load_json(cfg_path)
    
    if "health_data_location" not in cfg:
        print(json.dumps({"status": "error", "message": "未配置外部数据位置，请先执行 set-location"}, ensure_ascii=False))
        return

    loc = cfg["health_data_location"]
    
    # 智能搜索：从用户指定的根目录开始，自动查找包含 "健康同步 *" 子目录的层级
    extracted_dir = _find_health_data_root(loc)
    if extracted_dir is None:
        print(json.dumps({
            "status": "error",
            "message": f"在 {loc} 及其子目录中未找到包含 '健康同步 *' 数据文件夹的目录。请确认数据位置是否正确。"
        }, ensure_ascii=False))
        return
        
    try:
        # 动态导入引擎
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if scripts_dir not in sys.path:
            sys.path.append(scripts_dir)
            
        from health_metrics_engine import generate_health_report
        comprehensive_report = generate_health_report(extracted_dir, data_dir=data_dir)
        
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
