# Arma Reforger — Auto Queue Bot

A lightweight Windows GUI tool that automates joining full Arma Reforger servers. Select your server and preferred team, click Join, and let it handle everything — from navigating the game menus to securing your team slot the moment one opens.

> **v0.2 Alpha** — core functionality is (mostly) stable. Please open an issue if you encounter a bug.

---

## What it does

1. Pulls a live server list from the [BattleMetrics](https://www.battlemetrics.com) public API — no account needed
2. Displays servers with real-time player counts (via direct A2S server queries) and ping
3. Launches Arma Reforger via Steam automatically if it isn't already running
4. Navigates through the game's menus autonomously — title screen, main menu, server browser
5. Searches for your chosen server and attempts to join
6. If the server is full it cancels and retries instantly with no fixed delay
7. If mods need to download it waits patiently and resumes automatically
8. Once in queue it shows a success message and waits for the deployment screen
9. Automatically navigates deployment dialogues and secures your preferred team slot (BLUFOR or OPFOR) the moment one opens — or hands control back to you if **Manual** is selected

---

## How it works (transparency)

This tool automates mouse clicks and keypresses inside the Arma Reforger game window using standard Windows accessibility APIs — the same APIs used by screen readers and automated testing tools. It does **not** modify any game files, memory, or network traffic.

Screenshots of the game window are taken periodically to read on-screen text (OCR) and detect queue status. Screenshots are processed entirely in memory and are never saved or transmitted anywhere.

No data about you or your system is collected. The only outbound network requests are to the public BattleMetrics API (server list) and direct UDP queries to game servers (real-time player counts).

---

## Requirements & Setup

**Step 1 — Install Python** (3.10 or newer): [python.org/downloads](https://www.python.org/downloads/)

**Step 2 — Install dependencies:**

```
pip install pyautogui Pillow pytesseract pywin32 requests psutil
```

> **Tesseract OCR** is required for screen reading but installs automatically and silently on first run — no manual download needed.

**Step 3 — Run the bot:**

```
python arma_queue_bot.py
```

**Step 4 —** Search for your server, select your preferred team (BLUFOR / OPFOR / Manual), and click **Join**

> Designed for **1920x1080** but automatically scales to any resolution at runtime.

---

## Team selection modes

- **BLUFOR** — bot navigates the deployment dialogues and secures a BLUFOR slot the moment one opens
- **OPFOR** — same, but for OPFOR
- **Manual** *(default)* — bot stops once you're in the queue. You navigate the deployment screens and pick your team yourself. Use this if the server has unusual factions (e.g. INDFOR) or if you prefer to choose at the moment

---

## Usage tips

- **Double-click** a server to join it immediately
- The server list **auto-refreshes every 30 seconds** — your selection is preserved across refreshes
- **Clicking a server** immediately refreshes its player count and ping
- **Move your mouse outside the game window** to pause the bot — it resumes the moment you move back in
- Select your **preferred team** before clicking Join — the bot will navigate the deployment screens and secure your slot automatically
- If mods need to download the bot waits and displays download status — do not cancel the download manually
- If the server has an **INDFOR faction** the bot stops and notifies you to select a team manually
- If you **join a server manually** while the bot is open, a small banner appears offering to automate team selection
- The bot stops automatically once you have joined the server or secured a team slot

---

## Safety features

- **Foreground-window gate on team selection** — the bot will never click a "team available" pixel unless the Arma Reforger window is actually focused. Prevents stray clicks landing on your desktop if you alt-tab during deployment.
- **Pause-on-mouse-leave** — moving your mouse out of the game window pauses the bot instantly. Move back in and it resumes.
- **Stop button** — fully halts the bot and releases any held keys.
- **Max-attempt safety limit** — bot stops automatically after 500 retries to prevent runaway loops.
- **Cached pings & A2S queries** — the bot caches ping results (10 min) and A2S player counts (10 sec) to avoid hammering game servers on every refresh.

---

## Limitations

- Requires Arma Reforger to run in **Borderless Windowed** mode for most reliable operation
- UI coordinates are calibrated for the default Arma Reforger UI — custom UI scale mods may affect reliability
- Player counts from BattleMetrics may be a few minutes old — clicking a server triggers a live refresh via A2S

---

## FAQ

**Is this safe to use? Will I get banned?**
This tool only simulates mouse clicks and keypresses — exactly what a human player does manually. It does not read or modify game memory, files, or network packets. It does not interact with BattlEye. Use at your own discretion.

**Why does it need to take screenshots?**
Screenshots are used to read on-screen text (OCR) to detect queue status, mod downloads, and deployment screens. They are processed entirely in memory and never saved or transmitted.

**Why does it use low-level Windows APIs?**
Standard Python input libraries don't work reliably with DirectX game windows. The APIs used (`SendMessage`, `mouse_event`, `keybd_event`) are the same ones used by accessibility software and automated testing tools.

**It launched but nothing is happening in-game**
Make sure Arma Reforger is running in Borderless Windowed mode (Settings → Display).

**The bot isn't detecting my team selection screen**
Some servers have additional dialogue boxes before the faction selection screen. The bot handles these automatically by pressing Enter rapidly and then holding Enter for 3 seconds, repeating until it reaches the team picker.

**I want to pick my team myself**
Set the team selector to **Manual**. The bot will get you into the queue, then stop and let you handle deployment yourself.

---

## License

MIT — free to use, modify, and distribute.
