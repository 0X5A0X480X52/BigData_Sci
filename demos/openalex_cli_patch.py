from pathlib import Path
import openalex_cli

pkg_dir = Path(openalex_cli.__file__).resolve().parent
path = pkg_dir / "downloader.py"

text = path.read_text(encoding="utf-8")

old = """        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_shutdown)
"""

new = """        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                # Windows does not support asyncio signal handlers.
                # Continue without graceful signal handling.
                pass
"""

if old not in text:
    print("Patch target not found. Please open manually:", path)
else:
    backup = path.with_suffix(".py.bak")
    backup.write_text(text, encoding="utf-8")
    path.write_text(text.replace(old, new), encoding="utf-8")
    print("Patched:", path)
    print("Backup :", backup)