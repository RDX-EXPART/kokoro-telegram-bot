from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
KOKORO_API = os.getenv("KOKORO_API")
VOICE = os.getenv("VOICE", "af_bella")
