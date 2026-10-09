import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_IDS = {int(x) for x in os.environ["ADMIN_IDS"].replace(" ", "").split(",") if x}
