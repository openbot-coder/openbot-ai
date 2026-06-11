from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import tomllib


def test_source_checkout_import_uses_pyproject_version_without_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    script = textwrap.dedent(
        f"""
        import sys
        import types

        sys.path.insert(0, {str(repo_root)!r})
        fake = types.ModuleType("openbot.sdk")
        fake.openbot = object
        fake.RunResult = object
        sys.modules["openbot.sdk"] = fake

        import openbot

        print(openbot.__version__)
        """
    )

    proc = subprocess.run(
        [sys.executable, "-S", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected
