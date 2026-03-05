#!/usr/bin/env python3
"""
summary_report.py - 周报/月报生成器
功能: generate (weekly/monthly), demo
"""
import argparse, json, sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from datetime import datetime, date, timedelta
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

def get_date_range(end_date_str, report_type):
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else date.today()
    if report_type == "daily":
        start = end
    elif report_type == "weekly":
        start = end - timedelta(days=6)
    else:
        start = end.replace(day=1)
    return start, end

def build_daily_nutrition(log, start, end):
    daily = {}
    cur = start
    while cur <= end:
        ds = cur.isoformat()
        daily[ds] = {
            "calories": 0,
            "protein": 0,
            "carbs": 0,
            "fat": 0,
            "fiber": 0,
            "sodium": 0,
            "meals": 0,
            "foods": [],
        }
        cur += timedelta(days=1)

    for r in log.get("records", []):
        d = r.get("date")
        if d in daily:
            totals = r.get("totals", {})
            for k in ["calories", "protein", "carbs", "fat", "fiber", "sodium"]:
                daily[d][k] += totals.get(k, 0)
            daily[d]["meals"] += 1
            daily[d]["foods"].extend([f.get("name", "未知食物") for f in r.get("foods", [])])
    return daily

def summarize_diet_period(daily, targets):
    days_with_data = [d for d, v in daily.items() if v["meals"] > 0]
    n = len(days_with_data)
    if n == 0:
        return {
            "days_with_data": 0,
            "avg": {},
            "energy_balance": {},
            "top_foods": [],
            "score": None,
        }

    avgs = {
        k: round(sum(daily[d][k] for d in days_with_data) / n, 1)
        for k in ["calories", "protein", "carbs", "fat", "fiber", "sodium"]
    }

    total_in = round(sum(daily[d]["calories"] for d in days_with_data), 1)
    avg_in = round(total_in / n, 1)

    all_foods = []
    for d in days_with_data:
        all_foods.extend(daily[d]["foods"])

    top_foods = []
    if all_foods:
        from collections import Counter
        top_foods = Counter(all_foods).most_common(8)

    scores = []
    for d in days_with_data:
        sc = 100
        for k in ["calories", "protein", "carbs", "fat", "fiber"]:
            t = targets.get(k, 0)
            if t > 0:
                dev = abs(daily[d][k] / t * 100 - 100)
                if dev > 30:
                    sc -= 15
                elif dev > 20:
                    sc -= 10
                elif dev > 10:
                    sc -= 5
        scores.append(max(0, sc))

    return {
        "days_with_data": n,
        "avg": avgs,
        "avg_intake_kcal": avg_in,
        "total_intake_kcal": total_in,
        "top_foods": top_foods,
        "score": round(sum(scores) / len(scores)),
    }

def compute_avg_tdee(metrics, target_dates):
    energy_metrics = metrics.get("energy_expenditure", {})
    values = [energy_metrics[d].get("tdee_kcal", 0) for d in target_dates if d in energy_metrics]
    valid = [v for v in values if v > 0]
    if not valid:
        return 0.0
    return round(sum(valid) / len(valid), 1)

def build_llm_objective_payload(report_type, start, end, target_dates, targets, diet_summary, metrics):
    activity_metrics = metrics.get("daily_activity", {})
    sleep_metrics = metrics.get("sleep_recovery", {})
    hr_metrics = metrics.get("cardiovascular_health", {})
    energy_metrics = metrics.get("energy_expenditure", {})
    body_metrics = metrics.get("body_composition", {})

    steps = [activity_metrics[d].get("total_steps", 0) for d in target_dates if d in activity_metrics]
    sedentary_blocks = sum([activity_metrics[d].get("sedentary_3h_blocks_count", 0) for d in target_dates if d in activity_metrics])

    sleep_hours = [sleep_metrics[d].get("total_sleep_hours", 0) for d in target_dates if d in sleep_metrics]
    deep_ratio = [sleep_metrics[d].get("deep_sleep_ratio", 0) for d in target_dates if d in sleep_metrics]
    awake_mins = [sleep_metrics[d].get("awake_interruptions_mins", 0) for d in target_dates if d in sleep_metrics]

    tdee_values = [energy_metrics[d].get("tdee_kcal", 0) for d in target_dates if d in energy_metrics]
    tdee_low_values = [energy_metrics[d].get("tdee_kcal_low", energy_metrics[d].get("tdee_kcal", 0)) for d in target_dates if d in energy_metrics]
    tdee_high_values = [energy_metrics[d].get("tdee_kcal_high", energy_metrics[d].get("tdee_kcal", 0)) for d in target_dates if d in energy_metrics]
    active_burn_values = [energy_metrics[d].get("active_burn_kcal", 0) for d in target_dates if d in energy_metrics]
    active_burn_low_values = [energy_metrics[d].get("active_burn_kcal_low", energy_metrics[d].get("active_burn_kcal", 0)) for d in target_dates if d in energy_metrics]
    active_burn_high_values = [energy_metrics[d].get("active_burn_kcal_high", energy_metrics[d].get("active_burn_kcal", 0)) for d in target_dates if d in energy_metrics]
    resting_burn_values = [energy_metrics[d].get("resting_burn_kcal", 0) for d in target_dates if d in energy_metrics]
    confidence_labels = [energy_metrics[d].get("active_burn_confidence_label") for d in target_dates if d in energy_metrics]
    estimated_days = [d for d in target_dates if d in energy_metrics and energy_metrics[d].get("active_burn_source") == "estimated_from_hr"]

    body_records = [body_metrics[d] for d in target_dates if d in body_metrics]
    latest_body = body_records[-1] if body_records else {}

    avg_tdee = round(sum([v for v in tdee_values if v > 0]) / len([v for v in tdee_values if v > 0]), 1) if [v for v in tdee_values if v > 0] else 0.0
    avg_intake = diet_summary.get("avg_intake_kcal", 0)

    return {
        "report_period": {
            "type": report_type,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": len(target_dates),
        },
        "diet": {
            "days_with_records": diet_summary.get("days_with_data", 0),
            "avg_daily": diet_summary.get("avg", {}),
            "avg_intake_kcal": avg_intake,
            "target_daily": targets,
            "diet_balance_score": diet_summary.get("score"),
            "top_foods": [{"name": name, "count": cnt} for name, cnt in diet_summary.get("top_foods", [])],
        },
        "activity": {
            "avg_steps": round(sum(steps) / len(steps), 1) if steps else 0,
            "max_steps": max(steps) if steps else 0,
            "total_sedentary_3h_blocks": sedentary_blocks,
        },
        "sleep": {
            "avg_sleep_hours": round(sum(sleep_hours) / len(sleep_hours), 2) if sleep_hours else 0,
            "avg_deep_sleep_ratio": round(sum(deep_ratio) / len(deep_ratio), 3) if deep_ratio else 0,
            "total_awake_interruptions_minutes": round(sum(awake_mins), 1) if awake_mins else 0,
        },
        "cardiovascular": {
            "estimated_rhr": hr_metrics.get("baseline", {}).get("estimated_rhr"),
            "observed_peak_hr": hr_metrics.get("baseline", {}).get("observed_peak_hr"),
            "inferred_workout_count": len(hr_metrics.get("inferred_workouts", [])),
            "total_exercise_minutes_zone2_plus": hr_metrics.get("total_exercise_minutes_zone2_plus", 0),
        },
        "energy": {
            "avg_tdee_kcal": avg_tdee,
            "avg_tdee_kcal_low": round(sum(tdee_low_values) / len(tdee_low_values), 1) if tdee_low_values else 0,
            "avg_tdee_kcal_high": round(sum(tdee_high_values) / len(tdee_high_values), 1) if tdee_high_values else 0,
            "avg_active_burn_kcal": round(sum(active_burn_values) / len(active_burn_values), 1) if active_burn_values else 0,
            "avg_active_burn_kcal_low": round(sum(active_burn_low_values) / len(active_burn_low_values), 1) if active_burn_low_values else 0,
            "avg_active_burn_kcal_high": round(sum(active_burn_high_values) / len(active_burn_high_values), 1) if active_burn_high_values else 0,
            "avg_resting_burn_kcal": round(sum(resting_burn_values) / len(resting_burn_values), 1) if resting_burn_values else 0,
            "avg_intake_minus_tdee_kcal": round(avg_intake - avg_tdee, 1) if avg_intake and avg_tdee else None,
            "estimated_from_hr_days": len(estimated_days),
            "active_burn_confidence_labels": confidence_labels,
        },
        "body_composition_latest": latest_body,
    }

def save_report_files(data_dir, report_type, start, end, report_markdown, payload):
    dd = Path(data_dir)
    out_dir = dd / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"health_report_{report_type}_{start.isoformat()}_to_{end.isoformat()}"
    md_path = out_dir / f"{base_name}.md"
    json_path = out_dir / f"{base_name}.json"

    if md_path.exists() or json_path.exists():
        ts = datetime.now().strftime("%H%M%S")
        md_path = out_dir / f"{base_name}_{ts}.md"
        json_path = out_dir / f"{base_name}_{ts}.json"

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report_markdown)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return str(md_path), str(json_path)

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

    daily = build_daily_nutrition(log, start, end)

    # 统计
    days_with_data = [d for d,v in daily.items() if v["meals"]>0]
    n = len(days_with_data)
    period = {"daily": "日", "weekly": "周", "monthly": "月"}.get(report_type, "周期")
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
    payload = {
        "status": "success",
        "report_type": report_type,
        "period": title,
        "days_tracked": n,
        "report_markdown": report,
    }
    md_path, json_path = save_report_files(data_dir, report_type, start, end, report, payload)
    payload["saved_report_markdown_path"] = md_path
    payload["saved_report_json_path"] = json_path
    print(json.dumps(payload, ensure_ascii=False, indent=2))

def set_schedule(frequency, time_str, data_dir):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / "report_schedule.json"
    cfg = load_json(cfg_path)
    cfg["frequency"] = frequency
    cfg["time"] = time_str
    cfg["updated_at"] = datetime.now().isoformat()
    save_json(cfg_path, cfg)
    print(json.dumps({"status": "success", "message": f"成功设定定期汇报任务: {frequency} 周期, 触发时间 {time_str}"}, ensure_ascii=False))

def generate_merged_report(data_dir, report_type, end_date_str):
    dd = Path(data_dir)
    if not end_date_str:
        end_date_str = date.today().isoformat()
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    start, _ = get_date_range(end_date_str, report_type)
    target_dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]

    log = load_json(dd / "daily_log.json", {"records": []})
    prof = load_json(dd / "user_profile.json")
    targets = prof.get("daily_targets", {})
    daily = build_daily_nutrition(log, start, end)
    diet_summary = summarize_diet_period(daily, targets)
    
    lines = []
    lines.append(f"# 🧬 {report_type.capitalize()} 合并综合健康报告 ({start} ~ {end})\n")
    lines.append("*(结合饮食热量记录与外部设备活动数据进行交叉分析)*\n")
    
    lines.append("## 🍎 饮食模块分析")
    if diet_summary["days_with_data"] == 0:
        lines.append("- 当前周期没有饮食记录。")
    else:
        avg = diet_summary["avg"]
        lines.append(f"- **记录完整度**: {diet_summary['days_with_data']}/{len(target_dates)} 天")
        lines.append(f"- **日均摄入**: {avg.get('calories', 0)} kcal | 蛋白质 {avg.get('protein', 0)}g | 碳水 {avg.get('carbs', 0)}g | 脂肪 {avg.get('fat', 0)}g")
        if targets:
            tgt_cal = targets.get("calories", 0)
            if tgt_cal > 0:
                pct = round(avg.get("calories", 0) / tgt_cal * 100)
                lines.append(f"- **相对目标热量**: {pct}% (目标 {tgt_cal} kcal/天)")
        lines.append(f"- **饮食均衡评分**: {diet_summary.get('score', 0)}/100")
        if diet_summary.get("top_foods"):
            food_line = "、".join([f"{name}({cnt}次)" for name, cnt in diet_summary["top_foods"][:5]])
            lines.append(f"- **高频食物**: {food_line}")

    lines.append("\n## 🏃‍♂️ 外部健康指标概览")
    # 尝试加载缓存的外部数据，缓存不存在则自动触发 fetch
    period_key = "day" if report_type == "daily" else ("week" if report_type == "weekly" else "month")
    cache_path = dd / f"health_cache_{period_key}_{end_date_str}.json"
    
    if not cache_path.exists():
        print(f"[Report] 缓存 {cache_path.name} 不存在，尝试自动同步...", file=__import__('sys').stderr)
        try:
            scripts_dir = str(Path(__file__).resolve().parent)
            if scripts_dir not in __import__('sys').path:
                __import__('sys').path.insert(0, scripts_dir)
            from health_data_sync import fetch_data
            fetch_data(period_key, end_date_str, str(dd))
        except Exception as e:
            print(f"[Report] 自动同步失败: {e}", file=__import__('sys').stderr)

    if cache_path.exists():
        ext_data = load_json(cache_path)
        metrics = ext_data.get("metrics", {})
        
        # 1. 提取日常活动
        activity_metrics = metrics.get("daily_activity", {})
        total_steps_list = [activity_metrics[d].get('total_steps', 0) for d in target_dates if d in activity_metrics]
        total_sedentary_blocks = sum([activity_metrics[d].get('sedentary_3h_blocks_count', 0) for d in target_dates if d in activity_metrics])
        
        if total_steps_list:
            avg_steps = sum(total_steps_list) // len(total_steps_list)
            lines.append(f"- **步数概览**: 周期日均 {avg_steps} 步 (最高 {max(total_steps_list)} 步)")
            lines.append(f"- **连续久坐**: 周期累计 {total_sedentary_blocks} 波长时久坐")
        else:
            lines.append("- **步数概览**: 无有效步数记录")
            
        all_fast_walks = []
        for d in target_dates:
            if d in activity_metrics:
                all_fast_walks.extend(activity_metrics[d].get("fast_walks", []))
        
        if all_fast_walks:
            lines.append(f"- **高频快走推算**: 周期累计 {len(all_fast_walks)} 段连续快走 (耗时 {sum(w['duration_minutes'] for w in all_fast_walks)} 分钟)")
            if period_key == "day":
                for w in all_fast_walks:
                    lines.append(f"  - 🏃‍♂️ {w['start'][11:16]}~{w['end'][11:16]} | {w['duration_minutes']}分钟 | {w['total_steps']}步 (最高 {w['max_steps_per_min']}步/分)")
        
        # 2. 提取体成分 (取周期最后一条有效数据)
        body_metrics = metrics.get("body_composition", {})
        valid_bodies = [body_metrics[d] for d in target_dates if d in body_metrics]
        if valid_bodies:
            body = valid_bodies[-1] # 最新的一天
            muscle_fat_ratio = body.get('muscle_fat_ratio', body.get('smi_ratio', '未知'))
            line = (
                f"- **周期末体成分**: {body.get('weight_kg', '未知')}kg, "
                f"**体脂**: {body.get('body_fat_pct', '未知')}%, "
                f"**骨骼肌/脂肪比**: {muscle_fat_ratio}"
            )
            smi_kg_m2 = body.get('smi_kg_m2')
            if smi_kg_m2 is not None:
                line += f", **SMI(kg/m2)**: {smi_kg_m2}"
            lines.append(line)
        
        # 3. 提取心率与隐性运动
        hr_info = metrics.get("cardiovascular_health", {})
        lines.append(f"- **全天静息心率基线**: {hr_info.get('baseline', {}).get('estimated_rhr', '未知')} bpm")
        workouts = hr_info.get("inferred_workouts", [])
        if period_key == "day":
             target_date_str = target_dates[0]
             day_workouts = [w for w in workouts if target_date_str in w.get("start","")]
             if day_workouts:
                 lines.append(f"- **隐性运动探针**: 发现 {len(day_workouts)} 个有氧波段, 总计 {sum(w['duration_minutes'] for w in day_workouts)} 分钟")
             else:
                 lines.append("- **隐性运动探针**: 今日无明显中高强度心率波段记录")
        else:
             period_workouts = [w for w in workouts if w.get("start", "")[:10] in target_dates]
             total_mins = sum(w['duration_minutes'] for w in period_workouts)
             lines.append(f"- **全周期累计Zone2+有效运动**: {total_mins} 分钟 ({len(period_workouts)} 次)")
                 
        # 4. 提取睡眠架构
        sleep_metrics = metrics.get("sleep_recovery", {})
        sleeps = [sleep_metrics[d] for d in target_dates if d in sleep_metrics]
        if sleeps:
            avg_sleep_h = sum(s.get('total_sleep_hours', 0) for s in sleeps) / len(sleeps)
            avg_deep_ratio = sum(float(s.get('deep_sleep_ratio', 0)) for s in sleeps) / len(sleeps)
            total_awakes = sum(s.get('awake_interruptions_mins', 0) for s in sleeps)
            lines.append(f"- **睡眠架构**: 日均净睡眠 {avg_sleep_h:.1f} 小时 (深睡比例 {avg_deep_ratio * 100:.1f}%)")
            lines.append(f"- **微觉醒打断**: 周期累计 {int(total_awakes)} 分钟")
            
        # 5. 提取精密能耗
        energy_metrics = metrics.get("energy_expenditure", {})
        energies = [energy_metrics[d] for d in target_dates if d in energy_metrics]
        if energies:
            avg_resting = sum(e.get('resting_burn_kcal', 0) for e in energies) / len(energies)
            avg_active = sum(e.get('active_burn_kcal', 0) for e in energies) / len(energies)
            avg_tdee = sum(e.get('tdee_kcal', 0) for e in energies) / len(energies)
            lines.append(f"- **周期日均消耗**: 基础代谢 {avg_resting:.1f} kcal | 动态活动 {avg_active:.1f} kcal | TDEE {avg_tdee:.1f} kcal")
            est_energies = [e for e in energies if e.get('active_burn_source') == 'estimated_from_hr']
            if est_energies:
                avg_low = sum(e.get('active_burn_kcal_low', e.get('active_burn_kcal', 0)) for e in est_energies) / len(est_energies)
                avg_high = sum(e.get('active_burn_kcal_high', e.get('active_burn_kcal', 0)) for e in est_energies) / len(est_energies)
                labels = [e.get('active_burn_confidence_label') for e in est_energies if e.get('active_burn_confidence_label')]
                # 取最保守标签；若历史缓存无标签，则标注 unknown。
                if not labels:
                    label = 'unknown'
                else:
                    label = 'low' if 'low' in labels else ('medium' if 'medium' in labels else 'high')
                lines.append(
                    f"- **心率回退估算说明**: {len(est_energies)} 天使用心率推算，"
                    f"活动消耗区间约 {avg_low:.1f}~{avg_high:.1f} kcal/天，置信度 {label}"
                )
            
    else:
        lines.append("- 未检测到当期外部同步数据，请确保执行了 fetch 以连接健康系统。")

    objective_payload = build_llm_objective_payload(
        report_type=report_type,
        start=start,
        end=end,
        target_dates=target_dates,
        targets=targets,
        diet_summary=diet_summary,
        metrics=metrics if cache_path.exists() else {},
    )

    lines.append("\n## 🤖 建议生成输入（客观数据）")
    lines.append("以下为原始客观指标，由大模型基于这些数据生成个性化建议。")
    lines.append("```json")
    lines.append(json.dumps(objective_payload, ensure_ascii=False, indent=2))
    lines.append("```")

    report = "\n".join(lines)
    payload = {
        "status": "success",
        "report_type": report_type,
        "period": f"{start} ~ {end}",
        "is_merged": True,
        "days_tracked": diet_summary.get("days_with_data", 0),
        "llm_objective_input": objective_payload,
        "report_markdown": report,
    }
    md_path, json_path = save_report_files(data_dir, report_type, start, end, report, payload)
    payload["saved_report_markdown_path"] = md_path
    payload["saved_report_json_path"] = json_path
    print(json.dumps(payload, ensure_ascii=False, indent=2))

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
    p1.add_argument("--type", required=True, choices=["daily","weekly","monthly"])
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
