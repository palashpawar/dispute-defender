"""Cut demo/out/raw.webm to the narration and mux the final video.

Stretches record.py marked as "fast" (Claude working) are sped up so each scene lasts about
as long as its narration, with an on-screen "N× speed" label. Writes demo/out/demo.mp4 and
demo/out/captions.srt (upload the .srt to YouTube as subtitles).
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
MAX_SPEED = 6.0
# Seconds to keep the verdict on screen while the narration describes it (the drawer shows it after the fast part).
VERDICT_HOLD = {"accept": 5.0, "sandbox": 6.0}
FREEZE_GUARD = 0.7
SRC_W, SRC_H = 1440, 810  # the app's CSS viewport; record.py captures it 1:1, compose scales it to 1080p
FONT = "/System/Library/Fonts/SFNS.ttf"


def plan(marks, durations):
    pieces, audio_at, t = [], {}, 0.0
    for m in marks:
        n = durations.get(m["id"], 0)
        ranges, cur = [], m["start"]
        for a, b in m.get("fast", []):
            if a > cur:
                ranges.append((cur, a, False))
            ranges.append((a, b, True))
            cur = b
        if cur < m["end"]:
            ranges.append((cur, m["end"], False))
        normal = sum(b - a for a, b, f in ranges if not f)
        fast = sum(b - a for a, b, f in ranges if f)
        hold = VERDICT_HOLD.get(m["id"], 0.0) if fast else 0.0
        speed = 1.0
        if fast:
            speed = min(MAX_SPEED, max(1.0, fast / max(n + 0.6 - normal - hold, 0.1)))
        audio_at[m["id"]] = t
        scene_start = t
        for a, b, f in ranges:
            sp = speed if f else 1.0
            pieces.append({"start": a, "end": b, "speed": sp, "pad": 0.0})
            t += (b - a) / sp
        short = n + 0.6 - (t - scene_start)
        if short > 0.05:  # freeze the last frame (the verdict) until the narration finishes
            last = pieces[-1]
            # video timestamps run ~0.5s ahead of the marks; freeze before the next scene's first action
            cut = min(FREEZE_GUARD, (last["end"] - last["start"]) * 0.9)
            last["end"] -= cut
            t -= cut / last["speed"]
            last["pad"] = n + 0.6 - (t - scene_start)
            t += last["pad"]
    return pieces, audio_at, t


def srt_time(s: float) -> str:
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def captions(scenes, durations, audio_at) -> str:
    out, k = [], 1
    for s in scenes:
        if s["id"] not in audio_at:
            continue
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", s["text"]) if x]
        total, t = sum(len(x) for x in sentences), audio_at[s["id"]]
        for x in sentences:
            d = durations[s["id"]] * len(x) / total
            out.append(f"{k}\n{srt_time(t)} --> {srt_time(t + d)}\n{x}\n")
            t, k = t + d, k + 1
    return "\n".join(out)


def main():
    marks = json.loads((OUT / "marks.json").read_text())
    durations = json.loads((OUT / "durations.json").read_text())
    scenes = json.loads((ROOT / "narration.json").read_text())
    pieces, audio_at, total = plan(marks, durations)

    cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-i", str(OUT / "raw.webm")]
    ids = [m["id"] for m in marks if (OUT / "audio" / f"{m['id']}.mp3").exists()]
    for sid in ids:
        cmd += ["-i", str(OUT / "audio" / f"{sid}.mp3")]

    f = [f"[0:v]split={len(pieces)}" + "".join(f"[s{i}]" for i in range(len(pieces)))]
    for i, p in enumerate(pieces):
        chain = f"[s{i}]trim=start={p['start']}:end={p['end']},setpts=(PTS-STARTPTS)/{p['speed']:.4f}"
        chain += f",crop={SRC_W}:{SRC_H}:0:0,scale=1920:1080:flags=lanczos"
        if p["pad"]:
            chain += f",tpad=stop_mode=clone:stop_duration={p['pad']:.2f}"
        if p["speed"] > 1.05:
            label = f"Claude investigating · {p['speed']:.0f}x speed"
            chain += (f",drawtext=fontfile={FONT}:text='{label}':fontsize=30:fontcolor=white"
                      ":box=1:boxcolor=black@0.55:boxborderw=14:x=w-tw-40:y=h-th-40")
        f.append(chain + f",fps=30[v{i}]")
    f.append("".join(f"[v{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0[vout]")
    for j, sid in enumerate(ids, start=1):
        ms = int(audio_at[sid] * 1000)
        f.append(f"[{j}:a]adelay={ms}:all=1[a{j}]")
    f.append("".join(f"[a{j}]" for j in range(1, len(ids) + 1))
             + f"amix=inputs={len(ids)}:normalize=0,loudnorm=I=-16:TP=-1.5,apad=whole_dur={total:.2f}[aout]")

    cmd += ["-filter_complex", ";".join(f), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(OUT / "demo.mp4")]
    subprocess.run(cmd, check=True)
    (OUT / "captions.srt").write_text(captions(scenes, durations, audio_at))

    for m in marks:
        fast = [p for p in pieces if m["start"] <= p["start"] < m["end"] and p["speed"] > 1.05]
        print(f"{m['id']:12} at {audio_at[m['id']]:6.1f}s  narration {durations.get(m['id'], 0):5.1f}s"
              + (f"  sped up {fast[0]['speed']:.1f}x" if fast else ""))
    print(f"total {total:.1f}s -> {OUT / 'demo.mp4'}")


if __name__ == "__main__":
    main()
