#!/usr/bin/env python3
"""
summary_report.py - 周报/月报生成器
功能: generate (weekly/monthly), demo
"""
import argparse, calendar, hashlib, json, os, re, subprocess, sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from datetime import datetime, date, timedelta
from pathlib import Path

LLM_ADVICE_CONFIG_FILE = "llm_advice_config.json"

def load_json(fp, default=None):
    if default is None: default = {}
    if fp.exists():
        try:
            with open(fp,'r',encoding='utf-8') as f: return json.load(f)
        except Exception: return default
    return default


def _load_external_sync_config(data_dir):
    return load_json(Path(data_dir) / "external_data_config.json", {})


def _find_estimated_energy_days(metrics):
    energy = metrics.get("energy_expenditure", {}) if isinstance(metrics, dict) else {}
    days = []
    for day, item in energy.items():
        if not isinstance(item, dict):
            continue
        source = str(item.get("active_burn_source", "")).lower()
        method = str(item.get("active_burn_method", "")).lower()
        if "estimated" in source or "fallback" in method:
            days.append(day)
    return days


def _metrics_fingerprint(metrics):
    payload = json.dumps(metrics or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_active_cache_path(dd, period_key, end_date_str):
    canonical = dd / f"health_cache_{period_key}_{end_date_str}.json"
    pointer = dd / f"health_cache_{period_key}_{end_date_str}.latest.json"
    if pointer.exists():
        pointer_data = load_json(pointer, {})
        active_file = pointer_data.get("active_cache_file")
        if active_file:
            candidate = dd / active_file
            if candidate.exists():
                return candidate
    return canonical


def _cache_step_stats(metrics, target_dates):
    activity = metrics.get("daily_activity", {}) if isinstance(metrics, dict) else {}
    step_values = [int(activity[d].get("total_steps", 0)) for d in target_dates if d in activity]
    return {
        "avg_steps": round(sum(step_values) / len(step_values), 1) if step_values else 0,
        "max_steps": max(step_values) if step_values else 0,
        "step_values": step_values,
    }

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

def _daily_totals_from_log(log):
    daily = {}
    for rec in log.get("records", []):
        d = rec.get("date")
        if not d:
            continue
        item = daily.setdefault(d, {
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
            "fiber": 0.0,
            "sodium": 0.0,
            "meals": 0,
            "foods": [],
            "meal_types": {},
        })
        totals = rec.get("totals", {})
        for k in ["calories", "protein", "carbs", "fat", "fiber", "sodium"]:
            item[k] += float(totals.get(k, 0) or 0)
        item["meals"] += 1
        mt = rec.get("meal_type", "unknown")
        item["meal_types"][mt] = item["meal_types"].get(mt, 0) + 1
        for f in rec.get("foods", []):
            name = f.get("name")
            if name:
                item["foods"].append(name)
    return daily

def _recent_date_range(end_date, days):
    start = end_date - timedelta(days=max(days - 1, 0))
    return start.isoformat(), end_date.isoformat()

def _avg_from_daily(daily, key, start_iso, end_iso):
    values = [v.get(key, 0) for d, v in daily.items() if start_iso <= d <= end_iso and v.get("meals", 0) > 0]
    if not values:
        return None
    return round(sum(values) / len(values), 1)

def _top_foods(daily, start_iso=None, end_iso=None, limit=10):
    from collections import Counter
    foods = []
    for d, v in daily.items():
        if start_iso and d < start_iso:
            continue
        if end_iso and d > end_iso:
            continue
        foods.extend(v.get("foods", []))
    if not foods:
        return []
    return [{"name": name, "count": cnt} for name, cnt in Counter(foods).most_common(limit)]

def _weight_delta(weight_history, start_iso, end_iso):
    points = [w for w in weight_history if start_iso <= w.get("date", "") <= end_iso and isinstance(w.get("weight"), (int, float))]
    points = sorted(points, key=lambda x: x.get("date", ""))
    if len(points) < 2:
        return None
    return round(float(points[-1]["weight"]) - float(points[0]["weight"]), 2)

def build_personal_context(profile, log, metrics, end):
    daily_totals = _daily_totals_from_log(log)
    end_iso = end.isoformat()
    last7_start, last7_end = _recent_date_range(end, 7)
    last30_start, last30_end = _recent_date_range(end, 30)
    weight_history = profile.get("weight_history", []) if isinstance(profile, dict) else []

    profile_context = {
        "gender": profile.get("gender"),
        "age": profile.get("age"),
        "height_cm": profile.get("height"),
        "current_weight_kg": profile.get("weight"),
        "activity_level": profile.get("activity"),
        "goal": profile.get("goal"),
        "bmr_kcal": profile.get("bmr"),
        "tdee_kcal": profile.get("tdee"),
    }

    meal_type_distribution = {}
    for v in daily_totals.values():
        for mt, cnt in v.get("meal_types", {}).items():
            meal_type_distribution[mt] = meal_type_distribution.get(mt, 0) + cnt

    nutrition_trend = {
        "avg_calories_7d": _avg_from_daily(daily_totals, "calories", last7_start, last7_end),
        "avg_calories_30d": _avg_from_daily(daily_totals, "calories", last30_start, last30_end),
        "avg_protein_7d": _avg_from_daily(daily_totals, "protein", last7_start, last7_end),
        "avg_fiber_7d": _avg_from_daily(daily_totals, "fiber", last7_start, last7_end),
        "avg_sodium_7d": _avg_from_daily(daily_totals, "sodium", last7_start, last7_end),
    }

    activity = metrics.get("daily_activity", {}) if isinstance(metrics, dict) else {}
    sleep = metrics.get("sleep_recovery", {}) if isinstance(metrics, dict) else {}
    energy = metrics.get("energy_expenditure", {}) if isinstance(metrics, dict) else {}

    step_values = [int(v.get("total_steps", 0)) for d, v in activity.items() if isinstance(v, dict)]
    sleep_values = [float(v.get("total_sleep_hours", 0)) for d, v in sleep.items() if isinstance(v, dict)]
    tdee_values = [float(v.get("tdee_kcal", 0)) for d, v in energy.items() if isinstance(v, dict)]

    recovery_activity_context = {
        "avg_steps_in_window": round(sum(step_values) / len(step_values), 1) if step_values else None,
        "max_steps_in_window": max(step_values) if step_values else None,
        "avg_sleep_hours_in_window": round(sum(sleep_values) / len(sleep_values), 2) if sleep_values else None,
        "avg_tdee_in_window": round(sum([v for v in tdee_values if v > 0]) / len([v for v in tdee_values if v > 0]), 1) if [v for v in tdee_values if v > 0] else None,
    }

    data_gaps = []
    if not profile_context.get("age"):
        data_gaps.append("缺少年龄")
    if not profile_context.get("height_cm"):
        data_gaps.append("缺少身高")
    if not profile_context.get("goal"):
        data_gaps.append("缺少目标类型")
    if not step_values:
        data_gaps.append("缺少活动步数窗口数据")
    if not sleep_values:
        data_gaps.append("缺少睡眠窗口数据")

    custom_context_keys = [
        "medical_conditions",
        "allergies",
        "diet_preferences",
        "exercise_constraints",
        "work_schedule",
        "sleep_schedule",
        "stress_level",
        "medications",
    ]
    custom_context = {k: profile.get(k) for k in custom_context_keys if profile.get(k) is not None}

    return {
        "profile": profile_context,
        "custom_context": custom_context,
        "nutrition_trend": nutrition_trend,
        "weight_trend": {
            "delta_7d_kg": _weight_delta(weight_history, last7_start, end_iso),
            "delta_30d_kg": _weight_delta(weight_history, last30_start, end_iso),
            "latest_weight_from_history": sorted(weight_history, key=lambda x: x.get("date", ""))[-1]["weight"] if weight_history else None,
        },
        "behavior_patterns": {
            "meal_type_distribution": meal_type_distribution,
            "top_foods_7d": _top_foods(daily_totals, last7_start, last7_end, 8),
            "top_foods_30d": _top_foods(daily_totals, last30_start, last30_end, 12),
        },
        "recovery_activity_context": recovery_activity_context,
        "data_gaps": data_gaps,
    }

def build_llm_objective_payload(report_type, start, end, target_dates, targets, diet_summary, metrics, profile, log):
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
    personal_context = build_personal_context(profile, log, metrics, end)

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
        "personal_context": personal_context,
    }

def load_llm_advice(advice_file):
    if not advice_file:
        return []
    data = load_json(Path(advice_file), [])
    if not isinstance(data, list):
        return []
    lines = []
    for item in data:
        if isinstance(item, str):
            text = item.strip()
            if text:
                lines.append(text)
        elif isinstance(item, dict):
            title = str(item.get("title", "")).strip()
            detail = str(item.get("detail", "")).strip()
            if title and detail:
                lines.append(f"{title}：{detail}")
            elif detail:
                lines.append(detail)
            elif title:
                lines.append(title)
    return lines

def load_llm_advice_config(data_dir):
    dd = Path(data_dir)
    return load_json(dd / LLM_ADVICE_CONFIG_FILE, {})

def set_llm_advice_config(data_dir, model, api_key_env, base_url, temperature, max_tokens, timeout_seconds, system_prompt, enabled=True):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / LLM_ADVICE_CONFIG_FILE
    cfg = load_json(cfg_path, {})
    cfg["enabled"] = bool(enabled)
    cfg["model"] = model
    cfg["api_key_env"] = api_key_env
    cfg["base_url"] = base_url
    cfg["temperature"] = float(temperature)
    cfg["max_tokens"] = int(max_tokens)
    cfg["timeout_seconds"] = int(timeout_seconds)
    cfg["system_prompt"] = system_prompt or ""
    cfg["updated_at"] = datetime.now().isoformat()
    save_json(cfg_path, cfg)
    safe_cfg = dict(cfg)
    if "api_key_env" in safe_cfg:
        safe_cfg["api_key_env"] = safe_cfg["api_key_env"]
    print(json.dumps({"status": "success", "message": "已保存健康建议大模型配置", "config": safe_cfg}, ensure_ascii=False, indent=2))

def _extract_json_array_like(text):
    if not text:
        return []
    raw = text.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            if isinstance(parsed.get("advice"), list):
                return parsed.get("advice")
            if isinstance(parsed.get("suggestions"), list):
                return parsed.get("suggestions")
            if isinstance(parsed.get("items"), list):
                return parsed.get("items")
    except Exception:
        pass
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = raw[start:end+1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    lines = [line.strip("- ").strip() for line in raw.splitlines() if line.strip()]
    return lines

def generate_llm_advice(data_dir, report_type, objective_payload):
    cfg = load_llm_advice_config(data_dir)
    if not cfg.get("enabled", True):
        return [], {"source": "auto_llm", "enabled": False}, "大模型建议配置已禁用"
    model = str(cfg.get("model", "")).strip()
    if not model:
        return [], {"source": "auto_llm", "enabled": True}, "未配置模型名称，请先执行 set-llm-advice"
    api_key_env = str(cfg.get("api_key_env", "OPENAI_API_KEY")).strip() or "OPENAI_API_KEY"
    api_key = os.environ.get(api_key_env)
    if not api_key:
        return [], {"source": "auto_llm", "enabled": True, "api_key_env": api_key_env}, f"环境变量 {api_key_env} 未设置"

    try:
        import requests
    except Exception:
        return [], {"source": "auto_llm", "enabled": True}, "缺少 requests 依赖"

    advice_count = {"daily": "2-3", "weekly": "3-4", "monthly": "4-5"}.get(report_type, "3-4")
    system_prompt = cfg.get("system_prompt") or (
        "你是健康管理建议生成器。请严格基于输入的客观数据，输出 JSON 数组。"
    )
    user_payload = {
        "task": "生成健康建议",
        "report_type": report_type,
        "required_count": advice_count,
        "requirements": [
            "必须使用客观指标作为依据",
            "每条建议尽量包含：依据数据、执行动作、执行频次、预期变化、风险与备注",
            "禁止空泛口号",
            "只输出 JSON 数组，不要输出额外解释文本"
        ],
        "objective_data": objective_payload,
    }
    body = {
        "model": model,
        "temperature": float(cfg.get("temperature", 0.4)),
        "max_tokens": int(cfg.get("max_tokens", 900)),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }
    base_url = str(cfg.get("base_url", "https://api.openai.com/v1/chat/completions")).strip()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout_seconds = int(cfg.get("timeout_seconds", 90))

    try:
        resp = requests.post(base_url, headers=headers, json=body, timeout=timeout_seconds)
    except Exception as e:
        return [], {"source": "auto_llm", "base_url": base_url, "model": model}, f"调用大模型失败: {e}"
    if resp.status_code >= 400:
        detail = resp.text[:500] if resp.text else ""
        return [], {"source": "auto_llm", "base_url": base_url, "model": model, "status_code": resp.status_code}, f"大模型接口返回错误: {resp.status_code} {detail}"
    try:
        resp_json = resp.json()
    except Exception:
        return [], {"source": "auto_llm", "base_url": base_url, "model": model}, "大模型响应不是 JSON"
    content = ""
    if isinstance(resp_json, dict):
        choices = resp_json.get("choices", [])
        if choices and isinstance(choices, list):
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = msg.get("content", "")
        if not content and isinstance(resp_json.get("output_text"), str):
            content = resp_json.get("output_text", "")
    advice_items = _extract_json_array_like(content)
    lines = []
    for item in advice_items:
        if isinstance(item, str):
            t = item.strip()
            if t:
                lines.append(t)
        elif isinstance(item, dict):
            title = str(item.get("title", "")).strip()
            detail = str(item.get("detail", "")).strip()
            if title and detail:
                lines.append(f"{title}：{detail}")
            elif detail:
                lines.append(detail)
            elif title:
                lines.append(title)
    meta = {
        "source": "auto_llm",
        "base_url": base_url,
        "model": model,
        "api_key_env": api_key_env,
        "generated_count": len(lines),
    }
    if not lines:
        return [], meta, "大模型返回为空或格式不符合要求"
    return lines, meta, None

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
    cfg.setdefault("enabled", True)
    cfg.setdefault("auto_llm_advice", True)
    cfg.setdefault("strict_real_data", False)
    cfg.setdefault("llm_advice_file", None)
    cfg.setdefault("push_notion", False)
    cfg["updated_at"] = datetime.now().isoformat()
    save_json(cfg_path, cfg)
    print(json.dumps({"status": "success", "message": f"成功设定定期汇报任务: {frequency} 周期, 触发时间 {time_str}"}, ensure_ascii=False))

def set_schedule_advanced(frequency, time_str, data_dir, enabled, auto_llm_advice, strict_real_data, llm_advice_file, push_notion):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / "report_schedule.json"
    cfg = load_json(cfg_path, {})
    cfg["frequency"] = frequency
    cfg["time"] = time_str
    cfg["enabled"] = bool(enabled)
    cfg["auto_llm_advice"] = bool(auto_llm_advice)
    cfg["strict_real_data"] = bool(strict_real_data)
    cfg["llm_advice_file"] = llm_advice_file if llm_advice_file else None
    cfg["push_notion"] = bool(push_notion)
    cfg["updated_at"] = datetime.now().isoformat()
    save_json(cfg_path, cfg)
    print(json.dumps({"status": "success", "message": "定时任务配置已更新", "schedule": cfg}, ensure_ascii=False, indent=2))

def enrich_profile_from_data(data_dir, end_date_str=None):
    dd = Path(data_dir)
    profile_path = dd / "user_profile.json"
    profile = load_json(profile_path, {})
    log = load_json(dd / "daily_log.json", {"records": []})
    if not end_date_str:
        end_date_str = date.today().isoformat()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    start_30 = end_date - timedelta(days=29)
    daily = _daily_totals_from_log(log)
    daily_30 = {d: v for d, v in daily.items() if start_30.isoformat() <= d <= end_date.isoformat()}

    avg_steps_30 = None
    latest_weight = None
    latest_bfp = None
    latest_smi = None
    cache_path = _resolve_active_cache_path(dd, "day", end_date_str)
    cache_data = load_json(cache_path, {}) if cache_path.exists() else {}
    metrics = cache_data.get("metrics", {}) if isinstance(cache_data, dict) else {}
    activity = metrics.get("daily_activity", {})
    step_values = [int(v.get("total_steps", 0)) for d, v in activity.items() if start_30.isoformat() <= d <= end_date.isoformat()]
    if step_values:
        avg_steps_30 = round(sum(step_values) / len(step_values), 1)
    body = metrics.get("body_composition", {})
    if body:
        latest_day = sorted(body.keys())[-1]
        latest_weight = body[latest_day].get("weight_kg")
        latest_bfp = body[latest_day].get("body_fat_pct")
        latest_smi = body[latest_day].get("smi_kg_m2")

    inferred = {}
    if avg_steps_30 is not None:
        if avg_steps_30 < 5000:
            inferred["activity"] = "久坐"
        elif avg_steps_30 < 8000:
            inferred["activity"] = "轻度活动"
        elif avg_steps_30 < 11000:
            inferred["activity"] = "中度活动"
        else:
            inferred["activity"] = "高活动"
    if latest_weight is not None and not profile.get("weight"):
        inferred["weight"] = round(float(latest_weight), 1)

    cal_values = [v.get("calories", 0) for d, v in daily_30.items() if v.get("meals", 0) > 0]
    avg_cal_30 = round(sum(cal_values) / len(cal_values), 1) if cal_values else None
    energy = metrics.get("energy_expenditure", {})
    tdee_values = [float(v.get("tdee_kcal", 0)) for d, v in energy.items() if start_30.isoformat() <= d <= end_date.isoformat() and float(v.get("tdee_kcal", 0)) > 0]
    avg_tdee_30 = round(sum(tdee_values) / len(tdee_values), 1) if tdee_values else None
    if avg_cal_30 and avg_tdee_30 and not profile.get("goal"):
        gap = avg_cal_30 - avg_tdee_30
        if gap <= -200:
            inferred["goal"] = "减脂"
        elif gap >= 200:
            inferred["goal"] = "增重"
        else:
            inferred["goal"] = "维持"

    custom = {}
    if latest_bfp is not None:
        custom["latest_body_fat_pct"] = round(float(latest_bfp), 2)
    if latest_smi is not None:
        custom["latest_smi_kg_m2"] = round(float(latest_smi), 2)
    if avg_steps_30 is not None:
        custom["avg_steps_30d"] = avg_steps_30
    if avg_cal_30 is not None:
        custom["avg_intake_kcal_30d"] = avg_cal_30
    if avg_tdee_30 is not None:
        custom["avg_tdee_kcal_30d"] = avg_tdee_30
    if daily_30:
        meal_distribution = {}
        for item in daily_30.values():
            for mt, cnt in item.get("meal_types", {}).items():
                meal_distribution[mt] = meal_distribution.get(mt, 0) + cnt
        if meal_distribution:
            custom["meal_type_distribution_30d"] = meal_distribution
    top_foods = _top_foods(daily_30, limit=8)
    if top_foods:
        custom["top_foods_30d"] = top_foods

    profile.update({k: v for k, v in inferred.items() if v is not None and not profile.get(k)})
    profile["inferred_profile"] = custom
    profile["updated_at"] = datetime.now().isoformat()
    save_json(profile_path, profile)

    result = {
        "status": "success",
        "profile_path": str(profile_path),
        "inferred_fields": inferred,
        "inferred_profile": custom,
        "cache_used": str(cache_path) if cache_path.exists() else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result

def _weekday_from_text(text):
    normalized = str(text).strip().lower()
    en = {"mon": 0, "monday": 0, "tue": 1, "tuesday": 1, "wed": 2, "wednesday": 2, "thu": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
    if normalized in en:
        return en[normalized]
    zh = {"周一": 0, "星期一": 0, "周二": 1, "星期二": 1, "周三": 2, "星期三": 2, "周四": 3, "星期四": 3, "周五": 4, "星期五": 4, "周六": 5, "星期六": 5, "周日": 6, "星期日": 6, "周天": 6, "星期天": 6}
    return zh.get(text.strip())

def _parse_schedule_spec(frequency, time_str):
    raw = str(time_str or "").strip()
    m_hm = re.search(r"(\d{1,2}):(\d{2})", raw)
    hour = int(m_hm.group(1)) if m_hm else 20
    minute = int(m_hm.group(2)) if m_hm else 0
    hour = min(max(hour, 0), 23)
    minute = min(max(minute, 0), 59)

    if frequency == "weekly":
        weekday = None
        token = raw.replace("，", " ").replace(",", " ").split()
        for t in token:
            w = _weekday_from_text(t)
            if w is not None:
                weekday = w
                break
        if weekday is None:
            weekday = 6
        return {"hour": hour, "minute": minute, "weekday": weekday}

    if frequency == "monthly":
        dom = None
        m_dom = re.search(r"(?:day|每月|每个月)?\s*(\d{1,2})", raw)
        if m_dom:
            try:
                maybe_dom = int(m_dom.group(1))
                if 1 <= maybe_dom <= 31:
                    dom = maybe_dom
            except Exception:
                dom = None
        if dom is None:
            dom = 1
        return {"hour": hour, "minute": minute, "day_of_month": dom}

    return {"hour": hour, "minute": minute}

def _compute_due_datetime(now_dt, frequency, spec):
    if frequency == "weekly":
        weekday = spec.get("weekday", 6)
        current_weekday = now_dt.weekday()
        delta = weekday - current_weekday
        due_day = now_dt.date() + timedelta(days=delta)
        return datetime.combine(due_day, datetime.min.time()).replace(hour=spec["hour"], minute=spec["minute"])
    if frequency == "monthly":
        dom = spec.get("day_of_month", 1)
        last_day = calendar.monthrange(now_dt.year, now_dt.month)[1]
        dom = min(dom, last_day)
        due_day = now_dt.date().replace(day=dom)
        return datetime.combine(due_day, datetime.min.time()).replace(hour=spec["hour"], minute=spec["minute"])
    return datetime.combine(now_dt.date(), datetime.min.time()).replace(hour=spec["hour"], minute=spec["minute"])

def run_scheduled_pipeline(data_dir, now_str=None, force=False, dry_run=False):
    dd = Path(data_dir)
    cfg_path = dd / "report_schedule.json"
    cfg = load_json(cfg_path, {})
    if not cfg:
        result = {"status": "error", "message": "未找到 report_schedule.json，请先 set-schedule"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    if not cfg.get("enabled", True):
        result = {"status": "skip", "message": "定时任务已禁用"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    frequency = cfg.get("frequency", "daily")
    if frequency not in ("daily", "weekly", "monthly"):
        result = {"status": "error", "message": f"无效频率: {frequency}"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    time_str = cfg.get("time", "20:00")
    now_dt = datetime.strptime(now_str, "%Y-%m-%d %H:%M") if now_str else datetime.now()
    spec = _parse_schedule_spec(frequency, time_str)
    due_dt = _compute_due_datetime(now_dt, frequency, spec)
    last_run_at = cfg.get("last_run_at")
    last_run_dt = datetime.fromisoformat(last_run_at) if last_run_at else None
    due = force or (now_dt >= due_dt and (last_run_dt is None or last_run_dt < due_dt))

    preview = {
        "status": "pending" if due else "skip",
        "frequency": frequency,
        "time": time_str,
        "now": now_dt.isoformat(timespec="minutes"),
        "due_at": due_dt.isoformat(timespec="minutes"),
        "last_run_at": last_run_at,
        "force": force,
        "dry_run": dry_run,
    }
    if not due:
        preview["message"] = "当前未到触发时间或已执行过本周期"
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return preview
    if dry_run:
        preview["message"] = "dry-run 仅预览，不执行生成"
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return preview

    end_date = now_dt.date().isoformat()
    generate_result = generate_merged_report(
        data_dir=data_dir,
        report_type=frequency,
        end_date_str=end_date,
        strict_real_data=bool(cfg.get("strict_real_data", False)),
        llm_advice_file=cfg.get("llm_advice_file"),
        auto_llm_advice=bool(cfg.get("auto_llm_advice", True)),
    )
    if not isinstance(generate_result, dict) or generate_result.get("status") != "success":
        result = {
            "status": "error",
            "message": "定时链路执行失败：生成报告失败",
            "generate_result": generate_result,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    push_result = None
    if cfg.get("push_notion", False):
        report_file = generate_result.get("saved_report_json_path")
        if report_file:
            cmd = [
                sys.executable,
                str(Path(__file__).resolve().parent / "notion_health_sync.py"),
                "push-report",
                "--report-file",
                str(report_file),
                "--data-dir",
                str(dd),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            push_result = {
                "exit_code": proc.returncode,
                "stdout": (proc.stdout or "").strip()[-1200:],
                "stderr": (proc.stderr or "").strip()[-1200:],
            }
            if proc.returncode != 0:
                result = {
                    "status": "error",
                    "message": "报告已生成，但推送 Notion 失败",
                    "generate_result": generate_result,
                    "push_notion_result": push_result,
                }
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return result

    cfg["last_run_at"] = now_dt.isoformat()
    cfg["last_report_json_path"] = generate_result.get("saved_report_json_path")
    cfg["last_report_markdown_path"] = generate_result.get("saved_report_markdown_path")
    cfg["updated_at"] = datetime.now().isoformat()
    save_json(cfg_path, cfg)

    result = {
        "status": "success",
        "message": "定时链路执行成功",
        "generate_result": {
            "report_type": generate_result.get("report_type"),
            "period": generate_result.get("period"),
            "saved_report_markdown_path": generate_result.get("saved_report_markdown_path"),
            "saved_report_json_path": generate_result.get("saved_report_json_path"),
        },
        "push_notion_result": push_result,
        "schedule_state": {
            "last_run_at": cfg.get("last_run_at"),
            "last_report_json_path": cfg.get("last_report_json_path"),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result

def generate_merged_report(data_dir, report_type, end_date_str, strict_real_data=False, llm_advice_file=None, auto_llm_advice=False):
    dd = Path(data_dir)
    ext_cfg = _load_external_sync_config(data_dir)
    strict_real_data = bool(strict_real_data or ext_cfg.get("strict_real_data", False))
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
    total_meals = sum(daily[d]["meals"] for d in daily)
    total_food_items = sum(len(daily[d]["foods"]) for d in daily)
    period_key = "day" if report_type == "daily" else ("week" if report_type == "weekly" else "month")
    cache_path = _resolve_active_cache_path(dd, period_key, end_date_str)
    metrics = {}
    ext_data = {}

    def _pct(actual, target):
        return round(actual / target * 100) if target else 0

    def _status_from_pct(pct):
        if pct == 0:
            return "无目标"
        if pct < 80:
            return "偏低"
        if pct <= 120:
            return "达标"
        return "偏高"

    def _confidence_label(labels):
        normalized = [str(label).lower() for label in labels if label]
        if not normalized:
            return "unknown"
        if "low" in normalized:
            return "low"
        if "medium" in normalized:
            return "medium"
        return "high"

    def _fmt_optional(value, digits=1):
        if value is None:
            return "无"
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return str(value)

    if not cache_path.exists():
        print(f"[Report] 缓存 {cache_path.name} 不存在，尝试自动同步...", file=__import__('sys').stderr)
        try:
            scripts_dir = str(Path(__file__).resolve().parent)
            if scripts_dir not in __import__('sys').path:
                __import__('sys').path.insert(0, scripts_dir)
            from health_data_sync import fetch_data
            fetch_data(period_key, end_date_str, str(dd), strict_real_data=strict_real_data)
        except Exception as e:
            print(f"[Report] 自动同步失败: {e}", file=__import__('sys').stderr)

    if not cache_path.exists():
        result = {
            "status": "error",
            "message": "综合健康报告未生成：未检测到可用外部健康缓存。",
            "expected_cache": str(cache_path),
            "strict_real_data": strict_real_data,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    ext_data = load_json(cache_path, {})
    metrics = ext_data.get("metrics", {}) if isinstance(ext_data, dict) else {}
    cache_status = ext_data.get("status") if isinstance(ext_data, dict) else None
    if cache_status and cache_status != "success":
        result = {
            "status": "error",
            "message": "综合健康报告未生成：外部健康缓存状态非 success。",
            "cache_path": str(cache_path),
            "cache_status": cache_status,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    if not metrics:
        result = {
            "status": "error",
            "message": "综合健康报告未生成：外部健康缓存缺少有效指标数据。",
            "cache_path": str(cache_path),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    if strict_real_data:
        estimated_days = _find_estimated_energy_days(metrics)
        if estimated_days:
            result = {
                "status": "error",
                "message": "strict_real_data 已启用：缓存中包含估算能耗数据，拒绝生成报告。",
                "estimated_days": sorted(estimated_days),
                "cache_path": str(cache_path),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result

    activity_metrics = metrics.get("daily_activity", {})
    total_steps_list = [activity_metrics[d].get("total_steps", 0) for d in target_dates if d in activity_metrics]
    total_sedentary_blocks = sum(activity_metrics[d].get("sedentary_3h_blocks_count", 0) for d in target_dates if d in activity_metrics)
    all_fast_walks = []
    for d in target_dates:
        if d in activity_metrics:
            all_fast_walks.extend(activity_metrics[d].get("fast_walks", []))

    sleep_metrics = metrics.get("sleep_recovery", {})
    sleeps = [sleep_metrics[d] for d in target_dates if d in sleep_metrics]

    hr_info = metrics.get("cardiovascular_health", {})
    baseline = hr_info.get("baseline", {})
    workouts = hr_info.get("inferred_workouts", [])
    selected_workouts = [w for w in workouts if w.get("start", "")[:10] in target_dates]

    energy_metrics = metrics.get("energy_expenditure", {})
    energies = [energy_metrics[d] for d in target_dates if d in energy_metrics]

    body_metrics = metrics.get("body_composition", {})
    valid_body_days = [d for d in target_dates if d in body_metrics]

    objective_payload = build_llm_objective_payload(
        report_type=report_type,
        start=start,
        end=end,
        target_dates=target_dates,
        targets=targets,
        diet_summary=diet_summary,
        metrics=metrics,
        profile=prof,
        log=log,
    )

    step_stats = _cache_step_stats(metrics, target_dates)
    report_avg = objective_payload.get("activity", {}).get("avg_steps", 0)
    report_max = objective_payload.get("activity", {}).get("max_steps", 0)
    if int(report_avg) != int(step_stats["avg_steps"]) or int(report_max) != int(step_stats["max_steps"]):
        result = {
            "status": "error",
            "message": "步数一致性校验失败：报告步数与 source cache 不一致，已拒绝生成。",
            "cache_avg_steps": step_stats["avg_steps"],
            "cache_max_steps": step_stats["max_steps"],
            "report_avg_steps": report_avg,
            "report_max_steps": report_max,
            "cache_path": str(cache_path),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    lines = []
    report_title = {"daily": "综合健康日报", "weekly": "综合健康周报", "monthly": "综合健康月报"}.get(report_type, "综合健康报告")
    lines.append(f"# 🧬 {report_title} ({start} ~ {end})")
    lines.append("")
    lines.append("## 报告概览")
    lines.append(f"- 报告类型: {report_title}")
    lines.append(f"- 覆盖日期: {start} ~ {end}")
    lines.append("- 报告性质: 饮食记录与外部健康数据联合分析")
    lines.append(f"- 饮食数据来源: {dd / 'daily_log.json'}")
    lines.append(f"- 外部健康缓存: {str(cache_path)}")
    lines.append(f"- strict_real_data: {'true' if strict_real_data else 'false'}")

    lines.append("")
    lines.append("## 数据完整度与可信度说明")
    lines.append(f"- 饮食记录天数: {diet_summary.get('days_with_data', 0)}/{len(target_dates)}")
    lines.append(f"- 已记录餐次: {total_meals}")
    lines.append(f"- 已记录食物条目: {total_food_items}")
    lines.append(f"- 外部健康缓存状态: {ext_data.get('status', 'unknown')}")
    lines.append(f"- 外部健康缓存生成时间: {ext_data.get('cache_meta', {}).get('cache_generated_at', '无')}")
    lines.append(f"- 外部健康缓存指纹: {ext_data.get('cache_meta', {}).get('cache_fingerprint', '无')}")
    if energies:
        lines.append(f"- 能量数据置信度: {_confidence_label([e.get('active_burn_confidence_label') for e in energies])}")
        lines.append(f"- 能量数据来源: {', '.join(sorted(set(str(e.get('active_burn_source', 'unknown')) for e in energies)))}")
    else:
        lines.append("- 能量数据置信度: 无")

    lines.append("")
    lines.append("## 饮食分析")
    if diet_summary["days_with_data"] == 0:
        lines.append("- 当前周期没有饮食记录。")
    else:
        avg = diet_summary["avg"]
        lines.append("| 指标 | 摄入 | 目标 | 达标率 | 状态 |")
        lines.append("|------|------|------|--------|------|")
        for label, key in [("热量(kcal)", "calories"), ("蛋白质(g)", "protein"), ("碳水(g)", "carbs"), ("脂肪(g)", "fat"), ("纤维(g)", "fiber")]:
            actual = avg.get(key, 0)
            target = targets.get(key, 0)
            pct = _pct(actual, target)
            lines.append(f"| {label} | {actual} | {target} | {pct}% | {_status_from_pct(pct)} |")
        lines.append("")
        lines.append(f"- 饮食均衡评分: {diet_summary.get('score', 0)}/100")
        lines.append(f"- 总摄入热量: {diet_summary.get('total_intake_kcal', 0)} kcal")
        if diet_summary.get("top_foods"):
            lines.append("- 高频食物: " + "、".join(f"{name}({cnt}次)" for name, cnt in diet_summary["top_foods"][:8]))
        if report_type == "daily":
            day_foods = daily.get(target_dates[0], {}).get("foods", [])
            if day_foods:
                lines.append("- 当日食物明细: " + "、".join(day_foods))

    lines.append("")
    lines.append("## 活动与步数分析")
    if total_steps_list:
        lines.append(f"- 统计天数: {len(total_steps_list)}")
        lines.append(f"- 总步数: {sum(total_steps_list)}")
        lines.append(f"- 日均步数: {round(sum(total_steps_list) / len(total_steps_list), 1)}")
        lines.append(f"- 最高单日步数: {max(total_steps_list)}")
        lines.append(f"- 久坐 block 数: {total_sedentary_blocks}")
        if all_fast_walks:
            lines.append(f"- 快走片段: {len(all_fast_walks)} 段, 累计 {sum(w.get('duration_minutes', 0) for w in all_fast_walks)} 分钟")
            for walk in all_fast_walks[:8]:
                lines.append(
                    f"  - {walk.get('start', '未知')} ~ {walk.get('end', '未知')} | {walk.get('duration_minutes', 0)} 分钟 | "
                    f"{walk.get('total_steps', 0)} 步 | 峰值 {walk.get('max_steps_per_min', 0)} 步/分"
                )
        else:
            lines.append("- 快走片段: 未发现明确记录")
    else:
        lines.append("- 无有效步数记录")

    lines.append("")
    lines.append("## 睡眠恢复分析")
    if sleeps:
        avg_sleep_h = sum(s.get('total_sleep_hours', 0) for s in sleeps) / len(sleeps)
        avg_deep_ratio = sum(float(s.get('deep_sleep_ratio', 0)) for s in sleeps) / len(sleeps)
        avg_rem_ratio = sum(float(s.get('rem_ratio', 0)) for s in sleeps) / len(sleeps)
        avg_efficiency = sum(float(s.get('sleep_efficiency', 0)) for s in sleeps) / len(sleeps)
        total_awakes = sum(s.get('awake_interruptions_mins', 0) for s in sleeps)
        lines.append(f"- 平均睡眠时长: {avg_sleep_h:.2f} 小时")
        lines.append(f"- 平均深睡比例: {avg_deep_ratio * 100:.1f}%")
        lines.append(f"- 平均 REM 比例: {avg_rem_ratio * 100:.1f}%")
        lines.append(f"- 平均睡眠效率: {avg_efficiency * 100:.1f}%")
        lines.append(f"- 清醒打断总时长: {total_awakes:.1f} 分钟")
        if report_type == "daily":
            day_sleep = sleeps[0]
            lines.append(
                f"- 当日睡眠摘要: {day_sleep.get('total_sleep_hours', 0):.2f} 小时 | 深睡 {float(day_sleep.get('deep_sleep_ratio', 0)) * 100:.1f}% | "
                f"REM {float(day_sleep.get('rem_ratio', 0)) * 100:.1f}% | 效率 {float(day_sleep.get('sleep_efficiency', 0)) * 100:.1f}%"
            )
    else:
        lines.append("- 无睡眠恢复数据")

    lines.append("")
    lines.append("## 心率与运动分析")
    if baseline or selected_workouts:
        lines.append(f"- 静息心率基线: {baseline.get('estimated_rhr', '无')} bpm")
        lines.append(f"- 观测峰值心率: {baseline.get('observed_peak_hr', '无')} bpm")
        lines.append(f"- Zone2 阈值: {baseline.get('zonal_thresholds', {}).get('Zone2', '无')}")
        lines.append(f"- Zone3 阈值: {baseline.get('zonal_thresholds', {}).get('Zone3', '无')}")
        lines.append(f"- 推测运动次数: {len(selected_workouts)}")
        lines.append(f"- Zone2+ 总运动时长: {hr_info.get('total_exercise_minutes_zone2_plus', 0)} 分钟")
        if selected_workouts:
            for workout in selected_workouts[:8]:
                lines.append(
                    f"  - {workout.get('start', '未知')} ~ {workout.get('end', '未知')} | {workout.get('duration_minutes', 0)} 分钟 | "
                    f"平均 HR {workout.get('avg_hr', '无')} | 峰值 HR {workout.get('peak_hr', '无')}"
                )
        else:
            lines.append("- 未识别到明确的 Zone2+ 运动片段")
    else:
        lines.append("- 无心率与运动数据")

    lines.append("")
    lines.append("## 能量收支分析")
    if energies:
        avg_resting = sum(e.get('resting_burn_kcal', 0) for e in energies) / len(energies)
        avg_active = sum(e.get('active_burn_kcal', 0) for e in energies) / len(energies)
        avg_tdee = sum(e.get('tdee_kcal', 0) for e in energies) / len(energies)
        avg_tdee_low = sum(e.get('tdee_kcal_low', e.get('tdee_kcal', 0)) for e in energies) / len(energies)
        avg_tdee_high = sum(e.get('tdee_kcal_high', e.get('tdee_kcal', 0)) for e in energies) / len(energies)
        avg_active_low = sum(e.get('active_burn_kcal_low', e.get('active_burn_kcal', 0)) for e in energies) / len(energies)
        avg_active_high = sum(e.get('active_burn_kcal_high', e.get('active_burn_kcal', 0)) for e in energies) / len(energies)
        confidence = _confidence_label([e.get('active_burn_confidence_label') for e in energies])
        avg_intake = diet_summary.get('avg_intake_kcal', 0)
        intake_minus_tdee = round(avg_intake - avg_tdee, 1) if avg_intake and avg_tdee else None
        lines.append(f"- 平均静息消耗: {avg_resting:.1f} kcal")
        lines.append(f"- 平均活动消耗: {avg_active:.1f} kcal")
        lines.append(f"- 平均 TDEE: {avg_tdee:.1f} kcal")
        lines.append(f"- TDEE 区间: {avg_tdee_low:.1f} ~ {avg_tdee_high:.1f} kcal")
        lines.append(f"- 活动消耗区间: {avg_active_low:.1f} ~ {avg_active_high:.1f} kcal")
        lines.append(f"- 活动消耗置信度: {confidence}")
        if intake_minus_tdee is not None:
            lines.append(f"- 摄入减 TDEE: {intake_minus_tdee:+.1f} kcal")
    else:
        lines.append("- 无能量消耗数据")

    lines.append("")
    lines.append("## 体成分概览")
    if valid_body_days:
        latest_body_date = valid_body_days[-1]
        body = body_metrics[latest_body_date]
        lines.append(f"- 取值日期: {latest_body_date}")
        lines.append(f"- 体重: {_fmt_optional(body.get('weight_kg'))} kg")
        lines.append(f"- 体脂率: {_fmt_optional(body.get('body_fat_pct'))} %")
        lines.append(f"- 骨骼肌: {_fmt_optional(body.get('skeletal_muscle_kg'))} kg")
        lines.append(f"- BMR: {_fmt_optional(body.get('bmr_kcal'))} kcal")
        lines.append(f"- 肌肉/脂肪比: {_fmt_optional(body.get('muscle_fat_ratio', body.get('smi_ratio')))}")
        lines.append(f"- SMI: {_fmt_optional(body.get('smi_kg_m2'))}")
    else:
        lines.append("- 无体成分数据")

    lines.append("")
    lines.append("## 关键异常与亮点")
    findings = []
    if diet_summary.get("days_with_data", 0) > 0:
        avg = diet_summary.get("avg", {})
        fat_target = targets.get("fat", 0)
        if fat_target and _pct(avg.get("fat", 0), fat_target) < 80:
            findings.append(f"- 脂肪摄入偏低: {avg.get('fat', 0)}g / {fat_target}g, 达标率 {_pct(avg.get('fat', 0), fat_target)}%")
        protein_target = targets.get("protein", 0)
        if protein_target and _pct(avg.get("protein", 0), protein_target) >= 90:
            findings.append(f"- 蛋白质完成度较高: {avg.get('protein', 0)}g / {protein_target}g")
    if total_steps_list:
        if max(total_steps_list) >= 8000:
            findings.append(f"- 活动量尚可: 最高单日步数 {max(total_steps_list)}")
        if total_sedentary_blocks > 0:
            findings.append(f"- 久坐暴露存在: {total_sedentary_blocks} 个长时久坐 block")
    if sleeps:
        avg_sleep_h = sum(s.get('total_sleep_hours', 0) for s in sleeps) / len(sleeps)
        avg_deep_ratio = sum(float(s.get('deep_sleep_ratio', 0)) for s in sleeps) / len(sleeps)
        if avg_sleep_h >= 7.5:
            findings.append(f"- 睡眠时长充足: 平均 {avg_sleep_h:.2f} 小时")
        if avg_deep_ratio >= 0.2:
            findings.append(f"- 深睡占比表现不错: 平均 {avg_deep_ratio * 100:.1f}%")
    if energies and diet_summary.get('avg_intake_kcal', 0):
        avg_tdee = sum(e.get('tdee_kcal', 0) for e in energies) / len(energies)
        delta = round(diet_summary.get('avg_intake_kcal', 0) - avg_tdee, 1)
        if abs(delta) >= 300:
            findings.append(f"- 热量收支偏离较大: 摄入减 TDEE = {delta:+.1f} kcal")
    if not findings:
        findings.append("- 当前周期未检测到特别突出的异常或亮点，建议结合原始指标继续观察。")
    lines.extend(findings[:6])

    llm_advice_lines = load_llm_advice(llm_advice_file)
    llm_advice_meta = {"source": "file", "path": llm_advice_file, "generated_count": len(llm_advice_lines)} if llm_advice_file else None
    if auto_llm_advice and not llm_advice_lines:
        llm_advice_lines, llm_advice_meta, llm_error = generate_llm_advice(data_dir, report_type, objective_payload)
        if llm_error:
            result = {
                "status": "error",
                "message": "综合健康报告未生成：自动大模型建议生成失败。",
                "reason": llm_error,
                "llm_advice_meta": llm_advice_meta,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result
    if llm_advice_meta is None:
        llm_advice_meta = {"source": "none", "generated_count": len(llm_advice_lines)}

    lines.append("")
    lines.append("## 行动建议")
    if llm_advice_lines:
        for idx, advice in enumerate(llm_advice_lines, 1):
            lines.append(f"- 建议 {idx}：{advice}")
    else:
        lines.append("- 本部分由大模型基于下方客观数据生成，脚本不做规则化建议。")

    cache_fingerprint = ext_data.get("cache_meta", {}).get("cache_fingerprint")
    if not cache_fingerprint:
        cache_fingerprint = _metrics_fingerprint(metrics)

    lines.append("")
    lines.append("## 🤖 建议生成输入（客观数据）")
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
        "source_cache_path": str(cache_path) if cache_path.exists() else None,
        "source_cache_fingerprint": cache_fingerprint,
        "source_cache_step_avg": step_stats["avg_steps"],
        "source_cache_step_max": step_stats["max_steps"],
        "data_completeness": {
            "diet_days_with_data": diet_summary.get("days_with_data", 0),
            "period_days": len(target_dates),
            "total_meals_logged": total_meals,
            "total_food_items_logged": total_food_items,
            "external_cache_exists": True,
            "external_cache_status": ext_data.get("status"),
        },
        "llm_generated_advice": llm_advice_lines,
        "llm_advice_meta": llm_advice_meta,
        "llm_objective_input": objective_payload,
        "report_markdown": report,
    }
    md_path, json_path = save_report_files(data_dir, report_type, start, end, report, payload)
    payload["saved_report_markdown_path"] = md_path
    payload["saved_report_json_path"] = json_path
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload

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
    p2.add_argument("--enabled", action="store_true")
    p2.add_argument("--disabled", action="store_true")
    p2.add_argument("--auto-llm-advice", action="store_true")
    p2.add_argument("--strict-real-data", action="store_true")
    p2.add_argument("--llm-advice-file", default=None)
    p2.add_argument("--push-notion", action="store_true")
    p2.add_argument("--data-dir", required=True)
    
    p3 = sp.add_parser("generate-merged")
    p3.add_argument("--type", required=True, choices=["daily","weekly","monthly"])
    p3.add_argument("--end-date", default=None)
    p3.add_argument("--data-dir", required=True)
    p3.add_argument("--strict-real-data", action="store_true",
                    help="严格真实模式：发现估算能耗或缺少外部缓存时直接报错")
    p3.add_argument("--llm-advice-file", default=None,
                    help="由大模型生成建议后的 JSON 文件路径（数组）")
    p3.add_argument("--auto-llm-advice", action="store_true",
                    help="自动调用已配置大模型生成建议并写入报告")

    p4 = sp.add_parser("set-llm-advice")
    p4.add_argument("--model", required=True)
    p4.add_argument("--api-key-env", default="OPENAI_API_KEY")
    p4.add_argument("--base-url", default="https://api.openai.com/v1/chat/completions")
    p4.add_argument("--temperature", type=float, default=0.4)
    p4.add_argument("--max-tokens", type=int, default=900)
    p4.add_argument("--timeout-seconds", type=int, default=90)
    p4.add_argument("--system-prompt", default="")
    p4.add_argument("--disabled", action="store_true")
    p4.add_argument("--data-dir", required=True)

    p5 = sp.add_parser("enrich-profile-from-data")
    p5.add_argument("--end-date", default=None)
    p5.add_argument("--data-dir", required=True)

    p6 = sp.add_parser("run-scheduled")
    p6.add_argument("--now", default=None, help="模拟当前时间，格式 YYYY-MM-DD HH:MM")
    p6.add_argument("--force", action="store_true")
    p6.add_argument("--dry-run", action="store_true")
    p6.add_argument("--data-dir", required=True)

    sp.add_parser("demo")
    args = pa.parse_args()
    
    if args.command == "generate":
        generate_report(args.data_dir, args.type, args.end_date)
    elif args.command == "set-schedule":
        existing = load_json(Path(args.data_dir) / "report_schedule.json", {})
        enabled = bool(existing.get("enabled", True))
        if args.disabled:
            enabled = False
        elif args.enabled:
            enabled = True
        auto_llm_advice = bool(existing.get("auto_llm_advice", True))
        if args.auto_llm_advice:
            auto_llm_advice = True
        strict_real_data = bool(existing.get("strict_real_data", False))
        if args.strict_real_data:
            strict_real_data = True
        push_notion = bool(existing.get("push_notion", False))
        if args.push_notion:
            push_notion = True
        llm_advice_file = args.llm_advice_file if args.llm_advice_file is not None else existing.get("llm_advice_file")
        set_schedule_advanced(
            frequency=args.frequency,
            time_str=args.time,
            data_dir=args.data_dir,
            enabled=enabled,
            auto_llm_advice=auto_llm_advice,
            strict_real_data=strict_real_data,
            llm_advice_file=llm_advice_file,
            push_notion=push_notion,
        )
    elif args.command == "generate-merged":
        generate_merged_report(
            args.data_dir,
            args.type,
            args.end_date,
            args.strict_real_data,
            args.llm_advice_file,
            args.auto_llm_advice,
        )
    elif args.command == "set-llm-advice":
        set_llm_advice_config(
            data_dir=args.data_dir,
            model=args.model,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout_seconds,
            system_prompt=args.system_prompt,
            enabled=not args.disabled,
        )
    elif args.command == "enrich-profile-from-data":
        enrich_profile_from_data(args.data_dir, args.end_date)
    elif args.command == "run-scheduled":
        run_scheduled_pipeline(args.data_dir, args.now, args.force, args.dry_run)
    elif args.command == "demo":
        cmd_demo(args)
    else:
        pa.print_help()

if __name__ == "__main__": main()
