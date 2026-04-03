"""
Arma Reforger Auto Queue Bot
=============================
A GUI automation tool that helps players join busy Arma Reforger servers
by automatically retrying the queue until a spot becomes available.

REQUIREMENTS
------------
  Install dependencies:

      pip install pyautogui Pillow pytesseract pywin32 requests psutil

  Tesseract OCR is installed automatically on first run if not already present.
  No manual download required.

HOW IT WORKS
------------
This script automates interaction with the Arma Reforger game client on
Windows. It does not modify any game files, memory, or network traffic.
All actions performed are identical to what a human player would do manually:

  1. It reads the live server list from the public BattleMetrics API
     (https://www.battlemetrics.com) — no authentication required.
  2. The user selects a server from the GUI and clicks Join.
  3. The bot uses Windows UI automation APIs to click on-screen buttons
     inside the game window — the same APIs used by accessibility software.
  4. It uses OCR (Tesseract) to read text from the game screen in order to
     detect queue status, mod downloads, and connection state.
  5. If the server is full, it cancels and retries immediately until a slot
     opens up.

WHY CERTAIN APIS ARE USED
--------------------------
  - ctypes / win32api / win32gui:
      Used exclusively to send mouse clicks and keypresses to the Arma
      Reforger window. This is required because standard input methods do
      not work reliably with DirectX game windows. These are the same APIs
      used by accessibility tools and test automation frameworks.

  - ImageGrab (Pillow) + pytesseract:
      Used to take screenshots of the game window and read on-screen text
      (OCR). Screenshots are never saved or transmitted — they are processed
      in memory only and discarded immediately after reading.

  - subprocess:
      Used solely to launch Arma Reforger via Steam (steam://rungameid/).
      No other processes are spawned.

  - psutil:
      Used only to check whether Arma Reforger is already running before
      attempting to launch it via Steam.

  - requests:
      Used only to fetch the public server list from the BattleMetrics API.
      No user data is sent. No accounts are created or logged into.

PRIVACY & SAFETY
----------------
  - No data is collected, stored, or transmitted beyond what is needed to
    display the server list and connect to the selected server.
  - Screenshots taken for OCR are processed entirely in memory and are
    never written to disk during normal operation.
  - No game files, memory, or executables are modified.
  - This tool does not interact with BattlEye or any anti-cheat system.
  - Source code is fully open and readable — nothing is obfuscated.

SUPPORTED RESOLUTIONS
---------------------
  UI coordinates are recorded at 1920x1080 and automatically scaled to
  match any screen resolution at runtime.

LICENSE
-------
  MIT — see LICENSE file.
"""

import ctypes
import logging
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import psutil
import pyautogui
import pytesseract
import requests
import win32api
import win32gui
from PIL import Image, ImageEnhance, ImageGrab

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ArmaQueueBot")

# ── Tesseract auto-install ──────────────────────────────────────────────

TESSERACT_PATH = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
TESSERACT_URL  = (
    'https://github.com/UB-Mannheim/tesseract/releases/download/'
    'v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe'
)

def ensure_tesseract() -> None:
    """
    Checks if Tesseract OCR is installed at the default Windows path.
    If not found, downloads and installs it silently with no user interaction.
    Tesseract is used only to read on-screen text from the game window (OCR).
    This check is skipped on all subsequent launches once installed.
    """
    import os
    if os.path.exists(TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
        return
    print('Tesseract OCR not found — downloading and installing automatically...')
    print('This only happens once. Please wait...')
    tmp = os.path.join(os.environ.get('TEMP', r'C:\Temp'), 'tesseract_setup.exe')
    try:
        resp = requests.get(TESSERACT_URL, stream=True, timeout=60)
        resp.raise_for_status()
        with open(tmp, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        # /VERYSILENT suppresses all installer UI
        subprocess.run(
            [tmp, '/VERYSILENT', '/NORESTART', '/SUPPRESSMSGBOXES'],
            check=True, timeout=120
        )
        print('Tesseract installed successfully.')
    except Exception as e:
        print(f'Auto-install failed: {e}')
        print('Please install Tesseract manually: https://github.com/UB-Mannheim/tesseract/wiki')
        raise SystemExit(1)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

ensure_tesseract()

# ── Resolution-aware UI coordinates ───────────────────────────────────────────
# All coordinates were recorded at 1920x1080. They are scaled automatically
# to match the user's actual screen resolution at runtime.

def _scale_coords() -> dict:
    sw, sh = pyautogui.size()
    sx = sw / 1920
    sy = sh / 1080
    def s(x, y): return (int(x * sx), int(y * sy))
    return {
        "search_bar":      s(1389, 127),
        "first_result":    s(1124, 240),
        "status_area":     s(648,  482),
        "cancel_btn":      s(587,  789),
        "multiplayer_btn": s(344,  790),
        "all_tab":         s(188,  121),
    }

COORDS = _scale_coords()

# Scale OCR scan regions to match resolution
def _scale_region(x1, y1, x2, y2) -> tuple:
    sw, sh = pyautogui.size()
    sx, sy = sw / 1920, sh / 1080
    return (int(x1*sx), int(y1*sy), int(x2*sx), int(y2*sy))

DOWNLOAD_SCAN_REGION = _scale_region(320, 150, 1600, 600)
TITLE_SCREEN_COORD   = (int(941 * pyautogui.size()[0] / 1920),
                        int(866 * pyautogui.size()[1] / 1080))

# ── Configuration ──────────────────────────────────────────────────────────────

SCAN_INTERVAL         = 0.5    # Seconds between queue status scans
POST_CLICK_WAIT       = 0.8    # Seconds to wait after a click for UI to update
INITIAL_SCAN_DELAY    = 1.5    # Seconds to wait after clicking Connect before first scan
STATUS_REGION_PAD     = 400    # Pixels around status area to capture for OCR
SERVER_REFRESH_SEC    = 30     # How often to auto-refresh the server list
STEAM_REFORGER_ID     = "1874880"
BATTLEMETRICS_URL     = (
    "https://api.battlemetrics.com/servers"
    "?filter[game]=reforger"
    "&filter[status]=online"
    "&sort=-players"
    "&page[size]=100"
)

FAIL_KEYWORDS = [
    "queue is full", "server is full", "failed", "full",
    "unable to join", "try again", "no slots",
]
SUCCESS_KEYWORDS = [
    "queue position",
    "in queue",
    "leave queue",
    "connecting to server",
    "joining server",
]
DOWNLOAD_KEYWORDS = [
    "downloading required",
    "connected to the server",
    "will be connected",
]

# ── Window handle ──────────────────────────────────────────────────────────────

def get_reforger_hwnd() -> int | None:
    """Find and return the Arma Reforger window handle."""
    result = []
    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            if "reforger" in title or "arma" in title:
                result.append(hwnd)
    win32gui.EnumWindows(enum_handler, None)
    return result[0] if result else None

def is_game_active() -> bool:
    """Returns True if game is focused AND mouse is inside the game window."""
    hwnd = get_reforger_hwnd()
    if not hwnd:
        return True
    return (
        ctypes.windll.user32.GetForegroundWindow() == hwnd and
        is_mouse_in_reforger()
    )

def wait_if_paused(status_cb, stop_flag: list, state_ref: list) -> bool:
    """
    Single unified pause gate. Blocks until game is active again.
    Updates GUI status instantly. Returns False if stop_flag is set.
    Does not override success message.
    """
    if is_game_active():
        return True
    # Don't override the success message
    if state_ref[0] != "success":
        if state_ref[0] == "downloading":
            status_cb("⏬️ Downloading mods... (return to Arma Reforger when done)")
        else:
            status_cb("⏸️ Paused — return to Arma Reforger to continue, or click Stop.")
    log.info("  Bot paused")
    while not stop_flag[0]:
        if is_game_active():
            log.info("  Bot resumed")
            return True
        time.sleep(0.1)
    return False

def start_pause_watcher(status_cb, stop_flag: list, state_ref: list) -> None:
    """Watches mouse/focus 10x per second and instantly updates GUI status."""
    def _watch():
        was_active = True
        while not stop_flag[0]:
            active = is_game_active()
            if was_active and not active and state_ref[0] != "success":
                # Just left the window
                if state_ref[0] == "downloading":
                    status_cb("⏬️ Downloading mods... (return to Arma Reforger when done)")
                else:
                    status_cb("⏸️ Paused — return to Arma Reforger to continue, or click Stop.")
            elif not was_active and active and state_ref[0] != "success":
                # Just returned to the window — restore appropriate status
                if state_ref[0] == "downloading":
                    status_cb("⏬️ Downloading mods — please wait, do not interrupt...")
                elif state_ref[0] == "fail":
                    status_cb(f"⛔ Queue full — retrying...")
                elif state_ref[0] == "unknown":
                    status_cb("🔍 Scanning...")
            was_active = active
            time.sleep(0.1)
    threading.Thread(target=_watch, daemon=True).start()

def is_mouse_in_reforger() -> bool:
    """Returns True if the mouse cursor is within the Arma Reforger window bounds."""
    hwnd = get_reforger_hwnd()
    if not hwnd:
        return True  # Can't determine, assume OK
    try:
        rect = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
        mx, my = pyautogui.position()
        return rect[0] <= mx <= rect[2] and rect[1] <= my <= rect[3]
    except Exception:
        return True

def force_focus_reforger() -> bool:
    """Move mouse to game window so clicks land correctly."""
    hwnd = get_reforger_hwnd()
    if not hwnd:
        log.warning("Could not find Arma Reforger window")
        return False
    try:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        log.info("  Game window focused")
        return True
    except Exception as e:
        log.warning(f"  Focus failed: {e}")
        return False

# ── Windows low-level input ────────────────────────────────────────────────────

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004

# Windows messages
WM_KEYDOWN = 0x0100
WM_KEYUP   = 0x0101
WM_CHAR    = 0x0102

VK_RETURN  = 0x0D
VK_DELETE  = 0x2E
VK_A       = 0x41

def win_click(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, x, y, 0, 0)

def win_keypress(vk_code: int) -> None:
    """Send keypress via multiple methods to maximise compatibility."""
    # Method 1: SendMessage to window handle
    hwnd = get_reforger_hwnd()
    if hwnd:
        win32gui.SendMessage(hwnd, WM_KEYDOWN, vk_code, 0)
        time.sleep(0.05)
        win32gui.SendMessage(hwnd, WM_CHAR, vk_code, 0)
        time.sleep(0.05)
        win32gui.SendMessage(hwnd, WM_KEYUP, vk_code, 0)
        time.sleep(0.1)
        # Method 2: PostMessage (async) to window handle
        win32api.PostMessage(hwnd, WM_KEYDOWN, vk_code, 0)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, WM_KEYUP, vk_code, 0)
        time.sleep(0.1)
    # Note: Global keybd_event removed — it fires system-wide and can
    # interfere with game display mode during launch.

# ── Screen / OCR ───────────────────────────────────────────────────────────────

def grab_region(cx: int, cy: int, pad: int = STATUS_REGION_PAD) -> Image.Image:
    sw, sh = pyautogui.size()
    region = (
        max(0, cx - pad), max(0, cy - pad),
        min(sw, cx + pad), min(sh, cy + pad),
    )
    return ImageGrab.grab(bbox=region)

def grab_fullscreen() -> Image.Image:
    """Capture the middle portion of the screen where queue/status dialogs appear."""
    sw, sh = pyautogui.size()
    return ImageGrab.grab(bbox=(int(sw*0.25), int(sh*0.25), int(sw*0.75), int(sh*0.75)))

def grab_download_region() -> Image.Image:
    """Capture the centre top-half where mod download dialogue appears."""
    return ImageGrab.grab(bbox=_scale_region(320, 150, 1600, 600))

def image_to_text(img: Image.Image) -> str:
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(3.0)
    img = img.convert("L")
    return pytesseract.image_to_string(img, config="--psm 6").lower()

def classify_screen(text: str) -> str:
    # Check download first — prevents mod update text triggering fail keywords
    for kw in DOWNLOAD_KEYWORDS:
        if kw in text:
            return "downloading"
    for kw in SUCCESS_KEYWORDS:
        if kw in text:
            log.info(f"  Success keyword matched: '{kw}'")
            return "success"
    for kw in FAIL_KEYWORDS:
        if kw in text:
            return "fail"
    return "unknown"

# ── Ping ───────────────────────────────────────────────────────────────────────

def ping_host(ip: str, retries: int = 5) -> int:
    """Ping a host up to `retries` times. Returns latency in ms, or 9999 on failure."""
    import re
    for _ in range(retries):
        try:
            result = subprocess.run(
                ["ping", "-n", "1", "-w", "800", ip],
                capture_output=True, text=True, timeout=2
            )
            for line in result.stdout.splitlines():
                m = re.search(r'time[=<](\d+)', line)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
    return 9999

def ping_all_servers(servers: list[dict]) -> list[dict]:
    """Ping all servers in parallel using a thread pool."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def do_ping(s):
        s["ping"] = ping_host(s["ip"])
        return s
    with ThreadPoolExecutor(max_workers=50) as ex:
        futures = {ex.submit(do_ping, s): s for s in servers}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass
    return servers

def ping_label(ms: int) -> str:
    """Return ping as a plain text string."""
    if ms >= 9999:
        return "? ms"
    return f"{ms} ms"

# ── BattleMetrics API ──────────────────────────────────────────────────────────

def fetch_servers(search: str = "") -> list[dict]:
    try:
        url = BATTLEMETRICS_URL
        if search:
            url += f"&filter[search]={requests.utils.quote(search)}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        servers = []
        for s in resp.json().get("data", []):
            attr = s.get("attributes", {})
            servers.append({
                "name":       attr.get("name", "Unknown"),
                "ip":         attr.get("ip", ""),
                "port":       str(attr.get("port", "")),
                "players":    attr.get("players", 0),
                "maxPlayers": attr.get("maxPlayers", 0),
                "ping":       9999,
            })
        return servers
    except Exception as e:
        log.error(f"Failed to fetch servers: {e}")
        return []

# ── Steam launch ───────────────────────────────────────────────────────────────

def is_reforger_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        try:
            if "reforger" in proc.info["name"].lower():
                return True
        except Exception:
            pass
    return False

def launch_reforger() -> None:
    log.info("Launching Arma Reforger via Steam...")
    subprocess.Popen(
        ["cmd", "/c", f"start steam://rungameid/{STEAM_REFORGER_ID}"],
        shell=True
    )

# ── Queue bot logic ────────────────────────────────────────────────────────────

def run_queue_bot(server: dict, status_cb, done_cb, stop_flag: list) -> None:
    """
    stop_flag is a one-element list [False]. Set stop_flag[0] = True to stop.
    """
    ip                = server["ip"]
    port              = server["port"]
    server_name_short = server["name"][:30]
    state_ref         = ["unknown"]
    start_pause_watcher(status_cb, stop_flag, state_ref)

    attempt         = 0
    last_state      = None
    success_count   = 0     # Must see success twice in a row to avoid false positives
    unknown_count   = 0     # Consecutive unknowns after downloading = user cancelled
    was_downloading = False  # True once we've seen a downloading state
    start           = time.time()

    def do_join():
        nonlocal attempt
        if stop_flag[0]:
            return
        attempt += 1
        log.info(f"Attempt #{attempt} — searching for '{server_name_short}'")

        # Step 1: Force game window focus
        force_focus_reforger()

        # Step 2: Click All tab — double click with delay to ensure it registers
        time.sleep(0.3)
        win_click(*COORDS["all_tab"])
        time.sleep(0.2)
        win_click(*COORDS["all_tab"])
        log.info("  Clicked All tab")
        time.sleep(0.4)

        # Step 3: Click search bar once to focus it
        win_click(*COORDS["search_bar"])
        log.info("  Clicked search bar")
        time.sleep(0.4)

        # Step 4: Ctrl+A to select all existing text — typing will overwrite it
        VK_CTRL = 0x11
        scan_ctrl = ctypes.windll.user32.MapVirtualKeyW(VK_CTRL, 0)
        scan_a    = ctypes.windll.user32.MapVirtualKeyW(VK_A, 0)
        ctypes.windll.user32.keybd_event(VK_CTRL, scan_ctrl, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_A, scan_a, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_A, scan_a, 0x0002, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_CTRL, scan_ctrl, 0x0002, 0)
        time.sleep(0.15)
        log.info("  Selected all text in search bar")

        # Step 5: Type server name character by character via WM_CHAR
        hwnd = get_reforger_hwnd()
        for ch in server_name_short:
            if hwnd:
                win32api.PostMessage(hwnd, WM_CHAR, ord(ch), 0)
            time.sleep(0.07)
        log.info(f"  Typed search: {server_name_short}")
        time.sleep(0.3)

        # Step 4: Press Enter to search
        win_keypress(VK_RETURN)
        log.info("  Pressed Enter to search")
        time.sleep(1.5)

        # Step 5: Confirm first result is visible before double-clicking
        img  = grab_region(*COORDS["first_result"], pad=200)
        text = image_to_text(img)
        log.info(f"  Results scan: {text[:80]!r}")
        if not any(c.isalpha() for c in text):
            log.warning("  No results visible — search may have failed, retrying next attempt")
            status_cb(f"⚠️ Search returned no results — retrying... (attempt {attempt})")
            time.sleep(1)
            return

        # Step 6: Double-click first result
        win_click(*COORDS["first_result"])
        time.sleep(0.15)
        win_click(*COORDS["first_result"])
        log.info("  Double-clicked first result")

        # Step 7: Wait for game to process before scanning
        status_cb(f"Joining '{server_name_short}'... waiting for response (attempt {attempt})")
        time.sleep(INITIAL_SCAN_DELAY)

    # Launch game and navigate to server browser if not already running
    if not is_reforger_running():
        status_cb("🚀 Launching Arma Reforger via Steam...")
        launch_reforger()

        # Wait for the game process to appear (up to 60s)
        status_cb("⏳ Waiting for game to start...")
        for _ in range(60):
            if stop_flag[0]:
                done_cb(False)
                return
            if is_reforger_running():
                break
            time.sleep(1)

        # Wait for the game window handle to appear
        status_cb("⏳ Waiting for game window to appear...")
        for _ in range(60):
            if stop_flag[0]:
                done_cb(False)
                return
            if get_reforger_hwnd():
                break
            time.sleep(1)

        # Once the window exists, keep scanning the title screen area
        # until actual content appears (non-black/non-empty screen)
        # This handles the long loading time between process start and title screen
        status_cb("⏳ Game loading — waiting for title screen...")
        log.info("  Waiting for title screen to render...")
        for _ in range(120):  # Up to 2 minutes
            if stop_flag[0]:
                done_cb(False)
                return
            # Grab the title screen scan area and check if there's actual content
            img  = grab_region(*TITLE_SCREEN_COORD, pad=int(250 * pyautogui.size()[0] / 1920))
            text = image_to_text(img)
            log.info(f"  Loading scan: {text[:60]!r}")
            if any(w in text for w in ["continue", "press", "enter", "reforger", "arma"]):
                log.info("  Title screen content detected — starting Enter loop")
                break
            time.sleep(1)

        main_menu_detected = False
        for attempt_n in range(30):
            if stop_flag[0]:
                done_cb(False)
                return

            # Scan title screen area (scaled to resolution)
            img  = grab_region(*TITLE_SCREEN_COORD, pad=int(250 * pyautogui.size()[0] / 1920))
            text = image_to_text(img)
            log.info(f"  Title screen scan #{attempt_n+1}: {text[:80]!r}")

            # Fire Enter immediately as soon as "continue" or "press" is detected
            if any(w in text for w in ["continue", "press", "enter"]):
                log.info("  Title screen ready — sending Enter via all methods")
                win_keypress(VK_RETURN)
                time.sleep(0.2)
                hwnd = get_reforger_hwnd()
                if hwnd:
                    win32api.PostMessage(hwnd, WM_KEYDOWN, VK_RETURN, 0)
                    time.sleep(0.1)
                    win32api.PostMessage(hwnd, WM_KEYUP, VK_RETURN, 0)
                scan_code = ctypes.windll.user32.MapVirtualKeyW(VK_RETURN, 0)
                ctypes.windll.user32.keybd_event(VK_RETURN, scan_code, 0, 0)
                time.sleep(0.05)
                ctypes.windll.user32.keybd_event(VK_RETURN, scan_code, 0x0002, 0)
                time.sleep(1.5)
            else:
                # Title screen not ready yet — wait and retry
                time.sleep(1.0)

            # Check if main menu appeared
            img2  = grab_region(*COORDS["multiplayer_btn"], pad=200)
            text2 = image_to_text(img2)
            log.info(f"  Main menu scan #{attempt_n+1}: {text2[:80]!r}")
            if any(w in text2 for w in ["multiplayer", "play", "campaign", "settings", "exit"]):
                main_menu_detected = True
                log.info("  Main menu confirmed!")
                break

        if not main_menu_detected:
            status_cb("⚠️ Could not reach main menu — stopping.")
            log.warning("Main menu not detected after 30 attempts.")
            done_cb(False)
            return

        # Pause to let the main menu fully settle before clicking
        time.sleep(2.0)
        status_cb("⏳ Clicking Multiplayer...")
        win_click(*COORDS["multiplayer_btn"])

        # Wait for the server browser to load
        status_cb("⏳ Waiting for server browser to load...")
        time.sleep(1.5)
        status_cb("✅ Server browser ready — starting join attempts...")
    else:
        status_cb(f"✅ Game already running — connecting to {server['name']}...")

    do_join()

    while not stop_flag[0]:
        # Unified pause gate — handles all pause scenarios
        if not wait_if_paused(status_cb, stop_flag, state_ref):
            break
        # Check download dialogue first with its dedicated region
        dl_text = image_to_text(grab_download_region())
        if any(kw in dl_text for kw in DOWNLOAD_KEYWORDS):
            state = "downloading"
            log.info(f"OCR state='downloading' (download region matched)")
        else:
            img   = grab_fullscreen()
            text  = image_to_text(img)
            state = classify_screen(text)
            log.info(f"OCR state={state!r} text={text[:120]!r}")
        state_ref[0] = state

        if state == "success":
            success_count += 1
            log.info(f"  Success keyword seen ({success_count}/2) — confirming...")
            if success_count >= 2:
                elapsed = time.time() - start
                msg = f"✅ Success! You're in queue, enjoy your game. ({attempt} attempt(s), {elapsed:.0f}s)"
                log.info(msg)
                status_cb(msg, "#2ecc71")
                done_cb(True)
                return
            else:
                status_cb("✅ In queue!", "#2ecc71")

        elif state == "fail":
            success_count = 0
            attempt += 1
            log.info(f"Queue full — retrying (attempt {attempt})...")
            if attempt == 69:
                status_cb("⛔ Queue full — retrying... (attempt 69) (Nice lol)", "rainbow")
                time.sleep(2.0)
                status_cb("⛔ Queue full — retrying... (attempt 69)")
            else:
                status_cb(f"⛔ Queue full — retrying... (attempt {attempt})")
            # Wait for game to be active before clicking cancel + retry
            if not wait_if_paused(status_cb, stop_flag, state_ref):
                break
            win_click(*COORDS["cancel_btn"])
            time.sleep(0.2)
            # Wait again before clicking the server result
            if not wait_if_paused(status_cb, stop_flag, state_ref):
                break
            win_click(*COORDS["first_result"])
            time.sleep(0.15)
            win_click(*COORDS["first_result"])
            log.info("  Re-clicked first result to rejoin queue")

        elif state == "downloading":
            success_count = 0
            unknown_count = 0
            was_downloading = True
            if last_state != "downloading":
                log.info("Mods downloading — waiting...")
                status_cb("⏬️ Downloading mods — please wait, do not interrupt...")
            # Scan faster during downloads to detect cancellation quickly
            time.sleep(0.2)
            continue

        else:
            success_count = 0
            # Track consecutive unknowns after a download — regardless of last_state
            if was_downloading:
                unknown_count += 1
                log.info(f"  Unknown after downloading ({unknown_count}/2) — checking if cancelled...")
                if unknown_count >= 2:
                    log.info("  Mod download cancelled by user — stopping.")
                    status_cb("⛔ Mod download cancelled — bot stopped.", "#e74c3c")
                    done_cb(False)
                    return
            else:
                if last_state != "unknown":
                    status_cb(f"🔍 Scanning... (attempt {attempt})")

        last_state = state
        time.sleep(SCAN_INTERVAL)

    done_cb(False)

# ── GUI ────────────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root: tk.Tk):
        self.root      = root
        self.servers   = []
        self.joining   = False
        self.joined    = False  # True once successfully in queue
        self.stop_flag = [False]

        root.title("Arma Reforger — Auto Queue Bot")
        root.geometry("700x520")
        root.resizable(False, False)
        root.configure(bg="#1a1a1a")

        # Header
        tk.Label(
            root, text="Arma Reforger  Auto Queue Bot",
            bg="#1a1a1a", fg="#ffffff", font=("Segoe UI", 14, "bold")
        ).pack(pady=(14, 2))
        tk.Label(
            root, text="Live servers from BattleMetrics · Select a server · Click Join",
            bg="#1a1a1a", fg="#666666", font=("Segoe UI", 9)
        ).pack()

        # Search bar
        sf = tk.Frame(root, bg="#1a1a1a")
        sf.pack(fill="x", padx=16, pady=(10, 4))
        tk.Label(sf, text="Search:", bg="#1a1a1a", fg="#aaaaaa",
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._on_search())
        tk.Entry(sf, textvariable=self.search_var,
                 bg="#2a2a2a", fg="#ffffff", insertbackground="#ffffff",
                 font=("Segoe UI", 10), relief="flat", bd=4
                 ).pack(side="left", fill="x", expand=True)
        self.refresh_btn = tk.Button(
            sf, text="⟳ Refresh", command=self._refresh_servers,
            bg="#333333", fg="#ffffff", font=("Segoe UI", 9),
            relief="flat", padx=10, pady=2
        )
        self.refresh_btn.pack(side="left", padx=(6, 0))

        # Server table
        tf = tk.Frame(root, bg="#1a1a1a")
        tf.pack(fill="both", expand=True, padx=16, pady=4)
        cols = ("name", "players", "ping")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("name",    text="Server Name")
        self.tree.heading("players", text="Players")
        self.tree.heading("ping",    text="Ping")
        self.tree.column("name",    width=390, anchor="w")
        self.tree.column("players", width=90,  anchor="center")
        self.tree.column("ping",    width=80,  anchor="center")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
            background="#222222", foreground="#e0e0e0",
            fieldbackground="#222222", rowheight=24,
            font=("Segoe UI", 9))
        style.configure("Treeview.Heading",
            background="#333333", foreground="#ffffff",
            font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#c0392b")])

        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        # Status
        bf = tk.Frame(root, bg="#1a1a1a")
        bf.pack(fill="x", padx=16, pady=4)
        self.status_var = tk.StringVar(value="Loading server list...")
        self.status_label = tk.Label(bf, textvariable=self.status_var, bg="#1a1a1a",
                 fg="#f0a500", font=("Segoe UI", 9), anchor="w")
        self.status_label.pack(fill="x")

        # Buttons
        btnf = tk.Frame(root, bg="#1a1a1a")
        btnf.pack(pady=(0, 8))
        self.join_btn = tk.Button(
            btnf, text="Join", command=self._start_join,
            bg="#c0392b", fg="white", font=("Segoe UI", 10, "bold"),
            padx=24, pady=8, relief="flat", state="disabled"
        )
        self.join_btn.pack(side="left", padx=6)
        self.stop_btn = tk.Button(
            btnf, text="Stop", command=self._stop_join,
            bg="#444444", fg="white", font=("Segoe UI", 10),
            padx=24, pady=8, relief="flat", state="disabled"
        )
        self.stop_btn.pack(side="left", padx=6)


        self._refresh_servers()
        self._schedule_refresh()

    def _refresh_servers(self):
        self._set_status("Refreshing server list...")
        self.refresh_btn.config(state="disabled")
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        search = self.search_var.get().strip().lower()
        servers = fetch_servers(search)

        # Sort by name relevance if there's a search query:
        # exact match first, then starts-with, then contains, then rest
        if search:
            def relevance(s):
                name = s["name"].lower()
                if name == search:
                    return 0
                if name.startswith(search):
                    return 1
                if search in name:
                    return 2
                return 3
            servers.sort(key=relevance)

        self.servers = servers
        self.root.after(0, self._populate_table, servers)
        self.root.after(0, self._set_status, f"{len(servers)} server(s) found — pinging...")
        ping_all_servers(servers)
        self.servers = servers
        self.root.after(0, self._populate_table, servers)
        self.root.after(0, self.refresh_btn.config, {"state": "normal"})

    def _populate_table(self, servers):
        # Remember currently selected server name before clearing
        sel = self.tree.selection()
        selected_name = self.tree.item(sel[0])["values"][0] if sel else None

        self.tree.delete(*self.tree.get_children())
        restore_iid = None
        for s in servers:
            iid = self.tree.insert("", "end", values=(
                s["name"], f"{s['players']}/{s['maxPlayers']}", ping_label(s["ping"])
            ))
            if s["name"] == selected_name:
                restore_iid = iid

        # Restore selection
        if restore_iid:
            self.tree.selection_set(restore_iid)
            self.tree.see(restore_iid)

        self._set_status(
            f"{len(servers)} server(s) — select one and click Join."
            if servers else "No servers found. Try refreshing."
        )

    def _on_search(self):
        if hasattr(self, "_search_after"):
            self.root.after_cancel(self._search_after)
        self._search_after = self.root.after(500, self._refresh_servers)

    def _schedule_refresh(self):
        self.root.after(SERVER_REFRESH_SEC * 1000, self._auto_refresh)

    def _auto_refresh(self):
        if not self.joining and not self.joined:
            self._refresh_servers()
        self._schedule_refresh()

    def _on_select(self, _=None):
        if self.tree.selection() and not self.joining:
            self.join_btn.config(state="normal")

    def _on_double_click(self, _=None):
        if self.tree.selection() and not self.joining:
            self._start_join()

    def _selected_server(self):
        sel = self.tree.selection()
        if not sel:
            return None
        idx = self.tree.index(sel[0])
        return self.servers[idx] if idx < len(self.servers) else None

    def _start_join(self):
        server = self._selected_server()
        if not server:
            messagebox.showwarning("No server selected", "Please select a server first.")
            return
        if not server["ip"] or not server["port"]:
            messagebox.showerror("Missing data", "This server has no IP/port data.")
            return

        self.joining   = True
        self.joined    = False
        self.stop_flag = [False]
        self.join_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._set_status(f"Starting — {server['name']}")

        threading.Thread(
            target=run_queue_bot,
            args=(
                server,
                lambda msg, colour="#f0a500": self.root.after(0, self._set_status, msg, colour),
                lambda ok:  self.root.after(0, self._on_done, ok),
                self.stop_flag,
            ),
            daemon=True
        ).start()

    def _stop_join(self):
        self.stop_flag[0] = True
        self.joining = False
        self.joined  = False
        self._stop_rainbow()
        self.join_btn.config(state="normal" if self._selected_server() else "disabled")
        self.stop_btn.config(state="disabled")
        self._set_status("⛔ Bot stopped.", "#e74c3c")

    def _on_done(self, success: bool):
        self.joining = False
        self.joined  = success
        self.stop_btn.config(state="disabled")
        self.join_btn.config(state="normal")

    def _set_status(self, msg: str, colour: str = "#f0a500"):
        self.status_var.set(msg)
        if "rainbow" in colour:
            self._start_rainbow()
        else:
            self._stop_rainbow()
            self.status_label.config(fg=colour)

    def _start_rainbow(self):
        self._rainbow_colors = ["#ff0000","#ff7700","#ffff00","#00cc00","#0000ff","#8b00ff"]
        self._rainbow_index  = 0
        self._rainbow_active = True
        self._cycle_rainbow()

    def _stop_rainbow(self):
        self._rainbow_active = False

    def _cycle_rainbow(self):
        if not getattr(self, "_rainbow_active", False):
            return
        self.status_label.config(fg=self._rainbow_colors[self._rainbow_index])
        self._rainbow_index = (self._rainbow_index + 1) % len(self._rainbow_colors)
        self.root.after(120, self._cycle_rainbow)

    def on_close(self):
        self.stop_flag[0] = True
        self.root.destroy()

# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    pyautogui.FAILSAFE = True
    root = tk.Tk()

    # Centre window on screen
    w, h   = 700, 520
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x      = (sw - w) // 2
    y      = (sh - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    app  = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

if __name__ == "__main__":
    main()
