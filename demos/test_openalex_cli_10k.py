"""OpenAlex CLI 10k metadata speed test with tqdm, retries, and safe logs.

Default mode is exact-10k CLI test:
1. Collect target Work IDs using OpenAlex API cursor paging.
2. Pipe those IDs into `openalex download --stdin`.
3. Monitor generated JSON files and report elapsed time / files per second.

Notes:
- OpenAlex CLI currently has no generic --limit option, so exact-10k testing uses stdin IDs.
- On free keys, use low workers such as 3-10 to avoid 429 / daily credit exhaustion.
- Do not paste logs with raw api_key values. This script masks known key patterns in captured CLI output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
DEFAULT_SELECT = "id"
FULL_METADATA_SELECT = (
    "id,doi,title,publication_year,publication_date,type,cited_by_count,"
    "authorships,primary_location,topics,referenced_works,open_access"
)


def mask_secret(text: str) -> str:
    text = re.sub(r"(api_key=)[^&\s'\"]+", r"\1***", text)
    text = re.sub(r"(--api-key\s+)(\S+)", r"\1***", text)
    return text


def build_retry_session(user_agent: str) -> requests.Session:
    retry = Retry(
        total=10,
        connect=10,
        read=10,
        status=10,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": user_agent})
    return session


def count_metadata_json(output_dir: Path) -> int:
    count = 0
    for path in output_dir.rglob("*.json"):
        name = path.name.lower()
        if name.startswith(".") or "checkpoint" in name:
            continue
        count += 1
    return count


def sum_json_bytes(output_dir: Path) -> int:
    total = 0
    for path in output_dir.rglob("*.json"):
        name = path.name.lower()
        if name.startswith(".") or "checkpoint" in name:
            continue
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def collect_work_ids(
    *,
    target: int,
    filter_expr: str,
    api_key: str,
    ids_path: Path,
    user_agent: str,
    sleep: float,
) -> list[str]:
    params = {
        "filter": filter_expr,
        "select": DEFAULT_SELECT,
        "per_page": 100,
        "cursor": "*",
    }
    if api_key:
        params["api_key"] = api_key

    session = build_retry_session(user_agent)
    ids: list[str] = []

    with tqdm(total=target, desc="Collecting Work IDs", unit="id") as pbar:
        while len(ids) < target:
            try:
                resp = session.get(OPENALEX_WORKS_URL, params=params, timeout=(10, 90))
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else 10.0
                    tqdm.write(f"[WARN] API 429 while collecting IDs. sleep {wait:.1f}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                tqdm.write(f"[WARN] API request failed, sleep 10s and retry current cursor: {exc}")
                time.sleep(10)
                continue

            data = resp.json()
            results = data.get("results", [])
            if not results:
                break

            old = len(ids)
            for item in results:
                openalex_id = item.get("id", "")
                if openalex_id:
                    ids.append(openalex_id.rsplit("/", 1)[-1])
                if len(ids) >= target:
                    break
            pbar.update(len(ids) - old)

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor
            if sleep > 0:
                time.sleep(sleep)

    ids_path.write_text("\n".join(ids), encoding="utf-8")
    return ids


def api_download_jsonl(
    *,
    target: int,
    filter_expr: str,
    api_key: str,
    output_jsonl: Path,
    user_agent: str,
    sleep: float,
) -> tuple[float, int, int]:
    """Direct API metadata download baseline: cursor -> JSONL."""
    params = {
        "filter": filter_expr,
        "select": FULL_METADATA_SELECT,
        "per_page": 100,
        "cursor": "*",
    }
    if api_key:
        params["api_key"] = api_key

    session = build_retry_session(user_agent)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    total_bytes = 0
    t0 = time.perf_counter()

    with output_jsonl.open("w", encoding="utf-8") as f, tqdm(
        total=target, desc="API metadata JSONL", unit="work"
    ) as pbar:
        while count < target:
            try:
                resp = session.get(OPENALEX_WORKS_URL, params=params, timeout=(10, 90))
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else 10.0
                    tqdm.write(f"[WARN] API 429. sleep {wait:.1f}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                tqdm.write(f"[WARN] API request failed, sleep 10s and retry current cursor: {exc}")
                time.sleep(10)
                continue

            data = resp.json()
            results = data.get("results", [])
            if not results:
                break

            old = count
            for item in results:
                line = json.dumps(item, ensure_ascii=False)
                f.write(line + "\n")
                total_bytes += len(line.encode("utf-8")) + 1
                count += 1
                if count >= target:
                    break
            pbar.update(count - old)

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor
            if sleep > 0:
                time.sleep(sleep)

    return time.perf_counter() - t0, count, total_bytes


def monitor_cli_download(output_dir: Path, target: int, stop_event: threading.Event, interval: float) -> None:
    last_count = count_metadata_json(output_dir)
    with tqdm(total=target, desc="CLI metadata JSON", unit="file") as pbar:
        pbar.update(min(last_count, target))
        while not stop_event.is_set() and pbar.n < target:
            time.sleep(interval)
            current = count_metadata_json(output_dir)
            if current > last_count:
                pbar.update(min(current - last_count, target - pbar.n))
                last_count = current
        current = count_metadata_json(output_dir)
        if current > last_count:
            pbar.update(min(current - last_count, target - pbar.n))


def run_openalex_cli_stdin(
    *,
    ids: list[str],
    output_dir: Path,
    api_key: str,
    workers: int,
    monitor_interval: float,
    show_cli_output: bool,
) -> tuple[float, int, int, str, str, int]:
    cmd = [
        "openalex",
        "download",
        "--output",
        str(output_dir),
        "--stdin",
        "--workers",
        str(workers),
        "--nested",
        "--fresh",
    ]
    if api_key:
        cmd.extend(["--api-key", api_key])

    print("[CLI] running:", mask_secret(" ".join(cmd)))
    print(f"[CLI] target IDs: {len(ids)}; workers={workers}")

    stop_event = threading.Event()
    monitor_thread = threading.Thread(
        target=monitor_cli_download,
        args=(output_dir, len(ids), stop_event, monitor_interval),
        daemon=True,
    )

    t0 = time.perf_counter()
    monitor_thread.start()
    proc = subprocess.run(
        cmd,
        input="\n".join(ids) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    elapsed = time.perf_counter() - t0
    stop_event.set()
    monitor_thread.join(timeout=3)

    stdout = mask_secret(proc.stdout or "")
    stderr = mask_secret(proc.stderr or "")

    if show_cli_output or proc.returncode != 0:
        if stdout.strip():
            print("\n[CLI stdout]\n" + stdout)
        if stderr.strip():
            print("\n[CLI stderr]\n" + stderr)

    json_count = count_metadata_json(output_dir)
    byte_count = sum_json_bytes(output_dir)
    return elapsed, json_count, byte_count, stdout, stderr, proc.returncode


def load_ids(ids_path: Path, target: int) -> list[str]:
    ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return ids[:target]


def print_cli_diagnostics(stderr: str, returncode: int) -> None:
    lower = stderr.lower()
    if "credits exhausted" in lower:
        print("[DIAG] OpenAlex credits exhausted. Wait for reset or use a key with more budget.")
    if "429" in stderr or "rate limited" in lower:
        print("[DIAG] 429/rate-limit detected. Try --workers 3 or --workers 5, or use API JSONL mode.")
    if "notimplementederror" in lower and "add_signal_handler" in lower:
        print("[DIAG] Windows signal-handler bug detected. Run patch_openalex_windows_optimized.py.")
    if "unboundlocalerror" in lower and "meta_content" in lower:
        print("[DIAG] openalex_cli metadata failure bug detected. Run patch_openalex_windows_optimized.py.")
    if returncode != 0:
        print(f"[DIAG] CLI exited with code {returncode}. Partial downloads may still be usable.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test OpenAlex CLI / API speed for 10k metadata records.")
    parser.add_argument("--target", type=int, default=10000)
    parser.add_argument("--filter", default="publication_year:2024,type:article")
    parser.add_argument("--output", default="data/openalex_cli_speed_10k")
    parser.add_argument("--workers", type=int, default=5, help="Use 3-10 for free keys; 50 is likely to hit 429.")
    parser.add_argument("--api-key", default=os.environ.get("OPENALEX_API_KEY", ""))
    parser.add_argument("--user-agent", default="BigData_Sci OpenAlex speed test; mailto:your_email@example.com")
    parser.add_argument("--skip-id-collection", action="store_true")
    parser.add_argument("--clean-output", action="store_true")
    parser.add_argument("--monitor-interval", type=float, default=1.0)
    parser.add_argument("--api-sleep", type=float, default=0.2, help="Sleep between cursor pages while collecting IDs/API JSONL.")
    parser.add_argument("--mode", choices=["cli-ids", "api-jsonl"], default="cli-ids")
    parser.add_argument("--show-cli-output", action="store_true")
    parser.add_argument("--fail-on-cli-error", action="store_true", help="Return non-zero when openalex CLI fails.")
    args = parser.parse_args()

    output_dir = Path(args.output)
    ids_path = output_dir / f"work_ids_{args.target}.txt"

    if args.clean_output:
        if args.skip_id_collection:
            raise ValueError("--clean-output cannot be used with --skip-id-collection")
        if output_dir.exists():
            shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    print("========== OpenAlex metadata speed test ==========")
    print(f"Mode       : {args.mode}")
    print(f"Target     : {args.target}")
    print(f"Filter     : {args.filter}")
    print(f"Output     : {output_dir}")
    print(f"Workers    : {args.workers}")
    print("API key    :", "present" if args.api_key else "missing")
    print("=================================================")

    if args.mode == "api-jsonl":
        output_jsonl = output_dir / f"metadata_{args.target}.jsonl"
        elapsed, count, byte_count = api_download_jsonl(
            target=args.target,
            filter_expr=args.filter,
            api_key=args.api_key,
            output_jsonl=output_jsonl,
            user_agent=args.user_agent,
            sleep=args.api_sleep,
        )
        rate = count / elapsed if elapsed > 0 else 0.0
        print("\n========== API JSONL Result ==========")
        print(f"Records       : {count}")
        print(f"Bytes         : {format_bytes(byte_count)}")
        print(f"Elapsed       : {elapsed:.2f}s / {elapsed / 60:.2f}min")
        print(f"Average speed : {rate:.2f} records/s")
        print(f"Output JSONL  : {output_jsonl}")
        print("=====================================")
        return 0

    # cli-ids mode
    if args.skip_id_collection:
        if not ids_path.exists():
            raise FileNotFoundError(f"Cannot find IDs file: {ids_path}")
        ids = load_ids(ids_path, args.target)
        print(f"[API] reused {len(ids)} IDs from {ids_path}")
    else:
        t0 = time.perf_counter()
        ids = collect_work_ids(
            target=args.target,
            filter_expr=args.filter,
            api_key=args.api_key,
            ids_path=ids_path,
            user_agent=args.user_agent,
            sleep=args.api_sleep,
        )
        print(f"[API] saved {len(ids)} IDs to {ids_path}")
        print(f"[API] elapsed: {time.perf_counter() - t0:.2f}s")

    if not ids:
        raise RuntimeError("No Work IDs collected.")

    if args.workers > 10:
        print("[WARN] workers > 10 may quickly hit 429 or daily credit exhaustion on free keys.")

    elapsed, json_count, byte_count, stdout, stderr, returncode = run_openalex_cli_stdin(
        ids=ids,
        output_dir=output_dir,
        api_key=args.api_key,
        workers=args.workers,
        monitor_interval=args.monitor_interval,
        show_cli_output=args.show_cli_output,
    )

    rate = json_count / elapsed if elapsed > 0 else 0.0
    print_cli_diagnostics(stderr, returncode)

    print("\n========== OpenAlex CLI Result ==========")
    print(f"Exit code     : {returncode}")
    print(f"Target IDs    : {len(ids)}")
    print(f"Metadata JSON : {json_count}")
    print(f"Bytes         : {format_bytes(byte_count)}")
    print(f"Workers       : {args.workers}")
    print(f"Elapsed       : {elapsed:.2f}s / {elapsed / 60:.2f}min")
    print(f"Average speed : {rate:.2f} files/s")
    if rate > 0:
        print(f"10k estimate  : {args.target / rate / 60:.2f}min at observed speed")
    print("========================================")

    if returncode != 0 and args.fail_on_cli_error:
        return returncode
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
