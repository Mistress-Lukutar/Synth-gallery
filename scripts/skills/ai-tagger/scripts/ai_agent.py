#!/usr/bin/env python3
"""
Synth Gallery AI Tagger — API helper.

Provides convenient CLI commands for the AI agent (Kimi, etc.) to interact
with the job queue: poll, claim, download, submit. No VLM logic here —
the agent itself analyzes images via its own vision capabilities.
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path


class GalleryAPI:
    """Thin wrapper around Synth Gallery AI tagging API."""

    def __init__(self, api_key: str, base_url: str = "http://localhost:8000"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def call(self, method: str, path: str, body=None):
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("X-API-Key", self.api_key)
        if body is not None:
            req.add_header("Content-Type", "application/json")
            req.data = json.dumps(body).encode()
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode())
            except json.JSONDecodeError:
                err_body = {"raw": e.read().decode()}
            return {"_error": True, "status": e.code, "detail": err_body}

    def get_tags(self):
        return self.call("GET", "/api/ai/tags")

    def get_pending(self):
        return self.call("GET", "/api/ai/jobs/pending")

    def claim(self, job_id: int):
        return self.call("POST", f"/api/ai/jobs/{job_id}/claim")

    def download(self, item_id: str, dest_dir: Path = Path("downloads")) -> Path:
        dest_dir.mkdir(exist_ok=True)
        url = f"{self.base_url}/api/ai/items/{item_id}/file"
        req = urllib.request.Request(url)
        req.add_header("X-API-Key", self.api_key)
        ext = ".bin"
        with urllib.request.urlopen(req) as resp:
            ctype = resp.headers.get("Content-Type", "").split(";")[0]
            if ctype == "image/jpeg":
                ext = ".jpg"
            elif ctype == "image/png":
                ext = ".png"
            elif ctype == "image/webp":
                ext = ".webp"
            elif ctype == "video/mp4":
                ext = ".mp4"
            dest = dest_dir / f"{item_id}{ext}"
            with open(dest, "wb") as f:
                f.write(resp.read())
        return dest

    def submit(self, job_id: int, tag_names: list):
        return self.call("POST", f"/api/ai/jobs/{job_id}/results", {"tag_names": tag_names})

    def fail(self, job_id: int, reason: str):
        return self.call("POST", f"/api/ai/jobs/{job_id}/fail", {"error": reason})


def cmd_tags(args):
    api = GalleryAPI(args.api_key, args.base_url)
    data = api.get_tags()
    if data.get("_error"):
        print(json.dumps(data, indent=2))
        sys.exit(1)
    print(json.dumps(data, indent=2))


def cmd_pending(args):
    api = GalleryAPI(args.api_key, args.base_url)
    data = api.get_pending()
    if data.get("_error"):
        print(json.dumps(data, indent=2))
        sys.exit(1)
    print(json.dumps(data, indent=2))


def cmd_claim(args):
    api = GalleryAPI(args.api_key, args.base_url)
    data = api.claim(args.job_id)
    print(json.dumps(data, indent=2))


def cmd_download(args):
    api = GalleryAPI(args.api_key, args.base_url)
    data = api.claim(args.job_id)
    if data.get("_error"):
        print(json.dumps(data, indent=2))
        sys.exit(1)
    item_id = data["item"]["id"]
    path = api.download(item_id, Path(args.dest))
    print(f"Downloaded: {path}")
    # Also print metadata so the agent can read it
    print(json.dumps({
        "file_path": str(path),
        "item": data["item"],
        "existing_tags": data.get("existing_tags", []),
    }, indent=2))


def cmd_submit(args):
    api = GalleryAPI(args.api_key, args.base_url)
    tag_names = [t.strip() for t in args.tag_names.split(",")]
    data = api.submit(args.job_id, tag_names)
    print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Synth Gallery AI Tagger API helper")
    parser.add_argument("--api-key", default=os.getenv("SYNTH_AI_API_KEY"))
    parser.add_argument("--base-url", default="http://localhost:8000")
    sub = parser.add_subparsers(dest="command", required=True)

    p_tags = sub.add_parser("tags", help="List all available tags")
    p_tags.set_defaults(func=cmd_tags)

    p_pending = sub.add_parser("pending", help="List pending jobs")
    p_pending.set_defaults(func=cmd_pending)

    p_claim = sub.add_parser("claim", help="Claim a job")
    p_claim.add_argument("job_id", type=int)
    p_claim.set_defaults(func=cmd_claim)

    p_download = sub.add_parser("download", help="Claim and download media file")
    p_download.add_argument("job_id", type=int)
    p_download.add_argument("--dest", default="downloads")
    p_download.set_defaults(func=cmd_download)

    p_submit = sub.add_parser("submit", help="Submit tag results")
    p_submit.add_argument("job_id", type=int)
    p_submit.add_argument("tag_names", help="Comma-separated tag names")
    p_submit.set_defaults(func=cmd_submit)

    args = parser.parse_args()
    if not args.api_key:
        print("Error: API key required. Set SYNTH_AI_API_KEY or pass --api-key")
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
