import os
import pathlib
import sys

BOT_DIR = str(pathlib.Path(__file__).resolve().parent.parent)
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BOT_DIR, ".env"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("DATABASE_URL", "postgresql://test/test")
os.environ.setdefault("ADMIN_IDS", "111")
