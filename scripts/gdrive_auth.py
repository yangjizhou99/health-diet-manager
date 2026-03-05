#!/usr/bin/env python3
"""
gdrive_auth.py — Google Drive OAuth 授权 & 测试工具
用法:
  python gdrive_auth.py auth   --client-secret <path>   # 首次授权（会打开浏览器）
  python gdrive_auth.py test   --folder-id <id>         # 测试列出文件夹内容
  python gdrive_auth.py download --folder-id <id> --output <dir>  # 下载文件夹
"""
import argparse
import json
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
TOKEN_PATH = Path(__file__).resolve().parent.parent / "data" / "gdrive_token.json"


def get_credentials(client_secret_path=None):
    """获取有效的 OAuth 凭证，必要时触发浏览器授权。"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
            return creds
        except Exception as e:
            print(f"Token 刷新失败: {e}，需要重新授权", file=sys.stderr)

    # 需要全新授权
    if not client_secret_path:
        # 尝试自动查找
        for candidate in [
            Path(__file__).resolve().parent.parent / "data" / "client_secret.json",
            Path(__file__).resolve().parent / "client_secret.json",
        ]:
            if candidate.exists():
                client_secret_path = str(candidate)
                break
    if not client_secret_path:
        print("❌ 需要 --client-secret 参数指定 OAuth 客户端凭证文件路径", file=sys.stderr)
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    print(f"✅ 授权成功！Token 已保存到 {TOKEN_PATH}")
    return creds


def build_service(client_secret_path=None):
    from googleapiclient.discovery import build
    creds = get_credentials(client_secret_path)
    return build("drive", "v3", credentials=creds)


def list_folder(service, folder_id):
    """列出文件夹内容。"""
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name, mimeType, size)",
        pageSize=100,
    ).execute()
    return results.get("files", [])


def download_folder_recursive(service, folder_id, local_dir):
    """递归下载整个文件夹。"""
    from googleapiclient.http import MediaIoBaseDownload
    import io

    os.makedirs(local_dir, exist_ok=True)
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token, pageSize=100,
        ).execute()

        for item in resp.get("files", []):
            item_path = os.path.join(local_dir, item["name"])
            if item["mimeType"] == "application/vnd.google-apps.folder":
                print(f"  📁 {item['name']}/", file=sys.stderr)
                download_folder_recursive(service, item["id"], item_path)
            else:
                print(f"  📄 {item['name']}", file=sys.stderr)
                request = service.files().get_media(fileId=item["id"])
                with open(item_path, "wb") as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()

        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def cmd_auth(args):
    get_credentials(args.client_secret)
    print("✅ 授权完成，可以访问 Google Drive 了！")


def cmd_test(args):
    service = build_service(args.client_secret)
    folder_id = args.folder_id
    print(f"📂 列出文件夹 {folder_id} 的内容:\n")
    files = list_folder(service, folder_id)
    if not files:
        print("  (空文件夹或无权限访问)")
    for f in files:
        kind = "📁" if f["mimeType"] == "application/vnd.google-apps.folder" else "📄"
        size = f.get("size", "")
        size_str = f"  ({int(size)//1024} KB)" if size else ""
        print(f"  {kind} {f['name']}{size_str}")
    print(f"\n共 {len(files)} 个项目")


def cmd_download(args):
    service = build_service(args.client_secret)
    folder_id = args.folder_id
    output = args.output or "."
    print(f"⬇️  下载文件夹 {folder_id} 到 {output}...\n", file=sys.stderr)
    download_folder_recursive(service, folder_id, output)
    print(f"\n✅ 下载完成！文件保存在: {output}")


def main():
    pa = argparse.ArgumentParser(description="Google Drive OAuth 授权 & 下载工具")
    pa.add_argument("--client-secret", default=None, help="OAuth 客户端凭证 JSON 文件路径")
    sp = pa.add_subparsers(dest="command")

    sp.add_parser("auth")

    p_test = sp.add_parser("test")
    p_test.add_argument("--folder-id", required=True)

    p_dl = sp.add_parser("download")
    p_dl.add_argument("--folder-id", required=True)
    p_dl.add_argument("--output", default=None)

    args = pa.parse_args()
    if args.command == "auth":
        cmd_auth(args)
    elif args.command == "test":
        cmd_test(args)
    elif args.command == "download":
        cmd_download(args)
    else:
        pa.print_help()


if __name__ == "__main__":
    main()
