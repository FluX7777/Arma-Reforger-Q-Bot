# Arma Reforger — Auto Queue Bot

A lightweight Windows GUI tool that automatically retries joining a full Arma Reforger server until a slot opens up. Select your server, click Join, and let it handle the rest.

> **Alpha release** — core functionality is working but edge cases may exist. Please open an issue if you encounter a bug.

---

## What it does

1. Pulls a live server list directly from the [BattleMetrics](https://www.battlemetrics.com) public API.
2. Displays servers with player count and ping, sorted by relevance to your search
3. Launches Arma Reforger via Steam automatically if it isn't already running
4. Passes splash screen, navigates to the server browser, searches for your chosen server, and attempts to join
5. If the server is full, it cancels and retries instantly — no manual clicking
6. Detects mod downloads and waits patiently without interfering
7. Shows a success message the moment you're in queue, then stops

---

## How it works

This tool automates mouse clicks and keypresses inside the Arma Reforger game window using standard Windows accessibility APIs — the same APIs used by screen readers and test automation tools. It does **not** modify any game files, memory, or network traffic.

Screenshots of the game window are taken periodically to read on-screen text (OCR) and detect queue status. These screenshots are processed entirely in memory and are never saved or transmitted anywhere.

No data about you or your system is collected. The only outbound network request is to the public BattleMetrics API to fetch the server list.

---

## Requirements & Setup

**Step 1 — Install Python** (3.10 or newer): [python.org/downloads](https://www.python.org/downloads/)

**Step 2 — Install dependencies** (one command, that's it):

```
pip install pyautogui Pillow pytesseract pywin32 requests psutil
```

> **Tesseract OCR** is required for screen reading but installs automatically and silently on first run — no manual download needed.

**Step 3 — Run the bot:**

```
python arma_queue_bot.py
```

**Step 4 —** Search for your server in the list, select it, and click **Join**

> Designed for **1920x1080** but automatically scales to any resolution.

---

## Usage tips

- **Double-click** a server in the list to join it immediately
- The server list **auto-refreshes every 30 seconds** — your selection is preserved across refreshes
- **Move your mouse outside the game window** to pause the bot at any time — it resumes the moment you move back in
- If mods need to download, the bot will wait and display the download status — do not cancel the download manually if you want the bot to continue
- The bot stops automatically once you're successfully in queue

---

## Limitations

- Requires Arma Reforger to run in **Borderless Windowed** mode for most reliable operation
- UI coordinates are calibrated for the default Arma Reforger UI layout — custom UI mods may break coordinate detection
- Player counts shown in the GUI are sourced from BattleMetrics and may be a few minutes behind the in-game browser

---

## FAQ

**Is this safe to use? Will I get banned?**
This tool only simulates mouse clicks and keypresses — exactly what you would do manually. It does not touch game memory, files, or network packets. It interacts with BattlEye in no way. That said, use it at your own discretion.

**Why does it need to take screenshots?**
Screenshots are used to read on-screen text (OCR) to detect whether the queue succeeded, failed, or whether mods are downloading. They are processed in memory only and never saved or sent anywhere.

**Why does it use low-level Windows APIs?**
Standard Python input libraries don't work reliably with DirectX game windows. The Windows APIs used (`SendMessage`, `mouse_event`) are the same ones used by accessibility software and automated testing tools.

**It launched but nothing is happening in-game**
Make sure Arma Reforger is running in Borderless Windowed mode (Settings → Display).

---

## License

MIT — free to use, modify, and distribute.
