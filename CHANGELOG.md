# Changelog

## v0.2a Alpha

**Behavioural changes**

- Renamed team preference **"Any" → "Manual"** with new semantics: Manual mode now stops the bot once you're in the queue and hands control back to you for deployment. The previous "Any" mode (auto-grab whichever team opens first) has been removed because it caused unintended team selection in mixed servers.
- Default team preference is now **Manual** — opt-in to BLUFOR/OPFOR auto-selection.

**Reliability fixes**

- **Anti-ghost-click safety on team selection** — `is_team_available()` now bails out unless the Arma Reforger window is the foreground window. Fixes a bug where alt-tabbing during deployment caused `ImageGrab` to read black pixels from the backgrounded DirectX surface, satisfying the "available" threshold and triggering phantom clicks on the desktop.
- **Enter-hold during deployment navigation actually holds Enter now** — previously the 3-second "hold" was implemented as a single `keydown → sleep(3) → keyup` pair, which on most window-message paths only fires one Enter press. Now the bot rapidly re-fires `keydown` at ~30Hz for the full 3 seconds, mirroring hardware key-repeat. Properly releases the key on early exit (Stop button) so Enter is never left stuck down.
- **Resolution scaling fixed for game state detection** — `GAME_STATES` (server browser, deployment setup, faction selection) and `is_deployment_screen()` were using hardcoded 1920x1080 coordinates without scaling. They now scale to your actual screen resolution like the rest of the bot.

**Performance & polish**

- **A2S player count cache (10s TTL)** — stops the bot from hammering every game server with UDP queries on every 30s refresh. Player counts still update fast enough to feel real-time.
- **Ping cache TTL (10 min)** — replaces the previous "never expires" behaviour. Prevents stale ping values surviving forever if your network conditions change mid-session.
- Hoisted `socket` to top-level imports; dropped unused `struct` import.

---

## v0.2 Alpha

- Added team auto-selection — bot secures a BLUFOR or OPFOR slot the moment one becomes available on the faction selection screen
- Team preference selector added to GUI (BLUFOR / OPFOR / Any)
- Bot now navigates all deployment screens automatically after joining a server or queue — presses Enter rapidly then holds it to pass any intermediate dialogue boxes
- Deployment screen navigation works from any entry point: queue success, direct join, or manual GUI banner trigger
- If the server has an INDFOR faction the bot stops and notifies the user to select manually
- If the user manually joins a server while the bot is open, a small banner in the bottom-right corner offers to automate team selection
- Live player counts via A2S protocol — direct UDP queries to each server bypass BattleMetrics delay
- Clicking a server in the list immediately refreshes its player count and ping
- Ping cached per session — no more jitter or flickering on server list refresh
- Bot now correctly waits for the "Faction Selection" screen (confirmed via OCR) before attempting to pick a team — prevents false team clicks while still in queue
- Mod download cancellation detection improved — higher threshold and re-check to prevent loading screen from triggering a false cancellation
- Status bar updated to show relevant message for every bot state including "In queue — waiting for deployment to BLUFOR/OPFOR"
- Tesseract auto-install URL fixed (v5.5.0, correct repository)
- Fixed success message being overridden by pause message
- Fixed bot stuck when paused mid-retry between Cancel click and server re-click
- Fixed duplicate DOWNLOAD_SCAN_REGION definition
- Fixed `win32api` scoping error in do_join

---

## v0.1 Alpha

- Initial release — full rewrite from PowerShell proof-of-concept to Python GUI application
- Live server browser via BattleMetrics public API
- Real-time ping measurement for all servers
- Auto-launches Arma Reforger via Steam if not running
- Full menu navigation — title screen, main menu, server browser handled automatically
- Instant queue retry on full server with no fixed delay
- Mod download detection — waits and resumes automatically
- Detects cancelled mod downloads and stops cleanly
- Pauses when mouse leaves game window, resumes on return
- Resolution scaling — coordinates auto-adjust for any screen resolution
- Multi-monitor support — GUI launches on second monitor if available
- Tesseract OCR installs silently on first run
