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


# ═══════════════════════ Markdown 解析器 ═══════════════════════

def markdown_to_notion_blocks(markdown_text: str) -> list:
    """将 Markdown 纯文本解析为基础 Notion blocks。"""
    blocks = []
    lines = markdown_text.split("\n")
    in_code_block = False
    code_content = []
    code_lang = "plain text"

    for line in lines:
        stripped = line.strip()
        
        if stripped.startswith("```"):
            if in_code_block:
                blocks.append(_code_block("\n".join(code_content), code_lang))
                code_content = []
                in_code_block = False
            else:
                in_code_block = True
                lang = stripped[3:].strip()
                code_lang = lang if lang else "plain text"
            continue
            
        if in_code_block:
            code_content.append(line)
            continue
            
        if not stripped:
            blocks.append(_paragraph(""))
            continue
            
        if stripped.startswith("# "):
            blocks.append(_heading(1, stripped[2:].strip()))
        elif stripped.startswith("## "):
            blocks.append(_heading(2, stripped[3:].strip()))
        elif stripped.startswith("### "):
            blocks.append(_heading(3, stripped[4:].strip()))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append(_bulleted(stripped[2:].strip()))
        elif stripped[0].isdigit() and stripped.startswith(stripped.split()[0]) and stripped.split()[0].endswith("."):
            blocks.append(_numbered(stripped[stripped.find(".")+1:].strip()))
        elif stripped.startswith("> "):
            blocks.append(_quote(stripped[2:].strip()))
        else:
            blocks.append(_paragraph(stripped))
            
    if in_code_block:
        blocks.append(_code_block("\n".join(code_content), code_lang))
        
    return blocks

def build_notion_page_blocks(report_json: dict, cache_json: dict = None) -> list:
    """提取报告中的 report_markdown 并将其简易转成 Notion blocks。"""
    md = report_json.get("report_markdown", "")
    return markdown_to_notion_blocks(md)

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


    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
