# ChatGPT Discord Bot

A self-hosted Discord assistant with isolated conversations, slash commands, and selectable AI providers. The name describes the Discord experience; the project does not include a free OpenAI API key or proxy ChatGPT subscriptions.

## Free model option

The default provider is Google Gemini, using `gemini-3.5-flash-lite` when the model is set to `auto`. Google currently offers free Gemini API usage for eligible models and within account rate limits. The free tier may change, access and quotas vary by model and region, and Google says free-tier content may be used to improve its products. Review Google's [model list](https://ai.google.dev/gemini-api/docs/models), [pricing](https://ai.google.dev/gemini-api/docs/pricing), and [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) before choosing a model.

OpenAI's API is billed separately from ChatGPT plans. This bot does not treat ChatGPT Free, Plus, or Pro access as API credits. OpenAI is available only when you configure an API key and explicitly allow paid providers. See [OpenAI API pricing](https://developers.openai.com/api/docs/pricing). The bot does not switch to a paid provider when a free provider hits a quota or errors.

For local inference, Ollama can serve an already-installed model on your machine. The bot does not download models. Paid OpenAI, Anthropic, and xAI providers are optional. Image generation is disabled unless enabled and supported by your configured provider.

## What it does

- `/chat` sends a prompt to the selected provider.
- `/provider` selects an enabled provider and model.
- `/reset` clears the caller's conversation for the current Discord scope.
- `/private` toggles private or public `/chat` replies for the caller in that channel. `/chat` starts private by default.
- `/switchpersona` selects standard, creative, technical, or casual style. Configured bot administrators can also access the legacy jailbreak persona names; those prompts do not grant model capabilities or bypass provider safeguards.
- `/replyall` can enable replies to ordinary channel messages. It requires message content intent and should be limited to trusted channels. See the access controls below.
- `/draw` requests image generation when the feature is enabled and supported by the selected provider; `/help` shows the available commands.
- `/status` reports local configuration without contacting providers, and `python main.py --check-config` validates configuration without starting Discord.

Conversation history and settings live in memory and are separated by user and Discord scope; they are not a durable chat archive. The default `/chat` visibility is private, so a restart or session expiry cannot silently switch it to public. `/private` changes that user's `/chat` visibility in the current channel. Ordinary `/replyall` responses are always public and do not use private chat history. Restarting the process or expiring an idle session clears history and restores the private default. The bot does not post an unsolicited startup message. Configure `system_prompt.txt` to set the assistant's base instructions.

## Requirements

- Python 3.12 or 3.13
- A Discord application and bot token
- A Gemini API key for the default provider, or credentials for another enabled provider

### 1. Create the Discord application

Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, and copy its token into `.env`. Invite it with the `bot` and `applications.commands` OAuth scopes. Grant only the channel permissions it needs, such as View Channel, Send Messages, Embed Links, and Attach Files when using image features.

Slash commands work without Message Content Intent. Leave `ENABLE_MESSAGE_CONTENT=false` unless you want `/replyall`; for that feature, enable Message Content Intent in the Developer Portal and configure `REPLYALL_CHANNEL_IDS` with the allowed channel IDs. A user must have Manage Channels or be listed in `BOT_ADMIN_IDS` to toggle it. The bot needs View Channel and Read Message History in those channels.

### 2. Install and configure

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On macOS or Linux, use `python3.12 -m venv .venv` and `source .venv/bin/activate`, then run the same two pip commands.

Set `DISCORD_BOT_TOKEN` and `GEMINI_API_KEY` in `.env`, then run:

```sh
python main.py
```

Keep `.env` private. Never paste bot tokens or provider keys into Discord or commit them to Git.

## Provider and cost settings

`DEFAULT_PROVIDER=gemini` and `DEFAULT_MODEL=auto` select the default. Provider selection is limited to providers that have valid local configuration. `ALLOW_PAID_PROVIDERS=false` is the safe default: OpenAI, Anthropic, and xAI are not made available until you opt in. This flag is a bot-side gate, not a billing cap supplied by those services. Gemini billing and quotas belong to the Google Cloud project associated with the key; use a free-tier project with billing disabled if you need to avoid paid Gemini usage. Quota errors are reported and do not trigger paid fallback.

| Provider | Key / settings | Notes |
| --- | --- | --- |
| Gemini | `GEMINI_API_KEY`, optional `GEMINI_MODEL` | Free-tier quota may be available; model access and limits vary. |
| OpenAI | `OPENAI_API_KEY`, optional `OPENAI_MODEL` | API use requires separate API billing. Requires `ALLOW_PAID_PROVIDERS=true`. |
| Anthropic | `ANTHROPIC_API_KEY`, optional `CLAUDE_MODEL` | Requires `ALLOW_PAID_PROVIDERS=true`. |
| xAI | `XAI_API_KEY`, optional `GROK_MODEL` | Requires `ALLOW_PAID_PROVIDERS=true`. |
| Ollama | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Local OpenAI-compatible endpoint; install and start the model yourself. |

For backward compatibility, `GEMINI_KEY`, `OPENAI_KEY`, `CLAUDE_KEY`, and `GROK_KEY` are accepted as aliases. Canonical names are preferred. Ollama defaults to `http://localhost:11434/v1`; inside Docker, `localhost` refers to the container, so set a reachable host URL explicitly. Follow [Ollama's OpenAI compatibility guide](https://ollama.com/blog/openai-compatibility).

`ENABLE_IMAGE_GENERATION=true` enables the feature only where the selected provider supports it. It is off by default. No provider fallback is used for image generation either.

## Access and privacy

Set `ALLOWED_GUILD_IDS` and/or `ALLOWED_CHANNEL_IDS` to restrict where the bot accepts requests. Set `BOT_ADMIN_IDS` to comma-separated Discord user IDs for administrator-only controls and legacy restricted personas. `REPLYALL_CHANNEL_IDS` limits where `/replyall` can run. The bot's in-memory history is sent to the selected AI provider as conversation context; provider data handling follows that provider's terms and privacy settings. Gemini's free tier has a data-use caveat described above. Avoid sending sensitive information.

The bot applies bounded input, output, history, concurrency, and cooldown settings. Tune these through the documented limits in `.env.example`. They help control load; provider-side quotas and account billing remain outside the bot's control.

## Configuration check

```sh
python main.py --check-config
```

This is an offline configuration check. It does not verify that credentials are valid, contact Discord or an AI provider, or confirm that a model is available to your account.

## Docker

Copy `.env.example` to `.env`, fill in the required values, then run:

```sh
docker compose up --build -d
docker compose logs -f
docker compose down
```

The image runs as a non-root user and the container uses a read-only root filesystem. Docker Compose does not expose ports because the bot connects outward to Discord and its provider.

## Development

```sh
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
mypy src main.py utils
pytest
```

The GitHub Actions workflow runs lint, formatting, type checks, tests, bytecode compilation, dependency consistency checks, and a Python 3.12/3.13 matrix.

## License

See [LICENSE](LICENSE).
