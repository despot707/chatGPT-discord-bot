# ChatGPT Discord Bot

A self-hosted Discord assistant with slash commands, bounded conversations, web tools, image input, and selectable AI providers. “ChatGPT” describes the Discord experience; ChatGPT subscriptions do not include API credits for this bot.

## Commands and behavior

- `/chat` sends a prompt to the selected provider. It accepts an optional image attachment, `use_web`, and `context_messages` from 0 to 20. Replies are private by default; `/private` changes that setting for your chats in the current channel.
- `/search(query)` searches with Tavily and asks the selected AI provider to summarize up to five returned sources.
- `/browse(url, question)` fetches and summarizes a public HTML or plain-text page. It does not run JavaScript or sign in to websites.
- `/image(url, caption)` fetches an existing public image and sends it using your private/public reply setting. This is not AI image generation.
- `/draw(prompt)` requests image generation when enabled and supported. In this build, generation is an optional paid OpenAI feature: it requires `ENABLE_IMAGE_GENERATION=true`, `ALLOW_PAID_PROVIDERS=true`, an OpenAI API key, and a compatible model. There is no free image-generation provider configured by default.
- `/provider`, `/reset`, `/switchpersona`, `/status`, and `/help` select a provider, clear your conversation, select a style, show local settings, and list commands. `/replyall` toggles replies to ordinary channel messages for administrators in explicitly configured channels.

Conversation history and settings are held in memory, separated by user and Discord scope. A restart or idle expiry clears them. The bot does not provide general Discord control, member moderation, or self-bot behavior.

## Pick a game and build teams

Gaming commands run without AI calls. They work in server channels without Message Content Intent, and respect the same server/channel allowlists as chat.

1. Each player runs `/party join` in the channel you are using. Optional `skill` (1–10) and `role` describe that player's rating and preference for this session. Joining again updates them. Use the same rating scale across your group; these are self-reported ratings, not game ranks or verified MMR.
2. For Steam suggestions, each player runs `/steam link profile:<Steam profile URL or SteamID64>` once in that server. This saves a public profile reference you supply; it does not sign in to Steam or verify account ownership. Saving it allows the server's parties to compare that library. `/steam status` shows your saved reference privately; `/steam unlink` removes it.
3. `/games together` compares the channel party's visible libraries and suggests shared games. `mode:all` requires everyone to have the game; `mode:most` also considers games owned by part of the group and shows ownership counts. Multiplayer filtering is on by default; use `multiplayer_only:false` to see shared titles without that filter.
4. `/teams make` splits the party into 2–4 teams. Balanced mode uses the supplied ratings and spreads repeated role preferences where possible; `balanced:false` shuffles players. It does not fetch competitive ranks, enforce a game's role rules, move voice-channel members, or create in-game lobbies.
5. `/party show` displays the roster; `/party leave` removes yourself. `/party clear` requires Manage Channels or a configured bot administrator.

Parties contain at most 20 people and are separate for each server/channel. Steam profile references are separate for each server/user. Teams work without Steam, including for non-Steam games. Party and team results are visible in the channel, with mentions suppressed.

The bot host needs one `STEAM_API_KEY`, stored only in `.env` or hosting secret variables. Obtain it from [Steam's Web API key page](https://steamcommunity.com/dev/apikey); individual players only supply a profile link, never their password or API key. Steam returns owned games only when game details are visible to the key's caller. Private, missing, or failed libraries stop the comparison instead of silently leaving that person out. Played free games can appear; this is not a complete catalog of free games everyone could install. See Valve's [owned-games API](https://partner.steamgames.com/doc/webapi/iplayerservice) and [key documentation](https://partner.steamgames.com/doc/webapi_overview/auth).

Suggestions rank actual ownership coverage and recorded playtime. Store category metadata is fetched on a bounded, best-effort basis using Steam's undocumented store endpoint. Multiplayer filtering checks a limited set of top candidates and can miss a suitable title farther down the list. Unknown metadata is not proof of multiplayer support. Check a suggested game's online support and lobby size before choosing it: library ownership does not establish that your entire party can play together, that everyone has it installed, or that shared family licenses can be used simultaneously.

### Save gaming data on your PC or Railway

`GAMING_DATABASE_PATH` defaults to `data/gaming.sqlite3`. It stores Discord IDs, supplied Steam IDs/display names, and party names/ratings/roles. Libraries are cached temporarily in memory and are not sent to an AI provider. Keep the database private and back it up; it is excluded from Git and Docker build context. Members can remove their saved profile with `/steam unlink` and their current roster entry with `/party leave`.

On your PC, keep the `data` folder between runs. Docker Compose creates a writable `gaming-data` volume at `/app/data`; ordinary `docker compose down` preserves it. On Railway, attach a persistent volume at `/app/data` and set `GAMING_DATABASE_PATH=/app/data/gaming.sqlite3`; without a volume, redeploying loses this data. The Docker image normally runs as UID 10001, so the mounted directory must be writable by that user. Railway mounts volumes as root and documents `RAILWAY_RUN_UID=0` as its compatibility setting (this runs the service as root). See [Railway volume permissions](https://docs.railway.com/volumes). Use one worker/replica with this SQLite database. No Railway volume or paid hosting plan is created automatically by these instructions.

## Requirements

- Python 3.12 or 3.13 for Windows hosting
- A Discord application and bot token
- A Gemini key for the default provider, or credentials/configuration for another provider

Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, and invite it with the `bot` and `applications.commands` OAuth scopes. Grant only the channel permissions it needs, such as View Channel and Send Messages; add Embed Links and Attach Files for image features. If using threads, grant Send Messages in Threads.

Slash commands work without Message Content Intent. To let the bot answer when mentioned or when someone replies to it in selected channels, set `ENABLE_MESSAGE_CONTENT=true` and list channel IDs in `INTERACTION_CHANNEL_IDS`; enable Message Content Intent for the bot in the Developer Portal as well. Automatic context is off by default (`AUTOMATIC_CONTEXT_COUNT=0`); when enabled, it reads up to 20 earlier messages in that same channel. Both the bot and the invoking user need View Channel and Read Message History permissions for that channel.

`/replyall` has separate controls: configure `REPLYALL_CHANNEL_IDS`, enable Message Content Intent in Discord and in `.env`, then a user with Manage Channels permission or an ID in `BOT_ADMIN_IDS` must toggle it in each channel. The bot needs View Channel and Read Message History there. Keep this feature limited to trusted channels.

## Run on Windows

From PowerShell in the repository folder:

```powershell
.\setup.ps1
```

The script finds Python 3.12 or 3.13, creates the project-local `.venv`, installs the pinned runtime requirements, and copies `.env.example` to `.env` only if `.env` does not already exist. To select a specific interpreter, use `.\setup.ps1 -PythonPath 'C:\Path\To\python.exe'`. Setup does not change PowerShell's execution policy or start the bot. If your current policy blocks local scripts, run the script for this process only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

Put your Discord token and provider keys in `.env`, which is ignored by Git. Check the configuration offline, then start the bot:

```powershell
.\run.ps1 -CheckConfig
.\run.ps1
```

Keep that PowerShell window open while hosting on your PC. `run.ps1` uses the repository's `.venv` and returns the bot process exit code. The check validates local settings only; it does not contact Discord or verify that credentials, quotas, or models work.

`run.ps1` loads bot settings from the local `.env` file. The explicit `--env-file` option ignores inherited bot settings and credentials that are absent from that file, and treats `${NAME}` literally rather than expanding it from the process environment. Running `main.py` without that option keeps the usual environment-based behavior for cloud hosting.

## Run on Railway

The repository includes a Dockerfile and Railway configuration for one always-on worker process. To host it, connect this repository as a Railway service and deploy from the repository root. Add `DISCORD_BOT_TOKEN` and the provider keys as Railway service variables; add other settings from `.env.example` as needed. Do not put secret values in Git, README files, or a committed `.env`.

This is an outbound Discord worker. It does not serve an HTTP website, so Railway does not need a public domain or health-check port for it. The checked-in Railway configuration sets one replica, disables sleeping, and restarts on failure up to the configured retry limit. This documents the deployment setup; it does not mean a Railway deployment has been performed or verified.

Do not run a PC-hosted copy and a Railway copy at the same time with the same Discord token. Run one active instance to avoid duplicate responses and conflicting session state.

Railway's Free plan includes $1 of monthly usage credit, and new Trial accounts receive a one-time $5 credit. Those credits do not guarantee enough resources for uninterrupted 24/7 operation; check the current [Railway plans and pricing](https://docs.railway.com/pricing/plans) and usage before relying on it. Provider API charges are separate.

## Providers, fallback, and costs

Set `DEFAULT_PROVIDER` and optionally `DEFAULT_MODEL`; `/provider` can also change the selection per user and channel. Available provider keys are:

| Provider | Environment variables | Notes |
| --- | --- | --- |
| Gemini | `GEMINI_API_KEY`, optional `GEMINI_MODEL` | Default candidate: `gemini-3.5-flash-lite`. Free quotas and model access vary. |
| Groq | `GROQ_API_KEY`, optional `GROQ_MODEL` | Default candidate: `openai/gpt-oss-20b`. |
| OpenRouter | `OPENROUTER_API_KEY`, optional `OPENROUTER_MODEL` | Default candidate: `openrouter/free`; the free router may select changing models. |
| Ollama | `OLLAMA_MODEL`, optional `OLLAMA_BASE_URL` | Local OpenAI-compatible endpoint; install and start the model yourself. Image input requires `OLLAMA_SUPPORTS_VISION=true` and a vision-capable model. |
| OpenAI | `OPENAI_API_KEY`, optional `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT` | Defaults to `gpt-6-luna`; set reasoning effort to `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`. Leave it empty to use the selected model's default; `none` is recommended for budget chat with GPT-6 Luna. Disabled unless `ALLOW_PAID_PROVIDERS=true`; API billing is separate from ChatGPT plans. |
| Anthropic | `ANTHROPIC_API_KEY`, optional `CLAUDE_MODEL` | Disabled unless `ALLOW_PAID_PROVIDERS=true`. |
| xAI | `XAI_API_KEY`, optional `GROK_MODEL` | Disabled unless `ALLOW_PAID_PROVIDERS=true`. |

The default fallback order is `gemini,groq,openrouter,ollama`. Only providers with configured credentials/models are available. `MAX_PROVIDER_ATTEMPTS` allows 1–3 total provider attempts (including the selected provider); each attempt is capped at 20 seconds, within the 60-second default overall request timeout. `ENABLE_PROVIDER_FALLBACK=false` turns fallback off. The bot retries rate limits, timeouts, network errors, and server errors; authentication, safety, and other non-retryable failures stop the request. A retryable failure puts that provider on a fixed 30-second cooldown. Image requests only go to configured providers that advertise vision support; Groq and the default OpenRouter free router are text-only, and Ollama vision is opt-in.

The bot cannot detect whether your Gemini or Groq account has paid billing enabled. Use provider accounts configured for free usage or disable paid billing there if you need that boundary. OpenRouter's default free router and free model IDs are restricted unless you opt into paid providers. `ALLOW_PAID_PROVIDERS` is a bot-side gate, not a provider billing cap.

For Gemini's current model list, pricing, and limits, see Google's [models](https://ai.google.dev/gemini-api/docs/models), [pricing](https://ai.google.dev/gemini-api/docs/pricing), and [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits). Google says data from unpaid Gemini API services may be used to improve its products, so do not send sensitive information on that tier. Groq publishes its current [free-plan model rate limits](https://console.groq.com/docs/rate-limits) and [billing details](https://console.groq.com/docs/billing-faqs). OpenRouter lists [free models](https://openrouter.ai/collections/free-models/) and its [free-model usage limits](https://openrouter.ai/docs/faq#how-does-the-free-models-router-work). Availability, terms, quotas, and billing can change at those services.

### Web and image data

Set `TAVILY_API_KEY` to use `/search`; web search is enabled automatically when a key is present, unless `ENABLE_WEB_SEARCH` overrides it. Tavily currently lists 1,000 free API credits per month; credits can run out, after which searches stop until reset or upgrade. See [Tavily pricing](https://www.tavily.com/pricing). Search queries are sent to Tavily. Search results, fetched page text, chat history, and image inputs are sent to the selected AI provider as needed to answer; check both providers' data terms and avoid sensitive content.

Web browsing accepts only public HTTP/HTTPS pages served as HTML or plain text. It does not execute scripts or access authenticated pages. URL validation and DNS checks block private and reserved network addresses. Discord image uploads are limited to 5 MB and 20 million pixels and are decoded for validation before use.

## Configuration reference

See [.env.example](.env.example) for all supported settings and defaults. Important access settings include:

- `ALLOWED_GUILD_IDS` and `ALLOWED_CHANNEL_IDS` to restrict where the bot accepts interactions.
- `BOT_ADMIN_IDS` for configured bot administrators.
- `INTERACTION_CHANNEL_IDS`, `ENABLE_MESSAGE_CONTENT`, and `AUTOMATIC_CONTEXT_COUNT` for mention/reply listening and optional channel context.
- `REPLYALL_CHANNEL_IDS` for administrator-controlled reply-all mode.

Run `.\run.ps1 -CheckConfig` on Windows, or `python main.py --check-config` elsewhere, to validate configuration without connecting to Discord. It does not verify credentials, quota, or model availability.

## Docker

Copy `.env.example` to `.env`, fill in the required values, then run:

```sh
docker compose up --build -d
docker compose logs -f
docker compose down
```

The container runs as a non-root user with a read-only root filesystem and needs outbound network access, but no inbound port.

## Development

```sh
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
mypy src main.py utils
pytest
```

The GitHub Actions workflow runs lint, formatting, type checks, tests, bytecode compilation, dependency consistency checks, and Python 3.12/3.13 checks.

## License

See [LICENSE](LICENSE).
