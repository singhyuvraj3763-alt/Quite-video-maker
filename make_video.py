"""
Quiet Video Maker v2 - Hindi documentary video from a topic.
Modes:
  python make_video.py full
  python make_video.py script
  python make_video.py video
"""

import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
import requests
from PIL import Image

FPS = 25
W, H = 1920, 1080
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
VOICE = os.environ.get("VOICE", "hi-IN-MadhurNeural")

STYLE = (
    "cinematic documentary illustration, painterly digital art, muted warm "
    "color palette, soft dramatic lighting, highly detailed, wide 16:9 "
    "composition, no text, no letters, no logos, no watermark"
)

WORK = Path("work")
OUT = Path("out")

BAD_WORDS = (
    "image", "tts", "live", "audio", "embed", "robotics", "computer", "vision"
)

MSG_BAD_GEMINI = (
    "Your GEMINI_API_KEY is not valid. Make a new key at "
    "aistudio.google.com and update the GitHub secret."
)

DISCLAIMER = (
    "इस वीडियो के कई विजुअल्स AI से बनाए गए इलस्ट्रेशन हैं, असली तस्वीरें नहीं। "
    "यह वीडियो सिर्फ़ शैक्षणिक उद्देश्य के लिए है।"
)

def fatal(msg: str) -> None:
    print("\nERROR: " + msg, flush=True)
    sys.exit(2)

def log(msg: str) -> None:
    print(msg, flush=True)

# ==================== Gemini ====================
_good_model = None
_discovered = []

def gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        fatal("GEMINI_API_KEY is missing. Add it in Settings > Secrets and variables > Actions.")
    return key

def list_models(key: str) -> list:
    try:
        url = f"{GEMINI_BASE}/models?pageSize=200"
        r = requests.get(url, headers={"x-goog-api-key": key}, timeout=60)
        names = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in name:
                continue
            if any(b in name for b in BAD_WORDS):
                continue
            names.append(name)

        def version(n: str) -> float:
            nums = re.findall(r"\d+(?:\.\d+)?", n)
            return float(nums[0]) if nums else 0.0

        normal = sorted([n for n in names if "lite" not in n], key=version, reverse=True)
        lite = sorted([n for n in names if "lite" in n], key=version, reverse=True)
        return (normal + lite)[:6]
    except Exception as e:
        log(f"Could not list models: {e!r}")
        return []

def extract_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a : b + 1])
        raise

def gemini_json(prompt: str, validate, label: str, max_tokens: int = 16000, temperature: float = 0.8):
    global _good_model, _discovered
    key = gemini_key()
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    env_model = os.environ.get("GEMINI_MODEL", "").strip()
    for round_no in range(1, 5):
        candidates = [_good_model, env_model] + DEFAULT_MODELS + _discovered
        models, seen = [], set()
        for m in candidates:
            if m and m not in seen:
                seen.add(m)
                models.append(m)

        for model in models:
            url = f"{GEMINI_BASE}/models/{model}:generateContent"
            for attempt in range(1, 4):
                try:
                    r = requests.post(url, headers=headers, json=body, timeout=300)
                except Exception as e:
                    log(f"[{label}] network problem with {model}: {e!r}")
                    time.sleep(10 * attempt)
                    continue

                if r.status_code == 200:
                    try:
                        parts = r.json()["candidates"][0]["content"]["parts"]
                        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                        data = extract_json(text)
                        problem = validate(data)
                        if problem is None:
                            _good_model = model
                            return data
                        log(f"[{label}] answer from {model} not usable: {problem}")
                    except Exception as e:
                        log(f"[{label}] could not read answer from {model}: {e!r}")
                        time.sleep(5)
                        continue

                log(f"[{label}] {model} -> HTTP {r.status_code}: {r.text[:250]!r}")
                if "API key not valid" in r.text or "API_KEY_INVALID" in r.text:
                    fatal(MSG_BAD_GEMINI)
                if r.status_code in (400, 403, 404):
                    break
                time.sleep(min(10 * (2 ** (attempt - 1)), 60))

        if round_no == 1 or not _discovered:
            found = list_models(key)
            _discovered = [m for m in found if m not in DEFAULT_MODELS]
            log(f"Models this key can use: {found}")

        log(f"[{label}] all models failed in round {round_no}; waiting 60 s")
        time.sleep(60)

    sys.exit(f"Gemini gave no usable answer for '{label}' after many tries.")

def selftest_gemini() -> None:
    key = gemini_key()
    try:
        url = f"{GEMINI_BASE}/models?pageSize=1"
        r = requests.get(url, headers={"x-goog-api-key": key}, timeout=60)
        if "API key not valid" in r.text or "API_KEY_INVALID" in r.text:
            fatal(MSG_BAD_GEMINI)
        log(f"Gemini key check: HTTP {r.status_code}")
    except Exception as e:
        log(f"Gemini key check skipped (network): {e!r}")

# ==================== Script ====================
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#>`\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()

def words(t: str) -> int:
    return len(t.split())

def check_outline(d) -> str:
    ch = d.get("chapters") if isinstance(d, dict) else None
    if not isinstance(ch, list) or not (3 <= len(ch) <= 10):
        return "need 3 to 10 chapters"
    if not all(isinstance(c, dict) and c.get("title") and c.get("summary") for c in ch):
        return "chapter without title or summary"
    if not d.get("title"):
        return "missing title"
    return None

def make_check_scenes(m: int):
    def check(d) -> str:
        sc = d.get("scenes") if isinstance(d, dict) else None
        if not isinstance(sc, list) or len(sc) < m:
            return f"need {m} scenes, got {len(sc) if isinstance(sc, list) else 'none'}"
        for s in sc[:m]:
            bad = not isinstance(s, dict) or not s.get("narration") or not s.get("image_prompt")
            if bad:
                return "scene without narration or image_prompt"
            if words(clean_text(s["narration"])) < 20:
                return "narration too short"
        return None
    return check

def make_script(topic: str, n_scenes: int) -> dict:
    k = max(4, min(8, round(n_scenes / 3.5)))
    log(f"Planning outline for: {topic}")
    outline = gemini_json(
        "You are planning a calm, well-researched Hindi YouTube documentary that tells a business or history story.\n"
        f"Topic: {topic}\n"
        f"Plan it as {k} chapters in chronological order: a hook, the origin, the turning points, and what it means today.\n"
        "Return JSON only in this shape: "
        '{"title": "Hindi video title", "description": "2-3 sentence Hindi description", '
        '"chapters": [{"title": "short English chapter title", "summary": "2-3 English sentences"}]}',
        check_outline,
        label="outline",
    )
    chapters = outline["chapters"]
    k = len(chapters)
    counts = [n_scenes // k + (1 if i < (n_scenes % k) else 0) for i in range(k)]
    outline_text = "\n".join(f"{i + 1}. {c['title']}: {c['summary']}" for i, c in enumerate(chapters))
    scenes, prev = [], ""

    for i, (ch, m) in enumerate(zip(chapters, counts)):
        log(f"Writing chapter {i + 1}/{k}: {ch['title']} ({m} scenes)")
        role = ""
        if i == 0:
            role = "This is the opening chapter: scene 1 must be a strong hook. "
        if i == k - 1:
            role += "This is the final chapter: end with what it means today. "

        prompt = (
            f"You are the writer of a Hindi documentary channel.\nTopic: {topic}\n"
            f"Outline:\n{outline_text}\n"
            + (f"Narration so far:\n{prev}\n" if prev else "")
            + f"Now write chapter {i + 1} of {k}: {ch['title']} - {ch['summary']}\n{role}"
            f"Write exactly {m} scenes for this chapter.\n"
            "Rules:\n"
            "- narration: natural spoken Hindi in Devanagari, calm storytelling tone, 35-50 words per scene.\n"
            "- image_prompt: English, vivid historical/cinematic scene description. No text, no logos.\n"
            'Return JSON only: {"scenes": [{"narration": "", "image_prompt": ""}]}'
        )
        data = gemini_json(prompt, make_check_scenes(m), label=f"chapter {i + 1}/{k}")
        for s in data["scenes"][:m]:
            scenes.append({
                "chapter": ch["title"],
                "narration": clean_text(s["narration"]),
                "image_prompt": clean_text(s["image_prompt"]),
            })
        prev = scenes[-1]["narration"]
        time.sleep(2)

    total_words = sum(words(s["narration"]) for s in scenes)
    minutes = total_words / 2.3 / 60
    log(f"Script ready: {len(scenes)} scenes, {total_words} words, about {minutes:.1f} minutes of speech")
    return {
        "topic": topic,
        "title": clean_text(outline["title"]),
        "description": clean_text(outline.get("description", "")),
        "scenes": scenes,
    }

# ==================== Images ====================
def fit_cover(img: Image.Image) -> Image.Image:
    w, h = img.size
    target = W / H
    if w / h > target:
        nw = int(h * target)
        left = (w - nw) // 2
        img = img.crop((left, 0, left + nw, h))
    else:
        nh = int(w / target)
        top = (h - nh) // 2
        img = img.crop((0, top, w, top + nh))
    return img.resize((W, H), Image.LANCZOS)

def pollinations_get(prompt: str, size, seed: int, model, key: str):
    clean_p = quote(prompt, safe="")
    url = f"https://image.pollinations.ai/prompt/{clean_p}"
    params = {"width": size[0], "height": size[1], "seed": seed, "nologo": "true"}
    if model:
        params["model"] = model
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if key:
        params["key"] = key
    return requests.get(url, params=params, headers=headers, timeout=60)

def make_image(prompt: str, path: Path, seed: int, attempts: int = 4):
    key = os.environ.get("POLLINATIONS_API_KEY", "").strip()
    full = prompt.strip()[:300] + ", " + STYLE
    for attempt in range(attempts):
        model = "flux" if attempt % 2 == 0 else None
        try:
            r = pollinations_get(full, (W, H), seed + attempt * 7, model, key)
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image") and len(r.content) > 8000:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                fit_cover(img).save(path, quality=92)
                return True, 200
        except Exception as e:
            log(f"Image attempt failed: {e!r}")
            time.sleep(3)
    return False, 500

def selftest_images() -> None:
    WORK.mkdir(exist_ok=True)
    test_path = WORK / "selftest.jpg"
    ok, status = make_image("a mountain landscape at sunrise", test_path, 1, 2)
    if ok:
        log("Image service check: OK")
    else:
        log("Image service test had an issue; the run will retry automatically.")

def valid_file(p: Path, minimum: int = 5000) -> bool:
    return p.exists() and p.stat().st_size > minimum

def generate_images(scenes: list) -> list:
    n = len(scenes)
    ok = [False] * n
    for i, sc in enumerate(scenes):
        img = WORK / f"img_{i:03d}.jpg"
        if valid_file(img):
            ok[i] = True
            continue
        log(f"Generating Image {i + 1}/{n}")
        good, _ = make_image(sc["image_prompt"], img, seed=1000 + i)
        ok[i] = good
        time.sleep(1)

    paths = []
    for i in range(n):
        target = WORK / f"img_{i:03d}.jpg"
        if ok[i] and valid_file(target):
            paths.append(target)
            continue
        # Fallback to nearest rendered image
        prev = next((j for j in range(i - 1, -1, -1) if ok[j]), None)
        nxt = next((j for j in range(i + 1, n) if ok[j]), None)
        src = prev if prev is not None else nxt
        if src is not None:
            log(f"Scene {i + 1}: reusing image of scene {src + 1}")
            paths.append(WORK / f"img_{src:03d}.jpg")
        else:
            # Emergency solid color canvas if all fail
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#141e30:s=1920x1080:d=1", "-frames:v", "1", str(target)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            paths.append(target)
    return paths

# ==================== Voice ====================
async def _edge(text: str, path: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="-4%").save(str(path))

def audio_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0

def audio_ok(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 1000 and audio_duration(path) > 1.0

def speak(text: str, path: Path) -> None:
    text = clean_text(text)
    for attempt in range(3):
        try:
            asyncio.run(_edge(text, path))
            if audio_ok(path):
                return
        except Exception as e:
            log(f"Edge-TTS attempt {attempt + 1} failed: {e!r}")
            time.sleep(2)
    for attempt in range(2):
        try:
            from gtts import gTTS
            gTTS(text, lang="hi").save(str(path))
            if audio_ok(path):
                return
        except Exception as e:
            log(f"gTTS attempt {attempt + 1} failed: {e!r}")
            time.sleep(2)
    sys.exit("Could not create voiceover.")

# ==================== Video ====================
def motion(i: int, frames: int) -> str:
    f = frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    styles = [
        (f"1+0.12*on/{f}", cx, cy),
        (f"1.12-0.12*on/{f}", cx, cy),
        ("1.08", f"(iw-iw/zoom)*on/{f}", cy),
        ("1.08", f"(iw-iw/zoom)*(1-on/{f})", cy),
    ]
    z, x, y = styles[i % 4]
    return f"scale=3840:2160:flags=lanczos,zoompan=z='{z}':x='{x}':y='{y}':d={f}:s={W}x{H}:fps={FPS},format=yuv420p"

def render_clip(img: Path, aud: Path, clip: Path, i: int, dur: float) -> None:
    tmp = clip.with_name(clip.stem + ".tmp.mp4")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(img), "-i", str(aud),
        "-vf", motion(i, int(dur * FPS)), "-af", "apad",
        "-t", f"{dur:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k", str(tmp)
    ]
    subprocess.run(cmd, check=True)
    os.replace(tmp, clip)

def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"

def build_video(data: dict) -> None:
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    scenes = data["scenes"]
    n = len(scenes)

    log("=== Step 1/3: images ===")
    imgs = generate_images(scenes)

    log("=== Step 2/3: voice ===")
    auds, durs = [], []
    for i, sc in enumerate(scenes):
        aud = WORK / f"aud_{i:03d}.mp3"
        if not audio_ok(aud):
            log(f"Recording Voice {i + 1}/{n}")
            speak(sc["narration"], aud)
        auds.append(aud)
        durs.append(audio_duration(aud) + 0.4)

    log("=== Step 3/3: video ===")
    clips, chapters, t, last = [], [], 0.0, None
    for i, sc in enumerate(scenes):
        clip = WORK / f"clip_{i:03d}.mp4"
        if not valid_file(clip, 1000):
            log(f"Rendering Clip {i + 1}/{n}")
            render_clip(imgs[i], auds[i], clip, i, durs[i])
        if sc.get("chapter") and sc["chapter"] != last:
            chapters.append((t, sc["chapter"]))
            last = sc["chapter"]
        t += durs[i]
        clips.append(clip)

    listfile = WORK / "list.txt"
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))

    final = OUT / "final.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", "-movflags", "+faststart", str(final)],
        check=True
    )

    lines = [data.get("title", ""), "", data.get("description", ""), "", "CHAPTERS"]
    for k, (ts, name) in enumerate(chapters):
        lines.append(f"{stamp(0 if k == 0 else ts)} {name}")
    lines += ["", DISCLAIMER]
    (OUT / "description.txt").write_text("\n".join(lines), encoding="utf-8")
    log(f"DONE! Video length: {stamp(t)}. Saved to {final}")

# ==================== Main ====================
def write_script_files(data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    Path("script.json").write_text(text, encoding="utf-8")
    OUT.mkdir(exist_ok=True)
    (OUT / "script.json").write_text(text, encoding="utf-8")
    readable = [data.get("title", ""), ""]
    for i, sc in enumerate(data["scenes"], 1):
        readable += [f"[{i}] ({sc.get('chapter', '')})", sc["narration"], ""]
    (OUT / "script.txt").write_text("\n".join(readable), encoding="utf-8")

def get_topic() -> str:
    topic = os.environ.get("TOPIC", "").strip()
    if not topic and Path("topic.txt").exists():
        topic = Path("topic.txt").read_text(encoding="utf-8").strip()
    if not topic:
        topic = "The Mahindra story in Hindi"
    return topic

def load_or_make_script(topic: str, n: int) -> dict:
    p = Path("script.json")
    if os.environ.get("RESUME") == "1" and p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("topic") == topic and data.get("scenes"):
                log("Resuming with existing script.")
                return data
        except Exception:
            pass
    return make_script(topic, n)

def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    n = int(os.environ.get("SCENES", "24") or 24)

    if mode in ("full", "video"):
        selftest_images()
    if mode in ("full", "script"):
        selftest_gemini()
        topic = get_topic()
        data = load_or_make_script(topic, n)
        write_script_files(data)
        if mode == "script":
            log("Script saved.")
            return
    else:
        p = Path("script.json")
        if not p.exists():
            fatal("script.json not found. Run in 'script' or 'full' mode first.")
        data = json.loads(p.read_text(encoding="utf-8"))

    build_video(data)

if __name__ == "__main__":
    main()
