# Arma-Reforger-Q-Bot

A Python bot that automatically retries joining a full Arma Reforger server queue by repeatedly double-clicking the join button and cancelling when full, until a spot is secured.

The bot uses OCR (Optical Character Recognition) to read text directly off your screen to detect the queue status. When you first run it, it will ask you to hover your mouse over 3 spots — the join button, the cancel button, and the area where status messages appear. It records those positions and handles everything from there.

## Requirements

Install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki), then run:

```
pip install pyautogui Pillow pytesseract pywin32
```

## Usage:

```
python arma_queue_bot.py
```

1. Hover your mouse over the Join/Queue button on the server browser till the script records your mouse position.
2. Hover over the Cancel button and let the script record the position.
3. Hover over the "Queue Full" text to allow the script to recognise if it has suceeded or failed.
4. The bot will now keep trying till it is in the queue.

> **Note:** Run Arma Reforger in **Borderless Windowed** mode for best results.

## License

MIT
