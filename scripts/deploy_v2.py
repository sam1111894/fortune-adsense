#!/usr/bin/env python3
"""
Cloudflare Pages direct upload — deploy.py의 manifest 버그 우회
문제: Cloudflare API가 manifest의 'deployed'/'files' 메타 필드를 파일로 인식
해결: manifest 형식을 Cloudflare Pages 공식 형식(각 파일별 SHA256 hash + contentType)에 맞춤
"""
import os
import json
import hashlib
import zipfile
import io
import sys
import urllib.request
import mimetypes

EXT_TYPE = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
}

def content_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in EXT_TYPE:
        return EXT_TYPE[ext]
    mt, _ = mimetypes.guess_type(filename)
    return mt or "application/octet-stream"


def make_zip(src_dir):
    """Create ZIP — exclude archive files inside dist"""
    buf = io.BytesIO()
    skipped = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, fs in os.walk(src_dir):
            for f in fs:
                # skip zip files inside dist (Finder archives)
                if f.endswith(".zip"):
                    skipped += 1
                    continue
                # skip macOS metadata
                if f.startswith(".") and f not in (".well-known",):
                    continue
                full = os.path.join(root, f)
                arc = os.path.relpath(full, src_dir).replace(os.sep, "/")
                zf.write(full, arc)
    print(f"Skipped {skipped} archive file(s) inside dist/")
    return buf.getvalue()


def make_manifest(src_dir):
    """Cloudflare Pages 공식 manifest 형식:
    {
      "<path>": {
        "contentType": "<mime>",
        "contentHash": "<sha256-hex>"
      }
    }
    """
    files_meta = {}
    file_count = 0
    for root, _, fs in os.walk(src_dir):
        for f in fs:
            if f.endswith(".zip"):
                continue
            if f.startswith(".") and f not in (".well-known",):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            with open(full, "rb") as fp:
                data = fp.read()
            h = hashlib.sha256(data).hexdigest()
            files_meta[rel] = {
                "contentType": content_type(f),
                "contentHash": h,
            }
            file_count += 1
    print(f"Manifest: {file_count} file entries")
    return files_meta


def deploy(account_id, api_token, project, src_dir):
    zip_data = make_zip(src_dir)
    manifest = make_manifest(src_dir)
    manifest_str = json.dumps(manifest)

    boundary = "----CloudflarePagesDeployBoundaryXYZ"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="site.zip"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ).encode() + zip_data + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="manifest"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"{manifest_str}\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/pages/projects/{project}/deployments"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    print(f"Deploying to {url}")
    print(f"Zip size: {len(zip_data)} bytes")
    print(f"Manifest size: {len(manifest_str)} bytes")

    with urllib.request.urlopen(req) as resp:
        data = resp.read().decode()
        print(f"HTTP {resp.status}")
        result = json.loads(data)
        if result.get("success"):
            dep = result["result"]
            print(f"✅ Deploy success: {dep['id']}")
            print(f"   URL: {dep['url']}")
            return dep["id"]
        else:
            print(f"❌ Deploy failed: {json.dumps(result, indent=2)}")
            return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: deploy_v2.py <dist_dir>")
        sys.exit(1)

    src_dir = sys.argv[1]

    # load .env
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.isfile(env_path):
        with open(env_path) as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    project = os.environ.get("CLOUDFLARE_PAGES_PROJECT")

    if not all([account_id, api_token, project]):
        print("❌ Missing CLOUDFLARE_* env vars")
        sys.exit(1)

    deploy(account_id, api_token, project, src_dir)
