#!/usr/bin/env python3
"""
skill_updater.py - 技能自我更新管理器
功能: preview (生成修改预览), apply (应用修改), history (查看历史)
"""
import argparse, json, os, shutil, sys
from datetime import datetime
from pathlib import Path

def load_json(fp, default=None):
    if default is None: default = {}
    if fp.exists():
        try:
            with open(fp,'r',encoding='utf-8') as f: return json.load(f)
        except Exception: return default
    return default

def save_json(fp, data):
    tmp = fp.with_suffix('.tmp')
    with open(tmp,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)
    tmp.replace(fp)

def cmd_preview(args):
    """备份目标文件并返回当前内容供AI生成diff"""
    target = Path(args.target_file)
    if not target.exists():
        print(json.dumps({"status":"error","message":f"文件不存在: {args.target_file}"},ensure_ascii=False))
        sys.exit(1)

    dd = Path(args.data_dir); dd.mkdir(parents=True, exist_ok=True)
    backup_dir = dd / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 创建备份
    bid = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{target.name}.{bid}.bak"
    shutil.copy2(target, backup_path)

    # 读取当前内容
    with open(target, 'r', encoding='utf-8') as f:
        content = f.read()

    print(json.dumps({
        "status": "success",
        "message": "已备份文件，请生成修改方案",
        "backup_id": bid,
        "backup_path": str(backup_path),
        "target_file": str(target),
        "current_content_lines": len(content.splitlines()),
        "description": args.description,
    }, ensure_ascii=False, indent=2))

def cmd_apply(args):
    """应用修改（AI已经修改了文件后，记录更新历史）"""
    dd = Path(args.data_dir); dd.mkdir(parents=True, exist_ok=True)
    hist_path = dd / "update_history.json"
    hist = load_json(hist_path, {"updates": []})

    update_entry = {
        "id": args.backup_id,
        "timestamp": datetime.now().isoformat(),
        "target_file": args.target_file,
        "description": args.description,
        "result": "approved",
        "backup_path": str(Path(args.data_dir) / "backups" / f"{Path(args.target_file).name}.{args.backup_id}.bak"),
    }

    hist["updates"].append(update_entry)
    save_json(hist_path, hist)

    print(json.dumps({
        "status": "success",
        "message": "更新已记录",
        "update_id": args.backup_id,
        "total_updates": len(hist["updates"]),
    }, ensure_ascii=False, indent=2))

def cmd_reject(args):
    """拒绝修改，恢复备份"""
    dd = Path(args.data_dir)
    backup_path = dd / "backups" / f"{Path(args.target_file).name}.{args.backup_id}.bak"

    if backup_path.exists():
        target = Path(args.target_file)
        shutil.copy2(backup_path, target)
        backup_path.unlink()

    # 记录拒绝
    hist_path = dd / "update_history.json"
    hist = load_json(hist_path, {"updates": []})
    hist["updates"].append({
        "id": args.backup_id,
        "timestamp": datetime.now().isoformat(),
        "target_file": args.target_file,
        "description": args.description or "用户拒绝的修改",
        "result": "rejected",
    })
    save_json(hist_path, hist)

    print(json.dumps({
        "status": "success",
        "message": "已恢复原文件，修改已拒绝",
    }, ensure_ascii=False, indent=2))

def cmd_history(args):
    """查看更新历史"""
    dd = Path(args.data_dir)
    hist = load_json(dd / "update_history.json", {"updates": []})

    print(json.dumps({
        "status": "success",
        "total_updates": len(hist["updates"]),
        "updates": hist["updates"][-20:],  # 最近20条
    }, ensure_ascii=False, indent=2))

def main():
    pa = argparse.ArgumentParser(description="技能自我更新管理器")
    sp = pa.add_subparsers(dest="command")

    p1 = sp.add_parser("preview", help="备份文件并准备修改")
    p1.add_argument("--target-file", required=True)
    p1.add_argument("--description", required=True)
    p1.add_argument("--data-dir", required=True)

    p2 = sp.add_parser("apply", help="确认并记录修改")
    p2.add_argument("--target-file", required=True)
    p2.add_argument("--backup-id", required=True)
    p2.add_argument("--description", default="")
    p2.add_argument("--data-dir", required=True)

    p3 = sp.add_parser("reject", help="拒绝修改并恢复备份")
    p3.add_argument("--target-file", required=True)
    p3.add_argument("--backup-id", required=True)
    p3.add_argument("--description", default="")
    p3.add_argument("--data-dir", required=True)

    p4 = sp.add_parser("history", help="查看更新历史")
    p4.add_argument("--data-dir", required=True)

    args = pa.parse_args()
    cmds = {"preview":cmd_preview,"apply":cmd_apply,"reject":cmd_reject,"history":cmd_history}
    if args.command in cmds: cmds[args.command](args)
    else: pa.print_help()

if __name__ == "__main__": main()
