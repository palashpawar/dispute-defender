"""Render demo/out/thumbnail.png (1280x720) from thumbnail.html over a frame of demo/out/demo.mp4."""
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "out" / "thumb"
WORK.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "44.5", "-i", str(ROOT / "out" / "demo.mp4"),
                "-frames:v", "1", str(WORK / "app.png")], check=True)
shutil.copy(ROOT / "thumbnail.html", WORK / "thumbnail.html")
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto((WORK / "thumbnail.html").as_uri(), wait_until="networkidle")
    page.screenshot(path=str(ROOT / "out" / "thumbnail.png"))
    browser.close()
print(ROOT / "out" / "thumbnail.png")
