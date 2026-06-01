import requests

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters
)

from config import *

async def tts(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text

    payload = {
        "model": "kokoro",
        "input": text,
        "voice": VOICE
    }

    r = requests.post(
        f"{KOKORO_API}/v1/audio/speech",
        json=payload
    )

    with open("voice.wav", "wb") as f:
        f.write(r.content)

    await update.message.reply_voice(
        voice=open("voice.wav", "rb")
    )

app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        tts
    )
)

app.run_polling()
