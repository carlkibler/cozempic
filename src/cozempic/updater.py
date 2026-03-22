"""Auto-update: check PyPI once per day and upgrade in-place if a newer version is available."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import __version__

_PYPI_URL = "https://pypi.org/pypi/cozempic/json"
_COUNTER_URL = "https://api.counterapi.dev/v1/cozempic/auto-updates/up"
_INSTALL_COUNTER_URL = "https://api.counterapi.dev/v1/cozempic/installs/up"
_CHECK_INTERVAL = 86400  # 24 hours
_CACHE_FILE = Path.home() / ".cozempic_update_check"
_INSTALL_SENTINEL = Path.home() / ".cozempic_installed"


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _get_latest_version() -> str | None:
    try:
        req = Request(_PYPI_URL, headers={"User-Agent": f"cozempic/{__version__}"})
        with urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read())
        return data["info"]["version"]
    except Exception:
        return None


def _should_check() -> bool:
    try:
        if _CACHE_FILE.exists():
            last = float(_CACHE_FILE.read_text().strip())
            if time.time() - last < _CHECK_INTERVAL:
                return False
    except Exception:
        pass
    return True


def _mark_checked() -> None:
    try:
        _CACHE_FILE.write_text(str(time.time()))
    except Exception:
        pass


def _do_upgrade(latest: str) -> bool:
    """Run pip install cozempic==<latest>. Returns True on success."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", f"cozempic=={latest}", "--quiet", "--disable-pip-version-check"],
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def ping_install_if_new() -> None:
    """Ping the install counter once on first ever run. Silent no-op after that."""
    if _INSTALL_SENTINEL.exists():
        return
    try:
        _INSTALL_SENTINEL.write_text(__version__)
        urlopen(Request(_INSTALL_COUNTER_URL, headers={"User-Agent": f"cozempic/{__version__}"}), timeout=3)
    except Exception:
        pass


def maybe_auto_update() -> None:
    """Check PyPI and auto-update cozempic if a newer version is available.

    Throttled to one check per 24 hours. No-ops silently on network failures.
    Only runs when stdout is a TTY (not in piped/CI contexts).
    """
    if not sys.stdout.isatty():
        return
    if not _should_check():
        return

    _mark_checked()

    latest = _get_latest_version()
    if latest is None:
        return
    if _version_tuple(latest) <= _version_tuple(__version__):
        return

    print(f"  Updating cozempic {__version__} → {latest}...", flush=True)
    if _do_upgrade(latest):
        try:
            urlopen(Request(_COUNTER_URL, headers={"User-Agent": f"cozempic/{latest}"}), timeout=3)
        except Exception:
            pass
        print(f"  Updated to v{latest}. ✨ Self-updating now, atomic writes, strict session guard, zero false positives on team detection.", flush=True)
        print(f"  Restart cozempic to use the new version.", flush=True)
    else:
        print(f"  Auto-update failed. Run: pip install --upgrade cozempic", flush=True)
