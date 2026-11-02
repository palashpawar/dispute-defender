"""Generate one narration clip per scene with ElevenLabs.

    python demo/tts.py --voices        # list voices on the account
    python demo/tts.py [--voice ID]    # render demo/out/audio/<scene>.mp3
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs import ElevenLabs, VoiceSettings

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env")  # the SDK reads ELEVENLABS_API_KEY
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George": warm, measured narrator (premade)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", action="store_true")
    ap.add_argument("--voice", default=os.getenv("ELEVENLABS_VOICE", DEFAULT_VOICE))
    ap.add_argument("--only", nargs="*", help="scene ids to (re)render")
    args = ap.parse_args()
    if not os.getenv("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY is not set (run: npx -y @elevenlabs/cli@latest onboard init)")
    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])  # SDK default is read at import, before load_dotenv

    if args.voices:
        for v in client.voices.get_all().voices:
            print(v.voice_id, v.name, v.category)
        return

    scenes = json.loads((ROOT / "narration.json").read_text())
    out = ROOT / "out" / "audio"
    out.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(scenes):
        path = out / f"{s['id']}.mp3"
        if args.only is not None and s["id"] not in args.only:
            continue
        audio = client.text_to_speech.convert(
            text=s["text"],
            voice_id=args.voice,
            model_id="eleven_v4",
            output_format="mp3_44100_128",
            # request stitching: neighbouring text keeps intonation continuous across clips
            previous_text=scenes[i - 1]["text"] if i else None,
            next_text=scenes[i + 1]["text"] if i + 1 < len(scenes) else None,
            voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75),
        )
        with path.open("wb") as fh:
            for chunk in audio:
                fh.write(chunk)
        print(f"{s['id']:12} {duration(path):5.1f}s")

    durations = {s["id"]: round(duration(out / f"{s['id']}.mp3"), 2) for s in scenes if (out / f"{s['id']}.mp3").exists()}
    (ROOT / "out" / "durations.json").write_text(json.dumps(durations, indent=1))
    print(f"total {sum(durations.values()):.1f}s")


if __name__ == "__main__":
    main()
