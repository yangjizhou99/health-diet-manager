#!/usr/bin/env python3
"""
notion_health_sync.py — 将健康报告数据同步到 Notion 笔记
功能：
  init-config    设置 Notion API Token 和目标数据库/页面 ID
  push-report    将指定的 JSON 报告推送为 Notion 页面
  push-latest    自动查找最新报告并推送
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import requests
except ImportError:
    print("❌ 缺少 requests，请安装: pip install requests")
    sys.exit(1)

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"
CONFIG_FILENAME = "notion_sync_config.json"


# ═══════════════════════ 配置管理 ═══════════════════════

def load_config(data_dir: str) -> dict:
    path = Path(data_dir) / CONFIG_FILENAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def load_external_config(data_dir: str) -> dict:
    path = Path(data_dir) / "external_data_config.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def find_estimated_energy_days(metrics: dict) -> list:
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


def _metrics_fingerprint(metrics: dict) -> str:
    payload = json.dumps(metrics or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_active_cache_path(data_dir: str, period: str, target: str) -> Path:
    dd = Path(data_dir)
    canonical = dd / f"health_cache_{period}_{target}.json"
    pointer = dd / f"health_cache_{period}_{target}.latest.json"
    if pointer.exists():
        pointer_json = json.loads(pointer.read_text(encoding="utf-8"))
        active_file = pointer_json.get("active_cache_file")
        if active_file:
            candidate = dd / active_file
            if candidate.exists():
                return candidate
    return canonical


def save_config(data_dir: str, config: dict):
    path = Path(data_dir) / CONFIG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 配置已保存至 {path}")


def init_config(args):
    data_dir = args.data_dir
    config = load_config(data_dir)
    if args.token:
        config["notion_token"] = args.token
    if args.database_id:
        config["database_id"] = args.database_id
    if args.parent_page_id:
        config["parent_page_id"] = args.parent_page_id
    config["updated_at"] = datetime.now().isoformat()
    save_config(data_dir, config)


# ═══════════════════════ Notion API 工具 ═══════════════════════

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_API_VERSION,
    }


def _rich_text(content: str, bold=False, italic=False, color="default", code=False) -> dict:
    return {
        "type": "text",
        "text": {"content": content},
        "annotations": {
            "bold": bold, "italic": italic, "strikethrough": False,
            "underline": False, "code": code, "color": color,
        },
    }


def _paragraph(texts, color="default") -> dict:
    if isinstance(texts, str):
        texts = [_rich_text(texts)]
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": texts, "color": color}}


def _heading(level: int, text: str, color="default", toggleable=False) -> dict:
    key = f"heading_{level}"
    return {
        "object": "block", "type": key,
        key: {"rich_text": [_rich_text(text)], "color": color, "is_toggleable": toggleable},
    }


def _callout(text_parts, icon_emoji="💡", color="default") -> dict:
    if isinstance(text_parts, str):
        text_parts = [_rich_text(text_parts)]
    return {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": text_parts,
            "icon": {"type": "emoji", "emoji": icon_emoji},
            "color": color,
        },
    }


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _table_of_contents() -> dict:
    return {"object": "block", "type": "table_of_contents", "table_of_contents": {"color": "default"}}


def _quote(text: str, color="default") -> dict:
    return {
        "object": "block", "type": "quote",
        "quote": {"rich_text": [_rich_text(text)], "color": color},
    }


def _bulleted(text_parts) -> dict:
    if isinstance(text_parts, str):
        text_parts = [_rich_text(text_parts)]
    return {
        "object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": text_parts},
    }


def _todo(text: str, checked=False) -> dict:
    return {
        "object": "block", "type": "to_do",
        "to_do": {"rich_text": [_rich_text(text)], "checked": checked},
    }


def _table(headers: list, rows: list) -> dict:
    """创建 Notion 表格块。headers 为列头字符串列表, rows 为二维字符串列表。"""
    width = len(headers)
    table_rows = []
    # header row
    table_rows.append({
        "object": "block", "type": "table_row",
        "table_row": {"cells": [[_rich_text(h, bold=True)] for h in headers]},
    })
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            cells.append([_rich_text(str(cell))])
        # 如果列数不够则补空
        while len(cells) < width:
            cells.append([_rich_text("")])
        table_rows.append({
            "object": "block", "type": "table_row",
            "table_row": {"cells": cells},
        })
    return {
        "object": "block", "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        },
    }


def _toggle(text: str, children: list, color="default") -> dict:
    return {
        "object": "block", "type": "toggle",
        "toggle": {"rich_text": [_rich_text(text)], "color": color, "children": children},
    }


def _column_list(columns: list) -> dict:
    """columns: list of list[block]，每个子列表是一个 column 内的 blocks。"""
    col_blocks = []
    for col_children in columns:
        col_blocks.append({
            "object": "block", "type": "column",
            "column": {"children": col_children},
        })
    return {
        "object": "block", "type": "column_list",
        "column_list": {"children": col_blocks},
    }


# ═══════════════════════ 额外 Block 构建器 ═══════════════════════

def _code_block(content: str, language: str = "mermaid") -> dict:
    """Mermaid / 代码块。"""
    return {
        "object": "block", "type": "code",
        "code": {
            "rich_text": [_rich_text(content)],
            "language": language,
            "caption": [],
        },
    }


def _numbered(text_parts, color="default") -> dict:
    if isinstance(text_parts, str):
        text_parts = [_rich_text(text_parts)]
    return {
        "object": "block", "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": text_parts, "color": color},
    }


def _equation_block(expression: str) -> dict:
    """独立行公式 (通过 paragraph 内嵌 equation rich_text)。"""
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {
            "rich_text": [{
                "type": "equation",
                "equation": {"expression": expression},
                "annotations": {"bold": False, "italic": False, "strikethrough": False,
                                "underline": False, "code": False, "color": "default"},
                "plain_text": expression, "href": None,
            }],
            "color": "default",
        },
    }


def _bookmark(url: str, caption: str = "") -> dict:
    cap = [_rich_text(caption)] if caption else []
    return {"object": "block", "type": "bookmark", "bookmark": {"url": url, "caption": cap}}


def _breadcrumb() -> dict:
    return {"object": "block", "type": "breadcrumb", "breadcrumb": {}}


def _heading_toggle(level: int, text: str, children: list, color="default") -> list:
    """返回 [heading, ...children] 平层列表。
    Notion API 不允许 column_list、column 等作为 toggleable heading 的子块，
    所以用普通 heading + 平层 children 代替。用 divider 分割尾部。
    """
    key = f"heading_{level}"
    heading = {
        "object": "block", "type": key,
        key: {
            "rich_text": [_rich_text(text)],
            "color": color,
            "is_toggleable": False,
        },
    }
    return [heading] + children + [_divider()]


def _rich_callout(text_parts, icon_emoji="💡", color="default", children=None) -> dict:
    """支持子块的 Callout。"""
    if isinstance(text_parts, str):
        text_parts = [_rich_text(text_parts)]
    block = {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": text_parts,
            "icon": {"type": "emoji", "emoji": icon_emoji},
            "color": color,
        },
    }
    if children:
        block["callout"]["children"] = children
    return block


def _progress_bar(ratio, width=20):
    """生成文字进度条 ████░░░░ 63%。"""
    if ratio is None:
        return "—"
    filled = int(ratio * width)
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {ratio*100:.0f}%"


def _metric_card(emoji, label, value, sub_text="", bg_color="blue_background") -> dict:
    """创建一个指标卡片 Callout，用于 column 布局。"""
    parts = [
        _rich_text(f"{value}\n", bold=True, color="default"),
        _rich_text(label, italic=True, color="gray"),
    ]
    if sub_text:
        parts.append(_rich_text(f"\n{sub_text}", color="gray"))
    return _rich_callout(parts, icon_emoji=emoji, color=bg_color)


# ═══════════════════════ 模板构建器 ═══════════════════════

def _fmt(val, suffix="", decimals=1):
    if val is None or val == "":
        return "—"
    try:
        v = float(val)
        if decimals == 0:
            return f"{int(v)}{suffix}"
        return f"{v:.{decimals}f}{suffix}"
    except (ValueError, TypeError):
        return str(val)


def _pct(val):
    if val is None:
        return "—"
    return f"{float(val)*100:.1f}%"


def _status_emoji(ratio):
    if ratio is None:
        return "⬜"
    if ratio >= 0.9:
        return "✅"
    if ratio >= 0.6:
        return "⚠️"
    return "🔴"


# ────────────── 1. 封面 & 概览仪表盘 ──────────────

def build_cover_section(report: dict, metrics: dict, llm_input: dict) -> list:
    """构建封面区域：概览 Callout + 核心指标卡片仪表盘。"""
    blocks = []
    period = report.get("period", "未知")
    rtype = report.get("report_type", "daily")
    is_merged = report.get("is_merged", False)
    days = report.get("days_tracked", 0)

    type_map = {"daily": ("📅", "日报", "blue_background"),
                "weekly": ("📊", "周报", "green_background"),
                "monthly": ("📈", "月报", "purple_background")}
    emoji, label, bg = type_map.get(rtype, ("📋", rtype, "blue_background"))
    merged_tag = "综合分析" if is_merged else "饮食分析"

    # 主标题 Callout
    blocks.append(_rich_callout([
        _rich_text(f"  {label}  ", bold=True, color="default"),
        _rich_text(f"  {period}\n", color="default"),
        _rich_text(f"  {merged_tag}", italic=True, color="purple"),
        _rich_text(f"  ·  追踪 {days} 天", italic=True, color="gray"),
    ], icon_emoji="🧬", color=bg))

    blocks.append(_breadcrumb())

    # ── 核心指标仪表盘 (3 列 column_list) ──
    cv = metrics.get("cardiovascular_health", {})
    baseline = cv.get("baseline", {})
    rhr = baseline.get("estimated_rhr")
    sleep_data = metrics.get("sleep_recovery", {})
    activity = metrics.get("daily_activity", {})
    energy = metrics.get("energy_expenditure", {})
    body = metrics.get("body_composition", {})
    diet = llm_input.get("diet", {})

    # 计算摘要数据
    avg_sleep = 0
    if sleep_data:
        hours_list = [d.get("total_sleep_hours", 0) for d in sleep_data.values()]
        avg_sleep = sum(hours_list) / len(hours_list) if hours_list else 0

    avg_steps = 0
    if activity:
        steps_list = [d.get("total_steps", 0) for d in activity.values()]
        avg_steps = sum(steps_list) / len(steps_list) if steps_list else 0

    avg_tdee = 0
    if energy:
        tdee_list = [d.get("tdee_kcal", 0) for d in energy.values()]
        avg_tdee = sum(tdee_list) / len(tdee_list) if tdee_list else 0

    latest_weight = "—"
    if body:
        latest_date = sorted(body.keys())[-1]
        latest_weight = f"{body[latest_date].get('weight_kg', '—')} kg"

    intake_kcal = diet.get("avg_intake_kcal", 0)

    # 第一行：心率 / 睡眠 / 步数
    col1 = [_metric_card("❤️", "静息心率", f"{rhr} bpm" if rhr else "—",
                         "心血管健康基线", "red_background")]
    col2 = [_metric_card("😴", "平均睡眠", f"{avg_sleep:.1f} h" if avg_sleep else "—",
                         f"共 {len(sleep_data)} 晚数据", "purple_background")]
    col3 = [_metric_card("🏃", "日均步数", f"{int(avg_steps):,}" if avg_steps else "—",
                         f"目标 8,000 步", "green_background")]
    blocks.append(_column_list([col1, col2, col3]))

    # 第二行：TDEE / 摄入 / 体重
    col4 = [_metric_card("⚡", "日均 TDEE", f"{int(avg_tdee):,} kcal" if avg_tdee else "—",
                         "总能量消耗", "orange_background")]
    col5 = [_metric_card("🍽️", "日均摄入", f"{int(intake_kcal):,} kcal" if intake_kcal else "—",
                         f"记录 {diet.get('days_with_records', 0)} 天", "yellow_background")]
    col6 = [_metric_card("⚖️", "最新体重", latest_weight,
                         "体成分追踪", "blue_background")]
    blocks.append(_column_list([col4, col5, col6]))

    blocks.append(_paragraph([]))  # 空行
    blocks.append(_table_of_contents())
    blocks.append(_divider())

    return blocks


# ────────────── 2. 饮食分析 ──────────────

def build_diet_section(llm_input: dict) -> list:
    blocks = []
    diet = llm_input.get("diet", {})
    days_with = diet.get("days_with_records", 0)

    if days_with == 0:
        blocks.extend(_heading_toggle(1, "🍎 饮食分析", [
            _callout("当前周期没有饮食记录。拍照或文字描述你的餐食即可开始记录！",
                     icon_emoji="📝", color="yellow_background"),
            _quote("💡 Tip: 发送食物照片，我会自动识别并计算营养成分。", color="gray_background"),
        ]))
        return blocks

    avg = diet.get("avg_daily", {})
    target = diet.get("target_daily", {})
    score = diet.get("diet_balance_score")
    intake_kcal = diet.get("avg_intake_kcal", 0)
    diversity = diet.get("nutrition_diversity", {})

    inner_blocks = []

    # 评分卡片 (两列：评分 + 热量信息)
    if score is not None:
        score_col = [_rich_callout([
            _rich_text(f"{int(score)}/100\n", bold=True),
            _rich_text("饮食均衡评分", italic=True, color="gray"),
        ], icon_emoji="⭐", color="green_background")]
        kcal_col = [_rich_callout([
            _rich_text(f"{int(intake_kcal):,} kcal\n", bold=True),
            _rich_text(f"日均摄入 · 有效记录 {days_with} 天", italic=True, color="gray"),
        ], icon_emoji="🔥", color="orange_background")]
        inner_blocks.append(_column_list([score_col, kcal_col]))
    if diversity.get("score") is not None:
        avg_cov = diversity.get("avg_nutrient_dimension_coverage", diversity.get("avg_category_coverage", 0))
        missing_dims = diversity.get("missing_nutrient_dimensions", diversity.get("missing_categories", []))
        inner_blocks.append(_quote(
            f"🥗 营养素多样性 {int(diversity.get('score', 0))}/100 · {diversity.get('level', '未评级')} · "
            f"日均 {diversity.get('avg_unique_foods_per_day', 0)} 种食物 · 日均覆盖营养素维度 {avg_cov}",
            color="green_background",
        ))
        if missing_dims:
            inner_blocks.append(_paragraph([
                _rich_text("建议补充营养素维度：", bold=True, color="orange"),
                _rich_text("、".join(missing_dims)),
            ]))

    # 营养素达成率表 + 进度条
    nutrient_headers = ["营养素", "日均摄入", "目标推荐", "进度", "状态"]
    nutrient_rows = []
    # 用于 Mermaid 饼图的数据
    macro_data = {}
    nutrient_map = [
        ("calories", "🔥 热量", "kcal"), ("protein", "🥩 蛋白质", "g"),
        ("carbs", "🍚 碳水化合物", "g"), ("fat", "🥑 脂肪", "g"),
        ("fiber", "🥬 膳食纤维", "g"), ("sodium", "🧂 钠", "mg"),
    ]
    for key, label, unit in nutrient_map:
        avg_val = avg.get(key)
        tgt_val = target.get(key)
        if avg_val is not None and tgt_val and tgt_val > 0:
            ratio = avg_val / tgt_val
            bar = _progress_bar(ratio, 15)
            status = _status_emoji(ratio)
            nutrient_rows.append([label, f"{_fmt(avg_val)} {unit}",
                                  f"{_fmt(tgt_val)} {unit}", bar, status])
            if key in ("protein", "carbs", "fat"):
                macro_data[key] = avg_val
        elif avg_val is not None:
            nutrient_rows.append([label, f"{_fmt(avg_val)} {unit}", "—", "—", "—"])
            if key in ("protein", "carbs", "fat"):
                macro_data[key] = avg_val

    if nutrient_rows:
        inner_blocks.append(_table(nutrient_headers, nutrient_rows))

    # Mermaid 饼图：三大宏量营养素比例
    if macro_data and len(macro_data) >= 2:
        total_macro_g = sum(macro_data.values())
        if total_macro_g > 0:
            pie_lines = ["pie title 三大营养素占比 (克)"]
            name_map = {"protein": "蛋白质", "carbs": "碳水化合物", "fat": "脂肪"}
            for k, v in macro_data.items():
                pie_lines.append(f'    "{name_map.get(k, k)}" : {v:.1f}')
            inner_blocks.append(_paragraph([_rich_text("📊 宏量营养素分布", bold=True, color="purple")]))
            inner_blocks.append(_code_block("\n".join(pie_lines), "mermaid"))

    # 高频食物
    top_foods = diet.get("top_foods", [])
    if top_foods:
        food_items = []
        for f in top_foods[:6]:
            food_items.append(_bulleted([
                _rich_text(f.get("name", ""), bold=True),
                _rich_text(f"  ×{f.get('count', '?')}次", color="gray") if f.get("count") else _rich_text(""),
            ]))
        inner_blocks.append(_paragraph([_rich_text("🍱 高频食物 Top 6", bold=True, color="orange")]))
        inner_blocks.extend(food_items)

    blocks.extend(_heading_toggle(1, "🍎 饮食分析", inner_blocks, color="green_background"))
    return blocks


# ────────────── 3. 心血管健康 ──────────────

def build_cardiovascular_section(metrics: dict) -> list:
    blocks = []
    cv = metrics.get("cardiovascular_health", {})
    if not cv:
        blocks.extend(_heading_toggle(1, "❤️ 心血管健康", [
            _callout("暂无心率数据。连接你的手环/手表后将自动同步。", icon_emoji="💔", color="gray_background"),
        ]))
        return blocks

    baseline = cv.get("baseline", {})
    rhr = baseline.get("estimated_rhr")
    peak = baseline.get("observed_peak_hr")
    zones = baseline.get("zonal_thresholds", {})
    total_ex = cv.get("total_exercise_minutes_zone2_plus", 0)

    inner_blocks = []

    # 指标卡 2 列：RHR + Peak
    rhr_col = [_rich_callout([
        _rich_text(f"{rhr} bpm\n", bold=True),
        _rich_text("静息心率 (RHR)", italic=True, color="gray"),
        _rich_text(f"\n{'✅ 优秀' if rhr < 60 else '👍 正常' if rhr < 75 else '⚠️ 偏高'}", color="green" if rhr < 60 else "default"),
    ], icon_emoji="💚", color="green_background")] if rhr else [_paragraph("—")]
    peak_col = [_rich_callout([
        _rich_text(f"{peak} bpm\n", bold=True),
        _rich_text("观测峰值心率", italic=True, color="gray"),
    ], icon_emoji="🔴", color="red_background")] if peak else [_paragraph("—")]
    inner_blocks.append(_column_list([rhr_col, peak_col]))

    # 心率区间可视化 (Quote 带颜色)
    if zones:
        zone_blocks = []
        zone_colors = {"Zone1": "green_background", "Zone2": "blue_background",
                       "Zone3": "orange_background", "Zone4": "red_background", "Zone5": "pink_background"}
        zone_labels = {"Zone1": "🟢 轻松活动", "Zone2": "🔵 有氧燃脂", "Zone3": "🟠 有氧耐力",
                       "Zone4": "🔴 无氧阈值", "Zone5": "🟣 极限冲刺"}
        for z, rng in zones.items():
            label = zone_labels.get(z, z)
            color = zone_colors.get(z, "default")
            zone_blocks.append(_callout(f"{label}    {rng[0]}–{rng[1]} bpm", icon_emoji="💓", color=color))
        inner_blocks.append(_paragraph([_rich_text("🎯 心率训练区间", bold=True, color="red")]))
        inner_blocks.extend(zone_blocks)

    # 运动总量 + 运动次数
    workout_count = len(cv.get("inferred_workouts", []))
    inner_blocks.append(_quote(
        f"⏱️ Zone2+ 有效运动总计: {total_ex} 分钟  ·  共 {workout_count} 次运动  ·  "
        f"{'✅ 达到推荐量' if total_ex >= 150 else f'⚠️ 距周推荐 150 分钟还差 {150-total_ex} 分钟'}",
        color="blue_background",
    ))

    # 推测运动记录表
    workouts = cv.get("inferred_workouts", [])
    if workouts:
        headers = ["🕐 开始", "🏁 结束", "⏱ 时长", "💗 均心率", "📈 峰值", "🏷️ 强度"]
        rows = []
        for w in workouts:
            start = w.get("start", "")
            end = w.get("end", "")
            dur = w.get("duration_minutes", 0)
            avg_hr = w.get("avg_hr", 0)
            peak_hr = w.get("peak_hr", 0)
            z2_low = zones.get("Zone2", [0, 0])[0]
            z3_low = zones.get("Zone3", [0, 0])[0]
            if avg_hr >= z3_low:
                intensity = "🔥 高强度"
            elif avg_hr >= z2_low:
                intensity = "💪 中等"
            else:
                intensity = "🚶 轻度"
            rows.append([start, end, f"{dur} min", f"{avg_hr} bpm", f"{_fmt(peak_hr, '', 0)} bpm", intensity])
        inner_blocks.append(_paragraph([_rich_text("🏋️ AI 推测运动记录", bold=True, color="orange")]))
        inner_blocks.append(_table(headers, rows))

        # Mermaid 甘特图：运动时间线
        gantt_lines = ["gantt", "    title 运动时间线", "    dateFormat YYYY-MM-DD HH:mm",
                       "    axisFormat %m-%d %H:%M"]
        for idx, w in enumerate(workouts):
            start = w.get("start", "").replace(" ", " ")
            dur = w.get("duration_minutes", 0)
            avg_hr = w.get("avg_hr", 0)
            gantt_lines.append(f"    运动{idx+1} ({avg_hr}bpm) :w{idx}, {start}, {dur}m")
        inner_blocks.append(_code_block("\n".join(gantt_lines), "mermaid"))

    blocks.extend(_heading_toggle(1, "❤️ 心血管 & 运动分析", inner_blocks, color="red_background"))
    return blocks


# ────────────── 4. 睡眠恢复 ──────────────

def build_sleep_section(metrics: dict, llm_input: dict = None) -> list:
    blocks = []
    sleep_data = metrics.get("sleep_recovery", {})
    if not sleep_data:
        blocks.extend(_heading_toggle(1, "😴 睡眠恢复", [
            _callout("暂无睡眠数据。同步你的手环睡眠记录后将自动分析。", icon_emoji="🌙", color="gray_background"),
        ]))
        return blocks

    inner_blocks = []

    # 统计汇总
    dates = sorted(sleep_data.keys())
    hours_list = [sleep_data[d].get("total_sleep_hours", 0) for d in dates]
    deep_list = [sleep_data[d].get("deep_sleep_ratio", 0) for d in dates]
    rem_list = [sleep_data[d].get("rem_ratio", 0) for d in dates]
    eff_list = [sleep_data[d].get("sleep_efficiency", 0) for d in dates]
    awake_list = [sleep_data[d].get("awake_interruptions_mins", 0) for d in dates]
    count = len(dates)
    avg_h = sum(hours_list) / count
    avg_deep = sum(deep_list) / count
    avg_rem = sum(rem_list) / count
    avg_eff = sum(eff_list) / count
    total_awake = sum(awake_list)
    # 也可从 llm_input 获取预计算值
    if llm_input and llm_input.get("sleep", {}).get("total_awake_interruptions_minutes"):
        total_awake = max(total_awake, llm_input["sleep"]["total_awake_interruptions_minutes"])

    # 摘要卡片 第一行 3 列：时长 / 深睡 / REM
    col1 = [_rich_callout([
        _rich_text(f"{avg_h:.1f} h\n", bold=True),
        _rich_text("平均睡眠时长", italic=True, color="gray"),
        _rich_text(f"\n{'✅ 充足' if avg_h >= 7 else '⚠️ 不足 7h' if avg_h >= 6 else '🔴 严重不足'}", color="green" if avg_h >= 7 else "orange" if avg_h >= 6 else "red"),
    ], icon_emoji="🛏️", color="purple_background")]
    col2 = [_rich_callout([
        _rich_text(f"{avg_deep*100:.1f}%\n", bold=True),
        _rich_text("平均深睡比例", italic=True, color="gray"),
        _rich_text(f"\n{'✅ 良好 (>20%)' if avg_deep >= 0.2 else '⚠️ 偏低 (<20%)'}", color="green" if avg_deep >= 0.2 else "orange"),
    ], icon_emoji="🌊", color="blue_background")]
    col3 = [_rich_callout([
        _rich_text(f"{avg_rem*100:.1f}%\n", bold=True),
        _rich_text("平均 REM 比例", italic=True, color="gray"),
        _rich_text(f"\n{'✅ 正常 (>15%)' if avg_rem >= 0.15 else '⚠️ 偏低 (<15%)'}", color="green" if avg_rem >= 0.15 else "orange"),
    ], icon_emoji="💭", color="pink_background")]
    inner_blocks.append(_column_list([col1, col2, col3]))

    # 第二行 2 列：效率 / 总中断
    col4 = [_rich_callout([
        _rich_text(f"{avg_eff*100:.1f}%\n", bold=True),
        _rich_text("平均睡眠效率", italic=True, color="gray"),
        _rich_text(f"\n{'✅ 高效 (>95%)' if avg_eff >= 0.95 else '⚠️ 待改善'}", color="green" if avg_eff >= 0.95 else "orange"),
    ], icon_emoji="📈", color="green_background")]
    col5 = [_rich_callout([
        _rich_text(f"{_fmt(total_awake, '', 0)} min\n", bold=True),
        _rich_text(f"总中断时间 · {count} 晚", italic=True, color="gray"),
        _rich_text(f"\n日均 {total_awake/count:.0f} min" if count else "", color="gray"),
    ], icon_emoji="⏸️", color="orange_background")]
    inner_blocks.append(_column_list([col4, col5]))

    # Mermaid 柱状图：睡眠时长趋势
    if count >= 2:
        bar_lines = ["xychart-beta", '    title "睡眠时长趋势 (小时)"',
                     f'    x-axis [{", ".join(d[-5:] for d in dates)}]',
                     f'    y-axis "小时" 0 --> 12',
                     f'    bar [{", ".join(f"{h:.1f}" for h in hours_list)}]',
                     f'    line [{", ".join(["7.0"]*count)}]']
        inner_blocks.append(_code_block("\n".join(bar_lines), "mermaid"))

    # 详细表格 (Toggle 折叠)
    headers = ["📅 日期", "⏰ 时长", "🌊 深睡", "💭 REM", "📊 效率", "⏸️ 中断"]
    rows = []
    for date_str in dates:
        d = sleep_data[date_str]
        hours = d.get("total_sleep_hours", 0)
        deep = d.get("deep_sleep_ratio", 0)
        rem = d.get("rem_ratio", 0)
        eff = d.get("sleep_efficiency", 0)
        awake = d.get("awake_interruptions_mins", 0)
        if hours >= 7:
            h_emoji = "✅"
        elif hours >= 6:
            h_emoji = "⚠️"
        else:
            h_emoji = "🔴"
        rows.append([
            date_str, f"{h_emoji} {_fmt(hours, 'h')}",
            _progress_bar(deep, 10), _progress_bar(rem, 10),
            _progress_bar(eff, 10), f"{_fmt(awake, ' min', 0)}",
        ])
    inner_blocks.append(_toggle("📋 查看每日睡眠明细", [_table(headers, rows)]))

    blocks.extend(_heading_toggle(1, "😴 睡眠恢复分析", inner_blocks, color="purple_background"))
    return blocks


# ────────────── 5. 日常活动 ──────────────

def build_activity_section(metrics: dict, llm_input: dict = None) -> list:
    blocks = []
    activity = metrics.get("daily_activity", {})
    if not activity:
        blocks.extend(_heading_toggle(1, "🏃 日常活动", [
            _callout("暂无步数/活动记录。同步你的运动手环后将自动追踪。", icon_emoji="👟", color="gray_background"),
        ]))
        return blocks

    inner_blocks = []
    dates = sorted(activity.keys())

    # 汇总卡片行：日均步数 / 峰值步数 / 久坐总警告
    steps_list = [activity[d].get("total_steps", 0) for d in dates]
    sed_list = [activity[d].get("sedentary_3h_blocks_count", 0) for d in dates]
    avg_steps = sum(steps_list) / len(steps_list) if steps_list else 0
    max_steps_val = max(steps_list) if steps_list else 0
    total_sed = sum(sed_list)
    # 优先用 llm_input 预计算值
    if llm_input and llm_input.get("activity", {}).get("max_steps"):
        max_steps_val = max(max_steps_val, llm_input["activity"]["max_steps"])
    if llm_input and llm_input.get("activity", {}).get("total_sedentary_3h_blocks"):
        total_sed = max(total_sed, llm_input["activity"]["total_sedentary_3h_blocks"])

    col_a = [_metric_card("👟", "日均步数", f"{int(avg_steps):,}",
                          f"目标 8,000 步 · {_progress_bar(min(avg_steps/8000,1.0), 12)}",
                          "green_background" if avg_steps >= 8000 else "yellow_background")]
    col_b = [_metric_card("🏆", "峰值步数", f"{int(max_steps_val):,}",
                          f"最高单日记录", "blue_background")]
    col_c = [_metric_card("🪑", "久坐警告", f"{total_sed} 段",
                          f"连续 ≥3h 静坐" + (" · ⚠️ 注意" if total_sed >= 6 else ""),
                          "red_background" if total_sed >= 6 else "orange_background")]
    inner_blocks.append(_column_list([col_a, col_b, col_c]))

    # 每日步数卡片 (用 callout 列表展示，颜色编码)
    for date_str in dates:
        d = activity[date_str]
        steps = d.get("total_steps", 0)
        sed = d.get("sedentary_3h_blocks_count", 0)
        ratio = min(steps / 8000, 1.0)
        bar = _progress_bar(ratio, 20)
        if steps >= 8000:
            color, emoji_s = "green_background", "✅"
        elif steps >= 5000:
            color, emoji_s = "yellow_background", "⚠️"
        else:
            color, emoji_s = "red_background", "🔴"
        sed_warn = f"  ·  🪑 久坐 {sed} 段 (≥3h)" if sed >= 3 else ""
        inner_blocks.append(_callout(
            f"{date_str}    {emoji_s} {steps:,} 步    {bar}{sed_warn}",
            icon_emoji="👣", color=color,
        ))

    # Mermaid 柱状图
    if len(dates) >= 2:
        steps_list = [activity[d].get("total_steps", 0) for d in dates]
        max_s = max(steps_list) if steps_list else 10000
        bar_lines = ["xychart-beta", '    title "每日步数"',
                     f'    x-axis [{", ".join(d[-5:] for d in dates)}]',
                     f'    y-axis "步数" 0 --> {int(max_s*1.2)}',
                     f'    bar [{", ".join(str(s) for s in steps_list)}]',
                     f'    line [{", ".join(["8000"]*len(dates))}]']
        inner_blocks.append(_code_block("\n".join(bar_lines), "mermaid"))

    # 活动综述 Quote
    goal_days = sum(1 for s in steps_list if s >= 8000)
    inner_blocks.append(_quote(
        f"📊 达标天数: {goal_days}/{len(dates)}  ·  "
        f"日均 {int(avg_steps):,} 步  ·  峰值 {int(max_steps_val):,} 步  ·  "
        f"久坐总段数 {total_sed} 段",
        color="green_background" if goal_days >= len(dates) * 0.5 else "orange_background",
    ))

    blocks.extend(_heading_toggle(1, "🏃 日常活动", inner_blocks, color="green_background"))
    return blocks


# ────────────── 6. 能量收支 ──────────────

def build_energy_section(metrics: dict, llm_input: dict = None) -> list:
    blocks = []
    energy = metrics.get("energy_expenditure", {})
    if not energy:
        blocks.extend(_heading_toggle(1, "⚡ 能量代谢", [
            _callout("暂无能量消耗数据。", icon_emoji="🔋", color="gray_background"),
        ]))
        return blocks

    inner_blocks = []
    dates = sorted(energy.keys())

    # 能量汇总
    tdee_list = [energy[d].get("tdee_kcal", 0) for d in dates]
    tdee_low_list = [energy[d].get("tdee_kcal_low", energy[d].get("tdee_kcal", 0)) for d in dates]
    tdee_high_list = [energy[d].get("tdee_kcal_high", energy[d].get("tdee_kcal", 0)) for d in dates]
    active_list = [energy[d].get("active_burn_kcal", 0) for d in dates]
    active_low_list = [energy[d].get("active_burn_kcal_low", energy[d].get("active_burn_kcal", 0)) for d in dates]
    active_high_list = [energy[d].get("active_burn_kcal_high", energy[d].get("active_burn_kcal", 0)) for d in dates]
    resting_list = [energy[d].get("resting_burn_kcal", 0) for d in dates]
    neat_list = [energy[d].get("neat_estimate_kcal", 0) for d in dates]
    avg_tdee = sum(tdee_list) / len(tdee_list) if tdee_list else 0
    avg_active = sum(active_list) / len(active_list) if active_list else 0
    avg_resting = sum(resting_list) / len(resting_list) if resting_list else 0
    avg_neat = sum(neat_list) / len(neat_list) if neat_list else 0
    avg_active_low = sum(active_low_list) / len(active_low_list) if active_low_list else 0
    avg_active_high = sum(active_high_list) / len(active_high_list) if active_high_list else 0
    avg_tdee_low = sum(tdee_low_list) / len(tdee_low_list) if tdee_low_list else 0
    avg_tdee_high = sum(tdee_high_list) / len(tdee_high_list) if tdee_high_list else 0
    intake = 0
    if llm_input:
        intake = llm_input.get("diet", {}).get("avg_intake_kcal", 0)

    # 4 列卡片：TDEE / 活动消耗 / BMR / NEAT
    col1 = [_rich_callout([
        _rich_text(f"{int(avg_tdee):,} kcal\n", bold=True),
        _rich_text("日均总消耗 (TDEE)", italic=True, color="gray"),
    ], icon_emoji="🔥", color="orange_background")]
    col2 = [_rich_callout([
        _rich_text(f"{int(avg_active):,} kcal\n", bold=True),
        _rich_text("日均活动消耗", italic=True, color="gray"),
    ], icon_emoji="🏃", color="yellow_background")]
    col3 = [_rich_callout([
        _rich_text(f"{int(avg_resting):,} kcal\n", bold=True),
        _rich_text("基础代谢 (BMR)", italic=True, color="gray"),
    ], icon_emoji="💤", color="blue_background")]
    if avg_neat > 0:
        col4 = [_rich_callout([
            _rich_text(f"{int(avg_neat):,} kcal\n", bold=True),
            _rich_text("NEAT 非运动消耗", italic=True, color="gray"),
        ], icon_emoji="🧹", color="green_background")]
        inner_blocks.append(_column_list([col1, col2, col3, col4]))
    else:
        inner_blocks.append(_column_list([col1, col2, col3]))

    # 心率回退估算区间与置信度（仅在存在估算天时展示）
    estimated = [energy[d] for d in dates if energy[d].get("active_burn_source") == "estimated_from_hr"]
    if estimated:
        est_low = sum(e.get("active_burn_kcal_low", e.get("active_burn_kcal", 0)) for e in estimated) / len(estimated)
        est_high = sum(e.get("active_burn_kcal_high", e.get("active_burn_kcal", 0)) for e in estimated) / len(estimated)
        labels = [e.get("active_burn_confidence_label") for e in estimated if e.get("active_burn_confidence_label")]
        label = "unknown" if not labels else ("low" if "low" in labels else ("medium" if "medium" in labels else "high"))
        label_cn = {"low": "低", "medium": "中", "high": "高", "unknown": "未知"}.get(label, "未知")
        inner_blocks.append(_callout(
            f"⌚ 心率回退估算: {len(estimated)} 天  ·  活动消耗区间 {int(est_low):,}~{int(est_high):,} kcal/天  ·  置信度 {label_cn}",
            icon_emoji="📡", color="yellow_background",
        ))

    # BMR 公式展示 (equation block)
    if avg_resting > 0:
        inner_blocks.append(_quote(
            f"📐 基础代谢率 (BMR) ≈ {int(avg_resting):,} kcal/天  ·  "
            f"TDEE = BMR × 活动系数 ≈ {int(avg_tdee):,} kcal/天",
            color="blue_background",
        ))

    # 能量收支平衡分析
    # 优先用实际计算，也参考 llm 预计算的 avg_intake_minus_tdee
    diff = None
    if intake and avg_tdee:
        diff = intake - avg_tdee
    elif llm_input and llm_input.get("energy", {}).get("avg_intake_minus_tdee_kcal") is not None:
        diff = llm_input["energy"]["avg_intake_minus_tdee_kcal"]
    if diff is not None:
        if diff > 200:
            verdict = "📈 热量盈余（可能增重）"
            v_color = "red_background"
        elif diff < -500:
            verdict = "📉 热量大幅亏损（减重中）"
            v_color = "orange_background"
        elif diff < -200:
            verdict = "📉 适度热量缺口（健康减脂）"
            v_color = "green_background"
        else:
            verdict = "⚖️ 能量基本平衡"
            v_color = "blue_background"
        balance_text = f"{verdict}    "
        if intake and avg_tdee:
            balance_text += f"摄入 {int(intake):,} − 消耗 {int(avg_tdee):,} = {int(diff):+,} kcal/天"
        else:
            balance_text += f"能量差值 = {int(diff):+,} kcal/天"
        inner_blocks.append(_callout(balance_text, icon_emoji="📊", color=v_color))

    # 详细表格 Toggle（含 NEAT 列）
    headers = ["📅 日期", "🔥 TDEE", "🏃 活动消耗", "📏 区间", "💤 基础代谢", "🧹 NEAT", "📡 数据源", "🎯 置信度"]
    rows = []
    for date_str in dates:
        d = energy[date_str]
        source = d.get("active_burn_source", "unknown")
        source_label = {"estimated_from_hr": "⌚ 心率估算", "direct": "📱 设备直测"}.get(source, source)
        neat_val = d.get("neat_estimate_kcal", 0)
        low = d.get("active_burn_kcal_low", d.get("active_burn_kcal", 0))
        high = d.get("active_burn_kcal_high", d.get("active_burn_kcal", 0))
        conf = d.get("active_burn_confidence_label", "unknown")
        conf_label = {"low": "低", "medium": "中", "high": "高", "unknown": "—"}.get(conf, "—")
        rows.append([
            date_str, f"{_fmt(d.get('tdee_kcal', 0), '', 0)} kcal",
            f"{_fmt(d.get('active_burn_kcal', 0), '', 0)} kcal",
            f"{_fmt(low, '', 0)}~{_fmt(high, '', 0)}",
            f"{_fmt(d.get('resting_burn_kcal', 0), '', 0)} kcal",
            f"{_fmt(neat_val, '', 0)} kcal" if neat_val else "—",
            source_label,
            conf_label,
        ])
    inner_blocks.append(_toggle("📋 查看每日能量明细", [_table(headers, rows)]))

    # Mermaid 折线图
    if len(dates) >= 2:
        chart_lines = ["xychart-beta", '    title "每日能量消耗趋势 (kcal)"',
                       f'    x-axis [{", ".join(d[-5:] for d in dates)}]',
                       f'    y-axis "kcal" 0 --> {int(max(tdee_list)*1.3)}',
                       f'    line [{", ".join(str(int(t)) for t in tdee_list)}]',
                       f'    bar [{", ".join(str(int(a)) for a in active_list)}]']
        inner_blocks.append(_code_block("\n".join(chart_lines), "mermaid"))

    blocks.extend(_heading_toggle(1, "⚡ 能量代谢", inner_blocks, color="orange_background"))
    return blocks


# ────────────── 7. 体成分趋势 ──────────────

def build_body_composition_section(metrics: dict, llm_input: dict = None) -> list:
    blocks = []
    body = metrics.get("body_composition", {})
    # fallback: 如果 cache 没有体成分但 llm_input 有最新体成分数据
    if not body and llm_input and llm_input.get("body_composition_latest"):
        bcl = llm_input["body_composition_latest"]
        if bcl:  # 非空 dict
            body = {"latest": bcl}
    if not body:
        blocks.extend(_heading_toggle(1, "⚖️ 体成分趋势", [
            _callout("暂无体成分数据。建议每周至少上秤测量一次。", icon_emoji="📏", color="gray_background"),
        ]))
        return blocks

    inner_blocks = []
    dates = sorted(body.keys())

    # 最新数据卡片 (3 列)
    latest = body[dates[-1]]
    w = latest.get("weight_kg")
    bf = latest.get("body_fat_pct")
    sm = latest.get("skeletal_muscle_kg")
    bmr = latest.get("bmr_kcal")

    col1 = [_metric_card("⚖️", "体重", f"{w} kg" if w else "—", dates[-1], "blue_background")]
    col2 = [_metric_card("📉", "体脂率", f"{bf}%" if bf else "—",
                         f"{'✅ 正常' if bf and bf < 25 else '⚠️ 偏高'}" if bf else "", "yellow_background")]
    col3 = [_metric_card("💪", "骨骼肌", f"{sm} kg" if sm else "—",
                         f"BMR {int(bmr)} kcal" if bmr else "", "green_background")]
    inner_blocks.append(_column_list([col1, col2, col3]))

    # 趋势对比
    if len(dates) >= 2:
        first = body[dates[0]]
        w_diff = (latest.get("weight_kg", 0) or 0) - (first.get("weight_kg", 0) or 0)
        bf_diff = (latest.get("body_fat_pct", 0) or 0) - (first.get("body_fat_pct", 0) or 0)
        sm_diff = (latest.get("skeletal_muscle_kg", 0) or 0) - (first.get("skeletal_muscle_kg", 0) or 0)

        w_emoji = "📈 增" if w_diff > 0 else "📉 减" if w_diff < 0 else "➡️ 持平"
        bf_emoji = "📈 升" if bf_diff > 0 else "📉 降" if bf_diff < 0 else "➡️ 持平"
        sm_emoji = "📈 增" if sm_diff > 0 else "📉 减" if sm_diff < 0 else "➡️ 持平"

        inner_blocks.append(_callout(
            f"📐 {dates[0]} → {dates[-1]} 变化趋势\n"
            f"体重 {w_emoji} {w_diff:+.1f} kg    ·    "
            f"体脂 {bf_emoji} {bf_diff:+.1f}%    ·    "
            f"骨骼肌 {sm_emoji} {sm_diff:+.1f} kg",
            icon_emoji="📊", color="orange_background",
        ))

    # 详细表格
    headers = ["📅 日期", "⚖️ 体重", "📉 体脂率", "💪 骨骼肌", "🔥 BMR", "📏 骨骼肌/脂肪比", "🧮 SMI(kg/m2)"]
    rows = []
    for date_str in dates:
        d = body[date_str]
        muscle_fat_ratio = d.get("muscle_fat_ratio", d.get("smi_ratio"))
        rows.append([
            date_str, _fmt(d.get("weight_kg"), " kg"),
            f"{d.get('body_fat_pct', '—')}%" if d.get("body_fat_pct") else "—",
            _fmt(d.get("skeletal_muscle_kg"), " kg"),
            _fmt(d.get("bmr_kcal"), " kcal", 0),
            _fmt(muscle_fat_ratio),
            _fmt(d.get("smi_kg_m2")),
        ])
    inner_blocks.append(_table(headers, rows))

    blocks.extend(_heading_toggle(1, "⚖️ 体成分趋势", inner_blocks, color="blue_background"))
    return blocks


# ────────────── 8. AI 健康建议 ──────────────

def build_ai_advice_section(report: dict) -> list:
    blocks = []

    advice_lines = []

    llm_advice = report.get("llm_generated_advice")
    if isinstance(llm_advice, list):
        for item in llm_advice:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    advice_lines.append(text)
            elif isinstance(item, dict):
                title = str(item.get("title", "")).strip()
                detail = str(item.get("detail", "")).strip()
                if title and detail:
                    advice_lines.append(f"{title}：{detail}")
                elif detail:
                    advice_lines.append(detail)
                elif title:
                    advice_lines.append(title)

    if not advice_lines:
        md = report.get("report_markdown", "")
        in_advice = False
        in_code_block = False
        for line in md.split("\n"):
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            if ("建议生成输入" in line or "客观数据" in line) and ("##" in line or "🤖" in line):
                continue
            if ("建议" in line or "洞察" in line or "💡" in line) and ("##" in line):
                in_advice = True
                continue
            if in_advice:
                if line.startswith("## ") or line.startswith("# "):
                    break
                stripped = line.strip("- ").strip()
                if stripped and not stripped.startswith("{") and not stripped.startswith('"'):
                    advice_lines.append(stripped)

    inner_blocks = []
    if advice_lines:
        # 用不同颜色的 callout 展示每条建议
        advice_colors = ["blue_background", "green_background", "purple_background",
                         "orange_background", "yellow_background", "pink_background"]
        advice_emojis = ["💡", "🎯", "🔑", "⚡", "🌟", "📌"]
        for idx, adv in enumerate(advice_lines):
            color = advice_colors[idx % len(advice_colors)]
            emoji = advice_emojis[idx % len(advice_emojis)]
            if ":" in adv or "：" in adv:
                sep = "：" if "：" in adv else ":"
                title, detail = adv.split(sep, 1)
                inner_blocks.append(_rich_callout([
                    _rich_text(title.strip(), bold=True),
                    _rich_text(f"\n{detail.strip()}", color="gray"),
                ], icon_emoji=emoji, color=color))
            else:
                inner_blocks.append(_callout(adv, icon_emoji=emoji, color=color))

        # 待办清单
        inner_blocks.append(_paragraph([_rich_text("\n📝 今日行动清单", bold=True, color="purple")]))
        for adv in advice_lines[:3]:
            short = adv.split(":", 1)[-1].split("：", 1)[-1].strip()[:50]
            inner_blocks.append(_todo(short, checked=False))
    else:
        inner_blocks.append(_callout(
            "数据不足，暂无法生成个性化建议。请补充饮食记录和健康数据以获得精准 AI 指导。",
            icon_emoji="🤖", color="yellow_background",
        ))
        inner_blocks.append(_quote("💡 记录越完整，AI 建议越精准！试试拍一张午餐照片开始记录吧。", color="blue_background"))

    blocks.extend(_heading_toggle(1, "💡 AI 健康建议 & 行动计划", inner_blocks, color="pink_background"))
    return blocks


# ══════════════════════════════════════════════════════════════
#  日报专用模板 — 紧凑单日快照
# ══════════════════════════════════════════════════════════════

def _latest_val(data_dict: dict, key: str, default=None):
    """从按日期排序的字典中取最新一天的某字段。"""
    if not data_dict:
        return default
    latest_date = sorted(data_dict.keys())[-1]
    return data_dict[latest_date].get(key, default)


def build_daily_cover(report: dict, metrics: dict, llm_input: dict) -> list:
    """日报封面：单日快照仪表盘。"""
    blocks = []
    period = report.get("period", "")
    is_merged = report.get("is_merged", False)
    tag = "综合分析" if is_merged else "饮食分析"

    blocks.append(_rich_callout([
        _rich_text("  📅 日报  ", bold=True),
        _rich_text(f"  {period}\n", color="default"),
        _rich_text(f"  {tag}", italic=True, color="purple"),
    ], icon_emoji="🧬", color="blue_background"))
    blocks.append(_breadcrumb())

    cv = metrics.get("cardiovascular_health", {})
    rhr = cv.get("baseline", {}).get("estimated_rhr")
    sleep_data = metrics.get("sleep_recovery", {})
    activity = metrics.get("daily_activity", {})
    energy = metrics.get("energy_expenditure", {})
    body = metrics.get("body_composition", {})
    diet = llm_input.get("diet", {})

    sleep_h = _latest_val(sleep_data, "total_sleep_hours", 0)
    sleep_eff = _latest_val(sleep_data, "sleep_efficiency")
    steps = _latest_val(activity, "total_steps", 0)
    tdee = _latest_val(energy, "tdee_kcal", 0)
    intake = diet.get("avg_intake_kcal", 0)
    latest_weight = "—"
    if body:
        latest_weight = f"{body[sorted(body.keys())[-1]].get('weight_kg', '—')} kg"

    # Row 1: 睡眠 / 心率 / 步数
    col1 = [_metric_card("😴", "昨晚睡眠", f"{sleep_h:.1f} h" if sleep_h else "—",
                         f"效率 {_pct(sleep_eff)}" if sleep_eff else "", "purple_background")]
    col2 = [_metric_card("❤️", "静息心率", f"{rhr} bpm" if rhr else "—",
                         "心血管基线", "red_background")]
    col3 = [_metric_card("🏃", "今日步数", f"{steps:,}" if steps else "—",
                         f"{_progress_bar(min(steps/8000,1.0),10)}" if steps else "", "green_background")]
    blocks.append(_column_list([col1, col2, col3]))

    # Row 2: TDEE / 摄入 / 体重
    col4 = [_metric_card("⚡", "今日消耗", f"{int(tdee):,} kcal" if tdee else "—",
                         "TDEE", "orange_background")]
    col5 = [_metric_card("🍽️", "今日摄入", f"{int(intake):,} kcal" if intake else "—",
                         f"记录 {diet.get('days_with_records',0)} 餐", "yellow_background")]
    col6 = [_metric_card("⚖️", "当前体重", latest_weight, "最近测量", "blue_background")]
    blocks.append(_column_list([col4, col5, col6]))

    blocks.append(_table_of_contents())
    blocks.append(_divider())
    return blocks


def build_daily_diet(llm_input: dict) -> list:
    """日报饮食：单日饮食详情视图。"""
    blocks = []
    diet = llm_input.get("diet", {})
    days_with = diet.get("days_with_records", 0)

    if days_with == 0:
        blocks.extend(_heading_toggle(1, "🍎 今日饮食", [
            _callout("今天还没有饮食记录。拍照或描述你的餐食开始记录！",
                     icon_emoji="📝", color="yellow_background"),
        ]))
        return blocks

    inner = []
    avg = diet.get("avg_daily", {})
    target = diet.get("target_daily", {})
    intake = diet.get("avg_intake_kcal", 0)
    score = diet.get("diet_balance_score")
    diversity = diet.get("nutrition_diversity", {})

    # 热量 + 评分 卡片
    if score is not None:
        col1 = [_rich_callout([
            _rich_text(f"🔥 {int(intake):,} kcal\n", bold=True),
            _rich_text("今日总摄入", italic=True, color="gray"),
        ], icon_emoji="🍽️", color="orange_background")]
        col2 = [_rich_callout([
            _rich_text(f"⭐ {int(score)}/100\n", bold=True),
            _rich_text("饮食均衡评分", italic=True, color="gray"),
        ], icon_emoji="📊", color="green_background")]
        inner.append(_column_list([col1, col2]))
    if diversity.get("score") is not None:
        avg_cov = diversity.get("avg_nutrient_dimension_coverage", diversity.get("avg_category_coverage", 0))
        missing_dims = diversity.get("missing_nutrient_dimensions", diversity.get("missing_categories", []))
        inner.append(_quote(
            f"🥗 今日营养素多样性 {int(diversity.get('score', 0))}/100 · {diversity.get('level', '未评级')} · "
            f"{diversity.get('avg_unique_foods_per_day', 0)} 种食物 · 覆盖营养素维度 {avg_cov}",
            color="green_background",
        ))
        if missing_dims:
            inner.append(_paragraph([
                _rich_text("下一餐可补充营养素维度：", bold=True, color="orange"),
                _rich_text("、".join(missing_dims)),
            ]))

    # 营养素进度 (紧凑列表)
    nutrient_map = [
        ("protein", "🥩 蛋白质", "g"), ("carbs", "🍚 碳水", "g"),
        ("fat", "🥑 脂肪", "g"), ("fiber", "🥬 纤维", "g"),
    ]
    for key, label, unit in nutrient_map:
        avg_val = avg.get(key)
        tgt_val = target.get(key)
        if avg_val is not None and tgt_val and tgt_val > 0:
            ratio = avg_val / tgt_val
            bar = _progress_bar(ratio, 15)
            inner.append(_paragraph([
                _rich_text(f"{label}  ", bold=True),
                _rich_text(f"{_fmt(avg_val)}/{_fmt(tgt_val)} {unit}  "),
                _rich_text(bar, code=True),
                _rich_text(f"  {_status_emoji(ratio)}"),
            ]))

    # 宏量饼图
    macro_data = {}
    for key in ("protein", "carbs", "fat"):
        v = avg.get(key)
        if v:
            macro_data[key] = v
    if len(macro_data) >= 2:
        total_g = sum(macro_data.values())
        if total_g > 0:
            pie_lines = ["pie title 今日三大营养素"]
            nm = {"protein": "蛋白质", "carbs": "碳水", "fat": "脂肪"}
            for k, v in macro_data.items():
                pie_lines.append(f'    "{nm[k]}" : {v:.1f}')
            inner.append(_code_block("\n".join(pie_lines), "mermaid"))

    # 高频食物
    top_foods = diet.get("top_foods", [])
    if top_foods:
        inner.append(_paragraph([_rich_text("🍱 今日食物", bold=True, color="orange")]))
        for f in top_foods[:4]:
            inner.append(_bulleted([
                _rich_text(f.get("name", ""), bold=True),
                _rich_text(f"  ×{f.get('count','?')}", color="gray") if f.get("count") else _rich_text(""),
            ]))

    blocks.extend(_heading_toggle(1, "🍎 今日饮食", inner, color="green_background"))
    return blocks


def build_daily_sleep(metrics: dict) -> list:
    """日报睡眠：昨晚单夜睡眠详情 + 睡眠阶段饼图。"""
    blocks = []
    sleep_data = metrics.get("sleep_recovery", {})
    if not sleep_data:
        blocks.extend(_heading_toggle(1, "😴 昨晚睡眠", [
            _callout("暂无睡眠数据。", icon_emoji="🌙", color="gray_background"),
        ]))
        return blocks

    inner = []
    latest_date = sorted(sleep_data.keys())[-1]
    s = sleep_data[latest_date]
    hours = s.get("total_sleep_hours", 0)
    deep = s.get("deep_sleep_ratio", 0)
    rem = s.get("rem_ratio", 0)
    eff = s.get("sleep_efficiency", 0)
    awake = s.get("awake_interruptions_mins", 0)
    light = max(0, 1.0 - deep - rem)

    # 等级评定
    if hours >= 7.5 and eff >= 0.95:
        grade, gc = "A 优秀 🌟", "green_background"
    elif hours >= 7 and eff >= 0.9:
        grade, gc = "B 良好 ✅", "blue_background"
    elif hours >= 6:
        grade, gc = "C 一般 ⚠️", "yellow_background"
    else:
        grade, gc = "D 不足 🔴", "red_background"

    col1 = [_rich_callout([
        _rich_text(f"{grade}\n", bold=True),
        _rich_text(f"睡眠评级 · {latest_date}", italic=True, color="gray"),
    ], icon_emoji="🏅", color=gc)]
    col2 = [_rich_callout([
        _rich_text(f"{hours:.1f} 小时\n", bold=True),
        _rich_text(f"效率 {eff*100:.0f}% · 中断 {_fmt(awake, ' min', 0)}", italic=True, color="gray"),
    ], icon_emoji="⏰", color="purple_background")]
    inner.append(_column_list([col1, col2]))

    # 睡眠阶段 3 列
    col_d = [_metric_card("🌊", "深睡", f"{deep*100:.1f}%",
                          f"{'✅ >20%' if deep >= 0.2 else '⚠️ <20%'}", "blue_background")]
    col_r = [_metric_card("💭", "REM", f"{rem*100:.1f}%",
                          f"{'✅ >15%' if rem >= 0.15 else '⚠️ <15%'}", "pink_background")]
    col_l = [_metric_card("☁️", "浅睡", f"{light*100:.1f}%",
                          "轻度睡眠", "gray_background")]
    inner.append(_column_list([col_d, col_r, col_l]))

    # 睡眠阶段饼图
    pie_lines = ["pie title 睡眠阶段分布",
                 f'    "深睡" : {deep*100:.1f}',
                 f'    "REM" : {rem*100:.1f}',
                 f'    "浅睡" : {light*100:.1f}']
    inner.append(_code_block("\n".join(pie_lines), "mermaid"))

    blocks.extend(_heading_toggle(1, "😴 昨晚睡眠", inner, color="purple_background"))
    return blocks


def build_daily_activity(metrics: dict) -> list:
    """日报活动：今日步数 + 运动详情。"""
    blocks = []
    activity = metrics.get("daily_activity", {})
    cv = metrics.get("cardiovascular_health", {})
    inner = []

    # 今日步数
    if activity:
        latest_date = sorted(activity.keys())[-1]
        a = activity[latest_date]
        steps = a.get("total_steps", 0)
        sed = a.get("sedentary_3h_blocks_count", 0)
        ratio = min(steps / 8000, 1.0) if steps else 0
        bar = _progress_bar(ratio, 20)
        col1 = [_rich_callout([
            _rich_text(f"{steps:,} 步\n", bold=True),
            _rich_text(f"目标 8,000 步 · {bar}", italic=True, color="gray"),
        ], icon_emoji="👟", color="green_background" if steps >= 8000 else "yellow_background")]
        col2 = [_rich_callout([
            _rich_text(f"{sed} 段\n", bold=True),
            _rich_text("久坐警告 (≥3h 连续)", italic=True, color="gray"),
        ], icon_emoji="🪑", color="red_background" if sed >= 3 else "orange_background")]
        inner.append(_column_list([col1, col2]))

    # 今日运动
    workouts = cv.get("inferred_workouts", [])
    total_ex = cv.get("total_exercise_minutes_zone2_plus", 0)
    zones = cv.get("baseline", {}).get("zonal_thresholds", {})
    if workouts:
        inner.append(_paragraph([_rich_text(
            f"🏋️ 今日运动 · 共 {len(workouts)} 次 · {total_ex} 分钟",
            bold=True, color="orange")]))
        z2_low = zones.get("Zone2", [0, 0])[0]
        z3_low = zones.get("Zone3", [0, 0])[0]
        for w in workouts:
            avg_hr = w.get("avg_hr", 0)
            dur = w.get("duration_minutes", 0)
            if avg_hr >= z3_low:
                intensity, ic = "🔥 高强度", "red_background"
            elif avg_hr >= z2_low:
                intensity, ic = "💪 中等", "orange_background"
            else:
                intensity, ic = "🚶 轻度", "green_background"
            end_short = w.get("end", "").split(" ")[-1] if w.get("end") else ""
            inner.append(_callout(
                f"{w.get('start','')} → {end_short}  ·  {dur} min  ·  "
                f"❤️ {avg_hr} bpm  ·  {intensity}",
                icon_emoji="🏃", color=ic))
    elif not activity:
        inner.append(_callout("今天暂无活动和运动数据。", icon_emoji="👟", color="gray_background"))

    blocks.extend(_heading_toggle(1, "🏃 今日活动 & 运动", inner, color="green_background"))
    return blocks


def build_daily_energy(metrics: dict, llm_input: dict) -> list:
    """日报能量：单日能量收支视图。"""
    blocks = []
    energy = metrics.get("energy_expenditure", {})
    if not energy:
        blocks.extend(_heading_toggle(1, "⚡ 今日能量", [
            _callout("暂无能量数据。", icon_emoji="🔋", color="gray_background"),
        ]))
        return blocks

    inner = []
    latest_date = sorted(energy.keys())[-1]
    e = energy[latest_date]
    tdee = e.get("tdee_kcal", 0)
    tdee_low = e.get("tdee_kcal_low", tdee)
    tdee_high = e.get("tdee_kcal_high", tdee)
    active = e.get("active_burn_kcal", 0)
    active_low = e.get("active_burn_kcal_low", active)
    active_high = e.get("active_burn_kcal_high", active)
    resting = e.get("resting_burn_kcal", 0)
    neat = e.get("neat_estimate_kcal", 0)
    confidence = e.get("active_burn_confidence_label", "unknown")
    confidence_cn = {"low": "低", "medium": "中", "high": "高", "unknown": "未知"}.get(confidence, "未知")
    intake = llm_input.get("diet", {}).get("avg_intake_kcal", 0)

    # 能量分解卡片
    cols = [
        [_metric_card("🔥", "TDEE", f"{int(tdee):,} kcal", "总消耗", "orange_background")],
        [_metric_card("🏃", "活动消耗", f"{int(active):,} kcal",
                      e.get("active_burn_source", ""), "yellow_background")],
        [_metric_card("💤", "基础代谢", f"{int(resting):,} kcal", "BMR", "blue_background")],
    ]
    if neat > 0:
        cols.append([_metric_card("🧹", "NEAT", f"{int(neat):,} kcal",
                                  "非运动消耗", "green_background")])
    inner.append(_column_list(cols))

    if e.get("active_burn_source") == "estimated_from_hr":
        inner.append(_callout(
            f"⌚ 心率回退估算区间: 活动 {int(active_low):,}~{int(active_high):,} kcal  ·  "
            f"TDEE {int(tdee_low):,}~{int(tdee_high):,} kcal  ·  置信度 {confidence_cn}",
            icon_emoji="📡", color="yellow_background"
        ))

    # 收支平衡
    if intake and tdee:
        diff = intake - tdee
        if diff > 200:
            verdict, vc = "📈 热量盈余", "red_background"
        elif diff < -500:
            verdict, vc = "📉 大幅亏损", "orange_background"
        elif diff < -200:
            verdict, vc = "📉 适度缺口", "green_background"
        else:
            verdict, vc = "⚖️ 基本平衡", "blue_background"
        inner.append(_callout(
            f"{verdict}    摄入 {int(intake):,} − 消耗 {int(tdee):,} = {int(diff):+,} kcal",
            icon_emoji="📊", color=vc))

    blocks.extend(_heading_toggle(1, "⚡ 今日能量收支", inner, color="orange_background"))
    return blocks


# ── 日报主构建 ──

def build_daily_page_blocks(report_json: dict, cache_json: dict = None) -> list:
    """日报专用模板：紧凑的单日快照视图。"""
    blocks = []
    metrics = cache_json.get("metrics", {}) if cache_json else {}
    llm_input = report_json.get("llm_objective_input", {})

    blocks.extend(build_daily_cover(report_json, metrics, llm_input))
    blocks.extend(build_daily_diet(llm_input))
    blocks.extend(build_daily_sleep(metrics))
    blocks.extend(build_daily_activity(metrics))
    blocks.extend(build_daily_energy(metrics, llm_input))
    blocks.extend(build_body_composition_section(metrics, llm_input))
    blocks.extend(build_ai_advice_section(report_json))

    blocks.append(_divider())
    blocks.append(_column_list([
        [_paragraph([_rich_text("🥗 健康饮食管理助手", bold=True, color="green"),
                     _rich_text(" · 日报", color="gray")])],
        [_paragraph([_rich_text(f"生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                                italic=True, color="gray")])],
    ]))
    return blocks


# ══════════════════════════════════════════════════════════════
#  月报专用模板 — 趋势分析 + 周度对比 + 目标追踪
# ══════════════════════════════════════════════════════════════

def build_monthly_cover(report: dict, metrics: dict, llm_input: dict) -> list:
    """月报封面：月度概览 + 9 指标仪表盘。"""
    blocks = []
    period = report.get("period", "")
    days = report.get("days_tracked", 0)
    is_merged = report.get("is_merged", False)
    tag = "综合分析" if is_merged else "饮食分析"

    blocks.append(_rich_callout([
        _rich_text("  📈 月度健康报告  ", bold=True),
        _rich_text(f"  {period}\n", color="default"),
        _rich_text(f"  {tag}", italic=True, color="purple"),
        _rich_text(f"  ·  追踪 {days} 天", italic=True, color="gray"),
    ], icon_emoji="🧬", color="purple_background"))
    blocks.append(_breadcrumb())

    cv = metrics.get("cardiovascular_health", {})
    rhr = cv.get("baseline", {}).get("estimated_rhr")
    sleep_data = metrics.get("sleep_recovery", {})
    activity = metrics.get("daily_activity", {})
    energy = metrics.get("energy_expenditure", {})
    body = metrics.get("body_composition", {})
    diet = llm_input.get("diet", {})

    avg_sleep = sum(d.get("total_sleep_hours", 0) for d in sleep_data.values()) / len(sleep_data) if sleep_data else 0
    avg_steps = sum(d.get("total_steps", 0) for d in activity.values()) / len(activity) if activity else 0
    avg_tdee = sum(d.get("tdee_kcal", 0) for d in energy.values()) / len(energy) if energy else 0
    intake_kcal = diet.get("avg_intake_kcal", 0)
    total_workouts = len(cv.get("inferred_workouts", []))
    total_ex_min = cv.get("total_exercise_minutes_zone2_plus", 0)

    # 体重变化
    weight_diff = None
    weight_start_str, weight_end_str = "—", "—"
    if body:
        dates_b = sorted(body.keys())
        w_first = body[dates_b[0]].get("weight_kg")
        w_last = body[dates_b[-1]].get("weight_kg")
        if w_first and w_last:
            weight_start_str = f"{w_first} kg"
            weight_end_str = f"{w_last} kg"
            weight_diff = w_last - w_first

    # Row 1
    blocks.append(_column_list([
        [_metric_card("❤️", "静息心率", f"{rhr} bpm" if rhr else "—", "月均基线", "red_background")],
        [_metric_card("😴", "月均睡眠", f"{avg_sleep:.1f} h", f"共 {len(sleep_data)} 晚", "purple_background")],
        [_metric_card("🏃", "日均步数", f"{int(avg_steps):,}", "目标 8,000", "green_background")],
    ]))
    # Row 2
    blocks.append(_column_list([
        [_metric_card("⚡", "日均消耗", f"{int(avg_tdee):,} kcal", "TDEE", "orange_background")],
        [_metric_card("🍽️", "日均摄入", f"{int(intake_kcal):,} kcal" if intake_kcal else "—",
                      f"记录 {diet.get('days_with_records',0)} 天", "yellow_background")],
        [_metric_card("🏋️", "运动总量", f"{total_ex_min} min", f"共 {total_workouts} 次", "blue_background")],
    ]))

    # 体重变化概览
    if weight_diff is not None:
        w_emoji = "📈" if weight_diff > 0 else "📉" if weight_diff < 0 else "➡️"
        w_color = "red_background" if weight_diff > 1 else "green_background" if weight_diff < -0.5 else "blue_background"
        blocks.append(_callout(
            f"⚖️ 体重趋势: {weight_start_str} → {weight_end_str}    "
            f"{w_emoji} {weight_diff:+.1f} kg",
            icon_emoji="📊", color=w_color))

    blocks.append(_table_of_contents())
    blocks.append(_divider())
    return blocks


def build_monthly_goals(metrics: dict, llm_input: dict) -> list:
    """月报特有：月度目标达成率 + 综合评价。"""
    blocks = []
    inner = []
    goals = []

    # 睡眠 ≥ 7h
    sleep_data = metrics.get("sleep_recovery", {})
    if sleep_data:
        total = len(sleep_data)
        good = sum(1 for d in sleep_data.values() if d.get("total_sleep_hours", 0) >= 7)
        ratio = good / total if total else 0
        goals.append(("😴", "睡眠 ≥ 7h", f"{good}/{total} 天", ratio, "purple_background"))

    # 步数 ≥ 8000
    activity = metrics.get("daily_activity", {})
    if activity:
        total = len(activity)
        good = sum(1 for d in activity.values() if d.get("total_steps", 0) >= 8000)
        ratio = good / total if total else 0
        goals.append(("🏃", "步数 ≥ 8,000", f"{good}/{total} 天", ratio, "green_background"))

    # 月运动 ≥ 600 min (推荐 150min/周 × 4)
    cv = metrics.get("cardiovascular_health", {})
    total_ex = cv.get("total_exercise_minutes_zone2_plus", 0)
    if total_ex > 0:
        ratio = min(total_ex / 600, 1.0)
        goals.append(("🏋️", "月运动 ≥ 600min", f"{total_ex}/600 min", ratio, "orange_background"))

    # 饮食记录完整性
    diet = llm_input.get("diet", {})
    days_with = diet.get("days_with_records", 0)
    days_tracked = max(len(sleep_data), len(activity), len(metrics.get("energy_expenditure", {})), 1)
    if days_with > 0:
        ratio = min(days_with / days_tracked, 1.0)
        goals.append(("🍽️", "饮食记录完整", f"{days_with}/{days_tracked} 天", ratio, "yellow_background"))

    if not goals:
        return blocks

    for emoji, label, detail, ratio, color in goals:
        bar = _progress_bar(ratio, 20)
        inner.append(_rich_callout([
            _rich_text(f"{label}\n", bold=True),
            _rich_text(f"{detail}  ·  {bar}", color="gray"),
        ], icon_emoji=emoji, color=color))

    # 总体评价
    avg_ratio = sum(g[3] for g in goals) / len(goals)
    if avg_ratio >= 0.8:
        overall, oc = "🏆 本月表现优秀！继续保持！", "green_background"
    elif avg_ratio >= 0.5:
        overall, oc = "💪 本月表现尚可，还有提升空间！", "blue_background"
    else:
        overall, oc = "⚠️ 本月多项目标未达成，建议调整计划。", "orange_background"
    inner.append(_callout(overall, icon_emoji="📋", color=oc))

    blocks.extend(_heading_toggle(1, "🎯 月度目标达成", inner, color="green_background"))
    return blocks


def build_monthly_weekly_breakdown(metrics: dict) -> list:
    """月报特有：按周拆分对比表 + 趋势箭头。"""
    blocks = []
    sleep_data = metrics.get("sleep_recovery", {})
    activity = metrics.get("daily_activity", {})
    energy = metrics.get("energy_expenditure", {})

    all_dates = sorted(set(list(sleep_data.keys()) + list(activity.keys()) + list(energy.keys())))
    if len(all_dates) < 8:
        return blocks

    # 按 7 天分组
    weeks = []
    for i in range(0, len(all_dates), 7):
        weeks.append(all_dates[i:i + 7])
    if len(weeks) < 2:
        return blocks

    inner = []

    # 对比表
    headers = ["📊 指标"]
    for i, w in enumerate(weeks):
        headers.append(f"W{i+1} ({w[0][-5:]}~{w[-1][-5:]})")

    rows = []
    # 平均睡眠
    sleep_row = ["😴 平均睡眠"]
    for w in weeks:
        vals = [sleep_data[d].get("total_sleep_hours", 0) for d in w if d in sleep_data]
        sleep_row.append(f"{sum(vals)/len(vals):.1f} h" if vals else "—")
    rows.append(sleep_row)

    # 平均步数
    steps_row = ["🏃 日均步数"]
    for w in weeks:
        vals = [activity[d].get("total_steps", 0) for d in w if d in activity]
        steps_row.append(f"{int(sum(vals)/len(vals)):,}" if vals else "—")
    rows.append(steps_row)

    # 平均 TDEE
    tdee_row = ["⚡ 日均 TDEE"]
    for w in weeks:
        vals = [energy[d].get("tdee_kcal", 0) for d in w if d in energy]
        tdee_row.append(f"{int(sum(vals)/len(vals)):,} kcal" if vals else "—")
    rows.append(tdee_row)

    # 深睡比例
    deep_row = ["🌊 深睡比例"]
    for w in weeks:
        vals = [sleep_data[d].get("deep_sleep_ratio", 0) for d in w if d in sleep_data]
        deep_row.append(f"{sum(vals)/len(vals)*100:.1f}%" if vals else "—")
    rows.append(deep_row)

    inner.append(_table(headers, rows))

    # 首周 vs 末周趋势
    first_w, last_w = weeks[0], weeks[-1]
    comparisons = []
    s1 = [sleep_data[d].get("total_sleep_hours", 0) for d in first_w if d in sleep_data]
    s2 = [sleep_data[d].get("total_sleep_hours", 0) for d in last_w if d in sleep_data]
    if s1 and s2:
        diff = sum(s2) / len(s2) - sum(s1) / len(s1)
        arrow = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
        comparisons.append(f"睡眠 {arrow} {diff:+.1f}h")
    a1 = [activity[d].get("total_steps", 0) for d in first_w if d in activity]
    a2 = [activity[d].get("total_steps", 0) for d in last_w if d in activity]
    if a1 and a2:
        diff = sum(a2) / len(a2) - sum(a1) / len(a1)
        arrow = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
        comparisons.append(f"步数 {arrow} {int(diff):+,}")
    e1 = [energy[d].get("tdee_kcal", 0) for d in first_w if d in energy]
    e2 = [energy[d].get("tdee_kcal", 0) for d in last_w if d in energy]
    if e1 and e2:
        diff = sum(e2) / len(e2) - sum(e1) / len(e1)
        arrow = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
        comparisons.append(f"TDEE {arrow} {int(diff):+,} kcal")

    if comparisons:
        inner.append(_callout(
            f"📐 W1 → W{len(weeks)} 变化:  " + "  ·  ".join(comparisons),
            icon_emoji="📊", color="blue_background"))

    blocks.extend(_heading_toggle(1, "📅 周度对比分析", inner, color="blue_background"))
    return blocks


def build_monthly_distribution(metrics: dict) -> list:
    """月报特有：睡眠/活动分布模式饼图。"""
    blocks = []
    inner = []
    sleep_data = metrics.get("sleep_recovery", {})
    activity = metrics.get("daily_activity", {})

    # 睡眠质量分布饼图
    if sleep_data and len(sleep_data) >= 7:
        excellent = sum(1 for d in sleep_data.values()
                        if d.get("total_sleep_hours", 0) >= 8 and d.get("sleep_efficiency", 0) >= 0.95)
        good = sum(1 for d in sleep_data.values()
                   if 7 <= d.get("total_sleep_hours", 0) < 8)
        fair = sum(1 for d in sleep_data.values()
                   if 6 <= d.get("total_sleep_hours", 0) < 7)
        poor = sum(1 for d in sleep_data.values()
                   if d.get("total_sleep_hours", 0) < 6)
        pie_lines = ["pie title 睡眠质量分布",
                     f'    "优秀(≥8h+高效)" : {excellent}',
                     f'    "良好(7-8h)" : {good}',
                     f'    "一般(6-7h)" : {fair}',
                     f'    "不足(<6h)" : {poor}']
        inner.append(_paragraph([_rich_text("🌙 睡眠质量分布", bold=True, color="purple")]))
        inner.append(_code_block("\n".join(pie_lines), "mermaid"))

    # 步数分布饼图
    if activity and len(activity) >= 7:
        high = sum(1 for d in activity.values() if d.get("total_steps", 0) >= 10000)
        mid = sum(1 for d in activity.values() if 8000 <= d.get("total_steps", 0) < 10000)
        low = sum(1 for d in activity.values() if 5000 <= d.get("total_steps", 0) < 8000)
        sed = sum(1 for d in activity.values() if d.get("total_steps", 0) < 5000)
        pie_lines = ["pie title 每日步数分布",
                     f'    "活跃(≥10k)" : {high}',
                     f'    "达标(8-10k)" : {mid}',
                     f'    "不足(5-8k)" : {low}',
                     f'    "久坐(<5k)" : {sed}']
        inner.append(_paragraph([_rich_text("👟 活动水平分布", bold=True, color="green")]))
        inner.append(_code_block("\n".join(pie_lines), "mermaid"))

    if inner:
        blocks.extend(_heading_toggle(1, "📊 月度分布分析", inner, color="orange_background"))
    return blocks


# ── 月报主构建 ──

def build_monthly_page_blocks(report_json: dict, cache_json: dict = None) -> list:
    """月报专用模板：趋势分析 + 目标追踪 + 周度对比。"""
    blocks = []
    metrics = cache_json.get("metrics", {}) if cache_json else {}
    llm_input = report_json.get("llm_objective_input", {})

    blocks.extend(build_monthly_cover(report_json, metrics, llm_input))
    blocks.extend(build_monthly_goals(metrics, llm_input))
    blocks.extend(build_monthly_weekly_breakdown(metrics))
    blocks.extend(build_diet_section(llm_input))
    blocks.extend(build_cardiovascular_section(metrics))
    blocks.extend(build_sleep_section(metrics, llm_input))
    blocks.extend(build_activity_section(metrics, llm_input))
    blocks.extend(build_energy_section(metrics, llm_input))
    blocks.extend(build_body_composition_section(metrics, llm_input))
    blocks.extend(build_monthly_distribution(metrics))
    blocks.extend(build_ai_advice_section(report_json))

    blocks.append(_divider())
    blocks.append(_column_list([
        [_paragraph([_rich_text("🥗 健康饮食管理助手", bold=True, color="green"),
                     _rich_text(" · 月报", color="gray")])],
        [_paragraph([_rich_text(f"生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                                italic=True, color="gray")])],
    ]))
    return blocks


# ══════════════════════════════════════════════════════════════
#  周报模板 (原有)
# ══════════════════════════════════════════════════════════════

def build_weekly_page_blocks(report_json: dict, cache_json: dict = None) -> list:
    """周报模板：完整多维度分析。"""
    blocks = []
    metrics = {}
    if cache_json and cache_json.get("metrics"):
        metrics = cache_json["metrics"]
    llm_input = report_json.get("llm_objective_input", {})

    blocks.extend(build_cover_section(report_json, metrics, llm_input))
    blocks.extend(build_diet_section(llm_input))
    blocks.extend(build_cardiovascular_section(metrics))
    blocks.extend(build_sleep_section(metrics, llm_input))
    blocks.extend(build_activity_section(metrics, llm_input))
    blocks.extend(build_energy_section(metrics, llm_input))
    blocks.extend(build_body_composition_section(metrics, llm_input))
    blocks.extend(build_ai_advice_section(report_json))

    blocks.append(_divider())
    blocks.append(_column_list([
        [_paragraph([_rich_text("🥗 健康饮食管理助手", bold=True, color="green"),
                     _rich_text(" · 周报", color="gray")])],
        [_paragraph([_rich_text(f"生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                                italic=True, color="gray")])],
    ]))
    return blocks


# ────────────── 主构建函数 (按报告类型分发) ──────────────

def build_notion_page_blocks(report_json: dict, cache_json: dict = None) -> list:
    """根据 report_type 自动选择对应模板。"""
    rtype = report_json.get("report_type", "daily")
    if rtype == "daily":
        return build_daily_page_blocks(report_json, cache_json)
    elif rtype == "monthly":
        return build_monthly_page_blocks(report_json, cache_json)
    else:
        return build_weekly_page_blocks(report_json, cache_json)


def build_page_title(report_json: dict) -> str:
    rtype = report_json.get("report_type", "daily")
    period = report_json.get("period", "")
    label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(rtype, rtype)
    return f"🧬 健康{label} — {period}"


def build_page_properties_for_database(report_json: dict, cache_json: dict = None) -> dict:
    """
    为 Notion 数据库创建页面时，构建 properties。
    数据库需包含以下列: Name(title), 报告类型(select), 日期(date), 是否合并(checkbox)
    """
    rtype = report_json.get("report_type", "daily")
    llm = report_json.get("llm_objective_input", {})
    rp = llm.get("report_period", {})
    start_date = rp.get("start", "")
    end_date = rp.get("end", "")

    props = {
        "Name": {"title": [_rich_text(build_page_title(report_json))]},
    }

    # 可选属性 (只有当数据库已配置这些列时才会生效)
    type_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(rtype, rtype)
    props["报告类型"] = {"select": {"name": type_label}}

    if start_date:
        date_obj = {"start": start_date}
        if end_date and end_date != start_date:
            date_obj["end"] = end_date
        props["日期"] = {"date": date_obj}

    props["合并分析"] = {"checkbox": report_json.get("is_merged", False)}

    return props


# ═══════════════════════ Notion API 调用 ═══════════════════════

def create_page_in_database(token: str, database_id: str, properties: dict, children: list) -> dict:
    """在 Notion 数据库中创建新页面。"""
    # Notion API 限制每次最多 100 个 blocks，需要分批
    first_batch = children[:100]
    remaining = children[100:]

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": first_batch,
    }

    resp = requests.post(f"{NOTION_BASE_URL}/pages", headers=_headers(token), json=payload, timeout=30)
    resp.raise_for_status()
    page = resp.json()
    page_id = page["id"]

    # 追加剩余 blocks
    _append_remaining_blocks(token, page_id, remaining)

    return page


def create_page_under_parent(token: str, parent_page_id: str, title: str, children: list) -> dict:
    """在 Notion 页面下创建子页面。"""
    first_batch = children[:100]
    remaining = children[100:]

    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {"title": [_rich_text(title)]},
        "children": first_batch,
    }

    resp = requests.post(f"{NOTION_BASE_URL}/pages", headers=_headers(token), json=payload, timeout=30)
    resp.raise_for_status()
    page = resp.json()
    page_id = page["id"]

    _append_remaining_blocks(token, page_id, remaining)

    return page


def _append_remaining_blocks(token: str, page_id: str, blocks: list):
    """分批追加超出 100 个限制的 blocks。"""
    while blocks:
        batch = blocks[:100]
        blocks = blocks[100:]
        payload = {"children": batch}
        resp = requests.patch(
            f"{NOTION_BASE_URL}/blocks/{page_id}/children",
            headers=_headers(token), json=payload, timeout=30,
        )
        resp.raise_for_status()


# ═══════════════════════ 报告发现与推送 ═══════════════════════

def find_latest_report(data_dir: str, report_type: str = None) -> Path:
    """在 data/reports/ 下查找最新的 JSON 报告文件。"""
    reports_dir = Path(data_dir) / "reports"
    if not reports_dir.exists():
        return None

    pattern = "health_report_*.json"
    candidates = sorted(reports_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

    if report_type:
        candidates = [c for c in candidates if f"_{report_type}_" in c.name]

    return candidates[0] if candidates else None


def find_matching_cache(data_dir: str, report_json: dict) -> dict:
    """尝试查找与报告匹配的 cache 文件。"""
    llm = report_json.get("llm_objective_input", {})
    rp = llm.get("report_period", {})
    rtype_map = {"daily": "day", "weekly": "week", "monthly": "month"}
    period = rtype_map.get(rp.get("type", ""), "day")
    target = rp.get("end", rp.get("start", ""))
    ext_cfg = load_external_config(data_dir)
    strict_real_data = bool(ext_cfg.get("strict_real_data", False))

    if not target:
        return None

    cache_path = resolve_active_cache_path(data_dir, period, target)
    if not cache_path.exists() and target:
        print(f"[Notion] 缓存 {cache_path.name} 不存在，尝试自动同步...", file=sys.stderr)
        try:
            scripts_dir = str(Path(__file__).resolve().parent)
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from health_data_sync import fetch_data
            fetch_data(period, target, data_dir, strict_real_data=strict_real_data)
            cache_path = resolve_active_cache_path(data_dir, period, target)
        except Exception as e:
            print(f"[Notion] 自动同步失败: {e}", file=sys.stderr)
    if cache_path.exists():
        cache_json = json.loads(cache_path.read_text(encoding="utf-8"))
        if strict_real_data:
            if cache_json.get("status") != "success" or not cache_json.get("metrics"):
                print("[Notion] strict_real_data 已启用，缓存状态非 success 或无有效指标，已拒绝使用", file=sys.stderr)
                return None
            estimated_days = find_estimated_energy_days(cache_json.get("metrics", {}))
            if estimated_days:
                print(
                    f"[Notion] strict_real_data 已启用，缓存包含估算能耗数据: {', '.join(sorted(estimated_days))}，已拒绝使用",
                    file=sys.stderr,
                )
                return None
        return cache_json
    return None


def validate_report_cache_consistency(report_json: dict, cache_json: dict):
    if not report_json:
        return False, "报告为空，无法校验一致性。"
    if not cache_json:
        return False, "未找到可用缓存，拒绝推送。"

    report_fp = report_json.get("source_cache_fingerprint")
    cache_fp = cache_json.get("cache_meta", {}).get("cache_fingerprint")
    if not cache_fp:
        cache_fp = _metrics_fingerprint(cache_json.get("metrics", {}))

    # 新版报告必须带上 source_cache_fingerprint，避免推送历史混合口径报告。
    if not report_fp:
        return False, "报告缺少 source_cache_fingerprint，疑似旧版本报告，拒绝推送。"
    if report_fp != cache_fp:
        return False, (
            "报告与当前缓存指纹不一致，拒绝推送。"
            f" report={report_fp[:12]} cache={cache_fp[:12]}"
        )

    llm = report_json.get("llm_objective_input", {})
    rp = llm.get("report_period", {})
    start = rp.get("start")
    end = rp.get("end")
    activity = cache_json.get("metrics", {}).get("daily_activity", {})
    if start and end and activity:
        dates = sorted([d for d in activity.keys() if start <= d <= end])
        steps = [int(activity[d].get("total_steps", 0)) for d in dates]
        cache_avg = round(sum(steps) / len(steps), 1) if steps else 0
        cache_max = max(steps) if steps else 0

        report_activity = llm.get("activity", {})
        report_avg = report_activity.get("avg_steps", 0)
        report_max = report_activity.get("max_steps", 0)
        # 新版报告应携带 source_cache_step_*，旧字段作为后备校验。
        source_avg = report_json.get("source_cache_step_avg", report_avg)
        source_max = report_json.get("source_cache_step_max", report_max)

        if int(source_avg) != int(cache_avg) or int(source_max) != int(cache_max):
            return False, (
                "报告步数与缓存步数不一致，拒绝推送。"
                f" report_avg={source_avg} cache_avg={cache_avg}"
                f" report_max={source_max} cache_max={cache_max}"
            )

    return True, "ok"


def push_report(args):
    """推送指定的报告到 Notion。"""
    data_dir = args.data_dir
    config = load_config(data_dir)
    token = config.get("notion_token")
    if not token:
        print("❌ 未配置 Notion Token。请先运行: python notion_health_sync.py init-config --token <TOKEN>")
        sys.exit(1)

    # 加载报告
    report_path = Path(args.report_file)
    if not report_path.exists():
        print(f"❌ 报告文件不存在: {report_path}")
        sys.exit(1)

    report_json = json.loads(report_path.read_text(encoding="utf-8"))
    cache_json = find_matching_cache(data_dir, report_json)
    ok, reason = validate_report_cache_consistency(report_json, cache_json)
    if not ok:
        print(f"❌ 推送前一致性校验失败: {reason}")
        sys.exit(1)

    # 构建 blocks
    blocks = build_notion_page_blocks(report_json, cache_json)
    title = build_page_title(report_json)

    # 创建页面
    db_id = config.get("database_id")
    parent_id = config.get("parent_page_id")

    if db_id:
        props = build_page_properties_for_database(report_json, cache_json)
        page = create_page_in_database(token, db_id, props, blocks)
        print(f"✅ 报告已推送到 Notion 数据库！页面 URL: {page.get('url', 'N/A')}")
    elif parent_id:
        page = create_page_under_parent(token, parent_id, title, blocks)
        print(f"✅ 报告已推送到 Notion 页面！URL: {page.get('url', 'N/A')}")
    else:
        print("❌ 未配置 database_id 或 parent_page_id。请先运行 init-config。")
        sys.exit(1)


def push_latest(args):
    """查找并推送最新报告。"""
    data_dir = args.data_dir
    report_type = getattr(args, "type", None)
    latest = find_latest_report(data_dir, report_type)
    if not latest:
        print("❌ 未找到报告文件。请先生成报告。")
        sys.exit(1)

    print(f"📄 找到最新报告: {latest.name}")
    args.report_file = str(latest)
    push_report(args)


def preview_template(args):
    """预览模板：将指定报告转为 Notion blocks JSON 并输出到文件。"""
    data_dir = args.data_dir
    report_path = Path(args.report_file) if hasattr(args, "report_file") and args.report_file else find_latest_report(data_dir)

    if not report_path or not Path(report_path).exists():
        print("❌ 未找到报告文件。")
        sys.exit(1)

    report_json = json.loads(Path(report_path).read_text(encoding="utf-8"))
    cache_json = find_matching_cache(data_dir, report_json)
    ok, reason = validate_report_cache_consistency(report_json, cache_json)
    if not ok:
        print(f"❌ 模板预览一致性校验失败: {reason}")
        sys.exit(1)
    blocks = build_notion_page_blocks(report_json, cache_json)

    output_path = Path(data_dir) / "notion_template_preview.json"
    output_path.write_text(json.dumps(blocks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 模板预览已导出至: {output_path}")
    print(f"   共 {len(blocks)} 个顶级 blocks")


# ═══════════════════════ CLI ═══════════════════════

def main():
    parser = argparse.ArgumentParser(description="健康报告 Notion 同步工具")
    sub = parser.add_subparsers(dest="command")

    # init-config
    p_init = sub.add_parser("init-config", help="配置 Notion API")
    p_init.add_argument("--token", help="Notion Integration Token")
    p_init.add_argument("--database-id", help="目标 Notion 数据库 ID")
    p_init.add_argument("--parent-page-id", help="目标 Notion 父页面 ID (与 database-id 二选一)")
    p_init.add_argument("--data-dir", required=True, help="数据目录路径")
    p_init.set_defaults(func=init_config)

    # push-report
    p_push = sub.add_parser("push-report", help="推送指定报告到 Notion")
    p_push.add_argument("--report-file", required=True, help="报告 JSON 文件路径")
    p_push.add_argument("--data-dir", required=True, help="数据目录路径")
    p_push.set_defaults(func=push_report)

    # push-latest
    p_latest = sub.add_parser("push-latest", help="推送最新报告到 Notion")
    p_latest.add_argument("--type", choices=["daily", "weekly", "monthly"], help="报告类型过滤")
    p_latest.add_argument("--data-dir", required=True, help="数据目录路径")
    p_latest.set_defaults(func=push_latest)

    # preview
    p_preview = sub.add_parser("preview", help="预览模板 (导出 blocks JSON)")
    p_preview.add_argument("--report-file", help="指定报告 JSON (可选，默认最新)")
    p_preview.add_argument("--data-dir", required=True, help="数据目录路径")
    p_preview.set_defaults(func=preview_template)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
