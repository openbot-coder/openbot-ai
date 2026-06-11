"""Slash command routing and built-in handlers."""

from openbot.command.builtin import register_builtin_commands
from openbot.command.router import CommandContext, CommandRouter

__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
