"""Patch openalex-official on native Windows for asyncio signal handler compatibility.

Why this patch exists
---------------------
openalex-official 0.3.x calls ``loop.add_signal_handler(...)`` inside
``openalex_cli/downloader.py``. On native Windows event loops this API raises
``NotImplementedError``. As a result, ``openalex download`` can fail before any
metadata file is downloaded.

This script patches the installed ``openalex_cli/downloader.py`` in-place by:
1. Wrapping ``loop.add_signal_handler(...)`` with try/except.
2. Tracking only successfully registered signals.
3. Wrapping ``loop.remove_signal_handler(...)`` during cleanup.
4. Creating a timestamped backup before modifying the file.

Usage
-----
Check current status:
    python patch_openalex_windows.py --check

Patch installed openalex_cli:
    python patch_openalex_windows.py

Dry run:
    python patch_openalex_windows.py --dry-run

Restore latest backup:
    python patch_openalex_windows.py --restore-latest

Patch a specific downloader.py path:
    python patch_openalex_windows.py --path "C:\\...\\site-packages\\openalex_cli\\downloader.py"
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

PATCH_MARKER = "WINDOWS_SIGNAL_HANDLER_PATCH"

SETUP_PATTERN = re.compile(
    r"(?P<indent>[ \t]*)# Set up signal handlers for graceful shutdown\n"
    r"(?P=indent)loop = asyncio\.get_running_loop\(\)\n"
    r"(?P=indent)for sig in \(signal\.SIGINT, signal\.SIGTERM\):\n"
    r"(?P=indent)[ \t]+loop\.add_signal_handler\(sig, self\._request_shutdown\)\n",
)

CLEANUP_PATTERN = re.compile(
    r"(?P<indent>[ \t]*)# Remove signal handlers\n"
    r"(?P=indent)for sig in \(signal\.SIGINT, signal\.SIGTERM\):\n"
    r"(?P=indent)[ \t]+loop\.remove_signal_handler\(sig\)\n",
)


def resolve_downloader_path(path_arg: str | None) -> Path:
    """Resolve the target downloader.py path."""
    if path_arg:
        path = Path(path_arg).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Target file does not exist: {path}")
        return path

    spec = importlib.util.find_spec("openalex_cli")
    if spec is None or spec.origin is None:
        raise RuntimeError(
            "Cannot find installed package 'openalex_cli'. Install it first with:\n"
            "    pip install openalex-official"
        )

    package_dir = Path(spec.origin).resolve().parent
    path = package_dir / "downloader.py"
    if not path.exists():
        raise FileNotFoundError(f"Cannot find downloader.py under: {package_dir}")
    return path


def is_patched(text: str) -> bool:
    """Return True if the patch marker is already present."""
    return PATCH_MARKER in text


def build_replacements(text: str) -> tuple[str, int]:
    """Apply regex-based replacements and return patched text plus patch count."""
    patch_count = 0

    def replace_setup(match: re.Match[str]) -> str:
        nonlocal patch_count
        patch_count += 1
        indent = match.group("indent")
        inner = indent + "    "
        inner2 = indent + "        "
        return (
            f"{indent}# Set up signal handlers for graceful shutdown\n"
            f"{indent}# {PATCH_MARKER}: Windows event loops do not implement\n"
            f"{indent}# asyncio loop.add_signal_handler(...). Ignore that case so\n"
            f"{indent}# the downloader can continue on native Windows.\n"
            f"{indent}loop = asyncio.get_running_loop()\n"
            f"{indent}registered_signals: list[signal.Signals] = []\n"
            f"{indent}for sig in (signal.SIGINT, signal.SIGTERM):\n"
            f"{inner}try:\n"
            f"{inner2}loop.add_signal_handler(sig, self._request_shutdown)\n"
            f"{inner2}registered_signals.append(sig)\n"
            f"{inner}except (NotImplementedError, RuntimeError):\n"
            f"{inner2}# Native Windows does not support asyncio signal handlers.\n"
            f"{inner2}pass\n"
        )

    def replace_cleanup(match: re.Match[str]) -> str:
        nonlocal patch_count
        patch_count += 1
        indent = match.group("indent")
        inner = indent + "    "
        inner2 = indent + "        "
        return (
            f"{indent}# Remove signal handlers\n"
            f"{indent}for sig in registered_signals:\n"
            f"{inner}try:\n"
            f"{inner2}loop.remove_signal_handler(sig)\n"
            f"{inner}except (NotImplementedError, RuntimeError, ValueError):\n"
            f"{inner2}pass\n"
        )

    text = SETUP_PATTERN.sub(replace_setup, text, count=1)
    text = CLEANUP_PATTERN.sub(replace_cleanup, text, count=1)
    return text, patch_count


def backup_file(path: Path, backup_dir: Path | None = None) -> Path:
    """Create a timestamped backup and return the backup path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if backup_dir is None:
        backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
    else:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{path.name}.bak.{timestamp}"
    shutil.copy2(path, backup_path)
    return backup_path


def find_latest_backup(path: Path, backup_dir: Path | None = None) -> Path | None:
    """Find latest timestamped backup for the target file."""
    if backup_dir is None:
        directory = path.parent
    else:
        directory = backup_dir

    backups = sorted(directory.glob(f"{path.name}.bak.*"), key=lambda p: p.stat().st_mtime)
    return backups[-1] if backups else None


def patch_file(
    path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    backup_dir: Path | None = None,
) -> int:
    """Patch target file. Return process-style exit code."""
    text = path.read_text(encoding="utf-8")

    if is_patched(text) and not force:
        print(f"[OK] Already patched: {path}")
        return 0

    patched_text, patch_count = build_replacements(text)

    if patch_count < 2:
        print(f"[ERROR] Patch target pattern not fully found in: {path}")
        print(f"        Applied replacement count: {patch_count}/2")
        print("        The installed package may have changed. Use --path to patch manually")
        print("        or inspect downloader.py around add_signal_handler/remove_signal_handler.")
        return 2

    if patched_text == text:
        print(f"[OK] No changes needed: {path}")
        return 0

    if dry_run:
        print(f"[DRY-RUN] Would patch: {path}")
        print(f"[DRY-RUN] Replacement count: {patch_count}")
        return 0

    backup_path = backup_file(path, backup_dir=backup_dir)
    path.write_text(patched_text, encoding="utf-8")

    print(f"[OK] Patched: {path}")
    print(f"[OK] Backup : {backup_path}")
    return 0


def check_file(path: Path) -> int:
    """Check whether target file is patched and whether old patterns remain."""
    text = path.read_text(encoding="utf-8")
    patched = is_patched(text)
    has_old_setup = SETUP_PATTERN.search(text) is not None
    has_old_cleanup = CLEANUP_PATTERN.search(text) is not None

    print(f"Target file      : {path}")
    print(f"Patch marker     : {'YES' if patched else 'NO'}")
    print(f"Old setup block  : {'YES' if has_old_setup else 'NO'}")
    print(f"Old cleanup block: {'YES' if has_old_cleanup else 'NO'}")

    if patched and not has_old_setup and not has_old_cleanup:
        print("Status           : PATCHED")
        return 0

    if has_old_setup or has_old_cleanup:
        print("Status           : NEEDS PATCH")
        return 1

    print("Status           : UNKNOWN STRUCTURE")
    return 2


def restore_latest(path: Path, backup_dir: Path | None = None) -> int:
    """Restore latest backup for target file."""
    latest = find_latest_backup(path, backup_dir=backup_dir)
    if latest is None:
        print(f"[ERROR] No backup found for: {path}")
        return 2

    shutil.copy2(latest, path)
    print(f"[OK] Restored: {path}")
    print(f"[OK] From    : {latest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch openalex-official downloader.py for native Windows asyncio signal handling."
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Explicit path to openalex_cli/downloader.py. Defaults to installed package path.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check whether the file is patched.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be patched without modifying the file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force patch attempt even if patch marker already exists.",
    )
    parser.add_argument(
        "--restore-latest",
        action="store_true",
        help="Restore the latest backup created by this script.",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Optional directory for backups. Defaults to same directory as downloader.py.",
    )

    args = parser.parse_args()

    try:
        path = resolve_downloader_path(args.path)
        backup_dir = Path(args.backup_dir).expanduser().resolve() if args.backup_dir else None

        if args.check:
            return check_file(path)

        if args.restore_latest:
            return restore_latest(path, backup_dir=backup_dir)

        return patch_file(
            path,
            dry_run=args.dry_run,
            force=args.force,
            backup_dir=backup_dir,
        )

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
