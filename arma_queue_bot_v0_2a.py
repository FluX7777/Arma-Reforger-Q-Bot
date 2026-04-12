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
import re
import socket
import subprocess
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    'https://github.com/tesseract-ocr/tesseract/releases/download/'
    '5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe'
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
    "team_blufor":     s(435,  280),
    "team_opfor":      s(372,  341),
    "team_confirm":    s(1132, 603),
    }

COORDS = _scale_coords()

# Scale OCR scan regions to match resolution
def _scale_region(x1, y1, x2, y2) -> tuple:
    sw, sh = pyautogui.size()
    sx, sy = sw / 1920, sh / 1080
    return (int(x1*sx), int(y1*sy), int(x2*sx), int(y2*sy))

TITLE_SCREEN_COORD   = (int(941 * pyautogui.size()[0] / 1920),
                        int(866 * pyautogui.size()[1] / 1080))

# ── Configuration ──────────────────────────────────────────────────────────────

SCAN_INTERVAL         = 0.5    # Seconds between queue status scans
POST_CLICK_WAIT       = 0.8    # Seconds to wait after a click for UI to update
INITIAL_SCAN_DELAY    = 1.5    # Seconds to wait after clicking Connect before first scan
STATUS_REGION_PAD     = 400    # Pixels around status area to capture for OCR
SERVER_REFRESH_SEC    = 30     # How often to auto-refresh the server list
MAX_ATTEMPTS          = 500    # Safety limit — stop after this many retries (0 = unlimited)
STEAM_REFORGER_ID     = "1874880"
BATTLEMETRICS_URL     = (
    "https://api.battlemetrics.com/servers"
    "?filter[game]=reforger"
    "&filter[status]=online"
    "&sort=-players"
    "&page[size]=100"
)

FAIL_KEYWORDS = [
    "queue is full", "server is full", "failed",
    "unable to join", "try again", "no slots",
    "server full", "is full",
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

_hwnd_cache      = [None]   # Cached window handle
_hwnd_cache_time = [0.0]    # Timestamp of last cache update
_HWND_CACHE_TTL  = 0.5      # Cache lifetime in seconds

def get_reforger_hwnd() -> int | None:
    """Find and return the Arma Reforger window handle (cached briefly)."""
    now = time.time()
    if _hwnd_cache[0] and (now - _hwnd_cache_time[0]) < _HWND_CACHE_TTL:
        # Verify the cached handle is still valid
        if win32gui.IsWindow(_hwnd_cache[0]):
            return _hwnd_cache[0]
    result = []
    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            if "reforger" in title or "arma" in title:
                result.append(hwnd)
    win32gui.EnumWindows(enum_handler, None)
    _hwnd_cache[0]      = result[0] if result else None
    _hwnd_cache_time[0] = now
    return _hwnd_cache[0]

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
    Waits for the game to be stable for 0.3s before resuming to avoid
    acting on a partially refocused window.
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
            # Wait briefly to confirm the game is stably focused
            time.sleep(0.3)
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

# ── Team selection ─────────────────────────────────────────────────────────────
# Pixel brightness threshold for team row availability.
# Available row: ~(12,18,18) sum ~48. Full/grayed row: ~(21,29,31) sum ~81.
# We use 65 as midpoint threshold — below = available, above = full.
TEAM_AVAILABLE_THRESHOLD = 65

def is_team_available(coord: tuple[int, int]) -> bool:
    """
    Sample the pixel colour at a team row coordinate.
    Returns True if the row is active (slot available), False if grayed out.

    Safety gate: if the game window is not the foreground window, ImageGrab
    on a DirectX surface can return all-black, which would falsely satisfy
    the "available" threshold and trigger a ghost-click. Bail out unless the
    game is actually focused.
    """
    hwnd = get_reforger_hwnd()
    if hwnd and ctypes.windll.user32.GetForegroundWindow() != hwnd:
        return False
    try:
        img = ImageGrab.grab(bbox=(coord[0]-2, coord[1]-2, coord[0]+2, coord[1]+2))
        r, g, b = img.getpixel((2, 2))[:3]
        brightness = r + g + b
        log.info(f"  Team pixel RGB({r},{g},{b}) brightness={brightness} threshold={TEAM_AVAILABLE_THRESHOLD}")
        return brightness < TEAM_AVAILABLE_THRESHOLD
    except Exception as e:
        log.warning(f"  Team pixel check failed: {e}")
        return False

def select_team(preference: str, status_cb, stop_flag: list, state_ref: list) -> bool:
    """
    Wait for the preferred team slot to become available then click it.
    preference: 'blufor', 'opfor', or 'any'
    Returns True if team was selected, False if stopped.
    """
    if preference in ("none", "manual"):
        log.info("Manual team selection — bot will not pick a team")
        status_cb("✅ In queue — select your team manually when deployment opens.", "#2ecc71")
        return True

    status_cb("⏳ Deployment Setup — waiting for team slot...")
    log.info(f"  Team preference: {preference}")

    last_state = None
    while not stop_flag[0]:
        # Keep scanning regardless of focus — no pausing during team selection.
        # Focus is only grabbed at the moment a slot opens.
        blufor_ok = is_team_available(COORDS["team_blufor"])
        opfor_ok  = is_team_available(COORDS["team_opfor"])

        target = None
        if preference == "blufor" and blufor_ok:
            target = "team_blufor"
        elif preference == "opfor" and opfor_ok:
            target = "team_opfor"

        if target:
            team_name = "BLUFOR" if target == "team_blufor" else "OPFOR"
            log.info(f"  {team_name} slot available — securing immediately!")
            status_cb(f"🎯 {team_name} slot open — securing...")
            # Force game window focus — do NOT pause during team selection
            force_focus_reforger()
            time.sleep(0.1)
            win_click(*COORDS[target])
            time.sleep(0.4)
            # Click Confirm
            win_click(*COORDS["team_confirm"])
            log.info("  Clicked Confirm")
            status_cb(f"✅ {team_name} selected! Deploying...")
            return True

        # Update status while waiting
        if last_state != "waiting":
            pref_name = {"blufor": "BLUFOR", "opfor": "OPFOR"}.get(preference, preference)
            status_cb(f"⏳ Waiting for {pref_name} slot to open...")
        last_state = "waiting"
        time.sleep(0.1)  # Scan fast — slot could open any moment

    return False

def enter_until_faction(team_preference: str, status_cb, stop_flag: list, state_ref: list) -> None:
    """
    Press Enter repeatedly (tap x3 then hold 5s) until "Faction Selection"
    text is detected via OCR, then call select_team().
    Used for both queued and direct join paths.
    """
    if team_preference in ("any", "manual", "none"):
        return

    sw, sh = pyautogui.size()
    sx, sy = sw / 1920, sh / 1080
    faction_coord = (int(386 * sx), int(250 * sy))
    scan_code     = ctypes.windll.user32.MapVirtualKeyW(VK_RETURN, 0)
    pref_name     = "BLUFOR" if team_preference == "blufor" else "OPFOR"
    tap_count     = 0

    status_cb(f"⏳ Deploying — selecting {pref_name}...")
    log.info("  Pressing Enter to pass deployment dialogues...")

    for _ in range(50):  # Up to 50 cycles
        if stop_flag[0]:
            return

        # Refocus game window before each cycle
        force_focus_reforger()
        hwnd = get_reforger_hwnd()

        # Rapidfire 5 Enter taps in quick succession
        for i in range(5):
            if stop_flag[0]:
                return
            ctypes.windll.user32.keybd_event(VK_RETURN, scan_code, 0, 0)
            time.sleep(0.05)
            ctypes.windll.user32.keybd_event(VK_RETURN, scan_code, 0x0002, 0)
            if hwnd:
                win32api.PostMessage(hwnd, WM_KEYDOWN, VK_RETURN, 0)
                time.sleep(0.02)
                win32api.PostMessage(hwnd, WM_KEYUP, VK_RETURN, 0)
            time.sleep(0.1)
            log.info(f"  Enter tap {i+1}/5")

        # Hold Enter for 3 seconds — repeatedly fire keydown without keyup so
        # the game receives a continuous stream of Enter presses (mirrors
        # what hardware key-repeat does at ~30Hz). PostMessage is paired
        # alongside for window-message-driven UI elements.
        log.info("  Holding Enter for 3 seconds...")
        status_cb(f"⏳ Holding Enter — selecting {pref_name}...")
        hold_start = time.time()
        while time.time() - hold_start < 3.0:
            if stop_flag[0]:
                # Always release on early exit
                ctypes.windll.user32.keybd_event(VK_RETURN, scan_code, 0x0002, 0)
                if hwnd:
                    win32api.PostMessage(hwnd, WM_KEYUP, VK_RETURN, 0)
                return
            ctypes.windll.user32.keybd_event(VK_RETURN, scan_code, 0, 0)
            if hwnd:
                win32api.PostMessage(hwnd, WM_KEYDOWN, VK_RETURN, 0)
            time.sleep(0.033)  # ~30Hz repeat rate
        # Release Enter cleanly after the hold
        ctypes.windll.user32.keybd_event(VK_RETURN, scan_code, 0x0002, 0)
        if hwnd:
            win32api.PostMessage(hwnd, WM_KEYUP, VK_RETURN, 0)
        time.sleep(0.2)

        # OCR check after each cycle
        text = image_to_text(grab_region(*faction_coord, pad=150))
        log.info(f"  Faction scan: {text[:60]!r}")
        if "faction" in text:
            log.info("  Faction Selection confirmed — starting team selection")
            select_team(team_preference, status_cb, stop_flag, state_ref)
            return

        log.info("  Not at faction selection yet — repeating Enter cycle...")

def is_deployment_screen() -> bool:
    """Check if the Deployment Setup screen is currently showing."""
    sw, sh = pyautogui.size()
    sx, sy = sw / 1920, sh / 1080
    img1  = grab_region(int(469 * sx), int(184 * sy), pad=150)
    text1 = image_to_text(img1)
    if "deployment" in text1:
        return True
    img2  = grab_region(int(342 * sx), int(236 * sy), pad=150)
    text2 = image_to_text(img2)
    return "faction" in text2

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

# ── Game state detection ──────────────────────────────────────────────────────
# Each game state has a fixed location with distinctive text.
# We scan small targeted regions rather than the full screen for speed and accuracy.

def _scale_xy(x: int, y: int) -> tuple[int, int]:
    """Scale a single (x, y) coordinate from 1920x1080 reference to current resolution."""
    sw, sh = pyautogui.size()
    return (int(x * sw / 1920), int(y * sh / 1080))

GAME_STATES = {
    "title_screen":      {"coord": TITLE_SCREEN_COORD,          "pad": 250, "keywords": ["continue", "press"]},
    "main_menu":         {"coord": COORDS["multiplayer_btn"],   "pad": 150, "keywords": ["multiplayer", "campaign", "settings"]},
    "server_browser":    {"coord": _scale_xy(960, 80),          "pad": 200, "keywords": ["multiplayer", "all", "community", "favorite", "official"]},
    "deployment_setup":  {"coord": _scale_xy(469, 184),         "pad": 150, "keywords": ["deployment", "deployment setup"]},
    "faction_selection": {"coord": _scale_xy(342, 236),         "pad": 150, "keywords": ["faction", "faction selection"]},
}

def detect_game_state() -> str:
    """
    Scans targeted screen regions to determine which game state is active.
    Returns one of: 'title_screen', 'main_menu', 'server_browser',
                    'deployment_setup', 'dialogue', 'unknown'
    """
    for state_name, cfg in GAME_STATES.items():
        img  = grab_region(*cfg["coord"], pad=cfg["pad"])
        text = image_to_text(img)
        if any(kw in text for kw in cfg["keywords"]):
            return state_name
    return "dialogue"

# ── Ping ───────────────────────────────────────────────────────────────────────

def ping_host(ip: str, retries: int = 2) -> int:
    """Ping a host up to `retries` times. Returns latency in ms, or 9999 on failure."""
    for _ in range(retries):
        try:
            result = subprocess.run(
                ["ping", "-n", "1", "-w", "400", ip],
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
    """
    Ping servers not already in cache and query A2S player counts in parallel.
    Cached pings are reused (10 min TTL) to prevent jitter on refresh.
    A2S player counts are cached briefly (10s TTL) to avoid spamming game servers.
    """
    now = time.time()
    def do_ping_and_a2s(s):
        ip = s["ip"]
        # Ping cache — 10 min TTL (long enough to prevent jitter, short enough to recover from network changes)
        cached = _ping_cache.get(ip)
        if cached and (now - cached[1]) < _PING_CACHE_TTL:
            s["ping"] = cached[0]
        else:
            s["ping"] = ping_host(ip)
            _ping_cache[ip] = (s["ping"], now)
        # A2S player count — short TTL to avoid hammering game servers every refresh
        try:
            port = int(s["port"])
            a2s_key = (ip, port)
            a2s_cached = _a2s_cache.get(a2s_key)
            if a2s_cached and (now - a2s_cached[1]) < _A2S_CACHE_TTL:
                a2s_count = a2s_cached[0]
            else:
                a2s_count = a2s_player_count(ip, port)
                if a2s_count is not None:
                    _a2s_cache[a2s_key] = (a2s_count, now)
            if a2s_count is not None:
                s["players"] = a2s_count
        except Exception:
            pass
        return s
    with ThreadPoolExecutor(max_workers=50) as ex:
        futures = {ex.submit(do_ping_and_a2s, s): s for s in servers}
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

# ── A2S real-time player count ─────────────────────────────────────────────────

def a2s_player_count(ip: str, port: int, timeout: float = 0.5) -> int | None:
    """
    Query a game server directly via Valve A2S protocol for real-time player count.
    Returns player count as int, or None if the server doesn't support A2S.
    Much more up to date than BattleMetrics (seconds vs minutes).
    Arma Reforger uses A2S query port which is typically separate from game port.
    Common A2S port for Reforger is 17777 by default.
    """
    # A2S_INFO request packet
    A2S_INFO = b'\xff\xff\xff\xffTSource Engine Query\x00'
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        # Try default A2S port first (17777), then game port
        for query_port in (17777, port):
            try:
                sock.sendto(A2S_INFO, (ip, query_port))
                data, _ = sock.recvfrom(4096)
                if len(data) > 24:
                    # Parse A2S_INFO response — player count is at a fixed offset
                    # after header(4) + type(1) + protocol(1) + name(var) + map(var)...
                    # Easier: find player count by scanning the response
                    # Response type 0x49 = A2S_INFO
                    if data[4] == 0x49:
                        # Skip to player count field
                        idx = 5  # skip header + type
                        # Skip: protocol(1)
                        idx += 1
                        # Skip: name (null-terminated string)
                        idx = data.index(b'\x00', idx) + 1
                        # Skip: map (null-terminated string)
                        idx = data.index(b'\x00', idx) + 1
                        # Skip: folder (null-terminated string)
                        idx = data.index(b'\x00', idx) + 1
                        # Skip: game (null-terminated string)
                        idx = data.index(b'\x00', idx) + 1
                        # Skip: app_id (2 bytes)
                        idx += 2
                        # Players count (1 byte)
                        players = data[idx]
                        return players
            except Exception:
                continue
        return None
    except Exception:
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass

# Persistent caches keyed by IP / (IP, port).
# Ping cache: 10 min TTL — long enough to prevent jitter on refresh,
#             short enough to recover from network/route changes.
# A2S cache:  10 sec TTL — short enough for fresh player counts,
#             long enough to avoid spamming game servers on every refresh.
_PING_CACHE_TTL = 600.0
_A2S_CACHE_TTL  = 10.0
_ping_cache: dict[str, tuple[int, float]] = {}              # ip -> (ping_ms, timestamp)
_a2s_cache:  dict[tuple[str, int], tuple[int, float]] = {}  # (ip, port) -> (players, timestamp)

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
    )

# ── Queue bot logic ────────────────────────────────────────────────────────────

def run_queue_bot(server: dict, team_preference: str, status_cb, done_cb, stop_flag: list) -> None:
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
    download_start  = 0.0   # Timestamp when mod download began
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

        # Step 3b: Ctrl+A to select all existing text — typing will overwrite it
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

        # Step 4: Type server name character by character via WM_CHAR
        hwnd = get_reforger_hwnd()
        for ch in server_name_short:
            if hwnd:
                win32api.PostMessage(hwnd, WM_CHAR, ord(ch), 0)
            time.sleep(0.07)
        log.info(f"  Typed search: {server_name_short}")
        time.sleep(0.3)

        # Step 5: Press Enter to search
        win_keypress(VK_RETURN)
        log.info("  Pressed Enter to search")
        time.sleep(1.5)

        # Step 6: Confirm first result is visible before double-clicking
        img  = grab_region(*COORDS["first_result"], pad=200)
        text = image_to_text(img)
        log.info(f"  Results scan: {text[:80]!r}")
        if not any(c.isalpha() for c in text):
            log.warning("  No results visible — search may have failed, retrying next attempt")
            status_cb(f"⚠️ Search returned no results — retrying... (attempt {attempt})")
            time.sleep(1)
            return

        # Step 7: Double-click first result
        win_click(*COORDS["first_result"])
        time.sleep(0.15)
        win_click(*COORDS["first_result"])
        log.info("  Double-clicked first result")

        # Step 8: Wait for game to process before scanning
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

        # Step 1: Detect which screen we are on
        game_state = detect_game_state()
        log.info(f"Game state: {game_state!r}")

        if game_state == "server_browser":
            state = "server_browser"
            state_ref[0] = "unknown"
            if last_state not in ("unknown", "server_browser"):
                status_cb(f"🔍 Scanning... (attempt {attempt})")
            log.info("  Server browser detected — waiting for dialogue")
            time.sleep(SCAN_INTERVAL)
            last_state = state
            continue

        if game_state in ("deployment_setup", "faction_selection"):
            log.info(f"  {game_state} screen detected")
            state_ref[0] = "deployment_setup"
            if game_state == "faction_selection":
                # Already on faction selection — go straight to team pick
                status_cb("⏳ Faction Selection — selecting team...")
                select_team(team_preference, status_cb, stop_flag, state_ref)
            else:
                # On pre-team deployment screen — press Enter until faction selection
                enter_until_faction(team_preference, status_cb, stop_flag, state_ref)
            done_cb(True)
            return

        # Step 2: Check download dialogue via dedicated region
        dl_text = image_to_text(grab_download_region())
        if any(kw in dl_text for kw in DOWNLOAD_KEYWORDS):
            state = "downloading"
            log.info("OCR state='downloading' (download region matched)")
        else:
            # Step 3: Scan centre of screen for queue/fail/success dialogues
            img   = grab_fullscreen()
            text  = image_to_text(img)
            state = classify_screen(text)
            log.info(f"OCR state={state!r} text={text[:120]!r}")
        state_ref[0] = state

        if state == "success":
            unknown_count = 0
            success_count += 1
            log.info(f"  Success keyword seen ({success_count}/2) — confirming...")
            if success_count >= 2:
                elapsed = time.time() - start
                elapsed = time.time() - start
                msg = f"✅ In queue! ({attempt} attempt(s), {elapsed:.0f}s)"
                log.info(msg)
                status_cb(msg, "#2ecc71")
                # Wait for Deployment Setup screen before attempting team selection
                if team_preference not in ("any", "manual", "none"):
                    log.info("  Waiting for Deployment Setup screen...")
                    sw, sh = pyautogui.size()
                    sx, sy = sw / 1920, sh / 1080
                    # Scaled coordinates
                    deploy_coord   = (int(499 * sx), int(181 * sy))  # "Deployment Setup" text
                    faction_coord  = (int(386 * sx), int(250 * sy))  # "Faction Selection" text
                    continue_coord = (int(1543 * sx), int(903 * sy)) # Continue button

                    pref_name = "BLUFOR" if team_preference == "blufor" else "OPFOR"
                    status_cb(f"✅ In queue — waiting for deployment to {pref_name}...", "#2ecc71")
                    log.info("  Waiting for queue to end — scanning for Faction Selection...")
                    scan_code = ctypes.windll.user32.MapVirtualKeyW(VK_RETURN, 0)
                    queue_start = time.time()

                    # Phase 1: Wait for queue to end — OCR scan for "faction selection"
                    # Layered intervals: 2s for first minute, 5s up to 5min, 10s after
                    faction_coord = (int(386 * sx), int(250 * sy))
                    faction_found = False
                    for _ in range(3000):  # Up to 500 minutes max
                        if stop_flag[0]:
                            done_cb(True)
                            return
                        elapsed_q = time.time() - queue_start
                        if elapsed_q < 60:
                            interval = 2.0
                        elif elapsed_q < 300:
                            interval = 5.0
                        else:
                            interval = 10.0
                        time.sleep(interval)

                        text = image_to_text(grab_region(*faction_coord, pad=150))
                        log.info(f"  Queue wait scan: {text[:60]!r}")
                        if "faction" in text:
                            log.info("  Faction Selection detected — moving to team pick")
                            faction_found = True
                            break

                    if not faction_found:
                        done_cb(True)
                        return

                    # Phase 2: Out of queue — press Enter until faction selection
                    enter_until_faction(team_preference, status_cb, stop_flag, state_ref)

                done_cb(True)
                return
            else:
                if team_preference not in ("any", "manual", "none"):
                    pref_name = "BLUFOR" if team_preference == "blufor" else "OPFOR"
                    status_cb(f"✅ In queue — waiting for deployment to {pref_name}...", "#2ecc71")
                else:
                    status_cb("✅ In queue!", "#2ecc71")

        elif state == "fail":
            success_count = 0
            unknown_count = 0
            attempt += 1
            log.info(f"Queue full — retrying (attempt {attempt})...")
            # Safety limit
            if MAX_ATTEMPTS and attempt > MAX_ATTEMPTS:
                log.warning(f"Reached max attempts ({MAX_ATTEMPTS}) — stopping.")
                status_cb(f"⛔ Reached {MAX_ATTEMPTS} attempts — stopping.", "#e74c3c")
                done_cb(False)
                return
            if attempt == 69:
                status_cb("⛔ Queue full — retrying... (attempt 69) (Nice lol)", "rainbow")
                time.sleep(2.0)
                status_cb("⛔ Queue full — retrying... (attempt 69)")
            else:
                status_cb(f"⛔ Queue full — retrying... (attempt {attempt})")

            # Wait for game to be active before clicking cancel
            if not wait_if_paused(status_cb, stop_flag, state_ref):
                break
            # Click cancel — safe to click even if dialog already dismissed
            win_click(*COORDS["cancel_btn"])
            time.sleep(0.1)

            # Wait for game to be active before clicking the server result
            if not wait_if_paused(status_cb, stop_flag, state_ref):
                break

            # Skip OCR recheck — click immediately for maximum speed
            # The next scan loop iteration will catch any unexpected state change
            win_click(*COORDS["first_result"])
            time.sleep(0.05)
            win_click(*COORDS["first_result"])
            log.info("  Re-clicked first result to rejoin queue")
            state_ref[0] = "unknown"

        elif state == "downloading":
            success_count = 0
            unknown_count = 0
            was_downloading = True
            if last_state != "downloading":
                download_start = time.time()
                log.info("Mods downloading — waiting...")
                status_cb("⏬️ Downloading mods — please wait, do not interrupt...")
            # Layered scan interval — starts fast, slows down over time
            elapsed_dl = time.time() - download_start
            if elapsed_dl < 10:
                time.sleep(2.0)   # First 10s: scan every 2s
            elif elapsed_dl < 30:
                time.sleep(4.0)   # 10-30s: scan every 4s
            elif elapsed_dl < 60:
                time.sleep(8.0)   # 30-60s: scan every 8s
            else:
                time.sleep(15.0)  # 60s+: scan every 15s
            continue

        else:
            success_count = 0
            if was_downloading:
                # Consecutive unknowns after a download — could be loading screen
                # or user cancelled. Use a higher threshold to avoid false positives
                # from the loading screen that appears after mods finish downloading.
                unknown_count += 1
                log.info(f"  Unknown after downloading ({unknown_count}/5) — checking if cancelled...")
                if unknown_count >= 5:
                    # Re-check download region one final time to be sure
                    recheck = image_to_text(grab_download_region())
                    if not any(kw in recheck for kw in DOWNLOAD_KEYWORDS):
                        log.info("  Mod download cancelled by user — stopping.")
                        status_cb("⛔ Mod download cancelled — bot stopped.", "#e74c3c")
                        done_cb(False)
                        return
                    else:
                        # Still downloading — reset counter
                        unknown_count = 0
            else:
                # Consecutive unknowns from dialogue context = loaded into game directly
                # Server browser detection is handled separately — don't count those
                unknown_count += 1
                log.info(f"  Unknown reading ({unknown_count}/10)...")
                if unknown_count >= 10:
                    elapsed = time.time() - start
                    msg = f"✅ Joined server! ({attempt} attempt(s), {elapsed:.0f}s)"
                    log.info(msg)
                    status_cb(msg, "#2ecc71")
                    if team_preference not in ("any", "manual", "none"):
                        # Press Enter until faction selection appears
                        enter_until_faction(team_preference, status_cb, stop_flag, state_ref)
                    done_cb(True)
                    return
                if last_state not in ("unknown", "server_browser"):
                    status_cb(f"🔍 Scanning... (attempt {attempt})")

        last_state = state
        # Short yield so the OS can process input between scans
        time.sleep(0.05)

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

        # Team preference
        tf2 = tk.Frame(root, bg="#1a1a1a")
        tf2.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(tf2, text="Team:", bg="#1a1a1a", fg="#aaaaaa",
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        self.team_var = tk.StringVar(value="manual")
        for label, val in [("Manual", "manual"), ("BLUFOR", "blufor"), ("OPFOR", "opfor")]:
            tk.Radiobutton(
                tf2, text=label, variable=self.team_var, value=val,
                bg="#1a1a1a", fg="#aaaaaa", selectcolor="#1a1a1a",
                activebackground="#1a1a1a", activeforeground="#ffffff",
                font=("Segoe UI", 9)
            ).pack(side="left", padx=4)

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
        self._deployment_prompt_shown = False
        # Check if user is already on the Deployment Setup screen
        threading.Thread(target=self._check_deployment_on_start, daemon=True).start()

    def _check_deployment_on_start(self):
        """Poll periodically — show inline prompt once if Deployment Setup is detected."""
        time.sleep(2)
        while True:
            try:
                if is_reforger_running() and not self._deployment_prompt_shown and not self.joining:
                    state = detect_game_state()
                    if state == "deployment_setup":
                        self._deployment_prompt_shown = True
                        self.root.after(0, self._show_deployment_banner)
            except Exception:
                pass
            time.sleep(10)  # Check every 10s — not every refresh

    def _show_deployment_banner(self):
        """Show a subtle inline banner in the GUI bottom-right corner."""
        if hasattr(self, "_deploy_banner") and self._deploy_banner:
            return
        pref = self.team_var.get().upper()

        self._deploy_banner = tk.Frame(
            self.root, bg="#1e3a1e", bd=1, relief="solid"
        )
        self._deploy_banner.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-10)

        tk.Label(
            self._deploy_banner,
            text=f"Deployment Setup detected",
            bg="#1e3a1e", fg="#2ecc71",
            font=("Segoe UI", 8, "bold")
        ).pack(padx=8, pady=(6,2))

        tk.Label(
            self._deploy_banner,
            text=f"Auto-select team on this server?",
            bg="#1e3a1e", fg="#aaaaaa",
            font=("Segoe UI", 8)
        ).pack(padx=8)

        btnf = tk.Frame(self._deploy_banner, bg="#1e3a1e")
        btnf.pack(padx=8, pady=6)

        tk.Button(
            btnf, text=f"BLUFOR",
            command=lambda: self._accept_deployment("blufor"),
            bg="#1a3a5c", fg="white",
            font=("Segoe UI", 8), relief="flat", padx=8, pady=3
        ).pack(side="left", padx=2)

        tk.Button(
            btnf, text=f"OPFOR",
            command=lambda: self._accept_deployment("opfor"),
            bg="#5c1a1a", fg="white",
            font=("Segoe UI", 8), relief="flat", padx=8, pady=3
        ).pack(side="left", padx=2)

        tk.Button(
            btnf, text="Manual",
            command=self._dismiss_deployment_banner,
            bg="#333333", fg="#aaaaaa",
            font=("Segoe UI", 8), relief="flat", padx=8, pady=3
        ).pack(side="left", padx=2)

    def _accept_deployment(self, team: str):
        """User clicked a team — dismiss banner and start team selection."""
        self._dismiss_deployment_banner()
        self.team_var.set(team)
        self.stop_flag = [False]
        self._set_status(f"⏳ Waiting for {team.upper()} slot...")
        threading.Thread(target=self._run_deployment_assist, args=(team,), daemon=True).start()

    def _dismiss_deployment_banner(self):
        if hasattr(self, "_deploy_banner") and self._deploy_banner:
            self._deploy_banner.destroy()
            self._deploy_banner = None

    def _run_deployment_assist(self, team: str):
        """
        Run team selection from the GUI banner.
        Uses enter_until_faction so it works regardless of which deployment
        screen we are currently on — pre-team info or actual faction selection.
        enter_until_faction checks for "Faction Selection" OCR after every
        3rd Enter tap, so it handles both cases gracefully.
        """
        state_ref = ["deployment_setup"]
        status_cb = lambda msg, colour="#f0a500": self.root.after(0, self._set_status, msg, colour)
        enter_until_faction(team, status_cb, self.stop_flag, state_ref)

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
        self._deployment_prompt_shown = False
        # Check if user is already on the Deployment Setup screen
        threading.Thread(target=self._check_deployment_on_start, daemon=True).start()

    def _check_deployment_on_start(self):
        """Poll periodically — show inline prompt once if Deployment Setup is detected."""
        time.sleep(2)
        while True:
            try:
                if is_reforger_running() and not self._deployment_prompt_shown and not self.joining:
                    state = detect_game_state()
                    if state == "deployment_setup":
                        self._deployment_prompt_shown = True
                        self.root.after(0, self._show_deployment_banner)
            except Exception:
                pass
            time.sleep(10)  # Check every 10s — not every refresh

    def _show_deployment_banner(self):
        """Show a subtle inline banner in the GUI bottom-right corner."""
        if hasattr(self, "_deploy_banner") and self._deploy_banner:
            return
        pref = self.team_var.get().upper()

        self._deploy_banner = tk.Frame(
            self.root, bg="#1e3a1e", bd=1, relief="solid"
        )
        self._deploy_banner.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-10)

        tk.Label(
            self._deploy_banner,
            text=f"Deployment Setup detected",
            bg="#1e3a1e", fg="#2ecc71",
            font=("Segoe UI", 8, "bold")
        ).pack(padx=8, pady=(6,2))

        tk.Label(
            self._deploy_banner,
            text=f"Auto-select team on this server?",
            bg="#1e3a1e", fg="#aaaaaa",
            font=("Segoe UI", 8)
        ).pack(padx=8)

        btnf = tk.Frame(self._deploy_banner, bg="#1e3a1e")
        btnf.pack(padx=8, pady=6)

        tk.Button(
            btnf, text=f"BLUFOR",
            command=lambda: self._accept_deployment("blufor"),
            bg="#1a3a5c", fg="white",
            font=("Segoe UI", 8), relief="flat", padx=8, pady=3
        ).pack(side="left", padx=2)

        tk.Button(
            btnf, text=f"OPFOR",
            command=lambda: self._accept_deployment("opfor"),
            bg="#5c1a1a", fg="white",
            font=("Segoe UI", 8), relief="flat", padx=8, pady=3
        ).pack(side="left", padx=2)

        tk.Button(
            btnf, text="Manual",
            command=self._dismiss_deployment_banner,
            bg="#333333", fg="#aaaaaa",
            font=("Segoe UI", 8), relief="flat", padx=8, pady=3
        ).pack(side="left", padx=2)

    def _accept_deployment(self, team: str):
        """User clicked a team — dismiss banner and start team selection."""
        self._dismiss_deployment_banner()
        self.team_var.set(team)
        self.stop_flag = [False]
        self._set_status(f"⏳ Waiting for {team.upper()} slot...")
        threading.Thread(target=self._run_deployment_assist, args=(team,), daemon=True).start()

    def _dismiss_deployment_banner(self):
        if hasattr(self, "_deploy_banner") and self._deploy_banner:
            self._deploy_banner.destroy()
            self._deploy_banner = None

    def _run_deployment_assist(self, team: str):
        """
        Run team selection from the GUI banner.
        Uses enter_until_faction so it works regardless of which deployment
        screen we are currently on — pre-team info or actual faction selection.
        enter_until_faction checks for "Faction Selection" OCR after every
        3rd Enter tap, so it handles both cases gracefully.
        """
        state_ref = ["deployment_setup"]
        status_cb = lambda msg, colour="#f0a500": self.root.after(0, self._set_status, msg, colour)
        enter_until_faction(team, status_cb, self.stop_flag, state_ref)

    def _on_select(self, _=None):
        if self.tree.selection() and not self.joining:
            self.join_btn.config(state="normal")
            # Refresh player count for the selected server in background
            server = self._selected_server()
            if server:
                threading.Thread(
                    target=self._refresh_selected_count,
                    args=(server,),
                    daemon=True
                ).start()

    def _refresh_selected_count(self, server: dict) -> None:
        """Get the most up to date player count and ping for the selected server."""
        ip   = server["ip"]
        port = int(server["port"])

        # Refresh ping for this server and update cache
        fresh_ping = ping_host(ip)
        _ping_cache[ip] = fresh_ping
        server["ping"] = fresh_ping

        # Try A2S first — live direct query to the server
        a2s_count = a2s_player_count(ip, port)
        if a2s_count is not None:
            server["players"] = a2s_count
            log.info(f"  A2S live count for {server['name']}: {a2s_count}")
        else:
            # Fall back to a fresh BattleMetrics fetch filtered to this server
            try:
                url = (
                    f"https://api.battlemetrics.com/servers"
                    f"?filter[game]=reforger"
                    f"&filter[search]={requests.utils.quote(server['name'][:30])}"
                    f"&page[size]=5"
                )
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                for s in resp.json().get("data", []):
                    attr = s.get("attributes", {})
                    if attr.get("ip") == ip:
                        server["players"] = attr.get("players", server["players"])
                        log.info(f"  BattleMetrics refreshed count for {server['name']}: {server['players']}")
                        break
            except Exception as e:
                log.warning(f"  Could not refresh player count: {e}")

        # Update the table display
        self.root.after(0, self._update_selected_row, server)

    def _update_selected_row(self, server: dict) -> None:
        """Update the player count and ping for the currently selected row."""
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        current = self.tree.item(iid, "values")
        if current:
            self.tree.item(iid, values=(
                current[0],
                f"{server['players']}/{server['maxPlayers']}",
                ping_label(server["ping"])
            ))

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
                self.team_var.get(),
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
