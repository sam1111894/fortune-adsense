#!/usr/bin/env python3
"""Deploy to Cloudflare Pages with manifest"""
import os
import json
import hashlib
import zipfile
import io
import sys
import urllib.request
import urllib.error

def make_zip(src_dir):
    """Create ZIP of dist/ directory"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, fs in os.walk(src_dir):
            for f in fs:
                full = os.path.join(root, f)
                arc = os.path.relpath(full, src_dir).replace(os.sep, "/")
                zf.write(full, arc)
    return buf.getvalue()

def make_manifest(src_dir):
    """Build manifest dict"""
    files_meta = {}
    deployed = []
    for root, _, fs in os.walk(src_dir):
        for f in fs:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            with open(full, "rb") as fp:
                h = hashlib.sha256(fp.read()).hexdigest()
            files_meta[rel] = {"contentType": "application/octet-stream", "contentHash": h}
            deployed.append(rel)
    return {"files": files_meta, "deployed": deployed}

def make_multipart_body(zip_data, manifest_str, boundary):
    """Build multipart body manually with correct Content-Type per part"""
    return (
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

def ensure_extra_files(dist_dir, project_root):
    """Copy files from public/ that aren't in dist/ (like IndexNow key)"""
    import shutil, os
    public_dir = os.path.join(project_root, 'public')
    if os.path.isdir(public_dir):
        for f in os.listdir(public_dir):
            src = os.path.join(public_dir, f)
            dst = os.path.join(dist_dir, f)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy(src, dst)
                print(f"Copied: {f}")


def deploy(account_id, api_token, project, src_dir, project_root='.'):
    """Deploy to Cloudflare Pages"""
    # public/의 키 파일 등을 dist/로 복사
    ensure_extra_files(src_dir, project_root)

    """Deploy to Cloudflare Pages"""
    zip_data = make_zip(src_dir)
    manifest = make_manifest(src_dir)
    manifest_str = json.dumps(manifest)

    boundary = "----CloudflarePagesDeployBoundaryXYZ"
    body = make_multipart_body(zip_data, manifest_str, boundary)

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
    print(f"Manifest entries: {len(manifest['deployed'])}")

    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read().decode()
            print(f"HTTP {resp.status}")
            result = json.loads(data)
            if result.get("success"):
                dep = result["result"]
                print(f"✅ Deploy success: {dep['id']}")
                print(f"   URL: {dep['url']}")
                return 0
            else:
                print(f"❌ Failed: {data[:500]}")
                return 1
    except urllib.error.HTTPError as e:
        data = e.read().decode()
        print(f"❌ HTTP {e.code}: {data[:500]}")
        return 1

if __name__ == "__main__":
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_token = os.environ["CLOUDFLARE_API_TOKEN"]
    project = os.environ.get("CLOUDFLARE_PAGES_PROJECT", "fortune-adsense")
    src_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    sys.exit(deploy(account_id, api_token, project, src_dir))
