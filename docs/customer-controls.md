# Compact customer controls

## Customer surface

Four published slash commands: `settings`, `games`, `profile`, `plans`. All are code-only. Free controls never call a text/image model. AI-assisted profile proposals and paid slash entry points are absent from the customer command manifest and panel action registry.

Settings starts with More effort, Images, Chat style, Chat & data, and Plan & usage. Settings and games panels are ephemeral, owner/server/channel-bound, expire after ten minutes, and reject stale buttons, stale/replayed forms, and actions after closing. An operator diagnostics entry appears only for configured `BOT_ADMIN_IDS`; it is rechecked when invoked. Manage Channels still permits shared-chat and party clearing, without exposing provider/model/budget diagnostics.

Images defaults on to preserve existing explicit generation behavior; More effort defaults off. Both are free, per-user/channel, in-memory preferences. Image generation still requires operator enablement and the existing image allowance. Existing histories/profiles/allowances are not deleted or rewritten by this migration. No schema migration is required.

## Paid requests

Explicit mentions and direct replies to the bot use existing atomic paid admission/accounting. Commercial reply-all ignores messages that neither mention nor directly reply to the bot. There is no language-model router call.

- Ordinary questions use chat; attached images are inputs, not image generation
- Explicit make/create/generate/draw image requests go directly to the existing image operation
- Explicit thinking requests or the More effort preference use the existing reasoning operation
- Explicit web requests use the existing required-search operation; automatic web behavior remains otherwise unchanged
- Explicit search wins over a saved More effort preference; asking explicitly for both is declined before provider work
- Quoted/code/negated examples and non-image idioms do not activate extras
- The parser recognizes supported natural phrases, not every possible wording or arbitrary external action

Read-only allowance feedback explains missing/exhausted features. It does not reserve units, authorize providers, or replace the gateway’s atomic enforcement. Prices, product IDs, included quantities, renewal semantics, paid budget code, and persistent accounts are unchanged.

## Provider and privacy boundary

Provider/model selection is operator-owned and normalized from configuration even for old in-memory preferences and request snapshots. There is no customer provider picker. Retained diagnostics require configured operator identity, including direct invocation of the legacy status callback. Normal provider exceptions, technical errors, and fallback attribution are not disclosed.

Narrow deterministic input checks run before text/image dispatch for direct private-configuration questions. System instructions reinforce this, and output filtering removes recognizable self-disclosures before storing/sending them. Runtime credentials/config objects are not added to model context. These English-language text heuristics are defense in depth, not a guarantee against every paraphrase, encoding, or indirect prompt injection. Authoritative published privacy notices remain available and are not replaced by these filters.

## Command registration migration

1. Build the existing handlers without dispatching them; preserve needed code-only handlers as private application objects
2. Remove retired roots locally, including provider, chat, draw, search, browse, image, remember, reset, private, switchpersona, status, help, usage and old gaming/profile aliases
3. Publish only settings/games/profile/plans using the existing single global `CommandTree.sync()`
4. `discord.py` 2.7.1 implements this as a global bulk overwrite. The regression test seeds a simulated old remote manifest and verifies it is replaced, rather than appended to
5. Repeated setup does not re-add aliases. This repository does not register guild-scoped command copies; independently created guild commands would require a separate inventory/removal

Discord clients may cache an old command list briefly. Do not claim immediate live client verification from the offline schema tests. After an authorized rollout, verify the remote global manifest and a refreshed client menu. Testing `/settings`, `/games`, `/profile`, or `/plans` does not require an AI attempt; avoid a paid mention smoke test without the relevant approval.

## Checkout guidance

Native SKU checkout remains unchanged, with a separate verified Discord store link labeled for computer use. Guidance explains that purchases require the desktop app or a supported computer browser. It does not promise iPhone Safari support or interpret mobile “Product Unavailable” as a SKU-publication failure.

Official references:
- https://support-apps.discord.com/hc/en-us/articles/26501767768471-Premium-App-FAQ
- https://support.discord.com/hc/en-us/articles/213491697-What-are-the-OS-system-requirements-for-Discord
- https://docs.discord.com/developers/monetization/managing-skus#linking-to-your-store
