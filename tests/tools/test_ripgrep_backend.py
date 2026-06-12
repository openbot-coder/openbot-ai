"""Tests for the ripgrep backend and backend selection."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openbot.agent.tools.search import (
    GrepTool,
    _GrepBackend,
    _PythonGrepBackend,
    _RipgrepBackend,
    _select_backend,
)

# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def test_select_backend_uses_ripgrep_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """If `shutil.which('rg')` returns a path, the ripgrep backend is selected."""
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/rg" if name == "rg" else None)
    backend = _select_backend(ignore_dirs=set(), display_path=lambda f, r: str(f))
    assert isinstance(backend, _RipgrepBackend)
    assert backend.name == "ripgrep"


def test_select_backend_falls_back_to_python_when_rg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `rg` is not on PATH, the pure-Python backend is selected."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    backend = _select_backend(ignore_dirs=set(), display_path=lambda f, r: str(f))
    assert isinstance(backend, _PythonGrepBackend)
    assert backend.name == "python"


def test_grep_tool_uses_injected_backend(tmp_path: Path) -> None:
    """GrepTool accepts a custom backend via the constructor."""
    fake = MagicMock(spec=_GrepBackend)
    fake.name = "fake"
    fake.search.return_value = []  # no matches

    tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path, backend=fake)
    assert tool.backend_name == "fake"
    assert "Backend: fake" in tool.description


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_ripgrep_backend_builds_minimal_command(tmp_path: Path) -> None:
    """Without any options, the command includes only the always-on flags."""
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f),
    )
    cmd = backend._build_cmd(
        tmp_path,
        "needle",
        case_insensitive=False,
        fixed_strings=False,
        output_mode="content",
        glob=None,
        type_=None,
        context_before=0,
        context_after=0,
    )
    assert cmd[0] == "rg"
    assert "--json" in cmd
    assert "--no-config" in cmd
    assert "--no-messages" in cmd
    assert "--no-ignore" in cmd
    assert "-M" in cmd and "2000" in cmd
    # Pattern and target always come last, separated by `--`
    assert cmd[-3] == "--"
    assert cmd[-2] == "needle"
    assert cmd[-1] == str(tmp_path)


def test_ripgrep_backend_command_includes_search_flags(tmp_path: Path) -> None:
    """Each GrepTool option maps to the corresponding rg flag."""
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f),
    )
    cmd = backend._build_cmd(
        tmp_path,
        "needle",
        case_insensitive=True,
        fixed_strings=True,
        output_mode="content",
        glob="*.py",
        type_="py",
        context_before=2,
        context_after=3,
    )
    assert "-i" in cmd
    assert "-F" in cmd
    assert "-B" in cmd and "2" in cmd
    assert "-A" in cmd and "3" in cmd
    assert "-g" in cmd and "*.py" in cmd
    assert "-t" in cmd and "py" in cmd


def test_ripgrep_backend_command_translates_ignore_dirs(tmp_path: Path) -> None:
    """Each entry in `ignore_dirs` becomes a `-g '!name'` exclusion."""
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs={".git", "node_modules", "__pycache__"},
        display_path=lambda f, r: str(f),
    )
    cmd = backend._build_cmd(
        tmp_path,
        "needle",
        case_insensitive=False,
        fixed_strings=False,
        output_mode="content",
        glob=None,
        type_=None,
        context_before=0,
        context_after=0,
    )
    exclusions = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-g" and cmd[i + 1].startswith("!")]
    assert "!.git" in exclusions
    assert "!node_modules" in exclusions
    assert "!__pycache__" in exclusions
    # Exclusions come before the final `--` separator.
    sep_index = cmd.index("--")
    for excl in exclusions:
        assert cmd.index(excl) < sep_index


# ---------------------------------------------------------------------------
# JSON output parsing
# ---------------------------------------------------------------------------


def _make_rg_event(event_type: str, **data) -> str:
    return json.dumps({"type": event_type, "data": data})


def _fake_proc(returncode: int, lines: list[str], stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        returncode=returncode,
        stdout="\n".join(lines),
        stderr=stderr,
    )


def test_parse_output_content_mode(tmp_path: Path) -> None:
    """Content mode emits one _Match per matching line with line_no and text."""
    file_path = tmp_path / "a.py"
    file_path.write_text("alpha\nbeta\nneedle\ngamma\n", encoding="utf-8")

    lines = [
        _make_rg_event("begin", path={"text": str(file_path)}),
        _make_rg_event(
            "match",
            path={"text": str(file_path)},
            lines={"text": "needle\n"},
            line_number=3,
        ),
        _make_rg_event("end", path={"text": str(file_path)}, stats={"matches": 1}),
    ]
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f.relative_to(r)),
    )
    matches = backend._parse_output("\n".join(lines), tmp_path, "content")
    assert len(matches) == 1
    m = matches[0]
    assert m.line_no == 3
    assert m.text == "needle"
    assert m.count == 0
    assert m.display_path == "a.py"


def test_parse_output_files_with_matches_deduplicates(tmp_path: Path) -> None:
    """files_with_matches returns one entry per file, even with many matches."""
    file_path = tmp_path / "a.py"
    file_path.write_text("a\nb\nc\n", encoding="utf-8")

    lines = [
        _make_rg_event("begin", path={"text": str(file_path)}),
        _make_rg_event("match", path={"text": str(file_path)}, lines={"text": "a\n"}, line_number=1),
        _make_rg_event("match", path={"text": str(file_path)}, lines={"text": "b\n"}, line_number=2),
        _make_rg_event("match", path={"text": str(file_path)}, lines={"text": "c\n"}, line_number=3),
        _make_rg_event("end", path={"text": str(file_path)}, stats={"matches": 3}),
    ]
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f.relative_to(r)),
    )
    matches = backend._parse_output("\n".join(lines), tmp_path, "files_with_matches")
    assert len(matches) == 1
    assert matches[0].count == 1
    assert matches[0].line_no == 0
    assert matches[0].text == ""


def test_parse_output_count_mode(tmp_path: Path) -> None:
    """Count mode emits one entry per file with `count` set from end.stats."""
    file_path = tmp_path / "a.py"
    file_path.write_text("x\nx\nx\n", encoding="utf-8")

    lines = [
        _make_rg_event("begin", path={"text": str(file_path)}),
        _make_rg_event("match", path={"text": str(file_path)}, lines={"text": "x\n"}, line_number=1),
        _make_rg_event("match", path={"text": str(file_path)}, lines={"text": "x\n"}, line_number=2),
        _make_rg_event("match", path={"text": str(file_path)}, lines={"text": "x\n"}, line_number=3),
        _make_rg_event("end", path={"text": str(file_path)}, stats={"matches": 3}),
    ]
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f.relative_to(r)),
    )
    matches = backend._parse_output("\n".join(lines), tmp_path, "count")
    assert len(matches) == 1
    assert matches[0].count == 3
    assert matches[0].line_no == 0
    assert matches[0].text == ""


def test_parse_output_count_mode_omits_files_with_no_matches(tmp_path: Path) -> None:
    """Files whose matches == 0 do not produce a count entry."""
    file_path = tmp_path / "a.py"
    file_path.write_text("alpha\n", encoding="utf-8")

    lines = [
        _make_rg_event("begin", path={"text": str(file_path)}),
        _make_rg_event("end", path={"text": str(file_path)}, stats={"matches": 0}),
    ]
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f.relative_to(r)),
    )
    matches = backend._parse_output("\n".join(lines), tmp_path, "count")
    assert matches == []


def test_parse_output_skips_malformed_json_lines(tmp_path: Path) -> None:
    """Invalid JSON lines are silently skipped, valid lines still parsed."""
    file_path = tmp_path / "a.py"
    file_path.write_text("needle\n", encoding="utf-8")

    lines = [
        "{not json",
        _make_rg_event("begin", path={"text": str(file_path)}),
        "also not json",
        _make_rg_event(
            "match",
            path={"text": str(file_path)},
            lines={"text": "needle\n"},
            line_number=1,
        ),
        _make_rg_event("end", path={"text": str(file_path)}, stats={"matches": 1}),
    ]
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f.relative_to(r)),
    )
    matches = backend._parse_output("\n".join(lines), tmp_path, "content")
    assert len(matches) == 1
    assert matches[0].line_no == 1


# ---------------------------------------------------------------------------
# Subprocess invocation and error handling
# ---------------------------------------------------------------------------


def test_search_invokes_subprocess_with_built_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`search` calls subprocess.run with the command built by `_build_cmd`."""
    file_path = tmp_path / "a.py"
    file_path.write_text("needle\n", encoding="utf-8")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _fake_proc(0, [
            _make_rg_event("begin", path={"text": str(file_path)}),
            _make_rg_event(
                "match",
                path={"text": str(file_path)},
                lines={"text": "needle\n"},
                line_number=1,
            ),
            _make_rg_event("end", path={"text": str(file_path)}, stats={"matches": 1}),
        ])

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f.relative_to(r)),
    )
    matches = backend.search(
        tmp_path,
        "needle",
        case_insensitive=False,
        fixed_strings=False,
        output_mode="content",
        glob=None,
        type_=None,
        context_before=0,
        context_after=0,
        head_limit=None,
        offset=0,
    )
    assert captured["cmd"][0] == "rg"
    assert captured["cmd"][-2] == "needle"
    assert captured["kwargs"]["timeout"] == 30
    assert captured["kwargs"]["capture_output"] is True
    assert len(matches) == 1
    assert matches[0].text == "needle"


def test_search_treats_exit_code_1_as_no_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rg exit code 1 (no matches) is normal and produces an empty list."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_proc(1, [])
    )
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f),
    )
    matches = backend.search(
        tmp_path,
        "needle",
        case_insensitive=False,
        fixed_strings=False,
        output_mode="content",
        glob=None,
        type_=None,
        context_before=0,
        context_after=0,
        head_limit=None,
        offset=0,
    )
    assert matches == []


def test_search_raises_runtime_error_on_exit_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rg exit code 2+ (real error) raises RuntimeError with stderr excerpt."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(2, [], stderr="ripgrep exploded"),
    )
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f),
    )
    with pytest.raises(RuntimeError, match="ripgrep failed"):
        backend.search(
            tmp_path,
            "needle",
            case_insensitive=False,
            fixed_strings=False,
            output_mode="content",
            glob=None,
            type_=None,
            context_before=0,
            context_after=0,
            head_limit=None,
            offset=0,
        )


def test_search_raises_runtime_error_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subprocess timeout surfaces as RuntimeError."""

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="rg", timeout=30)

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f),
    )
    with pytest.raises(RuntimeError, match="timed out"):
        backend.search(
            tmp_path,
            "needle",
            case_insensitive=False,
            fixed_strings=False,
            output_mode="content",
            glob=None,
            type_=None,
            context_before=0,
            context_after=0,
            head_limit=None,
            offset=0,
        )


def test_search_raises_runtime_error_on_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `rg` disappears between detection and execution, raise RuntimeError."""

    def fake_run(*a, **k):
        raise FileNotFoundError("rg not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f),
    )
    with pytest.raises(RuntimeError, match="ripgrep binary not found"):
        backend.search(
            tmp_path,
            "needle",
            case_insensitive=False,
            fixed_strings=False,
            output_mode="content",
            glob=None,
            type_=None,
            context_before=0,
            context_after=0,
            head_limit=None,
            offset=0,
        )


# ---------------------------------------------------------------------------
# GrepTool integration with the ripgrep backend
# ---------------------------------------------------------------------------


def test_grep_tool_dispatches_to_ripgrep_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a ripgrep backend is injected, GrepTool delegates search to it."""
    (tmp_path / "a.py").write_text("alpha\nneedle\nbeta\n", encoding="utf-8")

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        file_path = str(tmp_path / "a.py")
        return _fake_proc(0, [
            _make_rg_event("begin", path={"text": file_path}),
            _make_rg_event(
                "match",
                path={"text": file_path},
                lines={"text": "needle\n"},
                line_number=2,
            ),
            _make_rg_event("end", path={"text": file_path}, stats={"matches": 1}),
        ])

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f.relative_to(r)),
    )
    tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path, backend=backend)
    assert tool.backend_name == "ripgrep"

    result = await_run(tool.execute, pattern="needle", path=".", output_mode="content")
    assert "a.py:2" in result
    assert "> 2| needle" in result


def test_grep_tool_falls_back_silently_on_backend_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the ripgrep backend raises, GrepTool returns a formatted Error string."""

    def fake_run(*a, **k):
        return _fake_proc(2, [], stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = _RipgrepBackend(
        rg_path="rg",
        ignore_dirs=set(),
        display_path=lambda f, r: str(f),
    )
    tool = GrepTool(workspace=tmp_path, allowed_dir=tmp_path, backend=backend)
    result = await_run(tool.execute, pattern="needle", path=".")
    assert result.startswith("Error:")
    assert "ripgrep failed" in result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def await_run(coro_factory, *args, **kwargs):  # type: ignore[no-untyped-def]
    """Run an async coroutine factory from sync test code."""

    async def _runner():
        return await coro_factory(*args, **kwargs)

    return asyncio.run(_runner())
