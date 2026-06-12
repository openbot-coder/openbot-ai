This file provides guidance to AI coding agents working with this repository.

## Project Overview

openbot is a lightweight, open-source AI agent framework written in Python with a React/TypeScript WebUI. It centers around a small agent loop that receives messages from chat channels, invokes an LLM provider, executes tools, and manages session memory.

## Development Commands

```bash
# Python: run single test / lint
pytest tests/test_openai_api.py::test_function -v
ruff check openbot/

# WebUI: dev server (proxies API/WS to gateway :8765), build, test
# Build outputs to ../openbot/web/dist (bundled into the Python wheel)
cd webui && bun run dev      # or openbot_API_URL=... bun run dev
cd webui && bun run build
cd webui && bun run test

# Gateway
openbot gateway
```

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`openbot/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`openbot/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`openbot/agent/loop.py`) consumes inbound messages, builds context, and coordinates the turn.
3. **`AgentRunner`** (`openbot/agent/runner.py`) handles the actual LLM conversation loop: send messages to the provider, receive tool calls, execute tools, and stream responses.
4. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Key Subsystems

- **Agent Loop** (`openbot/agent/loop.py`, `runner.py`): The core processing engine. `AgentLoop` manages session keys, hooks, and context building. `AgentRunner` executes the multi-turn LLM conversation with tool execution.
- **LLM Providers** (`openbot/providers/`): Provider implementations (Anthropic, OpenAI-compatible, OpenAI Responses API, Azure, Bedrock, GitHub Copilot, OpenAI Codex, etc.) built on a common base (`base.py`). Includes image generation (`image_generation.py`) and audio transcription (`transcription.py`). `factory.py` and `registry.py` handle instantiation and model discovery.
- **Channels** (`openbot/channels/`): Platform integrations (Telegram, Discord, Slack, Feishu, Matrix, WhatsApp, QQ, WeChat, WeCom, DingTalk, Email, MoChat, MS Teams, WebSocket). `manager.py` discovers and coordinates them. Channels are auto-discovered via `pkgutil` scan + entry-point plugins.
- **Tools** (`openbot/agent/tools/`): Agent capabilities exposed to the LLM: filesystem (read/write/edit/list), shell execution (with sandbox backends), concurrent multi-engine web search (Bing/Sogou/Baidu/360/DuckDuckGo/Brave HTML + news/academic/GitHub) and web fetch, MCP servers, cron, notebook editing, subagent spawning, long-running tasks / sustained goals (`long_task.py`), image generation, and self-modification. Tools are auto-discovered via `pkgutil` scan + entry-point plugins.
- **Memory** (`openbot/agent/memory.py`): Session history persistence with Dream two-phase memory consolidation. Uses atomic writes with fsync for durability.
- **Session Management** (`openbot/session/`): Per-session history, context compaction, TTL-based auto-compaction (`manager.py`), and sustained goal state tracking (`goal_state.py`).
- **Config** (`openbot/config/schema.py`, `loader.py`): Pydantic-based configuration loaded from `~/.openbot/config.json`. Supports camelCase aliases for JSON compatibility.
- **Bridge** (`bridge/`): TypeScript services (e.g. WhatsApp bridge) bundled into the wheel via `pyproject.toml` `force-include`.
- **WebUI** (`webui/`): Vite-based React SPA that talks to the gateway over a WebSocket multiplex protocol. The dev server proxies `/api`, `/webui`, `/auth`, and WebSocket traffic to the gateway.
- **API Server** (`openbot/api/server.py`): OpenAI-compatible HTTP API (`/v1/chat/completions`, `/v1/models`) for programmatic access.
- **Command Router** (`openbot/command/`): Slash command routing and built-in command handlers.
- **Heartbeat** (`openbot/templates/HEARTBEAT.md`): Periodic task list checked via `cron` jobs (legacy dedicated service removed).
- **Pairing** (`openbot/pairing/`): DM sender approval store with persistent pairing codes per channel.
- **Skills** (`openbot/skills/`): Built-in skill definitions (long-goal, cron, github, image-generation, etc.) loaded into agent context.
- **Security** (`openbot/security/`): PTH file guard and other security measures activated at CLI entry.

### Entry Points

- **CLI**: `openbot/cli/commands.py`
- **Python SDK**: `openbot/openbot.py`

## Project-Specific Notes

- Architecture constraints: [`.agent/design.md`](.agent/design.md)
- Security boundaries: [`.agent/security.md`](.agent/security.md)
- Common gotchas: [`.agent/gotchas.md`](.agent/gotchas.md)

## Contribution Flow

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for contribution flow and PR guidelines.

## Code Style

- Python 3.11+, asyncio throughout.
- Line length: 100.
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored).
- pytest with `asyncio_mode = "auto"`.

## Common File Locations

- Config schema: `openbot/config/schema.py`
- Provider base / new provider template: `openbot/providers/base.py`
- Channel base / new channel template: `openbot/channels/base.py`
- Tool registry: `openbot/agent/tools/registry.py`
- WebUI dev proxy config: `webui/vite.config.ts`
- Tests mirror the `openbot/` package structure.
