"""
Quiet Video Maker - Hindi documentary video from a topic.

Usage:
  python make_video.py script   -> writes script.json (and out/script.txt) from TOPIC
  python make_video.py video    -> reads script.json and renders out/final.mp4

Environment variables:
  GEMINI_API_KEY (required for "script")
  TOPIC, SCENES  (for "script")
  GEMINI_MODEL   (optional, default gemini-2.5-flash)
  VOICE          (optional, default hi-IN-MadhurNeural; female: hi-IN-SwaraNeural)
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
