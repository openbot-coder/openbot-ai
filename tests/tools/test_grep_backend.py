"""Tests for grep backends (PythonGrepBackend & RipgrepBackend) and backend selection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openbot.agent.tools.grep_backend import (
    GrepMatch,
    GrepResult,
    PythonGrepBackend,
    RipgrepBackend,
    _TYPE_GLOB_MAP,
    _is_binary,
    _match_glob,
    _matches_type,
    _normalize_pattern,
)
from openbot.agent.tools.search import GrepTool


# ---------------------------------------------------------------------------
# Shared dataclass / helper tests
# ---------------------------------------------------------------------------

class TestGrepMatch:
    def test_defaults(self) -> None:
        m = GrepMatch(path="a.py")
        assert m.path == "a.py"
        assert m.line_number is None
        assert m.line_text is None

    def test_full(self) -> None:
        m = GrepMatch(path="src/b.py", line_number=5, line_text="hello")
        assert m.line_number == 5
        assert m.line_text == "hello"


class TestGrepResult:
    def test_defaults(self) -> None:
        r = GrepResult()
        assert r.matches == []
        assert r.counts == {}
        assert r.file_mtimes == {}
        assert r.skipped_binary == 0
        assert r.skipped_large == 0
        assert r.total_matches == 0
        assert r.truncated is False


# ---------------------------------------------------------------------------
# _is_binary
# ---------------------------------------------------------------------------

class TestIsBinary:
    def test_empty_bytes_not_binary(self) -> None:
        assert _is_binary(b"") is False

    def test_null_byte_is_binary(self) -> None:
        assert _is_binary(b"\x00\x01\x02") is True

    def test_normal_text_not_binary(self) -> None:
        assert _is_binary(b"hello world\n") is False

    def test_high_non_text_ratio_is_binary(self) -> None:
        # 50% control chars (< 9 or 13 < byte < 32) → > 0.2 threshold
        data = bytes([1, 2, 3, 4, 5] + list(b"abcde"))
        assert _is_binary(data) is True

    def test_low_non_text_ratio_not_binary(self) -> None:
        # 10% control chars → < 0.2 threshold
        data = bytes([1]) + b"a" * 9
        assert _is_binary(data) is False


# ---------------------------------------------------------------------------
# _matches_type
# ---------------------------------------------------------------------------

class TestMatchesType:
    def test_none_type_matches_everything(self) -> None:
        assert _matches_type("anything.txt", None) is True

    def test_empty_string_matches_everything(self) -> None:
        assert _matches_type("anything.txt", "") is True
        assert _matches_type("anything.txt", "  ") is True

    def test_py_type_matches_py_files(self) -> None:
        assert _matches_type("main.py", "py") is True
        assert _matches_type("types.pyi", "py") is True
        assert _matches_type("main.py", "python") is True

    def test_py_type_rejects_non_py_files(self) -> None:
        assert _matches_type("main.js", "py") is False
        assert _matches_type("README.md", "python") is False

    def test_ts_type_matches(self) -> None:
        assert _matches_type("app.ts", "ts") is True
        assert _matches_type("app.tsx", "ts") is True
        assert _matches_type("app.mts", "ts") is True

    def test_unknown_type_falls_back_to_extension(self) -> None:
        assert _matches_type("file.xyz", "xyz") is True
        assert _matches_type("file.xyz2", "xyz") is False

    def test_case_insensitive(self) -> None:
        assert _matches_type("Main.PY", "PY") is True
        assert _matches_type("main.PY", "py") is True

    def test_all_type_glob_map_keys_non_empty(self) -> None:
        """Every key in _TYPE_GLOB_MAP should have non-empty globs."""
        for key, globs in _TYPE_GLOB_MAP.items():
            assert len(globs) > 0, f"Empty globs for key '{key}'"


# ---------------------------------------------------------------------------
# _normalize_pattern / _match_glob
# ---------------------------------------------------------------------------

class TestNormalizePattern:
    def test_strips_whitespace(self) -> None:
        assert _normalize_pattern("  *.py  ") == "*.py"

    def test_replaces_backslashes(self) -> None:
        assert _normalize_pattern("src\\*.py") == "src/*.py"


class TestMatchGlob:
    def test_empty_pattern_no_match(self) -> None:
        assert _match_glob("src/main.py", "main.py", "") is False

    def test_simple_filename_match(self) -> None:
        assert _match_glob("main.py", "main.py", "*.py") is True
        assert _match_glob("main.py", "main.py", "*.js") is False

    def test_path_with_slash(self) -> None:
        assert _match_glob("src/main.py", "main.py", "src/**") is True
        assert _match_glob("lib/main.py", "main.py", "src/**") is False

    def test_double_star_pattern(self) -> None:
        assert _match_glob("deep/nested/file.py", "file.py", "**/*.py") is True

    def test_backslash_normalized(self) -> None:
        assert _match_glob("src/main.py", "main.py", "src\\**") is True


# ---------------------------------------------------------------------------
# PythonGrepBackend
# ---------------------------------------------------------------------------

class TestPythonGrepBackend:
    def setup_method(self) -> None:
        self.backend = PythonGrepBackend()

    def test_is_always_available(self) -> None:
        assert self.backend.is_available() is True

    def test_name(self) -> None:
        assert self.backend.name == "python"

    def test_basic_content_search(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("hello\nworld\nhello again\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "hello", output_mode="content")
        assert len(result.matches) == 2
        assert result.matches[0].line_number == 1
        assert result.matches[0].line_text == "hello"
        assert result.matches[1].line_number == 3

    def test_files_with_matches_mode(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("needle\n" * 5, encoding="utf-8")
        (tmp_path / "b.py").write_text("needle\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "needle", output_mode="files_with_matches")
        assert len(result.matches) == 2
        paths = {m.path for m in result.matches}
        assert "a.py" in paths
        assert "b.py" in paths

    def test_count_mode(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x\nx\nx\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("x\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "x", output_mode="count")
        assert result.counts["a.py"] == 3
        assert result.counts["b.py"] == 1

    def test_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("Hello\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "hello", case_insensitive=True)
        assert len(result.matches) == 1

    def test_fixed_strings(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("[2026-04-02]\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "[2026-04-02]", fixed_strings=True)
        assert len(result.matches) == 1

    def test_glob_filter(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("match\n", encoding="utf-8")
        (tmp_path / "b.js").write_text("match\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "match", glob="*.py")
        assert len(result.matches) == 1
        assert result.matches[0].path == "a.py"

    def test_type_filter(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("match\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("match\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "match", type_="py")
        assert len(result.matches) == 1
        assert result.matches[0].path == "a.py"

    def test_head_limit(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x\n" * 10, encoding="utf-8")
        result = self.backend.search(tmp_path, "x", head_limit=3)
        assert len(result.matches) == 3
        assert result.truncated is True

    def test_offset(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x\nx\nx\nx\nx\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "x", head_limit=2, offset=2)
        assert len(result.matches) == 2
        assert result.matches[0].line_number == 3

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        (tmp_path / "bin").write_bytes(b"\x00\x01\x02")
        result = self.backend.search(tmp_path, "x")
        assert result.skipped_binary == 1
        assert len(result.matches) == 0

    def test_skips_large_files(self, tmp_path: Path) -> None:
        (tmp_path / "big.txt").write_text("x" * 100, encoding="utf-8")
        result = self.backend.search(tmp_path, "x", max_file_size=10)
        assert result.skipped_large == 1
        assert len(result.matches) == 0

    def test_skips_ignored_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "cached.py").write_text("match\n", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("match\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "match")
        paths = {m.path for m in result.matches}
        assert "src/main.py" in paths
        # __pycache__ files should be excluded from walk
        assert not any("__pycache__" in p for p in paths)

    def test_unicode_decode_error_counted_as_binary(self, tmp_path: Path) -> None:
        # Write bytes that are valid but will fail UTF-8 decode on specific sequences
        bad = bytes(range(256))  # Contains invalid UTF-8 sequences
        (tmp_path / "bad.bin").write_bytes(bad)
        result = self.backend.search(tmp_path, "x")
        assert result.skipped_binary >= 1

    def test_no_match(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "zzz_nonexistent_zzz")
        assert len(result.matches) == 0
        assert result.total_matches == 0

    def test_file_read_error_skipped(self, tmp_path: Path) -> None:
        """Unreadable files should be silently skipped."""
        f = tmp_path / "no_access.py"
        f.write_text("match\n", encoding="utf-8")
        f.chmod(0o000)
        try:
            result = self.backend.search(tmp_path, "match")
            assert result.skipped_binary == 0
            assert len(result.matches) == 0
        finally:
            f.chmod(0o644)

    def test_mtime_recorded(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("match\n", encoding="utf-8")
        os.utime(tmp_path / "a.py", (1000, 1000))
        result = self.backend.search(tmp_path, "match", output_mode="files_with_matches")
        assert "a.py" in result.file_mtimes
        assert result.file_mtimes["a.py"] == 1000.0


# ---------------------------------------------------------------------------
# RipgrepBackend
# ---------------------------------------------------------------------------

class TestRipgrepBackend:
    def setup_method(self) -> None:
        self.backend = RipgrepBackend()

    def test_is_available_depends_on_rg(self) -> None:
        if self.backend._rg_path:
            assert self.backend.is_available() is True
        else:
            assert self.backend.is_available() is False

    def test_name(self) -> None:
        assert self.backend.name == "ripgrep"

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_basic_content_search(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("hello\nworld\nhello again\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "hello", output_mode="content")
        assert len(result.matches) >= 1
        texts = [m.line_text for m in result.matches]
        assert any("hello" in t for t in texts)

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_files_with_matches_mode(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("needle\n" * 5, encoding="utf-8")
        (tmp_path / "b.py").write_text("no match\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "needle", output_mode="files_with_matches")
        paths = [m.path for m in result.matches]
        assert "a.py" in paths
        assert "b.py" not in paths

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_count_mode(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x\nx\nx\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("x\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "x", output_mode="count")
        assert result.total_matches == 4

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("Hello World\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "hello", case_insensitive=True)
        assert len(result.matches) == 1

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_fixed_strings(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("[2026-04-02] token\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "[2026-04-02]", fixed_strings=True)
        assert len(result.matches) == 1

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_head_limit_truncates(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x\n" * 20, encoding="utf-8")
        result = self.backend.search(tmp_path, "x", head_limit=5)
        assert len(result.matches) == 5
        assert result.truncated is True

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_offset_skips_matches(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x\nx\nx\nx\nx\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "x", head_limit=2, offset=3)
        assert len(result.matches) == 2
        assert result.matches[0].line_number == 4

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_no_match(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "zzz_nonexistent_zzz")
        assert len(result.matches) == 0
        assert result.total_matches == 0

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_content_mode_line_numbers(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("line1\nline2\nneedle\nline4\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "needle", output_mode="content")
        assert len(result.matches) == 1
        assert result.matches[0].line_number == 3
        assert result.matches[0].line_text == "needle"

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_colons_in_line_text(self, tmp_path: Path) -> None:
        """Ripgrep output format is filepath:linenum:text — text may contain colons."""
        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "def foo", output_mode="content")
        assert len(result.matches) == 1
        assert "def foo(): pass" in (result.matches[0].line_text or "")

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_glob_filter(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("match\n", encoding="utf-8")
        (tmp_path / "b.js").write_text("match\n", encoding="utf-8")
        result = self.backend.search(tmp_path, "match", glob="*.py")
        assert len(result.matches) == 1

    @pytest.mark.skipif(
        not RipgrepBackend()._rg_path,
        reason="ripgrep not installed",
    )
    def test_ripgrep_not_installed_raises(self, tmp_path: Path) -> None:
        """When rg path is None, search should raise FileNotFoundError."""
        backend = RipgrepBackend()
        saved = backend._rg_path
        backend._rg_path = None
        try:
            with pytest.raises(FileNotFoundError, match="ripgrep"):
                backend.search(tmp_path, "test")
        finally:
            backend._rg_path = saved

    def test_rel_from_target_strips_prefix(self, tmp_path: Path) -> None:
        result = RipgrepBackend._rel_from_target(
            str(tmp_path / "src" / "a.py"), tmp_path
        )
        assert result == "src/a.py"

    def test_rel_from_target_strips_leading_slash(self, tmp_path: Path) -> None:
        result = RipgrepBackend._rel_from_target(
            str(tmp_path) + "/src/a.py", tmp_path
        )
        assert result == "src/a.py"

    def test_rel_from_target_dot_for_empty(self, tmp_path: Path) -> None:
        result = RipgrepBackend._rel_from_target(str(tmp_path), tmp_path)
        assert result == "."


# ---------------------------------------------------------------------------
# Backend selection logic in GrepTool
# ---------------------------------------------------------------------------

class TestGrepToolBackendSelection:
    """Test _select_backend logic in GrepTool."""

    def test_selects_ripgrep_when_available(self, tmp_path: Path) -> None:
        tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path)
        if tool._ripgrep_backend.is_available():
            assert tool._select_backend() is tool._ripgrep_backend
        else:
            assert tool._select_backend() is tool._python_backend

    def test_falls_back_to_python_when_ripgrep_unavailable(self, tmp_path: Path) -> None:
        tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path)
        saved = tool._ripgrep_backend._rg_path
        tool._ripgrep_backend._rg_path = None
        try:
            backend = tool._select_backend()
            assert backend is tool._python_backend
        finally:
            tool._ripgrep_backend._rg_path = saved

    def test_falls_back_to_python_when_max_file_bytes_overridden(self, tmp_path: Path) -> None:
        tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path)
        tool._MAX_FILE_BYTES = 100  # Override from default 2_000_000
        backend = tool._select_backend()
        assert backend is tool._python_backend

    def test_ripgrep_when_default_max_file_bytes(self, tmp_path: Path) -> None:
        tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path)
        if tool._ripgrep_backend.is_available():
            assert tool._MAX_FILE_BYTES == 2_000_000
            backend = tool._select_backend()
            assert backend is tool._ripgrep_backend


# ---------------------------------------------------------------------------
# Fallback from ripgrep to Python on error
# ---------------------------------------------------------------------------

class TestRipgrepFallbackToPython:
    """Test that GrepTool.execute falls back to Python backend when ripgrep fails."""

    @pytest.mark.asyncio
    async def test_fallback_on_file_not_found(self, tmp_path: Path) -> None:
        """If ripgrep raises FileNotFoundError, Python backend is used instead."""
        (tmp_path / "a.py").write_text("match_here\n", encoding="utf-8")

        tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path)
        # Force ripgrep as selected backend but make it raise
        if not tool._ripgrep_backend.is_available():
            pytest.skip("ripgrep not installed")

        original_search = tool._ripgrep_backend.search
        original_max = tool._MAX_FILE_BYTES
        try:
            def broken_search(*args, **kwargs):
                raise FileNotFoundError("rg vanished")

            tool._ripgrep_backend.search = broken_search
            # Ensure _select_backend returns ripgrep
            tool._MAX_FILE_BYTES = 2_000_000

            result = await tool.execute(pattern="match_here", path=".", output_mode="files_with_matches")
            assert "a.py" in result
        finally:
            tool._ripgrep_backend.search = original_search
            tool._MAX_FILE_BYTES = original_max

    @pytest.mark.asyncio
    async def test_both_backends_work_end_to_end(self, tmp_path: Path) -> None:
        """Both backends produce the same results for simple search."""
        (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("nope\n", encoding="utf-8")

        # Search with Python backend
        py_backend = PythonGrepBackend()
        py_result = py_backend.search(tmp_path, "needle", output_mode="files_with_matches")
        py_paths = sorted(m.path for m in py_result.matches)

        if RipgrepBackend().is_available():
            rg_backend = RipgrepBackend()
            rg_result = rg_backend.search(tmp_path, "needle", output_mode="files_with_matches")
            rg_paths = sorted(m.path for m in rg_result.matches)
            assert py_paths == rg_paths


# ---------------------------------------------------------------------------
# Integration: GrepTool.execute with explicit backend paths
# ---------------------------------------------------------------------------

class TestGrepToolExecutePython:
    """End-to-end tests using the Python backend explicitly."""

    @pytest.mark.asyncio
    async def test_count_mode_with_python(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("x\nx\nx\n", encoding="utf-8")
        (tmp_path / "src" / "b.py").write_text("x\n", encoding="utf-8")

        tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path)
        # Force Python backend
        orig_max = tool._MAX_FILE_BYTES
        tool._MAX_FILE_BYTES = 100
        try:
            result = await tool.execute(pattern="x", path="src", output_mode="count")
            assert "total matches:" in result
        finally:
            tool._MAX_FILE_BYTES = orig_max

    @pytest.mark.asyncio
    async def test_content_mode_with_context_python(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("line1\nneedle\nline3\n", encoding="utf-8")

        tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path)
        orig_max = tool._MAX_FILE_BYTES
        tool._MAX_FILE_BYTES = 100  # Force Python backend
        try:
            result = await tool.execute(
                pattern="needle",
                path=".",
                output_mode="content",
                context_before=1,
                context_after=1,
            )
            assert "needle" in result
            assert "line1" in result
            assert "line3" in result
        finally:
            tool._MAX_FILE_BYTES = orig_max
