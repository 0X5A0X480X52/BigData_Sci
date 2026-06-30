"""Patch openalex-official 0.3.x for Windows + metadata-only stress tests.

What this script fixes in openalex_cli/downloader.py:
1. Windows asyncio event loops do not implement add_signal_handler/remove_signal_handler.
2. Metadata fetch failures can leave `meta_content` undefined and crash metadata-only runs.
3. Short 429/rate-limit/network bursts get a bounded exponential-backoff retry before failure.

Usage:
    python patch_openalex_windows_optimized.py --check
    python patch_openalex_windows_optimized.py --dry-run
    python patch_openalex_windows_optimized.py
    python patch_openalex_windows_optimized.py --restore-latest
    python patch_openalex_windows_optimized.py --path "C:/.../site-packages/openalex_cli/downloader.py"
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import re
import shutil
import sys
from pathlib import Path

MARKER = "BIGDATA_SCI_WINDOWS_RATE_PATCH_V2"

SIGNAL_OLD = '''        # Set up signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)
'''

SIGNAL_NEW = f'''        # Set up signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        registered_signals: list[signal.Signals] = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
                registered_signals.append(sig)
            except (NotImplementedError, RuntimeError):
                # {MARKER}: Windows event loops do not support asyncio signal handlers.
                pass
'''

SIGNAL_REMOVE_OLD = '''            # Remove signal handlers
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
'''

SIGNAL_REMOVE_NEW = f'''            # Remove signal handlers
            for sig in registered_signals:
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError, ValueError):
                    # {MARKER}: handler may not exist on Windows or during shutdown.
                    pass
'''

IMPORT_OLD = "import json\nimport signal\nimport time\n"
IMPORT_NEW = "import json\nimport random\nimport signal\nimport time\n"

# Exact metadata block from openalex-official 0.3.3 downloader.py.
METADATA_OLD = '''            # Always save full metadata (fetch from singleton API)
            try:
                full_metadata = await self.api_client.get_work_metadata(work.work_id)
                meta_path = str(
                    work_id_to_path(filename_base, "json", nested=self.config.nested)
                )
                meta_content = json.dumps(full_metadata, indent=2).encode()
                await self.storage.save(meta_path, meta_content, "application/json")
            except CreditsExhaustedError:
                self._handle_credits_exhausted()
                break
            except Exception as e:
                if self.progress_tracker:
                    self.progress_tracker.log_warning(
                        f"Failed to fetch metadata for {{work.work_id}}: {{e}}"
                    )

            # If no content requested, we're done with this work
'''

METADATA_NEW = f'''            # Always save full metadata (fetch from singleton API)
            meta_content: bytes | None = None
            try:
                full_metadata = await self._fetch_work_metadata_with_retry(work.work_id)
                meta_path = str(
                    work_id_to_path(filename_base, "json", nested=self.config.nested)
                )
                meta_content = json.dumps(full_metadata, indent=2).encode()
                await self.storage.save(meta_path, meta_content, "application/json")
            except CreditsExhaustedError:
                self._handle_credits_exhausted()
                break
            except Exception as e:
                if self.progress_tracker:
                    self.progress_tracker.log_warning(
                        f"Failed to fetch metadata for {{work.work_id}}: {{e}}"
                    )
                await self._results_queue.put(
                    DownloadResult(
                        work_id=work.work_id,
                        format=ContentFormat.NONE,
                        success=False,
                        error=str(e),
                    )
                )
                continue

            # If no content requested, we're done with this work
'''

HELPER_INSERT_BEFORE = '''    async def _download_worker(self, worker_id: int) -> None:
        """Worker that downloads metadata and optionally content."""
'''

HELPER_CODE = f'''    async def _fetch_work_metadata_with_retry(self, work_id: str) -> dict:
        """Fetch singleton Work metadata with bounded retry/backoff.

        {MARKER}: openalex-official 0.3.3 fetches singleton metadata for every
        Work. With many workers this can hit 429. Retrying avoids a crash and
        gives the server time to recover. For large metadata-only jobs, still
        prefer a low --workers value, e.g. 3-10 for free keys.
        """
        max_attempts = 5
        base_sleep = 1.0
        max_sleep = 30.0

        for attempt in range(1, max_attempts + 1):
            try:
                return await self.api_client.get_work_metadata(work_id)
            except CreditsExhaustedError:
                raise
            except Exception as exc:
                message = str(exc)
                is_rate_limit = (
                    "429" in message
                    or "Rate limited" in message
                    or "rate limit" in message.lower()
                    or "too many requests" in message.lower()
                )
                is_transient = (
                    is_rate_limit
                    or "Timeout" in message
                    or "Connection" in message
                    or "ServerDisconnected" in message
                    or "ClientOSError" in message
                    or "EOF" in message
                    or "503" in message
                    or "502" in message
                    or "504" in message
                )

                if attempt >= max_attempts or not is_transient:
                    raise

                delay = min(max_sleep, base_sleep * (2 ** (attempt - 1)))
                delay += random.uniform(0.0, 0.5)
                if self.progress_tracker:
                    self.progress_tracker.log_warning(
                        f"Metadata fetch retry {{attempt}}/{{max_attempts}} for {{work_id}} "
                        f"after transient error: {{exc}}; sleep {{delay:.1f}}s"
                    )
                await asyncio.sleep(delay)

        raise RuntimeError(f"Failed to fetch metadata for {{work_id}}")

'''


def locate_downloader() -> Path:
    spec = importlib.util.find_spec("openalex_cli")
    if spec is None or spec.origin is None:
        raise RuntimeError(
            "Cannot import openalex_cli. Install it first: pip install openalex-official"
        )
    return Path(spec.origin).resolve().parent / "downloader.py"


def latest_backup(target: Path, backup_dir: Path | None = None) -> Path | None:
    directory = backup_dir or target.parent
    candidates = sorted(directory.glob(f"{target.name}.bak.*"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def make_backup(target: Path, backup_dir: Path | None = None) -> Path:
    directory = backup_dir or target.parent
    directory.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = directory / f"{target.name}.bak.{stamp}"
    shutil.copy2(target, backup)
    return backup


def patch_text(text: str, force: bool = False) -> tuple[str, list[str]]:
    changes: list[str] = []

    if MARKER in text and not force:
        return text, ["already_patched"]

    new_text = text

    if IMPORT_NEW not in new_text:
        if IMPORT_OLD in new_text:
            new_text = new_text.replace(IMPORT_OLD, IMPORT_NEW, 1)
            changes.append("add_random_import")
        else:
            # Avoid failing the whole patch if import layout changed.
            changes.append("warn_import_pattern_not_found")

    if SIGNAL_OLD in new_text:
        new_text = new_text.replace(SIGNAL_OLD, SIGNAL_NEW, 1)
        changes.append("patch_signal_add")
    elif "registered_signals" in new_text:
        changes.append("signal_add_already_safe")
    else:
        changes.append("warn_signal_add_pattern_not_found")

    if SIGNAL_REMOVE_OLD in new_text:
        new_text = new_text.replace(SIGNAL_REMOVE_OLD, SIGNAL_REMOVE_NEW, 1)
        changes.append("patch_signal_remove")
    elif "for sig in registered_signals" in new_text:
        changes.append("signal_remove_already_safe")
    else:
        changes.append("warn_signal_remove_pattern_not_found")

    if HELPER_CODE.strip() not in new_text:
        if HELPER_INSERT_BEFORE in new_text:
            new_text = new_text.replace(
                HELPER_INSERT_BEFORE,
                HELPER_CODE + HELPER_INSERT_BEFORE,
                1,
            )
            changes.append("add_metadata_retry_helper")
        else:
            changes.append("warn_helper_insert_pattern_not_found")
    else:
        changes.append("metadata_retry_helper_already_present")

    if METADATA_OLD in new_text:
        new_text = new_text.replace(METADATA_OLD, METADATA_NEW, 1)
        changes.append("patch_metadata_failure_continue")
    elif "meta_content: bytes | None = None" in new_text:
        changes.append("metadata_failure_block_already_safe")
    else:
        changes.append("warn_metadata_block_pattern_not_found")

    return new_text, changes


def compile_check(path: Path) -> None:
    import py_compile

    py_compile.compile(str(path), doraise=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch openalex_cli/downloader.py for Windows and safer metadata-only tests.")
    parser.add_argument("--path", default="", help="Explicit path to openalex_cli/downloader.py")
    parser.add_argument("--backup-dir", default="", help="Optional backup directory")
    parser.add_argument("--check", action="store_true", help="Only print patch status")
    parser.add_argument("--dry-run", action="store_true", help="Show intended changes without writing")
    parser.add_argument("--force", action="store_true", help="Attempt to patch even if marker already exists")
    parser.add_argument("--restore-latest", action="store_true", help="Restore latest backup")
    args = parser.parse_args()

    target = Path(args.path).resolve() if args.path else locate_downloader()
    backup_dir = Path(args.backup_dir).resolve() if args.backup_dir else None

    if not target.exists():
        raise FileNotFoundError(target)

    if args.restore_latest:
        backup = latest_backup(target, backup_dir)
        if backup is None:
            print(f"No backup found for {target}")
            return 2
        shutil.copy2(backup, target)
        print(f"Restored {target} from {backup}")
        compile_check(target)
        print("Compile check: OK")
        return 0

    text = target.read_text(encoding="utf-8")
    patched = MARKER in text

    if args.check:
        print(f"Target : {target}")
        print(f"Patched: {patched}")
        print(f"Backup : {latest_backup(target, backup_dir)}")
        return 0

    new_text, changes = patch_text(text, force=args.force)
    changed = new_text != text

    print(f"Target : {target}")
    print("Changes:")
    for c in changes:
        print(f"  - {c}")

    warnings = [c for c in changes if c.startswith("warn_")]
    if warnings:
        print("\nWARNING: Some patterns were not found. The installed package may differ from openalex-official 0.3.3.")

    if not changed:
        print("No file changes needed.")
        return 0

    if args.dry_run:
        print("Dry run only. No file written.")
        return 0

    backup = make_backup(target, backup_dir)
    target.write_text(new_text, encoding="utf-8")
    compile_check(target)
    print(f"Backup written : {backup}")
    print("Patch written  : OK")
    print("Compile check  : OK")
    print("Suggested test : openalex --help")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
