# Gaming growth and release handoff

**Status: local changes are not deployed.** This note records current evidence and a bounded organic rollout. It makes no conversion, retention, revenue, or payout claims.

## Product work

- The product story leads with free gaming-community tools: private-by-default profiles, member-shared game discovery, channel parties, and skill-based team balancing.
- `/settings` and `/games` now expose a private first-use guide with practical setup steps and direct paths to profile, party, player search, usage, and AI plans.
- Player-search empty copy describes an empty catalog result, not missing member sharing. The guide explains that people appear only after they choose to share a game with the server.
- Public-site copy and a concise Discord directory description are drafts. No directory submission or approval is claimed.
- The guide and game-night tools require no paid AI call. No paid calls were made for this work.

### Directory description draft

> Find server members who share your games, gather a channel party, and build balanced teams using member-reported skill. Profiles are private by default; only games members choose to share appear in player search. Free game-night tools, with optional paid AI plans.

Review this copy against the deployed commands and directory limits before submitting. Do not claim Steam imports, voice features, or listing approval.

## Verified production state

Read-only Railway evidence reports one running replica with its existing volume. The deployed commit is `f87edb9bc28c25295c128598bc1bf8b3a660dacf`; deployment `659f2879-626d-4478-93ee-b3fe89549d4e` is `SUCCESS`. It is the same runtime as audited main `9950185`. The latest available log, `2026-10-24T02:49:35-07:00`, reports accounting unlocked, zero holds, and `financial_complete: false`.

The public site returns HTTP 200, but currently sends `noindex,nofollow` and its robots file blocks crawling. The product, guide, and copy changes in this handoff are not deployed. Current host evidence does not establish hosting cost, collected payout, or completed financial reconciliation; these are unknown.

Current publish access is insufficient: Cloudflare identity lacks Pages write permission, the Pages project-list request returned Authentication10000, and Railway CLI authentication failed. The connected Railway inspection used read-only calls. Do not treat an HTTP 200 as a current indexable release.

## Local verification

The complete Windows regression suite passed with 812 tests and 34 platform skips, with the existing `audioop` deprecation warning. Ruff lint and formatting, full mypy (43 source files), Python compilation, dependency consistency, and diff whitespace checks passed. The website passed 105 checks in live mode and 80 in preview mode, each covering seven routes. Desktop and 360/390-pixel mobile browser checks cover layout and the reviewed plan links. Linux and Docker checks run separately in GitHub Actions; local Windows skips do not establish those results.

The first fresh release audit failed on previously pinned dependencies. The release updates aiohttp to 3.14.4, Pillow to 13.0.0, certifi to 2026.10.19, and the development-tool dependency filelock to 4.2.1. An isolated Python environment passed dependency consistency and the full development-requirements vulnerability audit with no known vulnerabilities; the application regression suite also passed with the updated runtime packages. Existing shared Python environments were left unchanged.

## Organic rollout

1. Keep the production source and attached volume intact. Build and verify a tested commit before any deployment; do not reset, replace, or migrate the live volume as part of a copy update.
2. Before wider installation, run an owner-approved test-server smoke pass through `/profile` sharing, `/games` player matching, party join/show/leave, team creation, and the private first-use guide. Check `/settings` and `/plans` navigation without invoking paid AI.
3. Invite a small number of gaming communities through owner-approved, direct introductions. Ask admins to test the share → find players → party → teams loop and report friction voluntarily.
4. Prepare source-accurate directory listings from the draft above. Pursue organic directory placement only after access, eligibility, and the deployed product description are verified. No paid ads are planned here.
5. Publish only after deployment permissions are available, the tested revision is selected, and the test-server smoke pass succeeds. Separately verify the site's index directives and robots rules before claiming search visibility.

## 30-day measurement checklist

There is currently no retention or conversion instrumentation. Do not infer activity from deployments, page requests, profile records, or entitlements.

- **Day 0:** Record the date, participating pilot servers, and what each admin voluntarily confirms as the starting point. Use aggregate counts; do not add member-level tracking for this handoff.
- **Weekly:** Count pilot servers that voluntarily confirm at least one completed game-night flow (a shared game followed by a player search, or a party followed by team creation). Mark unconfirmed activity unknown, not zero.
- **Days 7 and 30:** Request an optional admin check-in and record which pilot servers say they returned to use the game-night tools. This is a small voluntary observation, not a measured retention rate.
- **When financial evidence exists:** Record actual authenticated paid receipts separately from actual payouts; record actual provider costs and hosting bills from their source records. Keep unknown values unknown and label the period and source.
- **Day 30:** Summarize observed pilot-server counts, voluntary return reports, actual paid receipts, actual provider costs, hosting evidence, and payout evidence. State sample size and missing data; do not invent conversion rates or extrapolate from the pilot.
