#!/usr/bin/env python3
"""Batch process audio files through the acestep.cpp /understand endpoint."""

import argparse
import json
import logging
import os
import sys
import time
import requests
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
DEFAULT_DELAY = 1.0
DEFAULT_RETRIES = 3
POLL_INTERVAL = 0.5


def find_audio_files(folder: str) -> list[str]:
    """Recursively find all audio files in folder and subfolders."""
    audio_files = []
    folder = Path(folder)
    for root, _dirs, files in os.walk(folder):
        for fname in sorted(files):
            if Path(fname).suffix.lower() in AUDIO_EXTS:
                audio_files.append(os.path.join(root, fname))
    audio_files.sort()
    return audio_files


def submit_understand(server_url: str, file_path: str, request_json: dict | None = None) -> dict:
    """Submit audio to /understand. Returns job id dict."""
    files = {"audio": (os.path.basename(file_path), open(file_path, "rb"))}
    data = {}
    if request_json:
        data["request"] = json.dumps(request_json)

    resp = requests.post(f"{server_url}/understand", files=files, data=data)
    files["audio"][1].close()

    if resp.status_code != 200:
        raise Exception(f"POST /understand failed: {resp.status_code} {resp.text}")

    return resp.json()


def poll_job(server_url: str, job_id: str) -> dict:
    """Poll job status until done/failed."""
    while True:
        resp = requests.get(f"{server_url}/job?id={job_id}")
        if resp.status_code != 200:
            raise Exception(f"GET /job failed: {resp.status_code} {resp.text}")
        text = resp.text.strip()
        if not text:
            log.debug("  Empty response from /job, retrying...")
            time.sleep(POLL_INTERVAL)
            continue
        data = resp.json()
        status = data.get("status")
        if status == "done":
            return data
        if status == "failed":
            raise Exception(f"Job {job_id} failed: {json.dumps(data)}")
        if status == "cancelled":
            raise Exception(f"Job {job_id} cancelled")
        time.sleep(POLL_INTERVAL)


def fetch_result(server_url: str, job_id: str) -> dict:
    """Fetch understand result. Returns the JSON metadata dict."""
    resp = requests.get(f"{server_url}/job?id={job_id}&result=1")
    if resp.status_code != 200:
        raise Exception(f"GET /job result failed: {resp.status_code} {resp.text}")

    # Parse multipart/mixed response: first part is JSON, second part is latent (ignored)
    content_type = resp.headers.get("Content-Type", "")
    boundary = ""
    if "boundary=" in content_type:
        boundary = content_type.split("boundary=", 1)[1].strip()

    body = resp.content
    boundary_marker = f"--{boundary}".encode()
    parts = body.split(boundary_marker)

    # Find the JSON part (first multipart section with headers then blank line then JSON)
    json_part = None
    for part in parts:
        if not part.strip():
            continue
        # Split on CRLF to find the header/body separator (double blank line)
        idx = part.find(b"\r\n\r\n")
        if idx >= 0:
            json_part = part[idx + 4:].strip()
            if json_part and not json_part.startswith(b"--"):
                break

    if not json_part:
        raise Exception("Could not parse JSON part from understand result")

    return json.loads(json_part.decode("utf-8"))


def process_file(
    server_url: str,
    file_path: str,
    request_json: dict | None,
    retries: int,
) -> tuple[str, dict | None, str]:
    """Process a single audio file. Returns (path, result_or_None, status_string)."""
    for attempt in range(1, retries + 1):
        try:
            log.info("  Submitting %s (attempt %d)", file_path, attempt)
            job = submit_understand(server_url, file_path, request_json)
            job_id = job["id"]
            log.info("  Job %s submitted", job_id)

            log.info("  Polling job %s...", job_id)
            poll_job(server_url, job_id)

            log.info("  Fetching result for job %s...", job_id)
            result = fetch_result(server_url, job_id)
            return (file_path, result, "ok")

        except Exception as e:
            log.warning("  Attempt %d failed for %s: %s", attempt, file_path, e)
            if attempt < retries:
                wait = POLL_INTERVAL * (2 ** attempt)
                log.info("  Retrying in %.1fs...", wait)
                time.sleep(wait)

    return (file_path, None, "failed")


def main():
    parser = argparse.ArgumentParser(description="Batch process audio files through acestep /understand")
    parser.add_argument("folder", help="Folder to scan for audio files (recursive)")
    parser.add_argument("--server-url", default="http://localhost:8080", help="Server URL (default: http://localhost:8080)")
    parser.add_argument("--output-dir", default="docs/understand", help="Output directory for results (default: docs/understand)")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help=f"Delay between files in seconds (default: {DEFAULT_DELAY})")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help=f"Max retries per file (default: {DEFAULT_RETRIES})")
    parser.add_argument("--resume", action="store_true", help="Skip files with existing results")
    parser.add_argument("--request-json", default=None, help="Path to optional request JSON to include with each file")
    parser.add_argument("--lm-model", default=None, help="LM model name (e.g. acestep-5Hz-lm-4B-Q8_0)")
    args = parser.parse_args()

    if not os.path.isdir(args.folder):
        log.error("Folder not found: %s", args.folder)
        sys.exit(1)

    # Load optional request JSON
    request_json = None
    if args.request_json:
        with open(args.request_json) as f:
            request_json = json.load(f)

    # Merge --lm-model into request_json (takes precedence)
    if args.lm_model:
        lm = args.lm_model
        if not lm.endswith(".gguf"):
            lm = lm + ".gguf"
        if request_json is None:
            request_json = {}
        request_json["lm_model"] = lm

    # Find audio files
    audio_files = find_audio_files(args.folder)
    if not audio_files:
        log.warning("No audio files found in %s", args.folder)
        return

    log.info("Found %d audio file(s) in %s", len(audio_files), args.folder)
    log.info("Server: %s", args.server_url)
    log.info("Output: %s", args.output_dir)

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Filter already-processed if --resume
    if args.resume:
        remaining = []
        for f in audio_files:
            stem = Path(f).stem
            out_json = os.path.join(args.output_dir, f"{stem}.json")
            if os.path.exists(out_json):
                log.info("  Skipping (exists): %s", f)
                continue
            remaining.append(f)
        audio_files = remaining

    if not audio_files:
        log.info("Nothing to process.")
        return

    # Process files
    results = []
    for i, file_path in enumerate(audio_files):
        log.info("[%d/%d] %s", i + 1, len(audio_files), file_path)
        path, result, status = process_file(
            args.server_url, file_path, request_json, args.retries
        )
        results.append({"file": path, "status": status, "result": result})

        # Save result
        if status == "ok" and result:
            # Clean up result: remove audio_codes, set lyrics to TODO, force language to en
            for item in result:
                item.pop("audio_codes", None)
                item["lyrics"] = "TODO"
                item["vocal_language"] = "en"
            stem = Path(file_path).stem
            out_path = os.path.join(args.output_dir, f"{stem}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            log.info("  Saved: %s", out_path)
        elif status == "ok":
            log.warning("  No result data for %s", file_path)
        else:
            log.error("  FAILED: %s", file_path)

        # Delay between files
        if i < len(audio_files) - 1:
            log.info("  Waiting %.1fs before next file...", args.delay)
            time.sleep(args.delay)

    # Write manifest
    manifest = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "server_url": args.server_url,
        "total_files": len(results),
        "successful": sum(1 for r in results if r["status"] == "ok"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "files": [
            {
                "file": r["file"],
                "status": r["status"],
                "output": os.path.join(args.output_dir, f"{Path(r['file']).stem}.json") if r["status"] == "ok" else None,
            }
            for r in results
        ],
    }
    manifest_path = os.path.join(args.output_dir, "batch_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    log.info("Manifest: %s", manifest_path)

    # Summary
    log.info("")
    log.info("=== Summary ===")
    log.info("Total: %d | Success: %d | Failed: %d", manifest["total_files"], manifest["successful"], manifest["failed"])


if __name__ == "__main__":
    main()
