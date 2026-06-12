"""Search tools: file discovery and grep.

GrepTool uses a pluggable backend: a ripgrep-backed implementation when
``rg`` is on ``PATH`` and a pure-Python fallback otherwise. The tool's
external contract (parameters, output format) is unchanged regardless of
which backend is active.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Protocol, TypeVar

from openbot.agent.tools.filesystem import ListDirTool, _FsTool

_DEFAULT_HEAD_LIMIT = 250
_DEFAULT_FILE_HEAD_LIMIT = 200
_RG_TIMEOUT = 30  # seconds
_RG_MAX_COLUMNS = 2000

T = TypeVar("T")

_TYPE_GLOB_MAP = {
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
}


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _match_glob(rel_path: str, name: str, pattern: str) -> bool:
    normalized = _normalize_pattern(pattern)
    if not normalized:
        return False
    if "/" in normalized or normalized.startswith("**"):
        return PurePosixPath(rel_path).match(normalized)
    return fnmatch.fnmatch(name, normalized)


def _is_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    non_text = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return (non_text / len(sample)) > 0.2


def _paginate(items: list[T], limit: int | None, offset: int) -> tuple[list[T], bool]:
    if limit is None:
        return items[offset:], False
    sliced = items[offset : offset + limit]
    truncated = len(items) > offset + limit
    return sliced, truncated


def _pagination_note(limit: int | None, offset: int, truncated: bool) -> str | None:
    if truncated:
        if limit is None:
            return f"(pagination: offset={offset})"
        return f"(pagination: limit={limit}, offset={offset})"
    if offset > 0:
        return f"(pagination: offset={offset})"
    return None


def _matches_type(name: str, file_type: str | None) -> bool:
    if not file_type:
        return True
    lowered = file_type.strip().lower()
    if not lowered:
        return True
    patterns = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


def _matches_query(rel_path: str, query: str | None) -> bool:
    if not query:
        return True
    haystack = rel_path.lower()
    terms = [part for part in query.lower().split() if part]
    return all(term in haystack for term in terms)


# ---------------------------------------------------------------------------
# Grep backends (ripgrep with pure-Python fallback)
# ---------------------------------------------------------------------------


@dataclass
class _Match:
    """A single search result from a grep backend.

    The same shape is used for all output modes:

    - ``output_mode="content"``: one record per matching line; ``line_no``
      and ``text`` are populated.
    - ``output_mode="files_with_matches"``: one record per file; ``line_no``
      is 0 and ``text`` is empty.
    - ``output_mode="count"``: one record per file; ``count`` holds the
      number of matches in that file.
    """

    file: Path
    line_no: int
    text: str
    count: int
    mtime: float
    display_path: str


class _GrepBackend(Protocol):
    """Backend abstraction for GrepTool.

    Backends receive a fully resolved target path and return raw matches.
    Output formatting, pagination, and notes live in ``GrepTool``.
    """

    name: str

    def search(
        self,
        target: Path,
        pattern: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
        output_mode: str,
        glob: str | None,
        type_: str | None,
        context_before: int,
        context_after: int,
        head_limit: int | None,
        offset: int,
    ) -> list[_Match] | tuple[list[_Match], int, int]: ...


class _PythonGrepBackend:
    """Pure-Python grep backend. Default fallback when ripgrep is absent.

    Returns a ``(matches, skipped_binary, skipped_large)`` tuple so the
    caller can surface skip counts in the tool output.
    """

    name = "python"
    _MAX_FILE_BYTES = 2_000_000

    def __init__(
        self,
        *,
        ignore_dirs: set[str],
        display_path: Callable[[Path, Path], str],
    ) -> None:
        self._ignore_dirs = ignore_dirs
        self._display_path = display_path

    def search(
        self,
        target: Path,
        pattern: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
        output_mode: str,
        glob: str | None,
        type_: str | None,
        context_before: int,
        context_after: int,
        head_limit: int | None,
        offset: int,
    ) -> tuple[list[_Match], int, int]:
        del head_limit, offset  # pagination is handled by GrepTool
        flags = re.IGNORECASE if case_insensitive else 0
        try:
            needle = re.escape(pattern) if fixed_strings else pattern
            regex = re.compile(needle, flags)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern: {exc}") from exc

        root = target if target.is_dir() else target.parent
        matches: list[_Match] = []
        skipped_binary = 0
        skipped_large = 0

        for file_path in _iter_search_files(target, self._ignore_dirs):
            rel_path = file_path.relative_to(root).as_posix()
            if glob and not _match_glob(rel_path, file_path.name, glob):
                continue
            if not _matches_type(file_path.name, type_):
                continue

            try:
                raw = file_path.read_bytes()
            except OSError:
                skipped_binary += 1
                continue

            if len(raw) > self._MAX_FILE_BYTES:
                skipped_large += 1
                continue
            if _is_binary(raw):
                skipped_binary += 1
                continue
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped_binary += 1
                continue

            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = 0.0

            display_path = self._display_path(file_path, root)
            lines = content.splitlines()

            if output_mode == "files_with_matches":
                for line in lines:
                    if regex.search(line):
                        matches.append(
                            _Match(
                                file=file_path,
                                line_no=0,
                                text="",
                                count=1,
                                mtime=mtime,
                                display_path=display_path,
                            )
                        )
                        break
            elif output_mode == "count":
                count = sum(1 for line in lines if regex.search(line))
                if count > 0:
                    matches.append(
                        _Match(
                            file=file_path,
                            line_no=0,
                            text="",
                            count=count,
                            mtime=mtime,
                            display_path=display_path,
                        )
                    )
            else:  # content
                for idx, line in enumerate(lines, start=1):
                    if not regex.search(line):
                        continue
                    matches.append(
                        _Match(
                            file=file_path,
                            line_no=idx,
                            text=line,
                            count=0,
                            mtime=mtime,
                            display_path=display_path,
                        )
                    )

        return matches, skipped_binary, skipped_large


class _RipgrepBackend:
    """ripgrep-backed grep backend.

    Faster than the Python backend and supports more encodings (UTF-16,
    GBK, etc.), 50+ file types via ``-t``, and respects ``.gitignore``
    when configured. Constructed only when ``shutil.which("rg")`` returns
    a path; otherwise ``_select_backend`` falls back to the Python
    backend.
    """

    name = "ripgrep"

    def __init__(
        self,
        *,
        rg_path: str,
        ignore_dirs: set[str],
        display_path: Callable[[Path, Path], str],
    ) -> None:
        self._rg = rg_path
        self._ignore_dirs = ignore_dirs
        self._display_path = display_path

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which("rg") is not None

    def _build_cmd(
        self,
        target: Path,
        pattern: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
        output_mode: str,
        glob: str | None,
        type_: str | None,
        context_before: int,
        context_after: int,
    ) -> list[str]:
        cmd: list[str] = [
            self._rg,
            "--json",
            "--no-config",
            "--no-messages",
            "--no-ignore",  # don't honor .gitignore (we use explicit -g)
            "-M",
            str(_RG_MAX_COLUMNS),  # skip lines wider than this
        ]
        if case_insensitive:
            cmd.append("-i")
        if fixed_strings:
            cmd.append("-F")
        if context_before > 0:
            cmd += ["-B", str(context_before)]
        if context_after > 0:
            cmd += ["-A", str(context_after)]
        if glob:
            cmd += ["-g", glob]
        if type_:
            cmd += ["-t", type_]
        for d in sorted(self._ignore_dirs):
            cmd += ["-g", f"!{d}"]
        cmd += ["--", pattern, str(target)]
        del output_mode  # rg's --json format is the same across modes
        return cmd

    def search(
        self,
        target: Path,
        pattern: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
        output_mode: str,
        glob: str | None,
        type_: str | None,
        context_before: int,
        context_after: int,
        head_limit: int | None,
        offset: int,
    ) -> list[_Match]:
        del head_limit, offset  # pagination is handled by GrepTool
        cmd = self._build_cmd(
            target,
            pattern,
            case_insensitive=case_insensitive,
            fixed_strings=fixed_strings,
            output_mode=output_mode,
            glob=glob,
            type_=type_,
            context_before=context_before,
            context_after=context_after,
        )
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_RG_TIMEOUT,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"ripgrep timed out after {_RG_TIMEOUT}s"
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(f"ripgrep binary not found: {exc}") from exc

        # rg exit codes: 0 = matches, 1 = no matches, 2+ = error
        if proc.returncode not in (0, 1):
            stderr = (proc.stderr or "").strip()[:200]
            raise RuntimeError(
                f"ripgrep failed (exit {proc.returncode}): {stderr}"
            )

        return self._parse_output(proc.stdout, target, output_mode)

    def _parse_output(
        self, stdout: str, target: Path, output_mode: str
    ) -> list[_Match]:
        root = target if target.is_dir() else target.parent
        matches: list[_Match] = []
        current_file: str | None = None
        current_path: Path | None = None
        current_display = ""
        current_mtime = 0.0
        file_match_count = 0
        seen_files: set[str] = set()

        for raw_line in stdout.splitlines():
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            data = event.get("data") or {}

            if event_type == "begin":
                path_text = (data.get("path") or {}).get("text", "")
                current_file = path_text
                if path_text:
                    current_path = Path(path_text)
                    try:
                        current_mtime = current_path.stat().st_mtime
                    except OSError:
                        current_mtime = 0.0
                    current_display = self._display_path(current_path, root)
                else:
                    current_path = None
                    current_display = ""
                file_match_count = 0

            elif event_type == "match":
                if not current_path or not current_file:
                    continue
                file_match_count += 1
                if output_mode == "files_with_matches":
                    if current_file not in seen_files:
                        seen_files.add(current_file)
                        matches.append(
                            _Match(
                                file=current_path,
                                line_no=0,
                                text="",
                                count=1,
                                mtime=current_mtime,
                                display_path=current_display,
                            )
                        )
                elif output_mode == "count":
                    # Per-file count is reported in the ``end`` event;
                    # ignore individual match events here.
                    continue
                else:  # content
                    line_no = int(data.get("line_number") or 0)
                    lines_obj = data.get("lines") or {}
                    text = lines_obj.get("text", "") if isinstance(lines_obj, dict) else ""
                    matches.append(
                        _Match(
                            file=current_path,
                            line_no=line_no,
                            text=text.rstrip("\n"),
                            count=0,
                            mtime=current_mtime,
                            display_path=current_display,
                        )
                    )

            elif event_type == "end":
                if (
                    output_mode == "count"
                    and current_path is not None
                    and file_match_count > 0
                ):
                    matches.append(
                        _Match(
                            file=current_path,
                            line_no=0,
                            text="",
                            count=file_match_count,
                            mtime=current_mtime,
                            display_path=current_display,
                        )
                    )
                current_file = None
                current_path = None
                current_display = ""

        return matches


def _select_backend(
    *,
    ignore_dirs: set[str],
    display_path: Callable[[Path, Path], str],
) -> _GrepBackend:
    """Pick the best available backend, falling back to Python if needed."""
    if _RipgrepBackend.is_available():
        rg_path = shutil.which("rg")
        assert rg_path is not None  # type narrowing for type checkers
        return _RipgrepBackend(
            rg_path=rg_path,
            ignore_dirs=ignore_dirs,
            display_path=display_path,
        )
    return _PythonGrepBackend(ignore_dirs=ignore_dirs, display_path=display_path)


def _iter_search_files(root: Path, ignore_dirs: set[str]) -> Iterable[Path]:
    """Walk *root* in deterministic order, skipping *ignore_dirs*."""
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ignore_dirs)
        current = Path(dirpath)
        for filename in sorted(filenames):
            yield current / filename


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------


class _SearchTool(_FsTool):
    _IGNORE_DIRS = set(ListDirTool._IGNORE_DIRS)

    def _display_path(self, target: Path, root: Path) -> str:
        workspace = self._display_workspace()
        if workspace:
            with suppress(ValueError):
                return target.relative_to(workspace).as_posix()
        return target.relative_to(root).as_posix()

    def _iter_files(self, root: Path) -> Iterable[Path]:
        return _iter_search_files(root, self._IGNORE_DIRS)


class FindFilesTool(_SearchTool):
    """Find files by path fragment, glob, or type."""
    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "find_files"

    @property
    def description(self) -> str:
        return (
            "Find files by path fragment, glob, or file type. "
            "Use this before read_file when you need to locate files, and "
            "prefer it over shell find/ls for ordinary workspace discovery. "
            "Returns workspace-relative paths and skips common dependency/build "
            "directories."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default '.')",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive path fragment search. "
                        "Whitespace-separated terms must all be present."
                    ),
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file filter, e.g. '*.py' or 'tests/**/test_*.py'",
                },
                "type": {
                    "type": "string",
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md', 'json'",
                },
                "include_dirs": {
                    "type": "boolean",
                    "description": "Include matching directories as well as files (default false)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["path", "modified"],
                    "description": "Sort by path or most recently modified first (default path)",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Maximum number of paths to return (default 200, 0 for all, max 1000)",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
            },
        }

    def _iter_paths(self, root: Path, *, include_dirs: bool) -> Iterable[Path]:
        if root.is_file():
            yield root
            return
        if include_dirs:
            yield root
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            if include_dirs and current != root:
                yield current
            for filename in sorted(filenames):
                yield current / filename

    async def execute(
        self,
        path: str = ".",
        query: str | None = None,
        glob: str | None = None,
        type: str | None = None,
        include_dirs: bool = False,
        sort: str = "path",
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        try:
            target = self._resolve(path or ".")
            if not target.exists():
                return f"Error: Path not found: {path}"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            if sort not in {"path", "modified"}:
                return "Error: sort must be 'path' or 'modified'"

            limit = (
                _DEFAULT_FILE_HEAD_LIMIT
                if head_limit is None
                else None if head_limit == 0 else head_limit
            )
            root = target if target.is_dir() else target.parent
            matches: list[tuple[str, float]] = []

            for candidate in self._iter_paths(target, include_dirs=include_dirs):
                if candidate.is_dir() and not include_dirs:
                    continue
                rel_path = candidate.relative_to(root).as_posix()
                display_path = self._display_path(candidate, root)
                name = candidate.name

                if glob and not _match_glob(rel_path, name, glob):
                    continue
                if candidate.is_file() and not _matches_type(name, type):
                    continue
                if candidate.is_dir() and type:
                    continue
                if not _matches_query(display_path, query):
                    continue
                try:
                    mtime = candidate.stat().st_mtime
                except OSError:
                    mtime = 0.0
                suffix = "/" if candidate.is_dir() else ""
                matches.append((display_path + suffix, mtime))

            if sort == "modified":
                matches.sort(key=lambda item: (-item[1], item[0]))
            else:
                matches.sort(key=lambda item: item[0])

            paths = [item[0] for item in matches]
            paged, truncated = _paginate(paths, limit, offset)
            if not paged:
                return "No files found"

            result = "\n".join(paged)
            note = _pagination_note(limit, offset, truncated)
            if note:
                result += "\n\n" + note
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error finding files: {e}"


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern.

    Backend selection is automatic: ripgrep is used when ``rg`` is on
    ``PATH``; otherwise a pure-Python backend is used. The external
    contract (parameters, output format) is identical between backends.
    Pass ``backend=`` to ``__init__`` to inject a custom backend (used by
    tests).
    """

    _scopes = {"core", "subagent"}

    _MAX_RESULT_CHARS = 128_000

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        backend: _GrepBackend | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(workspace=workspace, allowed_dir=allowed_dir, **kwargs)
        self._backend: _GrepBackend = backend or _select_backend(
            ignore_dirs=self._IGNORE_DIRS,
            display_path=self._display_path,
        )

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents with a regex pattern. "
            f"Backend: {self.backend_name}. "
            "Default output_mode is files_with_matches (file paths only); "
            "use content mode for matching lines with context. Prefer this "
            "over shell grep for ordinary workspace searches. "
            "Supports glob/type filtering; the python backend skips binary "
            "and files >2 MB."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex or plain text pattern to search for",
                    "minLength": 1,
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default '.')",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file filter, e.g. '*.py' or 'tests/**/test_*.py'",
                },
                "type": {
                    "type": "string",
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md', 'json'",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default false)",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "description": "Treat pattern as plain text instead of regex (default false)",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": (
                        "content: matching lines with optional context; "
                        "files_with_matches: only matching file paths; "
                        "count: matching line counts per file. "
                        "Default: files_with_matches"
                    ),
                },
                "context_before": {
                    "type": "integer",
                    "description": "Number of lines of context before each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "context_after": {
                    "type": "integer",
                    "description": "Number of lines of context after each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "max_matches": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in content mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in files_with_matches or count mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "head_limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of results to return. In content mode this limits "
                        "matching line blocks; in other modes it limits file entries. "
                        "Default 250"
                    ),
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
            },
            "required": ["pattern"],
        }

    # ----- Content-mode formatting -----

    @staticmethod
    def _format_block(
        display_path: str,
        lines: list[str],
        match_line: int,
        before: int,
        after: int,
    ) -> str:
        start = max(1, match_line - before)
        end = min(len(lines), match_line + after)
        block = [f"{display_path}:{match_line}"]
        for line_no in range(start, end + 1):
            marker = ">" if line_no == match_line else " "
            block.append(f"{marker} {line_no}| {lines[line_no - 1]}")
        return "\n".join(block)

    def _format_content(
        self,
        matches: list[_Match],
        *,
        context_before: int,
        context_after: int,
        limit: int | None,
        offset: int,
    ) -> tuple[str, bool, bool]:
        """Format content-mode matches.

        Returns ``(text, truncated, size_truncated)``.
        """
        if not matches:
            return ("", False, False)

        # Cache files: each path → list of lines. Reading the file once
        # is enough even when many matches are in the same file.
        file_cache: dict[Path, list[str]] = {}
        for m in matches:
            if m.file in file_cache:
                continue
            try:
                content = m.file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            file_cache[m.file] = content.splitlines()

        blocks: list[str] = []
        result_chars = 0
        seen = 0
        truncated = False
        size_truncated = False

        for m in matches:
            seen += 1
            if seen <= offset:
                continue
            if limit is not None and len(blocks) >= limit:
                truncated = True
                break
            lines = file_cache.get(m.file, [])
            block = self._format_block(
                m.display_path, lines, m.line_no, context_before, context_after
            )
            extra_sep = 2 if blocks else 0
            if result_chars + extra_sep + len(block) > self._MAX_RESULT_CHARS:
                size_truncated = True
                break
            blocks.append(block)
            result_chars += extra_sep + len(block)

        return ("\n\n".join(blocks), truncated, size_truncated)

    # ----- Files-with-matches and count formatting -----

    @staticmethod
    def _unique_paths_with_mtime(
        matches: list[_Match],
    ) -> dict[str, float]:
        unique: dict[str, float] = {}
        for m in matches:
            if m.display_path not in unique:
                unique[m.display_path] = m.mtime
        return unique

    def _format_files_with_matches(
        self,
        matches: list[_Match],
        *,
        limit: int | None,
        offset: int,
    ) -> tuple[str, bool]:
        unique = self._unique_paths_with_mtime(matches)
        ordered = sorted(unique.keys(), key=lambda n: (-unique[n], n))
        paged, truncated = _paginate(ordered, limit, offset)
        return ("\n".join(paged), truncated)

    def _format_count(
        self,
        matches: list[_Match],
        *,
        limit: int | None,
        offset: int,
    ) -> tuple[str, bool]:
        unique: dict[str, tuple[int, float]] = {}
        for m in matches:
            if m.display_path not in unique:
                unique[m.display_path] = (m.count, m.mtime)
        ordered_files = sorted(unique.keys(), key=lambda n: (-unique[n][1], n))
        paged, truncated = _paginate(ordered_files, limit, offset)
        lines = [f"{name}: {unique[name][0]}" for name in paged]
        return ("\n".join(lines), truncated)

    # ----- Main entry point -----

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        try:
            target = self._resolve(path or ".")
            if not target.exists():
                return f"Error: Path not found: {path}"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT

            try:
                search_result = self._backend.search(
                    target,
                    pattern,
                    case_insensitive=case_insensitive,
                    fixed_strings=fixed_strings,
                    output_mode=output_mode,
                    glob=glob,
                    type_=type,
                    context_before=context_before,
                    context_after=context_after,
                    head_limit=limit,
                    offset=offset,
                )
            except ValueError as exc:
                return f"Error: {exc}"
            except RuntimeError as exc:
                return f"Error: {exc}"
            except Exception as exc:
                return f"Error searching files: {exc}"

            skipped_binary = 0
            skipped_large = 0
            if isinstance(search_result, tuple):
                matches, skipped_binary, skipped_large = search_result
            else:
                matches = search_result

            truncated = False
            size_truncated = False
            if output_mode == "files_with_matches":
                if not matches:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    result, truncated = self._format_files_with_matches(
                        matches, limit=limit, offset=offset
                    )
            elif output_mode == "count":
                if not matches:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    result, truncated = self._format_count(
                        matches, limit=limit, offset=offset
                    )
            else:  # content
                if not matches:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    result, truncated, size_truncated = self._format_content(
                        matches,
                        context_before=context_before,
                        context_after=context_after,
                        limit=limit,
                        offset=offset,
                    )

            notes: list[str] = []
            if output_mode == "content" and truncated:
                notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif output_mode == "content" and size_truncated:
                notes.append("(output truncated due to size)")
            elif truncated and output_mode in {"count", "files_with_matches"}:
                notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif output_mode in {"count", "files_with_matches"} and offset > 0:
                notes.append(f"(pagination: offset={offset})")
            elif output_mode == "content" and offset > 0 and matches:
                notes.append(f"(pagination: offset={offset})")
            if skipped_binary:
                notes.append(
                    f"(skipped {skipped_binary} binary/unreadable files)"
                )
            if skipped_large:
                notes.append(f"(skipped {skipped_large} large files)")
            if output_mode == "count" and matches:
                total = sum(m.count for m in matches)
                notes.append(
                    f"(total matches: {total} in {len(matches)} files)"
                )
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error searching files: {e}"
