"""Search tools: file discovery and grep."""

from __future__ import annotations

import fnmatch
import os
import re
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, TypeVar

from openbot.agent.tools.filesystem import ListDirTool, _FsTool
from openbot.agent.tools.grep_backend import (
    GrepMatch,
    GrepResult,
    PythonGrepBackend,
    RipgrepBackend,
)
from openbot.security.workspace_policy import WorkspaceBoundaryError

_DEFAULT_HEAD_LIMIT = 250
_DEFAULT_FILE_HEAD_LIMIT = 200
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


def _paginate(items: list[T], limit: int | None, offset: int) -> tuple[list[T], bool]:
    if limit is None:
        return items[offset:], False
    sliced = items[offset : offset + limit]
    truncated = len(items) > offset + limit
    return sliced, truncated


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


class _SearchTool(_FsTool):
    _IGNORE_DIRS = set(ListDirTool._IGNORE_DIRS)

    def _display_path(self, target: Path, root: Path) -> str:
        workspace = self._display_workspace()
        if workspace:
            with suppress(ValueError):
                return target.relative_to(workspace).as_posix()
        return target.relative_to(root).as_posix()

    def _iter_files(self, root: Path) -> Iterable[Path]:
        if root.is_file():
            yield root
            return

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            for filename in sorted(filenames):
                yield current / filename


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
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md'",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 200)",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
                "include_dirs": {
                    "type": "boolean",
                    "description": "Include directories in results (default false)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["name", "modified"],
                    "description": "Sort order: 'name' (default) or 'modified' (mtime desc)",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        path: str = ".",
        query: str | None = None,
        glob: str | None = None,
        type: str | None = None,
        head_limit: int | None = _DEFAULT_FILE_HEAD_LIMIT,
        offset: int = 0,
        include_dirs: bool = False,
        sort: str | None = None,
    ) -> str:
        try:
            target = self._resolve(path)
        except WorkspaceBoundaryError as e:
            return f"Error: {e}"
        if not target.exists():
            return f"Error: {target} does not exist"
        if target.is_file():
            return target.name

        entries: list[tuple[str, float]] = []

        if include_dirs:
            # Walk directories and files separately
            for dirpath, dirnames, filenames in os.walk(target):
                dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
                current = Path(dirpath)
                # Add subdirectories
                for dirname in dirnames:
                    dir_entry = current / dirname
                    rel_path = dir_entry.relative_to(target).as_posix() + "/"
                    if not _matches_query(rel_path, query):
                        continue
                    try:
                        mtime = dir_entry.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    entries.append((self._display_path(dir_entry, target) + "/", mtime))
                # Add files
                for filename in sorted(filenames):
                    file_path = current / filename
                    rel_path = file_path.relative_to(target).as_posix()
                    if not _matches_query(rel_path, query):
                        continue
                    if glob and not _match_glob(rel_path, filename, glob):
                        continue
                    if not _matches_type(filename, type):
                        continue
                    try:
                        mtime = file_path.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    entries.append((self._display_path(file_path, target), mtime))
        else:
            for file_path in self._iter_files(target):
                rel_path = file_path.relative_to(target).as_posix()
                name = file_path.name
                if not _matches_query(rel_path, query):
                    continue
                if glob and not _match_glob(rel_path, name, glob):
                    continue
                if not _matches_type(name, type):
                    continue
                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                entries.append((self._display_path(file_path, target), mtime))

        # Sort
        if sort == "modified":
            entries.sort(key=lambda e: (-e[1], e[0]))
        else:
            entries.sort(key=lambda e: e[0])

        matches = [e[0] for e in entries]

        if not matches:
            return f"No files found matching criteria in {path}"

        paged, truncated = _paginate(matches, head_limit, offset)
        result = "\n".join(paged)
        notes: list[str] = []
        if truncated:
            notes.append(f"(pagination: limit={head_limit}, offset={offset})")
        elif offset > 0:
            notes.append(f"(pagination: offset={offset})")
        if notes:
            result += "\n\n" + "\n".join(notes)
        return result


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern."""
    _scopes = {"core", "subagent"}

    _MAX_RESULT_CHARS = 128_000
    _MAX_FILE_BYTES = 2_000_000

    # Backend singletons (created once)
    _ripgrep_backend = RipgrepBackend()
    _python_backend = PythonGrepBackend()

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents with a regex pattern. "
            "Default output_mode is files_with_matches (file paths only); "
            "use content mode for matching lines with context. Prefer this "
            "over shell grep for ordinary workspace searches. "
            "Skips binary and files >2 MB. Supports glob/type filtering."
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
                    "description": "Legacy alias for head_limit in content mode",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Legacy alias for head_limit in files_with_matches or count mode",
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

    def _select_backend(self):
        """Select the best available grep backend.

        Use ripgrep when available, unless _MAX_FILE_BYTES has been
        overridden (e.g. by tests), in which case fall back to Python
        so skip counts and binary detection work as expected.
        """
        if self._MAX_FILE_BYTES != 2_000_000:
            return self._python_backend
        if self._ripgrep_backend.is_available():
            return self._ripgrep_backend
        return self._python_backend

    def _workspace_rel(self, target: Path) -> str:
        """Compute the relative prefix from workspace to target."""
        workspace = self._display_workspace()
        if workspace:
            with suppress(ValueError):
                return target.relative_to(workspace).as_posix()
        return "."

    def _prefix_path(self, rel: str, prefix: str) -> str:
        """Prefix a backend-relative path with the workspace→target prefix."""
        if not prefix or prefix == ".":
            return rel
        if rel == "." or rel == "./":
            return prefix
        return f"{prefix}/{rel}"

    def _format_grep_result(
        self,
        pattern: str,
        path: str,
        output_mode: str,
        gre: GrepResult,
        context_before: int,
        context_after: int,
        limit: int | None,
        offset: int,
        path_prefix: str,
    ) -> str:
        """Format a GrepResult into the display string expected by the tool."""

        def _pf(p: str) -> str:
            return self._prefix_path(p, path_prefix)

        def _append_skip_notes(base: str) -> str:
            notes: list[str] = []
            if gre.skipped_binary:
                notes.append(f"(skipped {gre.skipped_binary} binary/unreadable files)")
            if gre.skipped_large:
                notes.append(f"(skipped {gre.skipped_large} large files)")
            if notes:
                base += "\n\n" + "\n".join(notes)
            return base

        if output_mode == "files_with_matches":
            if not gre.matches:
                return _append_skip_notes(f"No matches found for pattern '{pattern}' in {path}")
            ordered = sorted(
                gre.matches,
                key=lambda m: (-gre.file_mtimes.get(m.path, 0.0), m.path),
            )
            paged, truncated = _paginate([_pf(m.path) for m in ordered], limit, offset)
            result = "\n".join(paged)
            notes: list[str] = []
            if truncated:
                notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif offset > 0:
                notes.append(f"(pagination: offset={offset})")
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result

        if output_mode == "count":
            if not gre.counts:
                return f"No matches found for pattern '{pattern}' in {path}"
            ordered = sorted(
                gre.counts.keys(),
                key=lambda name: (-gre.file_mtimes.get(name, 0.0), name),
            )
            paged, truncated = _paginate(ordered, limit, offset)
            lines = [f"{_pf(name)}: {gre.counts[name]}" for name in paged]
            result = "\n".join(lines)
            notes_count: list[str] = []
            if truncated:
                notes_count.append(f"(pagination: limit={limit}, offset={offset})")
            elif offset > 0:
                notes_count.append(f"(pagination: offset={offset})")
            notes_count.append(
                f"(total matches: {gre.total_matches} in {len(gre.counts)} files)"
            )
            result += "\n\n" + "\n".join(notes_count)
            return result

        # content mode
        if not gre.matches:
            return f"No matches found for pattern '{pattern}' in {path}"

        blocks: list[str] = []
        result_chars = 0
        size_truncated = False

        for m in gre.matches:
            if m.line_number is None or m.line_text is None:
                continue
            display_path = _pf(m.path)
            # Read file for context lines
            file_path = Path(path) / m.path
            if not file_path.exists():
                file_path = self._resolve(m.path)
            if file_path.exists():
                try:
                    raw = file_path.read_bytes()
                    if len(raw) <= self._MAX_FILE_BYTES:
                        file_lines = raw.decode("utf-8", errors="replace").splitlines()
                    else:
                        file_lines = [m.line_text]
                except (OSError, PermissionError):
                    file_lines = [m.line_text]
            else:
                file_lines = [m.line_text]

            block = self._format_block(
                display_path,
                file_lines,
                m.line_number,
                context_before,
                context_after,
            )
            extra_sep = 2 if blocks else 0
            if result_chars + extra_sep + len(block) > self._MAX_RESULT_CHARS:
                size_truncated = True
                break
            blocks.append(block)
            result_chars += extra_sep + len(block)

        if not blocks:
            return f"No matches found for pattern '{pattern}' in {path}"

        result = "\n\n".join(blocks)
        notes_content: list[str] = []
        if gre.truncated:
            notes_content.append(f"(pagination: limit={limit}, offset={offset})")
        elif size_truncated:
            notes_content.append("(output truncated due to size)")
        elif offset > 0:
            notes_content.append(f"(pagination: offset={offset})")
        if gre.skipped_binary:
            notes_content.append(f"(skipped {gre.skipped_binary} binary/unreadable files)")
        if gre.skipped_large:
            notes_content.append(f"(skipped {gre.skipped_large} large files)")
        if notes_content:
            result += "\n\n" + "\n".join(notes_content)
        return result

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
    ) -> str:
        try:
            target = self._resolve(path)
            if not target.exists():
                return f"Error: {target} does not exist"

            if head_limit is None:
                if output_mode == "content":
                    head_limit = max_matches
                else:
                    head_limit = max_results
            if head_limit is None:
                head_limit = _DEFAULT_HEAD_LIMIT

            path_prefix = self._workspace_rel(target)

            backend = self._select_backend()

            try:
                gre = backend.search(
                    target,
                    pattern,
                    case_insensitive=case_insensitive,
                    fixed_strings=fixed_strings,
                    output_mode=output_mode,
                    glob=glob,
                    type_=type,
                    head_limit=head_limit,
                    offset=offset,
                    max_file_size=self._MAX_FILE_BYTES,
                )
            except FileNotFoundError:
                if backend is not self._python_backend:
                    gre = self._python_backend.search(
                        target,
                        pattern,
                        case_insensitive=case_insensitive,
                        fixed_strings=fixed_strings,
                        output_mode=output_mode,
                        glob=glob,
                        type_=type,
                        head_limit=head_limit,
                        offset=offset,
                        max_file_size=self._MAX_FILE_BYTES,
                    )
                else:
                    raise

            return self._format_grep_result(
                pattern, path, output_mode, gre,
                context_before, context_after, head_limit, offset,
                path_prefix,
            )
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error searching files: {e}"
