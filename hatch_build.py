"""WebUI build hook — now disabled.

webui has been removed from the project. This hook is kept as a no-op
to avoid breaking existing hatch build configurations.
"""

from __future__ import annotations

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebUIBuildHook(BuildHookInterface):
    PLUGIN_NAME = "webui-build"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: D401
        self.app.display_info("[webui-build] skipped — webui removed from project")
        return
