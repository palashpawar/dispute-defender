"""Drive the app with Playwright and record the walkthrough.

Needs two servers: the mock sandbox on :8812 and the real PayPal sandbox on :8811.
Each scene holds for its narration length (demo/out/durations.json); compose.py later
speeds up the stretches where Claude is working. Writes demo/out/raw.webm + marks.json.

    python demo/record.py [--sandbox-dispute PP-R-...] [--submit-sandbox]
"""
import argparse
import json
import shutil
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
MOCK, SANDBOX = "http://localhost:8812", "http://localhost:8811"
W, H = 1440, 810  # captured 1:1; compose.py scales to 1080p


class Recorder:
    def __init__(self, page: Page, durations: dict):
        self.page, self.dur, self.t0, self.marks = page, durations, time.monotonic(), []

    def now(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    def scene(self, sid: str):
        if self.marks:
            self.marks[-1]["end"] = self.now()
        self.marks.append({"id": sid, "start": self.now()})

    def hold_rest(self, pad=0.8):
        """Wait until the current scene has lasted as long as its narration."""
        m = self.marks[-1]
        left = m["start"] + self.dur.get(m["id"], 3) + pad - self.now()
        if left > 0:
            self.page.wait_for_timeout(left * 1000)

    def mark_fast(self, start: float):
        """Flag [start, now] as a stretch compose.py may speed up."""
        self.marks[-1].setdefault("fast", []).append([start, self.now()])

    def finish(self):
        self.marks[-1]["end"] = self.now()


def scroll_drawer(page: Page, to: str | int, ms=1200):
    """Smoothly scroll the case drawer to a selector (or pixel offset)."""
    page.evaluate("""([to, ms]) => {
        const el = document.querySelector('#drawer-body');
        const target = typeof to === 'number' ? to
            : document.querySelector(to).getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 20;
        const from = el.scrollTop, t0 = performance.now();
        const step = (t) => { const k = Math.min(1, (t - t0) / ms); el.scrollTop = from + (target - from) * (1 - Math.cos(Math.PI * k)) / 2;
                              if (k < 1) requestAnimationFrame(step); };
        requestAnimationFrame(step);
    }""", [to, ms])
    page.wait_for_timeout(ms + 150)


def open_case(page: Page, dispute_id: str):
    page.locator(".ag-center-cols-container [role=row]", has_text=dispute_id).first.click()
    page.wait_for_selector("#drawer:not([hidden]) #ai-section")
    page.wait_for_timeout(600)


def investigate(rec: Recorder, page: Page):
    page.click("#btn-investigate")
    t = rec.now()
    page.wait_for_selector("#ai-section .verdict", timeout=300_000)
    rec.mark_fast(t)
    page.wait_for_timeout(800)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fight", default="PP-D-27137")
    ap.add_argument("--accept", default="PP-D-27959")
    ap.add_argument("--sandbox-dispute", default="PP-R-DHG-10196035")
    ap.add_argument("--submit-sandbox", action="store_true", help="also send the real accept-claim call")
    args = ap.parse_args()
    durations = json.loads((OUT / "durations.json").read_text())
    vids = OUT / "video"
    shutil.rmtree(vids, ignore_errors=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, color_scheme="dark",
                                  record_video_dir=str(vids), record_video_size={"width": W, "height": H})
        page = ctx.new_page()
        rec = Recorder(page, durations)

        # ---- mock sandbox: the full story on the demo merchant ----
        page.goto(MOCK)
        page.wait_for_selector(".ag-center-cols-container [role=row]")
        rec.scene("intro")
        rec.hold_rest()

        rec.scene("investigate")
        open_case(page, args.fight)
        investigate(rec, page)
        rec.hold_rest(pad=0)

        rec.scene("verdict")
        page.wait_for_timeout(1500)
        scroll_drawer(page, ".facts", 1500)
        page.wait_for_timeout(2500)
        scroll_drawer(page, ".flags", 1500)
        rec.hold_rest()

        rec.scene("evidence")
        scroll_drawer(page, "#seg", 1500)
        page.wait_for_timeout(2500)
        scroll_drawer(page, "#statement", 1500)
        rec.hold_rest()

        rec.scene("submit")
        scroll_drawer(page, "#btn-respond", 800)
        page.click("#btn-respond")
        page.wait_for_selector("#confirm[open]")
        page.wait_for_timeout(4500)
        page.click("#confirm-ok")
        page.wait_for_selector(".result", timeout=60_000)
        rec.hold_rest()

        rec.scene("console")
        page.click("#close")
        page.click("#console > summary")
        page.wait_for_timeout(300)
        page.locator("#console").scroll_into_view_if_needed()
        page.locator("#console-body .call details", has_text="provide-evidence").first.click()
        rec.hold_rest()

        rec.scene("accept")
        page.click("#console > summary")
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(600)
        open_case(page, args.accept)
        investigate(rec, page)
        rec.hold_rest(pad=1.5)

        # ---- real PayPal sandbox ----
        rec.scene("sandbox")
        page.goto(SANDBOX)
        page.wait_for_selector(".ag-center-cols-container [role=row]")
        page.wait_for_timeout(1200)
        open_case(page, args.sandbox_dispute)
        if page.locator("#btn-investigate").count():
            investigate(rec, page)
        if args.submit_sandbox:
            scroll_drawer(page, "#btn-respond", 800)
            page.click("#btn-respond")
            page.wait_for_selector("#confirm[open]")
            page.wait_for_timeout(3000)
            page.click("#confirm-ok")
            page.wait_for_selector(".result", timeout=60_000)
        rec.hold_rest(pad=1.5)

        rec.scene("outro")
        page.click("#close")
        rec.hold_rest(pad=2.5)
        rec.finish()

        video = page.video
        ctx.close()
        browser.close()
        shutil.copy(video.path(), OUT / "raw.webm")
    (OUT / "marks.json").write_text(json.dumps(rec.marks, indent=1))
    print(json.dumps(rec.marks, indent=1))


if __name__ == "__main__":
    main()
