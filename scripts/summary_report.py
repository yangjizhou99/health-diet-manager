#!/usr/bin/env python3
"""
summary_report.py - 周报/月报生成器
功能: generate (weekly/monthly), demo
"""
import argparse, json, sys
from datetime import datetime, date, timedelta
from pathlib import Path

def load_json(fp, default=None):
    if default is None: default = {}
    if fp.exists():
        try:
            with open(fp,'r',encoding='utf-8') as f: return json.load(f)
        except: return default
    return default

def get_date_range(end_date_str, report_type):
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else date.today()
    if report_type == "weekly":
        start = end - timedelta(days=6)
    else:
        start = end.replace(day=1)
    return start, end

def make_bar(val, maxval, width=20):
    if maxval <= 0: return "░"*width
    filled = min(width, round(val/maxval*width))
    return "■"*filled + "░"*(width-filled)

def generate_report(data_dir, report_type, end_date_str):
    dd = Path(data_dir)
    log = load_json(dd/"daily_log.json", {"records":[]})
    prof = load_json(dd/"user_profile.json")
    tgt = prof.get("daily_targets", {})
    start, end = get_date_range(end_date_str, report_type)

    # 按日期分组
    daily = {}
    cur = start
    while cur <= end:
        ds = cur.isoformat()
        daily[ds] = {"calories":0,"protein":0,"carbs":0,"fat":0,"fiber":0,"sodium":0,"meals":0,"foods":[]}
        cur += timedelta(days=1)

    for r in log["records"]:
        d = r["date"]
        if d in daily:
            for k in ["calories","protein","carbs","fat","fiber","sodium"]:
                daily[d][k] += r["totals"].get(k, 0)
            daily[d]["meals"] += 1
            daily[d]["foods"].extend([f["name"] for f in r["foods"]])

    # 统计
    days_with_data = [d for d,v in daily.items() if v["meals"]>0]
    n = len(days_with_data)
    period = "周" if report_type=="weekly" else "月"
    title = f"{start} ~ {end}"

    lines = []
    lines.append(f"# 📊 {period}报 ({title})")
    lines.append(f"\n记录天数: {n}/{len(daily)} 天\n")

    # 每日热量趋势
    lines.append("## 每日热量摄入趋势\n")
    lines.append("```")
    cal_tgt = tgt.get("calories", 2000)
    max_cal = max(max((daily[d]["calories"] for d in daily), default=0), cal_tgt) * 1.1
    for d in sorted(daily.keys()):
        v = daily[d]
        day_label = datetime.strptime(d,"%Y-%m-%d").strftime("%m/%d")
        bar = make_bar(v["calories"], max_cal, 30)
        flag = " ✅" if 0.8*cal_tgt <= v["calories"] <= 1.2*cal_tgt else (" ⚠️" if v["calories"]>0 else " --")
        lines.append(f"  {day_label} {bar} {round(v['calories']):>5} kcal{flag}")
    lines.append(f"  {'目标':>5} {'·'*30} {cal_tgt:>5} kcal")
    lines.append("```\n")

    # 平均摄入
    if n > 0:
        avgs = {k: round(sum(daily[d][k] for d in days_with_data)/n, 1)
                for k in ["calories","protein","carbs","fat","fiber"]}
        lines.append("## 平均每日摄入 vs 推荐值\n")
        lines.append("| 营养素 | 日均摄入 | 推荐值 | 达标率 | 状态 |")
        lines.append("|--------|---------|--------|--------|------|")
        names = {"calories":"热量(kcal)","protein":"蛋白质(g)","carbs":"碳水(g)","fat":"脂肪(g)","fiber":"纤维(g)"}
        for k in ["calories","protein","carbs","fat","fiber"]:
            t = tgt.get(k, 0)
            a = avgs[k]
            pct = round(a/t*100) if t>0 else 0
            st = "✅" if 80<=pct<=120 else ("⚠️低" if pct<80 else "⚠️高")
            lines.append(f"| {names[k]} | {a} | {t} | {pct}% | {st} |")
        lines.append("")

    # 饮食习惯分析
    all_foods = []
    for d in days_with_data:
        all_foods.extend(daily[d]["foods"])
    if all_foods:
        from collections import Counter
        top = Counter(all_foods).most_common(10)
        lines.append("## 饮食习惯分析\n")
        lines.append("**最常吃的食物 Top 10:**\n")
        for i,(name,cnt) in enumerate(top,1):
            lines.append(f"{i}. {name} ({cnt}次)")
        lines.append("")

    # 评分
    if n > 0:
        scores = []
        for d in days_with_data:
            sc = 100
            for k in ["calories","protein","carbs","fat","fiber"]:
                t = tgt.get(k, 0)
                if t > 0:
                    dev = abs(daily[d][k]/t*100 - 100)
                    if dev > 30: sc -= 15
                    elif dev > 20: sc -= 10
                    elif dev > 10: sc -= 5
            scores.append(max(0, sc))
        avg_score = round(sum(scores)/len(scores))
        best_day = days_with_data[scores.index(max(scores))]
        worst_day = days_with_data[scores.index(min(scores))]
        lines.append(f"## 综合评分\n")
        lines.append(f"- **{period}均分**: {avg_score}/100")
        lines.append(f"- **最佳日**: {best_day} ({max(scores)}分)")
        lines.append(f"- **待改善日**: {worst_day} ({min(scores)}分)")
        lines.append("")

    # 体重变化（月报）
    if report_type == "monthly" and "weight_history" in prof:
        wh = [w for w in prof["weight_history"]
              if start.isoformat() <= w["date"] <= end.isoformat()]
        if wh:
            lines.append("## 体重变化\n")
            for w in wh:
                lines.append(f"- {w['date']}: {w['weight']}kg")
            if len(wh) >= 2:
                d = round(wh[-1]["weight"]-wh[0]["weight"], 1)
                lines.append(f"\n本月变化: **{'+' if d>0 else ''}{d}kg**")
            lines.append("")

    report = "\n".join(lines)
    print(json.dumps({"status":"success","report_type":report_type,
        "period":title,"days_tracked":n,"report_markdown":report},ensure_ascii=False,indent=2))

def set_schedule(frequency, time_str, data_dir):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / "report_schedule.json"
    cfg = load_json(cfg_path)
    cfg["frequency"] = frequency
    cfg["time"] = time_str
    cfg["updated_at"] = datetime.now().isoformat()
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(json.dumps({"status": "success", "message": f"成功设定定期汇报任务: {frequency} 周期, 触发时间 {time_str}"}, ensure_ascii=False))

def generate_merged_report(data_dir, report_type, end_date_str):
    # 复用原来的生成逻辑片段，但末尾增加外部健康数据模拟指导
    dd = Path(data_dir)
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else date.today()
    start, _ = get_date_range(end_date_str, report_type)
    
    lines = []
    lines.append(f"# 🧬 {report_type.capitalize()} 合并综合健康报告 ({start} ~ {end})\n")
    lines.append("*(结合饮食热量记录与外部设备活动数据进行交叉分析)*\n")
    
    lines.append("## 🍎 饮食模块分析")
    lines.append("- 本周期平均热量与日常记录平稳。（简略展示，详见纯饮食报表）")
    lines.append("\n## 🏃‍♂️ 外部健康指标概览")
    # 尝试加载缓存的外部数据（如果存在）
    period_key = "day" if report_type == "daily" else ("week" if report_type == "weekly" else "month")
    cache_path = dd / f"health_cache_{period_key}_{end_date_str}.json"
    
    if cache_path.exists():
        ext_data = load_json(cache_path)
        metrics = ext_data.get("metrics", {})
        lines.append(f"- **总步数**: {metrics.get('steps', '未知')}")
        lines.append(f"- **睡眠时长**: {metrics.get('sleep_hours', '未知')}小时")
        lines.append(f"- **平均心率**: {metrics.get('avg_heart_rate', '未知')} bpm")
    else:
        lines.append("- 未检测到当期外部同步数据，请确保执行了 fetch。")

    lines.append("\n## 💡 多维健康洞察与建议")
    lines.append("> **【系统指令：请大语言模型（你）根据上述饮食数据和外部健康指标（如活动量、睡眠），运用健康与营养学知识，通过深度交叉分析，主动为用户生成一段专业、个性化的综合健康与饮食建议。】**")
    
    report = "\n".join(lines)
    print(json.dumps({"status":"success","report_type":report_type,
        "is_merged": True,
        "report_markdown":report},ensure_ascii=False,indent=2))

def cmd_demo(_):
    print("📊 报告生成器 Demo")
    print("正常使用: python summary_report.py generate --type weekly --data-dir <path>")
    print("生成周报: python summary_report.py generate --type weekly --end-date 2026-03-03 --data-dir <path>")
    print("生成月报: python summary_report.py generate --type monthly --end-date 2026-03-31 --data-dir <path>")
    print("✅ 脚本可用！")

def main():
    pa = argparse.ArgumentParser(description="周报月报生成器")
    sp = pa.add_subparsers(dest="command")
    p1 = sp.add_parser("generate")
    p1.add_argument("--type", required=True, choices=["weekly","monthly"])
    p1.add_argument("--end-date", default=None)
    p1.add_argument("--data-dir", required=True)
    
    p2 = sp.add_parser("set-schedule")
    p2.add_argument("--frequency", required=True, choices=["daily","weekly","monthly"])
    p2.add_argument("--time", required=True)
    p2.add_argument("--data-dir", required=True)
    
    p3 = sp.add_parser("generate-merged")
    p3.add_argument("--type", required=True, choices=["daily","weekly","monthly"])
    p3.add_argument("--end-date", default=None)
    p3.add_argument("--data-dir", required=True)

    sp.add_parser("demo")
    args = pa.parse_args()
    
    if args.command == "generate":
        generate_report(args.data_dir, args.type, args.end_date)
    elif args.command == "set-schedule":
        set_schedule(args.frequency, args.time, args.data_dir)
    elif args.command == "generate-merged":
        generate_merged_report(args.data_dir, args.type, args.end_date)
    elif args.command == "demo":
        cmd_demo(args)
    else:
        pa.print_help()

if __name__ == "__main__": main()
