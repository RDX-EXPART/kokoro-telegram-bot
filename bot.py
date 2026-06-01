import requests

from telegram import Update
from telegram.ext import (
Application,
MessageHandler,
CommandHandler,
ContextTypes,
filters
)

from config import *

CURRENT_VOICE = VOICE

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
"🎤 Kokoro TTS Bot Ready!\n\n"
"/voices - Show voices\n"
"/voice voice_name - Change voice"
)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
"/voices - List voices\n"
"/voice af_bella\n"
"/voice hm_omega"
)

async def voices(update: Update, context: ContextTypes.DEFAULT_TYPE):
try:
r = requests.get(f"{KOKORO_API}/v1/audio/voices")
data = r.json()

```
    voice_list = "\n".join(
        [voice["id"] for voice in data["voices"]]
    )

    await update.message.reply_text(
        f"Available Voices:\n\n{voice_list}"
    )

except Exception as e:
    await update.message.reply_text(str(e))
```

async def change_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
global CURRENT_VOICE

```
if not context.args:
    await update.message.reply_text(
        "Usage:\n/voice af_bella"
    )
    return

CURRENT_VOICE = context.args[0]

await update.message.reply_text(
    f"✅ Voice changed to:\n{CURRENT_VOICE}"
)
```

async def tts(update: Update, context: ContextTypes.DEFAULT_TYPE):

```
text = update.message.text

payload = {
    "model": "kokoro",
    "input": text,
    "voice": CURRENT_VOICE
}

try:
    r = requests.post(
        f"{KOKORO_API}/v1/audio/speech",
        json=payload,
        timeout=120
    )

    with open("voice.wav", "wb") as f:
        f.write(r.content)

    await update.message.reply_voice(
        voice=open("voice.wav", "rb")
    )

except Exception as e:
    await update.message.reply_text(
        f"Error:\n{e}"
    )
```

app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("voices", voices))
app.add_handler(CommandHandler("voice", change_voice))

app.add_handler(
MessageHandler(
filters.TEXT & ~filters.COMMAND,
tts
)
)

app.run_polling()
