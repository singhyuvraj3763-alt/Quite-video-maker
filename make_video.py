import os
import sys
import json
import asyncio
import edge_tts

# Voice configuration for Hindi narration
VOICE = "hi-IN-SwaraNeural"

async def generate_voiceovers():
    if not os.path.exists("script.json"):
        print("Error: script.json not found!")
        sys.exit(1)

    with open("script.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    scenes = data.get("scenes", [])
    print(f"Found {len(scenes)} scenes to process for voiceover...")

    os.makedirs("audio", exist_ok=True)

    for scene in scenes:
        scene_num = scene["scene_number"]
        narration = scene["narration"]
        audio_path = f"audio/scene_{scene_num}.mp3"

        print(f"Generating audio for Scene {scene_num}...")
        communicate = edge_tts.Communicate(narration, VOICE)
        await communicate.save(audio_path)

    print("All scene voiceovers generated successfully inside 'audio/' folder!")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "audio"

    if mode == "audio":
        asyncio.run(generate_voiceovers())
    else:
        print(f"Unknown mode: {mode}")
