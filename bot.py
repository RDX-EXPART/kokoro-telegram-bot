import requests

from telegram import Update
from telegram.ext import (
Application,
CommandHandler,
MessageHandler,
ContextTypes,
filters
)

from config import *

CURRENT_VOICE = VOICE

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
msg = f"""
🎙 Welcome to RDX AI Voice Bot

✨ Convert any text into realistic AI speech.

🎤 Current Voice:
{CURRENT_VOICE}

📌 Commands:

/voices - Show all voices
/voice voice_name - Change voice
/help - Show help menu

💬 Just send any text and receive AI generated voice.
"""

```
await update.message.reply_text(msg)
```

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
msg = """
📖 RDX AI Voice Bot Help

🔹 Send any text to generate voice.

🔹 Commands:

/start - Start Bot
/help - Help Menu
/voices - Show Voices
/voice af_bella - Change Voice

🎤 Recommended Voices

Female:
• af_bella
• af_heart
• af_nova
• bf_emma

Male:
• am_adam
• am_michael
• bm_george

Hindi Style:
• hf_alpha
• hf_beta
• hm_omega
• hm_psi
"""

```
await update.message.reply_text(msg)
```

async def voices(update: Update, context: ContextTypes.DEFAULT_TYPE):

```
try:
    r = requests.get(
        f"{KOKORO_API}/v1/audio/voices",
        timeout=30
    )

    data = r.json()

    voice_list = "\n".join(
        [v["id"] for v in data["voices"]]
    )

    await update.message.reply_text(
        f"🎤 Available Voices\n\n{voice_list}"
    )

except Exception as e:
    await update.message.reply_text(
        f"❌ Error:\n{e}"
    )
```

async def change_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):

```
global CURRENT_VOICE

if not context.args:
    await update.message.reply_text(
        "Usage:\n/voice af_bella"
    )
    return

CURRENT_VOICE = context.args[0]

await update.message.reply_text(
    f"✅ Voice Changed Successfully\n\n🎤 {CURRENT_VOICE}"
)
```

async def tts(update: Update, context: ContextTypes.DEFAULT_TYPE):

```
text = update.message.text

try:

    payload = {
        "model": "kokoro",
        "input": text,
        "voice": CURRENT_VOICE
    }

    r = requests.post(
        f"{KOKORO_API}/v1/audio/speech",
        json=payload,
        timeout=120
    )

    with open("voice.wav", "wb") as f:
        f.write(r.content)

    await update.message.reply_voice(
        voice=open("voice.wav", "rb"),
        caption=f"🎙 Voice: {CURRENT_VOICE}"
    )

except Exception as e:

    await update.message.reply_text(
        f"❌ Error:\n{e}"
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

print("✅ RDX AI Voice Bot Started")

app.run_polling()
