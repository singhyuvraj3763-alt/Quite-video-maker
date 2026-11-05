"""
Quiet Video Maker - Hindi documentary video from a topic.

Usage:
  python make_video.py full     -> topic -> script -> voice -> video, all in one run
  python make_video.py script   -> only writes script.json (and out/script.txt) from the topic
  python make_video.py video    -> reads script.json and renders out/final.mp4

Environment variables:
  GEMINI_API_KEY (required for "script")
  TOPIC, SCENES  (for "full" and "script"; if TOPIC is empty, topic.txt is used)
  GEMINI_MODEL   (optional, default gemini-2.5-flash)
  VOICE          (optional, default hi-IN-MadhurNeural; female: hi-IN-SwaraNeural)
  POLLINATIONS_API_KEY (optional but recommended: free key from enter.pollinations.ai)
"""
import asyncio
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw

FPS = 25
W, H = 1920, 1080
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
VOICE = os.environ.get("VOICE", "hi-IN-MadhurNeural")
STYLE = (
    "cinematic documentary illustration, painterly digital art, muted warm "
    "color palette, soft dramatic lighting, highly detailed, wide 16:9 "
    "composition, no text, no letters, no logos, no watermark"
)
WORK = Path("work")
OUT = Path("out")

DISCLAIMER = (
    "इस वीडियो के कई विज़ुअल्स AI से बनाए गए इलस्ट्रेशन हैं, असली तस्वीरें नहीं। "
    "यह वीडियो सिर्फ़ शैक्षणिक उद्देश्य के लिए है।"
)


# ---------------------------------------------------------------- script ----
def gemini_script(topic: str, n_scenes: int) -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is missing. Add it in Settings > Secrets > Actions.")

    prompt = (
        "You are a documentary writer for a calm, well-researched Hindi YouTube "
        "channel that tells business and history stories as stories.\n"
        "Topic: " + topic + "\n"
        "Write a documentary script with exactly " + str(n_scenes) + " scenes.\n"
        "Rules:\n"
        "- narration: natural spoken Hindi in Devanagari, calm storytelling tone, "
        "45-60 words per scene, no bullet points, no stage directions.\n"
        "- Scene 1 must be a strong hook. Then tell the story in chronological "
        "order with clear turning points, and end with what it means today.\n"
        "- Use only facts you are highly confident about. If you are unsure of a "
        "date, number or name, leave it out or speak generally. Never invent "
        "quotes, people or events.\n"
        "- image_prompt: English, one vivid illustration description per scene "
        "(setting, mood, objects, era-appropriate details). No text, no logos, "
        "no real people's faces.\n"
        "- chapter: short English chapter title. Scenes in the same chapter share "
        "exactly the same title. Use 6 to 8 chapters in total.\n"
        "Return JSON only in this shape:\n"
        '{"title": "Hindi video title", "description": "2-3 sentence Hindi '
        'description", "scenes": [{"chapter": "", "narration": "", '
        '"image_prompt": ""}]}'
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.8,
            "maxOutputTokens": 32000,
        },
    }
    for attempt in range(4):
        try:
            r = requests.post(
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=body,
                timeout=300,
            )
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts)
                data = json.loads(text)
                assert data["scenes"] and all(
                    {"chapter", "narration", "image_prompt"} <= set(s) for s in data["scenes"]
                )
                return data
            print("Gemini error", r.status_code, r.text[:400])
        except Exception as e:  # noqa: BLE001
            print("Script attempt failed:", repr(e))
        time.sleep(10 * (attempt + 1))
    sys.exit("Could not get a valid script from Gemini. Check the key or model name.")


# ---------------------------------------------------------------- images ----
def placeholder(path: Path, seed: int) -> None:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    base = 25 + (seed * 17) % 40
    for y in range(H):
        c = int(base + 40 * y / H)
        d.line([(0, y), (W, y)], fill=(c, c - 5, c + 10))
    img.save(path, quality=90)


def make_image(prompt: str, path: Path, seed: int) -> bool:
    """Free Pollinations image. With a free API key: no watermark, higher limits.
    Without a key the anonymous tier allows ~1 image per 15 seconds and adds a
    watermark, so we wait between requests and crop the watermark off."""
    full = f"{prompt}, {STYLE}"
    key = os.environ.get("POLLINATIONS_API_KEY", "").strip()
    if key:
        url = "https://gen.pollinations.ai/image/" + quote(full)
        params = {"width": W, "height": H, "seed": seed, "key": key}
    else:
        url = "https://image.pollinations.ai/prompt/" + quote(full)
        params = {"width": W, "height": H, "nologo": "true", "seed": seed, "model": "flux"}
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, timeout=180)
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image") and len(r.content) > 10000:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                if not key:  # remove the corner watermark: crop 6% and scale back up
                    cw, ch = img.size
                    img = img.crop((0, 0, int(cw * 0.94), int(ch * 0.94)))
                img.resize((W, H), Image.LANCZOS).save(path, quality=92)
                return True
            print("Image problem", r.status_code, ctype, flush=True)
        except Exception as e:  # noqa: BLE001
            print("Image attempt failed:", repr(e), flush=True)
        time.sleep(16 * (attempt + 1))
    placeholder(path, seed)
    return False


# ----------------------------------------------------------------- voice ----
async def _edge(text: str, path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, VOICE, rate="-8%").save(str(path))


def speak(text: str, path: Path) -> None:
    try:
        asyncio.run(_edge(text, path))
        if path.exists() and path.stat().st_size > 1000:
            return
    except Exception as e:  # noqa: BLE001
        print("Edge-TTS failed, using gTTS fallback:", repr(e))
    from gtts import gTTS

    gTTS(text, lang="hi").save(str(path))


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# ----------------------------------------------------------------- video ----
def motion(i: int, frames: int) -> str:
    """Slow Ken Burns motion, alternating between 4 styles."""
    f = frames
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"
    styles = [
        (f"1+0.15*on/{f}", centre_x, centre_y),               # zoom in
        (f"1.15-0.15*on/{f}", centre_x, centre_y),            # zoom out
        ("1.12", f"(iw-iw/zoom)*on/{f}", centre_y),           # pan left -> right
        ("1.12", f"(iw-iw/zoom)*(1-on/{f})", centre_y),       # pan right -> left
    ]
    z, x, y = styles[i % 4]
    return (
        f"scale=3840:2160:flags=lanczos,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={f}:s={W}x{H}:fps={FPS},"
        f"format=yuv420p"
    )


def render_clip(img: Path, aud: Path, clip: Path, i: int, dur: float) -> None:
    frames = int(dur * FPS)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(img), "-i", str(aud),
        "-vf", motion(i, frames),
        "-af", "apad",
        "-t", f"{dur:.3f}",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k",
        str(clip),
    ]
    subprocess.run(cmd, check=True)


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def build_video(data: dict) -> None:
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    clips, chapters = [], []
    t, last, fails = 0.0, None, 0
    scenes = data["scenes"]

    for i, sc in enumerate(scenes):
        print(f"--- Scene {i + 1}/{len(scenes)} ---", flush=True)
        img = WORK / f"img_{i:03d}.jpg"
        aud = WORK / f"aud_{i:03d}.mp3"
        clip = WORK / f"clip_{i:03d}.mp4"
        if not make_image(sc["image_prompt"], img, seed=1000 + i):
            fails += 1
            if fails >= 3:
                sys.exit(
                    "Stopping: 3 images failed (the free image service is limiting "
                    "requests). Add a free POLLINATIONS_API_KEY secret and run again."
                )
        speak(sc["narration"], aud)
        dur = audio_duration(aud) + 0.6
        render_clip(img, aud, clip, i, dur)
        if sc["chapter"] != last:
            chapters.append((t, sc["chapter"]))
            last = sc["chapter"]
        t += dur
        clips.append(clip)
        # anonymous Pollinations allows about 1 image per 15 seconds
        time.sleep(1 if os.environ.get("POLLINATIONS_API_KEY", "").strip() else 16)

    if fails == len(scenes):
        sys.exit("Every image failed to generate. The image service may be down.")
    if fails:
        print(f"WARNING: {fails} scene(s) used a plain placeholder image.")

    listfile = WORK / "list.txt"
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    final = OUT / "final.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", str(final)],
        check=True,
    )

    lines = [data.get("title", ""), "", data.get("description", ""), "", "CHAPTERS"]
    for k, (ts, name) in enumerate(chapters):
        lines.append(f"{stamp(0 if k == 0 else ts)} {name}")
    lines += ["", "SOURCES", "[Add your real sources here]", "", DISCLAIMER]
    (OUT / "description.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"Done. Video length about {stamp(t)}. Saved to {final}")


# ------------------------------------------------------------------ main ----
def write_script_files(data: dict) -> None:
    Path("script.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT.mkdir(exist_ok=True)
    (OUT / "script.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    readable = [data.get("title", ""), ""]
    for i, sc in enumerate(data["scenes"], 1):
        readable += [f"[{i}] ({sc['chapter']})", sc["narration"], ""]
    (OUT / "script.txt").write_text("\n".join(readable), encoding="utf-8")


def get_topic() -> str:
    topic = os.environ.get("TOPIC", "").strip()
    if not topic and Path("topic.txt").exists():
        topic = Path("topic.txt").read_text(encoding="utf-8").strip()
    if not topic:
        sys.exit("No topic. Type one in the Run workflow box or put it in topic.txt.")
    return topic


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    if mode in ("script", "full"):
        n = int(os.environ.get("SCENES", "24") or 24)
        data = gemini_script(get_topic(), n)
        write_script_files(data)
        print("Script saved.", flush=True)
        if mode == "full":
            build_video(data)
    else:  # "video": render from the saved (and possibly hand-corrected) script.json
        p = Path("script.json")
        if not p.exists():
            sys.exit("script.json not found. Run in 'script' or 'full' mode first.")
        build_video(json.loads(p.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
