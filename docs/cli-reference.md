# CLI Reference

Use this page when you know what you want to run and need the command shape. For a guided first run, start with [`quick-start.md`](./quick-start.md).

## Choose a Command

| Goal | Command | Notes |
|---|---|---|
| Check the install | `openbot --version` | If this fails, try `python -m openbot --version` |
| Create or refresh config | `openbot onboard` | Creates `~/.openbot/config.json` and `~/.openbot/workspace/` |
| Use guided setup | `openbot onboard --wizard` | Best when you prefer prompts over hand-editing JSON |
| Check config without calling a model | `openbot status` | Reads the default config and summarizes the active model/provider |
| Send one test message | `openbot agent -m "Hello!"` | First proof that install, config, provider, model, and workspace all work |
| Chat in the terminal | `openbot agent` | Interactive local chat; exit with `exit`, `/exit`, `:q`, or `Ctrl+D` |
| Use WebUI or chat apps | `openbot gateway` | Keep this terminal running while those surfaces are in use |
| Serve an OpenAI-compatible API | `openbot serve` | Starts `/v1/chat/completions`, `/v1/models`, and `/health` |
| Check chat channel setup | `openbot channels status` | Useful before starting `openbot gateway` |
| Log in to QR/OAuth-style channels | `openbot channels login <channel>` | Used by channels such as WhatsApp and WeChat |
| Log in to OAuth model providers | `openbot provider login <provider>` | Used by OAuth providers such as OpenAI Codex and GitHub Copilot |

## Global

```bash
openbot --help
openbot --version
python -m openbot --help
python -m openbot --version
```

`python -m openbot ...` is useful when the package is installed but the `openbot` script is not on `PATH`.

## Common Patterns

Most day-to-day commands use the default config and workspace. Advanced or multi-instance runs usually pass both paths explicitly:

```bash
openbot agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
openbot gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
openbot serve --config ./bot-a/config.json --workspace ./bot-a/workspace
```

Use `--verbose` on long-running processes when you need startup or runtime logs:

```bash
openbot gateway --verbose
openbot serve --verbose
```

Long-running commands keep working until you stop them. Press `Ctrl+C` in that terminal to stop `openbot gateway` or `openbot serve`.

## Setup

| Command | Description |
|---|---|
| `openbot onboard` | Initialize or refresh the default config and workspace |
| `openbot onboard --wizard` | Use the interactive setup wizard |
| `openbot onboard --config <path> --workspace <path>` | Initialize or refresh a specific instance |

Default paths:

| Path | Default |
|---|---|
| Config | `~/.openbot/config.json` |
| Workspace | `~/.openbot/workspace/` |

## Agent CLI

| Command | Description |
|---|---|
| `openbot agent -m "Hello!"` | Send one message and exit |
| `openbot agent` | Start interactive terminal chat |
| `openbot agent --session <id>` | Use a specific session key |
| `openbot agent --workspace <path>` | Override workspace |
| `openbot agent --config <path>` | Use a specific config file |
| `openbot agent --no-markdown` | Print plain text instead of Rich-rendered Markdown |
| `openbot agent --logs` | Show runtime logs while chatting |

Interactive mode exits with `exit`, `quit`, `/exit`, `/quit`, `:q`, or `Ctrl+D`.

## Gateway

`openbot gateway` starts enabled chat channels, WebUI/WebSocket when configured, cron-backed system jobs, Dream, heartbeat, and the health endpoint.

| Command | Description |
|---|---|
| `openbot gateway` | Start the gateway with config defaults |
| `openbot gateway --verbose` | Show verbose runtime output |
| `openbot gateway --port <port>` | Override `gateway.port` for the health endpoint |
| `openbot gateway --workspace <path>` | Override workspace |
| `openbot gateway --config <path>` | Use a specific config file |

Default health endpoint:

```text
http://127.0.0.1:18790/health
```

The bundled WebUI is served by the WebSocket channel, usually on port `8765`, not by the gateway health endpoint.

## OpenAI-Compatible API

| Command | Description |
|---|---|
| `openbot serve` | Start `/v1/chat/completions`, `/v1/models`, and `/health` |
| `openbot serve --host <host>` | Override API bind host |
| `openbot serve --port <port>` | Override API port |
| `openbot serve --timeout <seconds>` | Override per-request timeout |
| `openbot serve --verbose` | Show runtime logs |
| `openbot serve --workspace <path>` | Override workspace |
| `openbot serve --config <path>` | Use a specific config file |

Default API endpoint:

```text
http://127.0.0.1:8900
```

See [`openai-api.md`](./openai-api.md) for request examples.

## Status

```bash
openbot status
```

Shows the default config path, workspace path, active model, and provider summary. This command does not currently accept `--config`; use explicit `--config` and `--workspace` on `agent`, `gateway`, or `serve` when debugging a specific instance.

## Channels

| Command | Description |
|---|---|
| `openbot channels status` | Show configured channel status |
| `openbot channels status --config <path>` | Show channel status for a specific config |
| `openbot channels login <channel>` | Run interactive login for supported channels |
| `openbot channels login <channel> --force` | Re-authenticate even if credentials already exist |
| `openbot channels login <channel> --config <path>` | Use a specific config file |

Examples:

```bash
openbot channels login whatsapp
openbot channels login weixin
openbot channels status
```

See [`chat-apps.md`](./chat-apps.md) for channel-specific setup.

## Provider OAuth

| Command | Description |
|---|---|
| `openbot provider login openai-codex` | Authenticate OpenAI Codex provider |
| `openbot provider login github-copilot` | Authenticate GitHub Copilot provider |
| `openbot provider logout openai-codex` | Remove OpenAI Codex OAuth state |
| `openbot provider logout github-copilot` | Remove GitHub Copilot OAuth state |

See [`providers.md`](./providers.md#oauth-providers) for when OAuth providers need explicit provider/model selection.

## Useful First Checks

```bash
openbot --version
openbot status
openbot agent -m "Hello!"
```

If these fail, use [`troubleshooting.md`](./troubleshooting.md) before debugging WebUI, chat apps, Docker, systemd, or SDK integrations.
