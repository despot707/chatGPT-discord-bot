# Prepaid allowances implementation plan

Goal: prepare $0.99 Basic, Plus and Premium and prepaid add-ons without opening sales or altering the live service.
Architecture: durable per-server grants, atomic pre-dispatch reservations, conservative fixed request ceilings, one metered OpenAI adapter, aggregate storage admission, and private plan UI. No public grant endpoint. Receipt verification and host-native limits are mandatory launch gates.

1. Write failing tests for plan economics, grants/replay/revocation, cross-server isolation, concurrency, expiration and storage. Implement a versioned catalog and SQLite ledger with integer USD micro-units.
2. Write failing tests for missing scope/unknown model, overlong inputs, exhausted allowance, timeout retention and no fallback. Implement metered provider requests and thread server scope through all completion/image routes.
3. Gate other network methods, core commands and all persistent profile/chat/gaming writes. Keep deletion/help/privacy free. Test failure paths and partial shutdown.
4. Add private /plans and /usage, clearly mark add-ons and unavailable purchase buttons. Add operational launch report requiring real receipt validation, fee floor, model smoke tests, funding for fixed costs, Railway compute/Agent caps and OpenAI project hard cap. Do not generate keys or enable payments.
5. Run full existing tests, lint, types, compile, and new regression tests. Commit to an isolated preparation branch, not the automatically deployed production branch.

Constraints: preserve current bot name, current icon, recent context and privacy; no user passwords; no auto top-ups; no credit from client claims or Discord test entitlements; no assumption that a base hosting bill disappears with zero customers. Unknown/stale prices, currencies, incomplete hosting attestation or missing funding must deny commercial calls.
