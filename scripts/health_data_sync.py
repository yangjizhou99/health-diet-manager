#!/usr/bin/env python3
"""
health_data_sync.py - 外部健康数据同步工具
功能: set-location, fetch
"""
import argparse
import hashlib
import json
import os
import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
import tempfile
import shutil
import subprocess


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

def _is_google_drive_token(location):
    if not location:
        return False
    # Google Drive folder token
    is_valid_len = 15 <= len(location) <= 45
    has_no_slashes = "/" not in location and "\\" not in location
    return (is_valid_len or location.startswith("0AIK")) and has_no_slashes and not Path(location).exists()

def _download_from_drive(folder_token, dest_dir, token_path=None):
    """按优先级尝试多种方式从 Google Drive 下载文件夹:
    1. gog drive (自定义工具)
    2. rclone (需预先配置 remote 'gdrive')
    3. Google Drive API (需 service account credentials)
    4. gdown (最不稳定但零配置)
    """

    # ── 方式 1: gog CLI (Google Workspace CLI, https://gogcli.sh) ──
    # gog 需要先 `gog auth credentials <client_secret.json>` + `gog auth add <email> --services drive`
    # 使用 gog drive search 列出文件，然后逐个下载
    try:
        gog_check = subprocess.run(
            ["gog", "drive", "search", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if gog_check.returncode == 0:
            print("检测到 gog CLI，尝试使用 gog drive 下载...", file=sys.stderr)
            import json as _json
            # 列出目标文件夹下所有文件 (递归)
            res = subprocess.run(
                ["gog", "drive", "search", f'"{folder_token}" in parents', "--max", "500", "--json", "--no-input"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if res.returncode == 0 and res.stdout.strip():
                items = _json.loads(res.stdout)
                for item in items:
                    item_id = item.get("id", "")
                    item_name = item.get("name", "unknown")
                    mime = item.get("mimeType", "")
                    if mime == "application/vnd.google-apps.folder":
                        # 递归子文件夹 —— 交给后续方式处理
                        continue
                    # 用 gog docs export 导出 Google Docs 类 / 普通文件跳到方式3
                    # gog CLI 适用于简单场景；复杂递归下载交给方式3 API
                # gog 不支持递归文件夹下载，跳到方式3
                print("gog drive search 成功但不支持递归文件夹下载，交给 API 方式", file=sys.stderr)
        else:
            print("gog CLI 未配置或不可用，跳过", file=sys.stderr)
    except FileNotFoundError:
        print("gog CLI 未安装，跳过", file=sys.stderr)
    except Exception as e:
        print(f"gog drive 报错: {e}", file=sys.stderr)

    # ── 方式 2: rclone (推荐，稳定) ──
    # 需要预先运行 `rclone config` 配置一个名为 gdrive 的 remote
    try:
        rclone_check = subprocess.run(
            ["rclone", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if rclone_check.returncode == 0:
            print("检测到 rclone，尝试使用 rclone 下载...", file=sys.stderr)
            rclone_src = f"gdrive:{{id={folder_token}}}"
            res = subprocess.run(
                ["rclone", "copy", rclone_src, str(dest_dir), "--drive-shared-with-me", "-P"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if res.returncode == 0 and os.listdir(dest_dir):
                print("rclone 下载成功！", file=sys.stderr)
                return str(dest_dir)
            else:
                print(f"rclone 失败: {res.stderr.strip()}", file=sys.stderr)
    except FileNotFoundError:
        print("rclone 未安装，跳过", file=sys.stderr)
    except Exception as e:
        print(f"rclone 报错: {e}", file=sys.stderr)

    oauth_error = None

    # ── 方式 3: Google Drive API + OAuth 用户凭证 (最可靠) ──
    try:
        if token_path is None:
            scripts_dir = os.path.dirname(os.path.abspath(__file__))
            data_dir_path = os.path.join(scripts_dir, "..", "data")
            token_path = os.path.join(data_dir_path, "gdrive_token.json")
        token_path = Path(token_path)

        if token_path.exists():
            print("检测到 OAuth Token，尝试 Google Drive API...", file=sys.stderr)
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload

            creds = Credentials.from_authorized_user_file(
                str(token_path), ["https://www.googleapis.com/auth/drive.readonly"])
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")

            service = build("drive", "v3", credentials=creds)

            import re as _re
            def _sanitize_filename(name):
                """替换 Windows 不允许的文件名字符 <>:\"/\\|?*"""
                return _re.sub(r'[<>:"/\\|?*]', '_', name)

            # Google Docs 原生文件导出映射 (只导出 Sheets → CSV)
            _EXPORT_MAP = {
                "application/vnd.google-apps.spreadsheet": (
                    "text/csv", ".csv"),
            }
            # 其他 Google Docs 类型直接跳过
            _GDOCS_PREFIX = "application/vnd.google-apps."

            def _download_folder_recursive(svc, folder_id, local_dir):
                os.makedirs(local_dir, exist_ok=True)
                page_token = None
                while True:
                    resp = svc.files().list(
                        q=f"'{folder_id}' in parents and trashed=false",
                        fields="nextPageToken, files(id, name, mimeType)",
                        pageToken=page_token,
                        pageSize=100,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True).execute()
                    for item in resp.get("files", []):
                        mime = item["mimeType"]
                        safe_name = _sanitize_filename(item["name"])
                        if mime == "application/vnd.google-apps.folder":
                            _download_folder_recursive(svc, item["id"],
                                                       os.path.join(local_dir, safe_name))
                        elif mime in _EXPORT_MAP:
                            export_mime, ext = _EXPORT_MAP[mime]
                            request = svc.files().export_media(
                                fileId=item["id"], mimeType=export_mime)
                            if not safe_name.endswith(ext):
                                safe_name += ext
                            file_path = os.path.join(local_dir, safe_name)
                            with open(file_path, "wb") as fh:
                                downloader = MediaIoBaseDownload(fh, request)
                                done = False
                                while not done:
                                    _, done = downloader.next_chunk()
                        elif mime.startswith(_GDOCS_PREFIX):
                            # 跳过其他 Google Docs 原生文件 (文档/演示等)
                            continue
                        else:
                            request = svc.files().get_media(fileId=item["id"])
                            file_path = os.path.join(local_dir, safe_name)
                            with open(file_path, "wb") as fh:
                                downloader = MediaIoBaseDownload(fh, request)
                                done = False
                                while not done:
                                    _, done = downloader.next_chunk()
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        break

            _download_folder_recursive(service, folder_token, str(dest_dir))
            if os.listdir(dest_dir):
                print("Google Drive API (OAuth) 下载成功！", file=sys.stderr)
                return str(dest_dir)
            oauth_error = (
                "Google Drive API 未下载到任何文件。"
                "请确认该账号对目标文件夹有访问权限，且文件夹内包含可下载文件。"
            )
        else:
            print(f"未找到 OAuth Token ({str(token_path)})，跳过。首次使用请运行: "
                  f"python scripts/gdrive_auth.py auth --client-secret <path>", file=sys.stderr)
    except ImportError:
        print("未安装 google-api-python-client，跳过 Drive API 方式", file=sys.stderr)
    except Exception as e:
        oauth_error = f"Google Drive API 报错: {e}"
        print(oauth_error, file=sys.stderr)

    # ── 方式 4: gdown (兜底，不稳定) ──
    # 默认关闭，避免反复触发“需要公开链接”的误导性报错。
    if os.getenv("HEALTH_SYNC_ENABLE_GDOWN", "0") != "1":
        msg = "所有稳定下载方式均失败，且已禁用 gdown 公链兜底。"
        if oauth_error:
            msg = f"{msg} {oauth_error}"
        raise RuntimeError(msg)

    try:
        import gdown
    except ImportError:
        print("未检测到 gdown，正在自动安装...", file=sys.stderr)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "gdown", "--quiet"])
        import gdown

    url = f"https://drive.google.com/drive/folders/{folder_token}"
    print(f"正在使用 gdown 从 Google Drive 下载: {url}", file=sys.stderr)
    gdown.download_folder(url, output=str(dest_dir), quiet=True, use_cookies=False)
    return str(dest_dir)


def _load_local_cache_json(path_obj):
    try:
        content = load_json(path_obj, default=None)
        if not isinstance(content, dict):
            return None
        if "metrics" in content and isinstance(content.get("metrics"), dict):
            return content

        metric_keys = {
            "cardiovascular_health",
            "sleep_recovery",
            "body_composition",
            "daily_activity",
            "energy_expenditure",
        }
        if metric_keys.intersection(set(content.keys())):
            return {
                "status": "success",
                "metrics": content,
                "message": f"使用本地缓存文件: {path_obj.name}",
            }
    except Exception:
        return None
    return None


def _pick_local_cache_file(cfg, data_dir, target_date):
    candidates = []
    cache_file = cfg.get("local_fallback_cache_file")
    if cache_file:
        candidates.append(Path(cache_file))

    fallback_path = cfg.get("local_fallback_path")
    if fallback_path and Path(fallback_path).is_file():
        candidates.append(Path(fallback_path))

    dd = Path(data_dir)
    candidates.append(dd / f"health_data_{target_date}.json")
    candidates.append(dd / "health_data_latest.json")

    for p in candidates:
        if p.exists() and p.is_file():
            payload = _load_local_cache_json(p)
            if payload:
                return payload, p
    return None, None


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


def _find_estimated_energy_days(metrics):
    energy = metrics.get("energy_expenditure", {}) if isinstance(metrics, dict) else {}
    estimated_days = []
    for day, item in energy.items():
        if not isinstance(item, dict):
            continue
        source = str(item.get("active_burn_source", "")).lower()
        method = str(item.get("active_burn_method", "")).lower()
        if "estimated" in source or "fallback" in method:
            estimated_days.append(day)
    return estimated_days


def _metrics_fingerprint(metrics):
    payload = json.dumps(metrics or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_cache_files(dd, period, target_date, output_data):
    base_name = f"health_cache_{period}_{target_date}"
    canonical_path = dd / f"{base_name}.json"
    pointer_path = dd / f"{base_name}.latest.json"

    generated_at = datetime.now().strftime("%Y%m%d_%H%M%S")
    versioned_name = f"{base_name}_{generated_at}.json"
    versioned_path = dd / versioned_name

    metrics = output_data.get("metrics", {}) if isinstance(output_data, dict) else {}
    fingerprint = _metrics_fingerprint(metrics)

    if isinstance(output_data, dict):
        meta = {
            "cache_fingerprint": fingerprint,
            "cache_generated_at": datetime.now().isoformat(),
            "cache_file": versioned_name,
        }
        output_data["cache_meta"] = meta

    # 1) 版本化缓存：永不覆盖
    save_json(versioned_path, output_data)
    # 2) 兼容旧逻辑：更新固定文件名
    save_json(canonical_path, output_data)
    # 3) latest 指针：指向当前有效版本
    save_json(pointer_path, {
        "status": output_data.get("status") if isinstance(output_data, dict) else "unknown",
        "period": period,
        "target_date": target_date,
        "active_cache_file": versioned_name,
        "active_cache_path": str(versioned_path),
        "cache_fingerprint": fingerprint,
        "updated_at": datetime.now().isoformat(),
    })

    return canonical_path, versioned_path, pointer_path


def fetch_data(period, target_date, data_dir, strict_real_data=False):
    dd = Path(data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    cfg_path = dd / "external_data_config.json"
    cfg = load_json(cfg_path)
    strict_real_data = bool(strict_real_data or cfg.get("strict_real_data", False))

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
        result = _error_result(period, target_date, f"不支持远程 URL: {loc}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    temp_dir = None
    extracted_dir = None
    output_data = None
    exit_code = 0

    if _is_google_drive_token(loc):
        temp_dir = tempfile.mkdtemp(prefix="health_sync_gdrive_")
        try:
            downloaded_dir = _download_from_drive(loc, temp_dir, token_path=dd / "gdrive_token.json")
            extracted_dir = _find_health_data_root(downloaded_dir)
            if extracted_dir is None:
                result = _error_result(period, target_date, f"在 Google Drive 下载内容中未找到包含“健康同步 *”的目录")
        except Exception as e:
            print(f"从 Google Drive 下载失败: {e}，尝试本地回退路径...", file=sys.stderr)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir = None
            # 回退 1: 使用明确配置或默认命名的本地缓存 JSON（真实数据，不再伪造）
            cache_payload, cache_file = _pick_local_cache_file(cfg, data_dir, target_date)
            if cache_payload is not None:
                output_data = {
                    "status": "success",
                    "period": period,
                    "target_date": target_date,
                    "metrics": cache_payload.get("metrics", {}),
                    "message": f"Google Drive 下载失败，改用本地缓存文件: {cache_file}",
                }
                extracted_dir = None
                exit_code = 0

            # 回退 2: 检查 config 中的 local_fallback_path 目录，重新解析原始导出文件
            fallback_path = cfg.get("local_fallback_path")
            if output_data is None and fallback_path and Path(fallback_path).is_dir():
                extracted_dir = _find_health_data_root(fallback_path)
                if extracted_dir:
                    print(f"已回退到本地路径: {extracted_dir}", file=sys.stderr)
            if output_data is None and extracted_dir is None:
                result = _error_result(period, target_date,
                    f"从 Google Drive 下载失败: {e}。可在 external_data_config.json 中设置 "
                    f"\"local_fallback_cache_file\" 指向本地健康缓存 JSON，或设置 "
                    f"\"local_fallback_path\" 指向健康数据目录作为离线回退。")
                print(json.dumps(result, ensure_ascii=False))
                return 1
    else:
        if not Path(loc).is_dir():
            result = _error_result(period, target_date, f"数据目录不存在或不可访问: {loc}")
            print(json.dumps(result, ensure_ascii=False))
            return 1
        extracted_dir = _find_health_data_root(loc)
        if extracted_dir is None:
            result = _error_result(period, target_date, f"在 {loc} 及其子目录中未找到包含“健康同步 *”数据文件夹的目录")

    if output_data is None and extracted_dir is None:
        print(json.dumps(result, ensure_ascii=False))
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        return 1

    if output_data is None:
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
                allow_estimated_energy=not strict_real_data,
            )

            output_data = {
                "status": "success",
                "period": period,
                "target_date": target_date,
                "metrics": comprehensive_report.get("metrics", {}),
                "message": f"成功抓取并计算了 {start_date} ~ {end_date} 的健康数据",
            }
            exit_code = 0
        except Exception as e:
            output_data = _error_result(period, target_date, f"健康数据引擎运行失败: {e}")
            exit_code = 1
        finally:
            # 清理临时目录
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
    elif temp_dir and os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)

    cache_path = dd / f"health_cache_{period}_{target_date}.json"
    if output_data.get("status") == "success" and strict_real_data:
        estimated_days = _find_estimated_energy_days(output_data.get("metrics", {}))
        if estimated_days:
            output_data = _error_result(
                period,
                target_date,
                "strict_real_data 已启用：检测到估算能耗数据，拒绝输出。"
                f"涉及日期: {', '.join(sorted(estimated_days))}"
            )
            exit_code = 1

    canonical_path, versioned_path, pointer_path = _write_cache_files(dd, period, target_date, output_data)
    output_data.setdefault("cache_meta", {})
    output_data["cache_meta"].update({
        "canonical_cache_path": str(canonical_path),
        "latest_pointer_path": str(pointer_path),
    })
    # 将路径信息同步回 canonical/versioned，方便后续追溯
    save_json(canonical_path, output_data)
    save_json(versioned_path, output_data)

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
    p2.add_argument("--strict-real-data", action="store_true",
                    help="严格真实模式：检测到估算能耗数据则报错")

    args = pa.parse_args()
    if args.command == "set-location":
        return set_location(args.location, args.data_dir)
    if args.command == "fetch":
        return fetch_data(args.period, args.target_date, args.data_dir, args.strict_real_data)
    pa.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
