import os
import sys
import pathlib

# Ensure "/app/Tg Bot" is importable despite the space in folder name
BOT_DIR = str(pathlib.Path(__file__).resolve().parent.parent)
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

# Load .env for config module
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BOT_DIR, ".env"))
