"""
Make the selling page's pictures, video and sound with ElevenLabs (Pro plan).

    python3 scripts/landing/eleven.py image NAME "prompt" [--model gemini-3-pro-image] [--ref GEN_ID] [--aspect 16:9]
    python3 scripts/landing/eleven.py video NAME "prompt" --start GEN_ID [--end GEN_ID] [--secs 6]
    python3 scripts/landing/eleven.py sfx NAME "prompt" [--secs 10]

Files land in public/landing/ (NAME.png / NAME.mp4 / NAME.mp3), plus NAME.json with the
generation id and prompt, so a later step can reuse a picture as a reference.

The key: ELEVENLABS_API_KEY from the environment, or from the .env file given with
--env-file (default: ~/Documents/projects/israel-startup-story/.env). It is never printed.
Uses only the Python standard library.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.elevenlabs.io"
OUT = Path(__file__).resolve().parents[2] / "public" / "landing"
DEFAULT_ENV = Path.home() / "Documents/projects/israel-startup-story/.env"

# python.org Python on macOS ships without root certificates. Use certifi's if it's installed.
try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CTX = ssl.create_default_context()


def api_key(env_file: Path) -> str:
    key = os.getenv("ELEVENLABS_API_KEY", "")
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("No ELEVENLABS_API_KEY found (env var or --env-file).")
    return key


def call(method: str, path: str, key: str, body: dict | None = None, raw: bool = False):
    req = urllib.request.Request(API + path, method=method, headers={"xi-api-key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=300, context=CTX) as r:
            data = r.read()
            return data if raw else json.loads(data)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed: {e.code} {e.read().decode()[:600]}")


def wait(kind: str, gen_id: str, key: str, every: float) -> dict:
    """Poll a Flows generation until it's done (images every 2 s, video every 10 s)."""
    start = time.time()
    while True:
        g = call("GET", f"/v1/flows/{kind}/{gen_id}", key)
        status = g.get("status")
        if status in ("completed", "failed"):
            return g
        if time.time() - start > 900:
            sys.exit(f"Timed out waiting for {kind} {gen_id}")
        print(f"  {kind} {gen_id}: {status} ({int(time.time() - start)} s)", flush=True)
        time.sleep(every)


def download(url: str, path: Path) -> None:
    with urllib.request.urlopen(url, timeout=300, context=CTX) as r:
        path.write_bytes(r.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["image", "video", "sfx"])
    ap.add_argument("name")
    ap.add_argument("prompt")
    ap.add_argument("--model", default=None)
    ap.add_argument("--ref", action="append", default=[], help="earlier generation id to use as a reference image")
    ap.add_argument("--start", help="generation id of the first frame (video)")
    ap.add_argument("--end", help="generation id of the last frame (video, optional)")
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--resolution", default=None)
    ap.add_argument("--secs", type=float, default=None)
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    a = ap.parse_args()
    key = api_key(a.env_file)
    OUT.mkdir(parents=True, exist_ok=True)

    if a.kind == "sfx":
        body = {"text": a.prompt, "duration_seconds": a.secs or 10, "prompt_influence": 0.5}
        audio = call("POST", "/v1/sound-generation", key, body, raw=True)
        (OUT / f"{a.name}.mp3").write_bytes(audio)
        (OUT / f"{a.name}.json").write_text(json.dumps({"kind": "sfx", **body}, indent=1))
        print(f"saved public/landing/{a.name}.mp3")
        return

    if a.kind == "image":
        body = {"model_id": a.model or "gemini-3-pro-image", "prompt": a.prompt, "aspect_ratio": a.aspect,
                "resolution": a.resolution or "2K"}
        if a.ref:
            body["images"] = [{"type": "generation", "generation_id": r} for r in a.ref]
    else:
        if not a.start:
            sys.exit("video needs --start GEN_ID (the first frame)")
        body = {"model_id": a.model or "veo-3.1-fast-generate-001", "prompt": a.prompt, "aspect_ratio": a.aspect,
                "duration_secs": int(a.secs or 6), "resolution": a.resolution or "1080p", "generate_audio": False,
                "start_frame": {"type": "generation", "generation_id": a.start}}
        if a.end:
            body["end_frame"] = {"type": "generation", "generation_id": a.end}
    created = call("POST", f"/v1/flows/{a.kind}", key, body)
    print(f"submitted {a.kind} {created['id']}", flush=True)
    g = wait(a.kind, created["id"], key, 2 if a.kind == "image" else 10)
    meta = {"id": created["id"], "kind": a.kind, "status": g.get("status"), "body": body}
    if g.get("status") != "completed":
        (OUT / f"{a.name}.json").write_text(json.dumps({**meta, "error": g}, indent=1))
        sys.exit(f"{a.kind} failed: {g.get('failure_reason')} {g.get('error_message')}")
    ext = "png" if a.kind == "image" else "mp4"
    if "jpeg" in str(g.get("content_mime_type", "")):
        ext = "jpg"
    download(g["content_url"], OUT / f"{a.name}.{ext}")
    (OUT / f"{a.name}.json").write_text(json.dumps(meta, indent=1))
    print(f"saved public/landing/{a.name}.{ext}  (generation id {created['id']})")


if __name__ == "__main__":
    main()
