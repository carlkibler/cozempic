"""Auto-update: check PyPI once per day and upgrade in-place if a newer version is available."""

from __future__ import annotations

import json
import os
import shutil
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
    """Try to upgrade cozempic. Tries uv first (fast, common), then pip."""
    # Try uv pip install first (works in uv-managed environments)
    if shutil.which("uv"):
        try:
            result = subprocess.run(
                ["uv", "pip", "install", f"cozempic=={latest}", "--quiet"],
                capture_output=True,
                timeout=60,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    # Try pip via sys.executable (works in pip-managed venvs)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", f"cozempic=={latest}",
             "--quiet", "--disable-pip-version-check"],
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass

    # Try bare pip (works when pip is on PATH but not in current venv)
    if shutil.which("pip"):
        try:
            result = subprocess.run(
                ["pip", "install", f"cozempic=={latest}",
                 "--quiet", "--disable-pip-version-check"],
                capture_output=True,
                timeout=60,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    # Try pipx upgrade (for pipx-installed users)
    if shutil.which("pipx"):
        try:
            result = subprocess.run(
                ["pipx", "upgrade", "cozempic"],
                capture_output=True,
                timeout=60,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


def ping_install_if_new() -> None:
    """Ping counters once per installed version.

    Re-pings when the version in the sentinel doesn't match the running version.
    If the sentinel existed with a DIFFERENT version (upgrade, not first install),
    also pings the auto-update counter — this catches upgrades from the SessionStart
    hook and npm install.js which bypass the Python auto-updater.
    """
    try:
        is_upgrade = False
        if _INSTALL_SENTINEL.exists():
            old_version = _INSTALL_SENTINEL.read_text().strip()
            if old_version == __version__:
                return
            # Sentinel exists with different version = upgrade (not first install)
            is_upgrade = bool(old_version)
        _INSTALL_SENTINEL.write_text(__version__)
        if os.environ.get("COZEMPIC_NO_TELEMETRY"):
            return
        urlopen(Request(_INSTALL_COUNTER_URL, headers={"User-Agent": f"cozempic/{__version__}"}), timeout=3)
        if is_upgrade:
            urlopen(Request(_COUNTER_URL, headers={"User-Agent": f"cozempic/{__version__}"}), timeout=3)
    except Exception:
        pass


def maybe_auto_update(force: bool = False, silent: bool = False) -> None:
    """Check PyPI and auto-update cozempic if a newer version is available.

    Throttled to one check per 24 hours. No-ops silently on network failures.

    Args:
        force: Bypass the TTY check (for guard daemon and MCP server startup).
        silent: Suppress all output (required for MCP context where stdout is the protocol stream).

    Set COZEMPIC_NO_AUTO_UPDATE=1 to disable all automatic upgrade behaviour.
    """
    if os.environ.get("COZEMPIC_NO_AUTO_UPDATE"):
        return
    # Removed TTY check — auto-update should work from hooks, daemons, and CLI.
    # The 24h throttle and silent mode are sufficient controls.
    if not _should_check():
        return

    _mark_checked()

    latest = _get_latest_version()
    if latest is None:
        return
    if _version_tuple(latest) <= _version_tuple(__version__):
        return

    if not silent:
        print(f"  Cozempic: updating {__version__} → {latest}...", flush=True)
    if _do_upgrade(latest):
        if not os.environ.get("COZEMPIC_NO_TELEMETRY"):
            try:
                urlopen(Request(_COUNTER_URL, headers={"User-Agent": f"cozempic/{latest}"}), timeout=3)
            except Exception:
                pass
        if not silent:
            print(f"  Cozempic: updated to v{latest}.", flush=True)
    else:
        if not silent:
            print(f"  Cozempic: auto-update failed. Run: pip install --upgrade cozempic", flush=True)
