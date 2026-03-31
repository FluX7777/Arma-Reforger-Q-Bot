"""
Arma Reforger - Auto Queue Bot
================================
Automatically attempts to join a server queue and retries if it's full.
Uses win32api for clicking, which works in fullscreen game windows.

Requirements:
    pip install pyautogui Pillow pytesseract pywin32
"""

import pyautogui
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
import time
import sys
import ctypes
import logging
from PIL import Image, ImageGrab, ImageEnhance

# ── Configuration ──────────────────────────────────────────────────────────────

RETRY_DELAY     = 0.1    # Seconds to wait between retries when queue is full
CLICK_DELAY     = 0.8     # Seconds to wait after clicking before reading screen
SCAN_REGION_PAD = 300   # Pixels around status area to scan
MAX_RETRIES     = 0     # 0 = retry forever

FAIL_KEYWORDS = [
    "queue is full", "server is full", "failed", "full",
    "unable to join", "try again", "no slots",
]

SUCCESS_KEYWORDS = [
    "position", "in queue", "joining", "connecting",
    "loading", "connected", "leave queue",
]

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ArmaQueueBot")

# ── Low-level click using Windows API ─────────────────────────────────────────
# This method works even in fullscreen games where pyautogui clicks are ignored.

# Windows mouse event constants
MOUSEEVENTF_MOVE       = 0x0001
MOUSEEVENTF_LEFTDOWN   = 0x0002
MOUSEEVENTF_LEFTUP     = 0x0004
MOUSEEVENTF_ABSOLUTE   = 0x8000

def win_click(x: int, y: int) -> None:
    """Move mouse and send a left click using the Windows API."""
    # Move mouse first
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.1)
    # Send left button down then up
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, x, y, 0, 0)

def win_double_click(x: int, y: int) -> None:
    """Send a double-click using the Windows API."""
    win_click(x, y)
    time.sleep(0.1)
    win_click(x, y)

# ── Helpers ────────────────────────────────────────────────────────────────────

def pick_point(prompt: str) -> tuple[int, int]:
    print(f"\n{prompt}")
    print("You have 5 seconds to hover your mouse over the target...")
    for i in range(5, 0, -1):
        print(f"  Recording in {i}...", end="\r")
        time.sleep(1)
    pos = pyautogui.position()
    print(f"\n  Recorded position: {pos}          ")
    return pos


def grab_region(cx: int, cy: int, pad: int = SCAN_REGION_PAD) -> Image.Image:
    region = (cx - pad, cy - pad, cx + pad, cy + pad)
    sw, sh = pyautogui.size()
    region = (
        max(0, region[0]), max(0, region[1]),
        min(sw, region[2]), min(sh, region[3]),
    )
    return ImageGrab.grab(bbox=region)


def image_to_text(img: Image.Image) -> str:
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(3.0)
    img = img.convert("L")
    return pytesseract.image_to_string(img, config="--psm 6").lower()


def classify_screen(text: str) -> str:
    for kw in SUCCESS_KEYWORDS:
        if kw in text:
            return "success"
    for kw in FAIL_KEYWORDS:
        if kw in text:
            return "fail"
    return "unknown"

# ── Main bot loop ──────────────────────────────────────────────────────────────

def run_bot(join_pos, cancel_pos, status_pos) -> None:
    jx, jy = join_pos
    cx, cy = cancel_pos
    sx, sy = status_pos

    attempt = 0
    start   = time.time()

    log.info("Bot started. Move mouse to a screen corner to emergency-stop.")
    log.info(f"Join @ {join_pos} | Cancel @ {cancel_pos} | Status scan @ {status_pos}")
    print()

    while True:
        attempt += 1
        if MAX_RETRIES and attempt > MAX_RETRIES:
            log.warning(f"Reached max retries ({MAX_RETRIES}). Stopping.")
            break

        log.info(f"── Attempt #{attempt} ──")

        # Double-click the join button
        log.info("  Double-clicking join button...")
        win_double_click(jx, jy)
        time.sleep(CLICK_DELAY)

        # Read the screen
        img   = grab_region(sx, sy)
        text  = image_to_text(img)
        state = classify_screen(text)

        if state == "success":
            elapsed = time.time() - start
            log.info(f"SUCCESS — in queue after {attempt} attempt(s) in {elapsed:.0f}s! Enjoy your game!")
            break

        elif state == "fail":
            log.info("Queue full — clicking Cancel...")
            win_click(cx, cy)
            time.sleep(1.5)
            log.info(f"Waiting {RETRY_DELAY}s before retrying...")
            time.sleep(RETRY_DELAY)

        else:
            debug_path = rf"C:\Users\pc\Desktop\debug_attempt_{attempt}.png"
            img.save(debug_path)
            log.warning(f"Couldn't read screen — screenshot saved to Desktop as 'debug_attempt_{attempt}.png'")
            log.info("Clicking Cancel just in case, then retrying...")
            win_click(cx, cy)
            time.sleep(1.5)
            time.sleep(RETRY_DELAY)

# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("       Arma Reforger — Auto Queue Bot")
    print("=" * 60)
    print()
    print("Make sure Arma Reforger is open and your target server")
    print("is visible in the server browser before continuing.")
    print()

    join_pos   = pick_point("Step 1: Hover over the JOIN / QUEUE button.")
    cancel_pos = pick_point("Step 2: Hover over the CANCEL button\n        (trigger it manually once first so you know where it is).")
    status_pos = pick_point("Step 3: Hover over where the status message appears\n        (e.g. where 'Queue is full' shows up).")

    print()
    print("Starting in 5 seconds — switch back to the game now!")
    time.sleep(5)

    try:
        run_bot(join_pos, cancel_pos, status_pos)
    except KeyboardInterrupt:
        print()
        log.info("Stopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    pyautogui.FAILSAFE = True
    main()
