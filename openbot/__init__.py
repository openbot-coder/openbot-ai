"""
openbot - A lightweight AI agent framework
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    text = pyproject.read_text(encoding="utf-8")
    # Minimal TOML version extraction without requiring tomllib/tomli.
    # Handles: version = "x.y.z" and [project] version = "x.y.z"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version"):
            # e.g. version = "0.2.1"
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                val = parts[1].strip().strip("\"'")
                if val:
                    return val
    return None


def _resolve_version() -> str:
    try:
        return _pkg_version("openbot-ai")
    except PackageNotFoundError:
        # Source checkouts often import openbot without installed dist-info.
        return _read_pyproject_version() or "0.2.1"


__version__ = _resolve_version()
__logo__ = "🐈"

_LAZY_EXPORTS = {
    "openbot": ".sdk",
    "RunResult": ".sdk",
}


def __getattr__(name: str):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    mod = import_module(module_path, __name__)
    val = getattr(mod, name)
    globals()[name] = val
    return val


__all__ = ["openbot", "RunResult"]
