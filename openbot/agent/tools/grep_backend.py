"""Grep backends: Python fallback and ripgrep native."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_DEFAULT_HEAD_LIMIT = 250
_MAX_RESULT_CHARS = 128_000
_MAX_FILE_BYTES = 2_000_000
_IGNORE_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", "node_modules",
    ".mypy_cache", ".pytest_cache", ".tox", ".venv", "venv",
    ".eggs", "*.egg-info", "dist", "build", ".next", ".nuxt",
    ".cache", ".parcel-cache", ".sass-cache", "coverage",
    ".terraform", ".vagrant",
})


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GrepMatch:
    """Single structured match returned by backends."""
    path: str
    line_number: int | None = None
    line_text: str | None = None


@dataclass
class GrepResult:
    """Aggregated result from a backend search."""
    matches: list[GrepMatch] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    file_mtimes: dict[str, float] = field(default_factory=dict)
    skipped_binary: int = 0
    skipped_large: int = 0
    total_matches: int = 0
    truncated: bool = False


# ---------------------------------------------------------------------------
# Shared type-glob map
# ---------------------------------------------------------------------------

_TYPE_GLOB_MAP: dict[str, tuple[str, ...]] = {
    "py": ("*.py", "*.pyi"),
    "python": ("*.py", "*.pyi"),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "tsx": ("*.tsx",),
    "jsx": ("*.jsx",),
    "json": ("*.json",),
    "md": ("*.md", "*.mdx"),
    "markdown": ("*.md", "*.mdx"),
    "go": ("*.go",),
    "rs": ("*.rs",),
    "rust": ("*.rs",),
    "java": ("*.java",),
    "sh": ("*.sh", "*.bash"),
    "yaml": ("*.yaml", "*.yml"),
    "yml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",),
    "sql": ("*.sql",),
    "html": ("*.html", "*.htm"),
    "css": ("*.css", "*.scss", "*.sass"),
    "proto": ("*.proto",),
    "vue": ("*.vue",),
    "svelte": ("*.svelte",),
    "astro": ("*.astro",),
    "graphql": ("*.graphql", "*.gql", "*.graphqls"),
    "tf": ("*.tf", "*.tfvars"),
    "dart": ("*.dart",),
    "kt": ("*.kt", "*.kts"),
    "kotlin": ("*.kt", "*.kts"),
    "swift": ("*.swift",),
    "rb": ("*.rb", "*.rake", "*.gemspec"),
    "ruby": ("*.rb", "*.rake", "*.gemspec"),
    "zig": ("*.zig",),
    "lua": ("*.lua",),
    "r": ("*.r", "*.R"),
    "scala": ("*.scala", "*.sc"),
    "haskell": ("*.hs",),
    "ex": ("*.ex", "*.exs"),
    "elixir": ("*.ex", "*.exs"),
    "clj": ("*.clj", "*.cljs", "*.cljc"),
    "clojure": ("*.clj", "*.cljs", "*.cljc"),
    "c": ("*.c", "*.h"),
    "cpp": ("*.cpp", "*.cxx", "*.cc", "*.hpp"),
    "cs": ("*.cs",),
    "csharp": ("*.cs",),
    "xml": ("*.xml",),
    "dockerfile": ("Dockerfile*",),
    "makefile": ("Makefile*", "makefile*", "*.mk"),
    "cmake": ("CMakeLists.txt", "*.cmake"),
    "ipynb": ("*.ipynb",),
}


def _is_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    non_text = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return (non_text / len(sample)) > 0.2


def _matches_type(name: str, file_type: str | None) -> bool:
    if not file_type:
        return True
    lowered = file_type.strip().lower()
    if not lowered:
        return True
    patterns = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _match_glob(rel_path: str, name: str, pattern: str) -> bool:
    normalized = _normalize_pattern(pattern)
    if not normalized:
        return False
    if "/" in normalized or normalized.startswith("**"):
        return PurePosixPath(rel_path).match(normalized)
    return fnmatch.fnmatch(name, normalized)


# ---------------------------------------------------------------------------
# Python backend
# ---------------------------------------------------------------------------

class PythonGrepBackend:
    """Pure-Python grep backend — always available, no external deps."""

    name = "python"

    def is_available(self) -> bool:
        return True

    def search(
        self,
        target: Path,
        pattern: str,
        *,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "content",
        glob: str | None = None,
        type_: str | None = None,
        head_limit: int | None = _DEFAULT_HEAD_LIMIT,
        offset: int = 0,
        max_file_size: int = _MAX_FILE_BYTES,
    ) -> GrepResult:
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(
            re.escape(pattern) if fixed_strings else pattern,
            flags,
        )
        result = GrepResult()
        limit = head_limit
        seen_content_matches = 0
        truncated = False

        for root, dirnames, filenames in os.walk(target):
            dirnames[:] = sorted(
                d for d in dirnames if d not in _IGNORE_DIRS
            )
            for filename in sorted(filenames):
                if type_ and not _matches_type(filename, type_):
                    continue
                file_path = Path(root) / filename
                rel_path = file_path.relative_to(target).as_posix()
                if glob and not _match_glob(rel_path, filename, glob):
                    continue
                seen_content_matches, truncated = self._process_file(
                    file_path, rel_path, regex, output_mode,
                    result, limit, offset, seen_content_matches,
                    max_file_size,
                )
                if truncated:
                    break
            if truncated:
                break

        return result

    def _process_file(
        self, file_path, rel_path, regex, output_mode,
        result, limit, offset, seen_content_matches, max_file_size,
    ) -> tuple[int, bool]:
        truncated = False
        try:
            raw = file_path.read_bytes()
        except (OSError, PermissionError):
            return seen_content_matches, truncated

        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            mtime = 0.0

        if len(raw) > max_file_size:
            result.skipped_large += 1
            return seen_content_matches, truncated
        if _is_binary(raw):
            result.skipped_binary += 1
            return seen_content_matches, truncated
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            result.skipped_binary += 1
            return seen_content_matches, truncated

        lines = content.splitlines()
        file_had_match = False

        for idx, line in enumerate(lines, start=1):
            if not regex.search(line):
                continue
            file_had_match = True

            if output_mode == "count":
                result.counts[rel_path] = result.counts.get(rel_path, 0) + 1
                continue
            if output_mode == "files_with_matches":
                if rel_path not in result.counts:
                    result.matches.append(GrepMatch(path=rel_path))
                    result.file_mtimes[rel_path] = mtime
                break

            seen_content_matches += 1
            result.total_matches += 1
            if seen_content_matches <= offset:
                continue
            if limit is not None and len(result.matches) >= limit:
                result.truncated = True
                return seen_content_matches, True

            result.matches.append(GrepMatch(
                path=rel_path,
                line_number=idx,
                line_text=line,
            ))
            result.file_mtimes[rel_path] = mtime

        if output_mode == "count" and file_had_match:
            if rel_path not in result.counts:
                result.counts[rel_path] = 0
        if output_mode in {"count", "files_with_matches"} and file_had_match:
            if rel_path not in result.file_mtimes:
                result.file_mtimes[rel_path] = mtime

        return seen_content_matches, truncated


# ---------------------------------------------------------------------------
# Ripgrep backend
# ---------------------------------------------------------------------------

# Pattern for rg --no-heading --with-filename --line-number output:
#   <filepath>:<linenum>:<text>
# The text may contain colons, so we match from the right.
_RG_CONTENT_RE = re.compile(r"^(.+):(\d+):(.*)$")


class RipgrepBackend:
    """Native ripgrep backend — fast, encoding-aware, handles large files."""

    name = "ripgrep"

    def __init__(self) -> None:
        self._rg_path: str | None = shutil.which("rg")

    def is_available(self) -> bool:
        return self._rg_path is not None

    def search(
        self,
        target: Path,
        pattern: str,
        *,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "content",
        glob: str | None = None,
        type_: str | None = None,
        head_limit: int | None = _DEFAULT_HEAD_LIMIT,
        offset: int = 0,
        max_file_size: int = _MAX_FILE_BYTES,
    ) -> GrepResult:
        if not self._rg_path:
            raise FileNotFoundError("ripgrep (rg) not installed")

        cmd: list[str] = [
            self._rg_path,
            "--no-config",
            "--no-heading",
            "--with-filename",
            "--line-number",
            "--no-ignore",
            "--sort-files",
            "--max-columns", "500",
            "--max-columns-preview",
        ]
        for d in _IGNORE_DIRS:
            cmd += ["--glob", f"!{d}/"]

        if case_insensitive:
            cmd.append("-i")
        if fixed_strings:
            cmd.append("-F")

        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")

        if glob:
            cmd += ["-g", glob]
        if type_:
            cmd += ["-t", type_]

        cmd.append(pattern)
        cmd.append(str(target))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return GrepResult(truncated=True)
        except FileNotFoundError:
            return GrepResult()

        result = GrepResult()
        output = proc.stdout
        if not output.strip():
            return result

        lines = output.splitlines()
        seen_content_matches = 0
        limit = head_limit

        for line in lines:
            if not line.strip():
                continue

            if output_mode == "files_with_matches":
                display = self._rel_from_target(line, target)
                result.matches.append(GrepMatch(path=display))
                # Stat file for mtime
                abs_path = target / display if target.is_dir() else target
                with suppress(OSError):
                    result.file_mtimes[display] = abs_path.stat().st_mtime
                continue

            if output_mode == "count":
                sep_idx = line.rfind(":")
                if sep_idx > 0:
                    fpath = self._rel_from_target(line[:sep_idx], target)
                    try:
                        cnt = int(line[sep_idx + 1:])
                    except ValueError:
                        cnt = 0
                    result.counts[fpath] = cnt
                    result.total_matches += cnt
                continue

            # content mode — parse with regex to handle colons in text
            m = _RG_CONTENT_RE.match(line)
            if m is None:
                continue

            filepath_raw, linenum_str, text = m.groups()
            try:
                line_num = int(linenum_str)
            except ValueError:
                continue

            display = self._rel_from_target(filepath_raw, target)
            parsed = GrepMatch(
                path=display,
                line_number=line_num,
                line_text=text,
            )

            seen_content_matches += 1
            if seen_content_matches <= offset:
                continue
            if limit is not None and len(result.matches) >= limit:
                result.truncated = True
                break

            result.matches.append(parsed)
            # Stat file for mtime
            abs_path = target / display if target.is_dir() else target
            with suppress(OSError):
                result.file_mtimes[display] = abs_path.stat().st_mtime

        return result

    @staticmethod
    def _rel_from_target(path_str: str, target: Path) -> str:
        """Make path relative to target directory."""
        target_prefix = str(target)
        if path_str.startswith(target_prefix):
            rel = path_str[len(target_prefix):]
            return rel.lstrip("/") or "."
        # Handle file targets: rg may output just the filename
        if target.is_file():
            # rg outputs just the filename for file targets
            return Path(path_str).name
        # Path might already be relative
        if path_str.startswith("./"):
            return path_str[2:]
        return path_str
