import os
import sys
import json
import time
import asyncio
import subprocess
import urllib.parse
import urllib.request
import edge_tts

# ----------------- STEP 1: SCRIPT LOGIC -----------------
def generate_script(topic, num_scenes=24):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is missing!")
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=api_key)

    print(f"Generating script for topic: '{topic}' with {num_scenes} scenes...")
    
    prompt = f"""
    Create a detailed documentary-style video script in Hindi about: {topic}.
    Provide the output strictly as a valid JSON object with the following structure:
    {{
      "title": "Video title in Hindi",
      "scenes": [
        {{
          "scene_number": 1,
          "narration": "Narration text in Hindi for scene 1",
          "visual_prompt": "Cinematic visual description in English for image generation"
        }}
      ]
    }}
    Ensure there are exactly {num_scenes} scenes in the 'scenes' list. Output raw JSON only without markdown formatting.
    """

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt,
            )
            text = response.text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            return text.strip()
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep((attempt + 1) * 5)
            else:
                raise e

# ----------------- STEP 2: AUDIO LOGIC -----------------
VOICE = "hi-IN-SwaraNeural"

async def generate_voiceovers():
    if not os.path.exists("script.json"):
        print("Error: script.json not found!")
        sys.exit(1)

    with open("script.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    scenes = data.get("scenes", [])
    os.makedirs("audio", exist_ok=True)

    for scene in scenes:
        scene_num = scene["scene_number"]
        narration = scene["narration"]
        audio_path = f"audio/scene_{scene_num}.mp3"

        if os.path.exists(audio_path):
            continue

        print(f"Generating audio for Scene {scene_num}...")
        communicate = edge_tts.Communicate(narration, VOICE)
        await communicate.save(audio_path)

    print("Voiceovers ready in 'audio/' folder!")

# ----------------- STEP 3: RELIABLE VISUALS LOGIC -----------------
def generate_visuals():
    if not os.path.exists("script.json"):
        print("Error: script.json not found!")
        sys.exit(1)

    with open("script.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    scenes = data.get("scenes", [])
    os.makedirs("images", exist_ok=True)

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    for scene in scenes:
        scene_num = scene["scene_number"]
        prompt = scene.get("visual_prompt", "cinematic documentary shot")
        img_path = f"images/scene_{scene_num}.jpg"

        if os.path.exists(img_path) and os.path.getsize(img_path) > 10000:
            continue

        print(f"Generating visual for Scene {scene_num}...")

        # Clean prompt: strip punctuation and take key words
        clean_prompt = "".join([c for c in prompt if c.isalnum() or c.isspace()])
        keywords = " ".join(clean_prompt.split()[:12])
        query_prompt = f"{keywords}, cinematic documentary photograph, photorealistic 8k, dramatic lighting"
        encoded = urllib.parse.quote(query_prompt)

        # Primary source: Pollinations with seed to force reliable generation
        seed = 1000 + scene_num
        url = f"[https://image.pollinations.ai/prompt/](https://image.pollinations.ai/prompt/){encoded}?width=1280&height=720&seed={seed}&nologo=true&model=flux"

        downloaded = False
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=45) as res:
                    img_data = res.read()
                    if len(img_data) > 10000:
                        with open(img_path, "wb") as f_out:
                            f_out.write(img_data)
                        downloaded = True
                        break
            except Exception as e:
                print(f"Attempt {attempt + 1} failed for Scene {scene_num}: {e}")
                time.sleep(3)

        if not downloaded:
            # Fallback 1: Simplified prompt
            try:
                simple_prompt = urllib.parse.quote(f"India vintage vehicle history documentary shot {scene_num}")
                simple_url = f"[https://image.pollinations.ai/prompt/](https://image.pollinations.ai/prompt/){simple_prompt}?width=1280&height=720&seed={seed}&nologo=true"
                req = urllib.request.Request(simple_url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as res:
                    with open(img_path, "wb") as f_out:
                        f_out.write(res.read())
                downloaded = True
            except Exception:
                pass

        # Duplicate prior scene image if download completely fails so video never goes black
        if not downloaded:
            prev_img = f"images/scene_{scene_num - 1}.jpg"
            if os.path.exists(prev_img):
                import shutil
                shutil.copy(prev_img, img_path)
            else:
                fallback_cmd = [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", "color=c=#1a1a2e:s=1280x720:d=1",
                    "-frames:v", "1", img_path
                ]
                subprocess.run(fallback_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("All scene visuals ready in 'images/' folder!")

# ----------------- STEP 4: VIDEO RENDERING WITH KEN BURNS -----------------
def render_full_video():
    if not os.path.exists("script.json"):
        print("Error: script.json not found!")
        sys.exit(1)

    with open("script.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    scenes = data.get("scenes", [])
    os.makedirs("temp_clips", exist_ok=True)
    clip_list_path = "clips.txt"
    valid_clips = []

    for scene in scenes:
        scene_num = scene["scene_number"]
        audio_file = f"audio/scene_{scene_num}.mp3"
        image_file = f"images/scene_{scene_num}.jpg"
        clip_file = f"temp_clips/clip_{scene_num}.mp4"

        if not os.path.exists(audio_file) or not os.path.exists(image_file):
            continue

        print(f"Rendering clip for Scene {scene_num} with camera motion...")

        # Get audio duration
        probe_cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_file
        ]
        duration_res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            duration = float(duration_res.stdout.strip())
        except Exception:
            duration = 10.0

        total_frames = int(duration * 25)

        # Smooth Ken Burns zoom effect
        zoom_filter = (
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='min(zoom+0.0010,1.15)':d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=25"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_file,
            "-i", audio_file,
            "-vf", zoom_filter,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration),
            "-shortest", clip_file
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        valid_clips.append(f"file '{clip_file}'")

    if not valid_clips:
        print("Error: No clips rendered.")
        sys.exit(1)

    with open(clip_list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(valid_clips))

    print(f"Stitching {len(valid_clips)} clips into final documentary video...")
    final_output = "final_documentary.mp4"
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", clip_list_path,
        "-c", "copy",
        final_output
    ]
    subprocess.run(concat_cmd, check=True)
    print(f"Video created successfully: {final_output}")

# ----------------- MAIN DISPATCHER -----------------
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "video"
    topic = sys.argv[2] if len(sys.argv) > 2 else "The Mahindra story in Hindi"
    num_scenes = int(sys.argv[3]) if len(sys.argv) > 3 else 24

    if mode == "script":
        script_json_str = generate_script(topic, num_scenes=num_scenes)
        try:
            parsed = json.loads(script_json_str)
        except Exception as e:
            print("Failed to parse JSON:", e)
            sys.exit(1)
            
        with open("script.json", "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        print("script.json successfully created and saved!")

    elif mode in ["audio", "video"]:
        asyncio.run(generate_voiceovers())
        generate_visuals()
        render_full_video()

    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
