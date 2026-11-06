"""
Quiet Video Maker v2 - Hindi documentary video from a topic.

  python make_video.py full     topic -> script -> images -> voice -> video (one run)
  python make_video.py script   only write script.json (+ out/script.txt)
  python make_video.py video    render from an existing script.json

Environment:
  GEMINI_API_KEY        required (script writing)
  POLLINATIONS_API_KEY  required (images, free key from enter.pollinations.ai)
  TOPIC, SCENES         topic text and number of scenes (topic.txt is used if TOPIC is empty)
  GEMINI_MODEL          optional model override
  VOICE                 optional, default hi-IN-MadhurNeural (female: hi-IN-SwaraNeural)
  RESUME=1, ATTEMPT=n   set by the workflow when it retries; finished work is kept

Exit codes: 0 = done, 1 = temporary problem (the workflow retries), 2 = permanent problem.
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
DEFAULT_MODELS = ["gemini-3.8-flash"]
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


def fatal(msg: str) -> None:
    """Permanent problem: retrying will not help."""
    print("\nERROR: " + msg, flush=True)
    sys.exit(2)


def log(msg: str) -> None:
    print(msg, flush=True)


# ============================================================ Gemini ======
_good_model = None
_discovered: list = []


def gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        fatal("GEMINI_API_KEY is missing. Add it in Settings > Secrets and variables > Actions.")
    return key


def list_models(key: str) -> list:
    """Ask Google which text models this key can use. Newest 'flash' first, 'lite' last."""
    try:
        r = requests.get(f"{GEMINI_BASE}/models?pageSize=200", headers={"x-goog-api-key": key}, timeout=60)
        names = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in name:
                continue
            if any(b in name for b in ("image", "tts", "live", "audio", "embed", "robotics", "computer", "vision")):
                continue
            names.append(name)

        def version(n: str) -> float:
            nums = re.findall(r"\d+(?:\.\d+)?", n)
            return float(nums[0]) if nums else 0.0

        normal = sorted([n for n in names if "lite" not in n], key=version, reverse=True)
        lite = sorted([n for n in names if "lite" in n], key=version, reverse=True)
        return (normal + lite)[:6]
    except Exception as e:  # noqa: BLE001
        log(f"Could not list models: {e!r}")
        return []


def extract_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a : b + 1])
        raise


def gemini_json(prompt: str, validate, label: str, max_tokens: int = 16000, temperature: float = 0.8):
    """Call Gemini until we get valid, validated JSON. Retries temporary errors and
    switches to other models if one is unavailable or overloaded."""
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
                except Exception as e:  # noqa: BLE001
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
                    except Exception as e:  # noqa: BLE001
                        log(f"[{label}] could not read answer from {model}: {e!r}")
                    time.sleep(5)
                    continue
                log(f"[{label}] {model} -> HTTP {r.status_code}: {r.text[:250]!r}")
                if "API key not valid" in r.text or "API_KEY_INVALID" in r.text:
                    fatal("Your GEMINI_API_KEY is not valid. Make a new key at aistudio.google.com and update the GitHub secret.")
                if r.status_code in (400, 403, 404):
                    break  # this model cannot be used -> try the next one
                time.sleep(min(10 * 2 ** (attempt - 1), 60))  # 429/5xx: wait and retry
        if round_no == 1 or not _discovered:
            found = list_models(key)
            _discovered = [m for m in found if m not in DEFAULT_MODELS]
            log(f"Models this key can use: {found}")
        log(f"[{label}] all models failed in round {round_no}; waiting 60 s")
        time.sleep(60)
    sys.exit(f"Gemini did not give a usable answer for '{label}' after many tries (temporary problem).")


def selftest_gemini() -> None:
    key = gemini_key()
    try:
        r = requests.get(f"{GEMINI_BASE}/models?pageSize=1", headers={"x-goog-api-key": key}, timeout=60)
        if "API key not valid" in r.text or "API_KEY_INVALID" in r.text:
            fatal("Your GEMINI_API_KEY is not valid. Make a new key at aistudio.google.com and update the GitHub secret.")
        log(f"Gemini key check: HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        log(f"Gemini key check skipped (network): {e!r}")


# ============================================================ Script ======
def clean_text(t: str) -> str:
    t = re.sub(r"[*_#`>\[\]{}]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()


def words(t: str) -> int:
    return len(t.split())


def check_outline(d) -> str:
    ch = d.get("chapters") if isinstance(d, dict) else None
    if not isinstance(ch, list) or not 3 <= len(ch) <= 10:
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
            if not isinstance(s, dict) or not s.get("narration") or not s.get("image_prompt"):
                return "scene without narration or image_prompt"
            if words(clean_text(s["narration"])) < 28:
                return "narration too short"
        return None

    return check


def make_script(topic: str, n_scenes: int) -> dict:
    k = max(4, min(8, round(n_scenes / 3.5)))
    log(f"Planning outline for: {topic}")
    outline = gemini_json(
        "You are planning a calm, well-researched Hindi YouTube documentary that tells "
        "a business or history story as a story.\nTopic: " + topic + "\n"
        "Plan it as " + str(k) + " chapters in chronological order: a hook, the origin, "
        "the turning points, and what it means today.\n"
        "Use only facts you are highly confident about; never invent events, people or quotes.\n"
        "Return JSON only in this shape: "
        '{"title": "Hindi video title", "description": "2-3 sentence Hindi description", '
        '"chapters": [{"title": "short English chapter title", '
        '"summary": "2-3 English sentences with the key events of this chapter"}]}',
        check_outline,
        label="outline",
    )
    chapters = outline["chapters"]
    k = len(chapters)
    counts = [n_scenes // k + (1 if i < n_scenes % k else 0) for i in range(k)]
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
            "You are the writer of a calm, well-researched Hindi documentary channel.\n"
            "Topic: " + topic + "\nFull story outline:\n" + outline_text + "\n"
            + ("Narration so far (end of previous chapter): " + prev + "\n" if prev else "")
            + "Now write chapter " + str(i + 1) + " of " + str(k) + ": " + ch["title"] + " - " + ch["summary"] + "\n"
            + role
            + "Write exactly " + str(m) + " scenes for this chapter.\n"
            "Rules:\n"
            "- narration: natural spoken Hindi in Devanagari, calm storytelling tone, "
            "45-60 words per scene, no bullet points, no stage directions, do not repeat earlier scenes.\n"
            "- Use only facts you are highly confident about. If unsure of a date, number or name, "
            "leave it out or speak generally. Never invent quotes, people or events.\n"
            "- image_prompt: English, one vivid illustration description (setting, mood, objects, "
            "era-appropriate details). No text, no logos, no real people's faces.\n"
            'Return JSON only: {"scenes": [{"narration": "", "image_prompt": ""}]}'
        )
        data = gemini_json(prompt, make_check_scenes(m), label=f"chapter {i + 1}/{k}")
        for s in data["scenes"][:m]:
            scenes.append(
                {
                    "chapter": ch["title"],
                    "narration": clean_text(s["narration"]),
                    "image_prompt": clean_text(s["image_prompt"]),
                }
            )
        prev = scenes[-1]["narration"]
        time.sleep(3)

    total_words = sum(words(s["narration"]) for s in scenes)
    log(f"Script ready: {len(scenes)} scenes, {total_words} words, about {total_words / 2.3 / 60:.1f} minutes of speech")
    return {
        "topic": topic,
        "title": clean_text(outline["title"]),
        "description": clean_text(outline.get("description", "")),
        "scenes": scenes,
    }


# ============================================================ Images ======
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


def pollinations_get(prompt: str, size, seed: int, model, key: str, query_key: bool):
    url = "https://gen.pollinations.ai/image/" + quote(prompt, safe="")
    params = {"width": size[0], "height": size[1], "seed": seed}
    if model:
        params["model"] = model
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if key and query_key:
        params["key"] = key
    return requests.get(url, params=params, headers=headers, timeout=240)


def make_image(prompt: str, path: Path, seed: int, attempts: int = 6):
    """Returns (ok, last_http_status). Never raises."""
    key = os.environ.get("POLLINATIONS_API_KEY", "").strip()
    full = prompt.strip()[:450] + ", " + STYLE
    last, query_key = None, False
    for attempt in range(attempts):
        model = "flux" if attempt % 2 == 0 else None
        try:
            r = pollinations_get(full, (W, H), seed + attempt * 7, model, key, query_key)
            last = r.status_code
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image") and len(r.content) > 10000:
                fit_cover(Image.open(io.BytesIO(r.content)).convert("RGB")).save(path, quality=92)
                return True, 200
            note = r.text[:120] if ("text" in ctype or "json" in ctype) else ""
            log(f"  image problem: HTTP {r.status_code} {ctype} {note!r}")
            if r.status_code in (401, 403) and key and not query_key:
                query_key = True  # try sending the key as a URL parameter instead
                continue
            if r.status_code in (401, 402, 403):
                return False, r.status_code  # permanent: key rejected or no balance
        except Exception as e:  # noqa: BLE001
            log(f"  image attempt failed: {e!r}")
        time.sleep(min(10 * (attempt + 1), 60))
    return False, last


def selftest_images() -> None:
    key = os.environ.get("POLLINATIONS_API_KEY", "").strip()
    if not key:
        fatal("POLLINATIONS_API_KEY is missing. Create a free key at enter.pollinations.ai and add it as a GitHub secret.")
    WORK.mkdir(exist_ok=True)
    ok, status = make_image("a simple mountain landscape at sunrise", WORK / "selftest.jpg", 1, attempts=3)
    if ok:
        log("Image service check: OK")
    elif status in (401, 403):
        fatal("Pollinations rejected your API key (HTTP %s). Check the POLLINATIONS_API_KEY secret, or make a new key." % status)
    elif status == 402:
        fatal("Pollinations says your account has no credit left (HTTP 402). Wait for the free allowance to refill.")
    else:
        log(f"WARNING: image service test failed (HTTP {status}); continuing, the run will retry.")


def valid_file(p: Path, minimum: int = 10000) -> bool:
    return p.exists() and p.stat().st_size > minimum


def generate_images(scenes: list) -> list:
    n = len(scenes)
    ok = [False] * n
    last_status = None
    for i, sc in enumerate(scenes):
        img = WORK / f"img_{i:03d}.jpg"
        if valid_file(img):
            ok[i] = True
            continue
        log(f"Image {i + 1}/{n}")
        good, status = make_image(sc["image_prompt"], img, seed=1000 + i)
        ok[i] = good
        last_status = status
        if not good and status in (401, 402, 403):
            fatal(f"Pollinations refused the request (HTTP {status}). Check your key and credit.")
        time.sleep(1)

    failed = [i for i in range(n) if not ok[i]]
    if failed:
        log(f"{len(failed)} image(s) could not be generated: scenes {[i + 1 for i in failed]} (last HTTP {last_status})")
        final_attempt = int(os.environ.get("ATTEMPT", "3") or 3) >= 3
        if not final_attempt:
            sys.exit(1)  # the workflow waits and tries again, keeping finished images
        if len(failed) > n // 2:
            sys.exit(1)
    # fill any remaining gaps with the nearest good image so the screen is never black
    paths = []
    for i in range(n):
        if ok[i]:
            paths.append(WORK / f"img_{i:03d}.jpg")
            continue
        prev = next((j for j in range(i - 1, -1, -1) if ok[j]), None)
        nxt = next((j for j in range(i + 1, n) if ok[j]), None)
        src = prev if prev is not None else nxt
        log(f"  scene {i + 1}: reusing image of scene {src + 1}")
        paths.append(WORK / f"img_{src:03d}.jpg")
    return paths


# ============================================================ Voice =======
async def _edge(text: str, path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, VOICE, rate="-8%").save(str(path))


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out) if out else 0.0


def audio_ok(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 1000 and audio_duration(path) > 1.0
    except Exception:  # noqa: BLE001
        return False


def speak(text: str, path: Path) -> None:
    text = clean_text(text)
    for attempt in range(3):
        try:
            asyncio.run(_edge(text, path))
            if audio_ok(path):
                return
        except Exception as e:  # noqa: BLE001
            log(f"  Edge-TTS attempt {attempt + 1} failed: {e!r}")
        time.sleep(5 * (attempt + 1))
    log("  Edge-TTS did not work, trying the gTTS fallback voice")
    for attempt in range(3):
        try:
            from gtts import gTTS

            gTTS(text, lang="hi").save(str(path))
            if audio_ok(path):
                return
        except Exception as e:  # noqa: BLE001
            log(f"  gTTS attempt {attempt + 1} failed: {e!r}")
        time.sleep(5 * (attempt + 1))
    sys.exit("Could not create the voiceover (temporary problem).")


# ============================================================ Video =======
def motion(i: int, frames: int) -> str:
    f = frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    styles = [
        (f"1+0.15*on/{f}", cx, cy),
        (f"1.15-0.15*on/{f}", cx, cy),
        ("1.12", f"(iw-iw/zoom)*on/{f}", cy),
        ("1.12", f"(iw-iw/zoom)*(1-on/{f})", cy),
    ]
    z, x, y = styles[i % 4]
    return (
        f"scale=3840:2160:flags=lanczos,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={f}:s={W}x{H}:fps={FPS},format=yuv420p"
    )


def render_clip(img: Path, aud: Path, clip: Path, i: int, dur: float) -> None:
    tmp = clip.with_name(clip.stem + ".tmp.mp4")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(img), "-i", str(aud),
        "-vf", motion(i, int(dur * FPS)), "-af", "apad", "-t", f"{dur:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k", str(tmp),
    ]
    subprocess.run(cmd, check=True)
    os.replace(tmp, clip)


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def black_check(final: Path) -> None:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(final), "-vf",
             "scale=320:-2,blackdetect=d=2:pix_th=0.04", "-an", "-f", "null", "-"],
            capture_output=True, text=True,
        ).stderr
        segs = re.findall(r"black_start:([\d.]+).*?black_duration:([\d.]+)", out)
        if segs:
            log("WARNING: black segments found (start, seconds): " + ", ".join(f"{a}s/{b}s
