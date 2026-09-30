# Sidecord Ai

A self-hosted Discord assistant you can mention during a conversation. It supports saved channel chat, image input, web lookups, and selectable AI providers. Run it on your PC or as one Railway worker. “ChatGPT” describes the Discord experience; ChatGPT subscriptions do not include API credits for this bot.

Public website: [Sidecord Ai](https://sidecord-ai.com/), including [Privacy](https://sidecord-ai.com/privacy/), [Terms](https://sidecord-ai.com/terms/), and [Support](https://sidecord-ai.com/support/). The deployed site and its public routes have been verified; paid plans remain a preview and sales are off. See the [commercial launch handoff](docs/commercial-launch.md) for remaining account and payment gates, and [site/README.md](site/README.md) to rebuild the site.

## Everyday chat

Mention the actual bot account in Discord, for example `@Chat GPT can you check who's right here?`. Attach a screenshot to ask about it, or reply to a message and mention the bot to supply that message as context. Public bot conversations are shared within the channel or thread, so different people can continue the discussion. Private `/chat` conversations remain separate for each person.

Reply to a native Discord poll and mention the bot to discuss its question, answer options, and available results. The replied-to message is the primary subject; nearby posts supply background. Readable context includes poll data, embed cards, and visible component text such as button labels and select options. It does not press buttons or cast votes. Finalized poll counts include zero-vote options; unavailable counts are labeled unknown instead of assumed to be zero. Discord's [poll API](https://docs.discord.com/developers/resources/poll) can omit live results, and the current library cannot reliably distinguish every omitted count from zero. Reading another message requires channel-history permissions, and deleted or inaccessible references are identified as unavailable.

Enable `ENABLE_OPENAI_WEB_SEARCH=true` with an OpenAI model that supports Responses web search, such as GPT-6 Luna. The model can search when answering factual or current questions and show clickable source links. This uses the existing OpenAI key and incurs additional search charges; it is limited to one tool call per answer. URLs in an invocation can also be read using the public-page browser. Websites requiring login or JavaScript are not supported.

Only completed conversations with the bot are saved, in `CHAT_DATABASE_PATH` (default `data/chat.sqlite3`). History is bounded by the configured message, character, session, and retention limits; the default retention is 30 days. It survives normal restarts. Nearby channel messages and uploaded image bytes are used for the current request, not saved as a background channel archive. Keep the database private. `/reset` clears your private chat here; `/reset channel:true` clears the shared channel conversation and requires Manage Channels or bot administrator permission. Personal provider/style/privacy settings reset on restart or idle expiry.

Expiration is checked when chat history is accessed: expired turns are excluded from the next answer and deleted during that operation. An idle or stopped bot does not purge its database on a timer.

## Commands and behavior

- `/chat` sends a prompt to the selected provider. It accepts an optional image attachment, `use_web`, and `context_messages` from 0 to 20. Replies are private by default; `/private` changes that setting for your chats in the current channel.
- `/search(query)` searches with Tavily and asks the selected AI provider to summarize up to five returned sources.
- `/browse(url, question)` fetches and summarizes a public HTML or plain-text page. It does not run JavaScript or sign in to websites.
- `/image(url, caption)` fetches an existing public image and sends it using your private/public reply setting. This is not AI image generation.
- `/draw(prompt)` requests image generation when enabled and supported. In this build, generation is an optional paid OpenAI feature: it requires `ENABLE_IMAGE_GENERATION=true`, `ALLOW_PAID_PROVIDERS=true`, an OpenAI API key, and a compatible model. There is no free image-generation provider configured by default.
- `/provider`, `/reset`, `/switchpersona`, `/status`, and `/help` select a provider, clear your conversation, select a style, show local settings, and list commands. `/replyall` toggles replies to ordinary channel messages for administrators in explicitly configured channels.

The bot does not provide general Discord control, member moderation, or self-bot behavior. `/image` sends an existing image; `/draw` generates a new one only when the optional paid feature is enabled.

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

For a public launch before paid AI access is available, set `AI_ACCESS_MODE=disabled`
in the bot's environment. Chat, image generation, and model-backed search/browse
then return a generic unavailable message without calling an AI provider. Discord,
Steam profiles, parties, game matching, and team balancing remain available.
`AI_ACCESS_MODE=personal` is the legacy default and permits existing AI behavior;
it is not a paid-access check. Restart the bot after changing this setting.

- Python 3.12 or 3.13 for Windows hosting
- A Discord application and bot token
- A Gemini key for the default provider, or credentials/configuration for another provider,
  when `AI_ACCESS_MODE=personal`

Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, and invite it with the `bot` and `applications.commands` OAuth scopes. Grant only the channel permissions it needs, such as View Channel and Send Messages; add Embed Links and Attach Files for image features. If using threads, grant Send Messages in Threads.

Slash commands and explicit bot mentions work without Message Content Intent. Empty `INTERACTION_CHANNEL_IDS` allows explicit mentions in every channel permitted by the server/channel allowlists; fill it to restrict mentions. For recent channel context and replies without an explicit mention, set `ENABLE_MESSAGE_CONTENT=true` and enable Message Content Intent in the Developer Portal. `AUTOMATIC_CONTEXT_COUNT` defaults to 10 and can be set from 0 to 20. Both the bot and invoking user need View Channel and Read Message History for that channel. Without access to recent history, the bot can still answer the current mention. See [Discord's message-content rules](https://docs.discord.com/developers/events/gateway#message-content-intent).

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

To run in the background instead, use `start-local.ps1`. Use `local-status.ps1` to check it and `stop-local.ps1` to stop it. Logs are written under `logs/`; the scripts track the local process and refuse to stop an unrelated process. Your PC must stay on, awake, and connected to the internet. After a reboot, start the bot again. Keep `.env` and `data/` between runs.

For a desktop shortcut, target Windows PowerShell with `-NoProfile -NoExit -ExecutionPolicy Bypass -File "<repository path>\desktop-bot.ps1"` and use a normal window. The wrapper starts the background bot if needed and keeps status or startup errors visible. Closing this status window leaves the bot running.

`run.ps1` loads bot settings from the local `.env` file. The explicit `--env-file` option ignores inherited bot settings and credentials that are absent from that file, and treats `${NAME}` literally rather than expanding it from the process environment. Running `main.py` without that option keeps the usual environment-based behavior for cloud hosting.

## Run on Railway

The repository includes a Dockerfile and Railway configuration for one always-on worker process. To host it, connect this repository as a Railway service and deploy from the repository root. Add `DISCORD_BOT_TOKEN` and the provider keys as Railway service variables; add other settings from `.env.example` as needed. Do not put secret values in Git, README files, or a committed `.env`.

This is an outbound Discord worker. It does not serve an HTTP website, so Railway does not need a public domain or health-check port for it. The checked-in Railway configuration sets one replica, disables sleeping, and restarts on failure up to the configured retry limit. This documents the deployment setup; it does not mean a Railway deployment has been performed or verified.

For chat persistence, mount a writable volume at `/app/data` and set `CHAT_DATABASE_PATH=/app/data/chat.sqlite3`. Stop the PC copy before starting Railway. To migrate history, stop both copies and transfer `data/chat.sqlite3` securely onto the volume; treat it as private chat data. Without a volume, redeployments can erase saved conversations.

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

The default fallback order is `gemini,groq,openrouter,ollama`. Only providers with configured credentials/models are available. `MAX_PROVIDER_ATTEMPTS` allows 1–3 total provider attempts (including the selected provider); ordinary attempts are capped at 20 seconds and OpenAI search attempts at 45 seconds, within the 60-second default overall request timeout. `ENABLE_PROVIDER_FALLBACK=false` turns fallback off. The bot retries rate limits, timeouts, network errors, and server errors; authentication, safety, and other non-retryable failures stop the request. A retryable failure puts that provider on a fixed 30-second cooldown. Image requests only go to configured providers that advertise vision support; Groq and the default OpenRouter free router are text-only, and Ollama vision is opt-in. If a search-enabled request uses a provider without native search, the reply discloses that live web verification was unavailable.

The bot cannot detect whether your Gemini or Groq account has paid billing enabled. Use provider accounts configured for free usage or disable paid billing there if you need that boundary. OpenRouter's default free router and free model IDs are restricted unless you opt into paid providers. `ALLOW_PAID_PROVIDERS` is a bot-side gate, not a provider billing cap.

For Gemini's current model list, pricing, and limits, see Google's [models](https://ai.google.dev/gemini-api/docs/models), [pricing](https://ai.google.dev/gemini-api/docs/pricing), and [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits). Google says data from unpaid Gemini API services may be used to improve its products, so do not send sensitive information on that tier. Groq publishes its current [free-plan model rate limits](https://console.groq.com/docs/rate-limits) and [billing details](https://console.groq.com/docs/billing-faqs). OpenRouter lists [free models](https://openrouter.ai/collections/free-models/) and its [free-model usage limits](https://openrouter.ai/docs/faq#how-does-the-free-models-router-work). Availability, terms, quotas, and billing can change at those services.

### Strict API spending limit

Set `HARD_BUDGET_ENABLED=true` for a shared, persistent admission limit across all
Discord users, servers, slash commands, and mention replies. The default plan is
`BUDGET_MONTHLY_USD=10` with `BUDGET_LUNA_USD=7` protected for GPT-6 Luna and the
remaining $3 for extras. Amounts above $10 are rejected in this mode.

Each pool gets its monthly allocation divided by the number of days in that
calendar month, rounded down to a microdollar. In a 30-day month, that is about
$0.233333/day for Luna and $0.10/day for extras; in a 31-day month it is about
$0.225806 and $0.096774. Unused daily allowance does not carry forward. Daily
limits reset at midnight in `BUDGET_TIMEZONE` (default `America/Los_Angeles`, with
daylight-saving changes handled by the timezone database). Monthly spending is
not erased by the daily reset. The monthly stop always takes precedence.

The bot counts input tokens, including images and request structure, before
generation and reserves enough for the input plus the maximum output. Native
web search reserves a separate amount from extras, including its tool fee and
a conservative allowance for returned context. A SQLite transaction makes these
reservations atomic across concurrent requests sharing the same database.
Verified usage can reduce a reservation. Timeouts, interrupted requests, missing
usage, and uncertain results keep their full reservation; restarting does not
restore it. This is intentionally more conservative than an invoice estimate.

Ordinary chat can continue without live search after extras run out. Explicit
search requests stop when they cannot reserve their maximum cost. Strict mode
pins chat to GPT-6 Luna and Standard processing. Ordinary chat uses no extra
reasoning and at most 500 output tokens. An explicit reasoning request uses low
effort and at most 2,000 total output tokens. It blocks other providers,
expensive model overrides, Tavily searches,
and image generation. Voice is not implemented. These paths must have verified
cost bounds before being enabled; allowing a model in the OpenAI dashboard does
not enable that feature in the bot. Existing-image posting and public-page
fetching do not themselves call a paid AI model; summarizing a page uses the
metered Luna path.

Reasoning is not a separate free allowance: any reported reasoning tokens are
included in the API's total output-token usage, which is charged to Luna's $7
pool. Each response's cap covers reasoning and visible output together. Strict
mode defaults to `reasoning.effort=none`, even if a different effort is
configured. `/chat reason:true` or a direct request such as "think carefully
about this" enables low reasoning for that request only. It does not change
the next message or select a more expensive chat model. See OpenAI's
[reasoning cost controls](https://developers.openai.com/api/docs/guides/reasoning#controlling-costs).

Discord requests explicitly select reasoning per message, including outside
strict mode: an environment reasoning preference does not silently turn it on.
Web access remains an innate capability the model may use when needed; it does
not require a separate request. Explicit `/search` still requires a search.
Mention requests such as "draw a blue bird" route only to the image handler,
never to an initial chat completion. Quoted commands, prior replies, and phrases
such as "draw a conclusion" do not activate extra capabilities. Image generation
remains disabled in strict mode because GPT Image 2.5 has no documented enforced
pre-request output-token or charge ceiling; output cost estimates are not a hard cap.

Provider and budget failures in normal Discord replies say "I can't do that
right now." without exposing spending, quota, or provider configuration details.
`/budget` is an explicitly
requested private administrator report; it is not advertised by `/status`.

Before activation, record the project's existing calendar-month API spend in
`BUDGET_OPENING_MONTH_SPEND_USD`. A blank value means unknown and blocks paid
generation during the first month until supplied. Do not enter zero unless it
has been verified. The baseline is imported once and cannot reset existing
spend. It counts against the total ceiling; the Luna/extras split tracks spending
after activation because the earlier category breakdown is unknown.
`/budget` shows the shared limits, reservations, remaining allowances,
and next resets without calling an AI model.

Keep `BUDGET_DATABASE_PATH` on persistent storage. On Railway use a persistent
volume, stop the PC bot first, and transfer the budget database along with chat
data while both copies are stopped. Never run independent ledgers against one
paid project. Do not delete this database to regain allowance. The app cannot
cap charges from other programs, previously untracked requests, taxes, hosting,
or future provider price changes. Use a dedicated OpenAI project/key and enable
the project's $10 hard spending limit as an additional backstop. OpenAI notes
that provider enforcement can lag slightly, so it complements the bot's
pre-request checks rather than replacing them.

References: [token counting](https://developers.openai.com/api/docs/guides/token-counting),
[Luna pricing](https://developers.openai.com/api/docs/models/gpt-6-luna),
[search limits](https://developers.openai.com/api/docs/guides/tools-web-search#limitations),
and [project hard limits](https://developers.openai.com/api/docs/guides/spend-limits).

### Web and image data

OpenAI native search uses `ENABLE_OPENAI_WEB_SEARCH=true` and a supported Responses model, without a Tavily key. It is optional per answer and uses low search context. Strict mode requests at most one built-in tool call with parallel calls disabled. Accounting distinguishes paid `search` actions from `open_page` and `find_in_page` activity; page content still consumes input tokens. Missing or unknown action types count conservatively as paid searches. Multiple paid searches, unexpected tool use, or usage exceeding a reservation still lock paid requests. Internal diagnostics record only usage numbers and normalized action counts, never prompts, queries, or page contents. Tool calls and tokens are billable; output/cooldown limits are not a monthly spending cap. See [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search).

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
