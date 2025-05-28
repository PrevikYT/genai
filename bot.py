import os
import logging
import tempfile
import base64
from io import BytesIO
from PIL import Image
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

from google import genai
from google.genai import types

# ─── Ваши ключи ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = "7902518696:AAHq6WC78afvINs72Z64-tDryHVwVaLkZV0"
GENAI_API_KEY   = "AIzaSyCNtiVyVWvIDRmtplc1i7wv_Gel3OoSN68"
FREEPIK_API_KEY = "FPSX022da7144e020749736d3cfd0cec253b"
# ───────────────────────────────────────────────────────────────────────────────

# Настройка Google GenAI
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
genai_client = genai.Client(api_key=GENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Состояния ───────────────────────────────────────────────────────────────
auth_styles         = {}   # chat_id → style_name
generation_provider = {}   # chat_id → provider or None
# ───────────────────────────────────────────────────────────────────────────────

# ─── Хэндлеры ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Команды:\n"
        "/sarcastic — стиль «саркастичный AI»\n"
        "/philosopher — стиль «доброжелательный философ»\n"
        "/prompt <стиль> — свой стиль\n"
        "/generate — режим генерации картинок\n"
        "/exit_generation — выйти из режима генерации\n\n"
        "• В обычном режиме я отвечаю в вашем стиле на текст (темп=1.0).\n"
        "• В режиме генерации каждый текст — запрос на картинку.\n"
        "• Отправляйте фото — я опишу их в вашем стиле (темп=1.0)."
    )

async def set_sarcastic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth_styles[update.effective_chat.id] = "саркастичный AI"
    await update.message.reply_text("Стиль установлен: саркастичный AI.")

async def set_philosopher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auth_styles[update.effective_chat.id] = "доброжелательный философ"
    await update.message.reply_text("Стиль установлен: доброжелательный философ.")

async def set_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if not context.args:
        return await update.message.reply_text("Используй: /prompt <твой стиль>")
    auth_styles[cid] = " ".join(context.args)
    await update.message.reply_text(f"Стиль установлен: {auth_styles[cid]}.")

async def generate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Freepik Classic Fast", callback_data="prov:classic")],
        [InlineKeyboardButton("Google Gemini",         callback_data="prov:google")],
    ]
    await update.message.reply_text(
        "Режим генерации: выберите провайдера для создания изображения:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def exit_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    generation_provider.pop(update.effective_chat.id, None)
    await update.message.reply_text("Вы вышли из режима генерации изображений.")

async def on_provider_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 1)
    if len(parts) != 2:
        return await query.edit_message_text("Неправильный выбор провайдера.")
    provider = parts[1]
    generation_provider[query.message.chat_id] = provider
    await query.edit_message_text(
        f"Провайдер «{provider}» выбран.\n"
        "Отправьте описание для генерации картинки\n"
        "или /exit_generation для выхода."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid, text = update.effective_chat.id, update.message.text.strip()
    # Режим генерации
    if cid in generation_provider:
        await generate_image(update, generation_provider[cid], text)
        return

    # Обычный ролевой чат
    style = auth_styles.get(cid)
    if not style:
        return await update.message.reply_text(
            "Сначала выберите стиль: /sarcastic, /philosopher или /prompt <ваш стиль>."
        )
    user_text = text[len(f"{style}:"):].strip() if text.lower().startswith(style.lower()+":") else text
    prompt = f"Отвечай как {style}.\nUser: {user_text}\nAI:"
    try:
        resp = genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[prompt],
            config=types.GenerateContentConfig(temperature=1.0)
        )
        await update.message.reply_text(resp.text.strip())
    except Exception as e:
        logger.error("GenAI text error: %s", e)
        await update.message.reply_text("Ошибка генерации текста.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    style = auth_styles.get(cid)
    if not style:
        return await update.message.reply_text(
            "Сначала выберите стиль: /sarcastic, /philosopher или /prompt <ваш стиль>."
        )

    photo_file = await update.message.photo[-1].get_file()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        await photo_file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    with open(tmp_path, "rb") as f:
        data = f.read()
    image_part = types.Part.from_bytes(data=data, mime_type="image/jpeg")
    text_part  = types.Part(text=f"Опиши, что на этом фото, в стиле: {style}.")

    try:
        resp = genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[image_part, text_part],
            config=types.GenerateContentConfig(temperature=1.0)
        )
        await update.message.reply_text(resp.text.strip())
    except Exception as e:
        logger.error("GenAI image-desc error: %s", e)
        await update.message.reply_text("Ошибка описания изображения.")

async def generate_image(update: Update, provider: str, prompt: str):
    msg = update.message
    if provider == "google":
        await msg.reply_text("Генерирую через Google Gemini…")
        try:
            resp = genai_client.models.generate_content(
                model="gemini-2.0-flash-preview-image-generation",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT","IMAGE"],
                    temperature=1.0
                )
            )
            for part in resp.candidates[0].content.parts:
                if part.inline_data:
                    bio = BytesIO(part.inline_data.data)
                    bio.name = "gemini.png"
                    await msg.reply_photo(photo=bio)
                elif part.text:
                    await msg.reply_text(part.text)
        except Exception as e:
            logger.error("Google ImageGen error: %s", e)
            await msg.reply_text("Ошибка Google Gemini Image-Gen.")
    elif provider == "classic":
        await msg.reply_text("Генерирую через Freepik Classic Fast…")
        try:
            r = requests.post(
                "https://api.freepik.com/v1/ai/text-to-image",
                json={"prompt": prompt, "num_images": 1},
                headers={"x-freepik-api-key": FREEPIK_API_KEY},
                timeout=60
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            b64 = data[0].get("base64") if data and isinstance(data[0], dict) else None
            if b64:
                img = Image.open(BytesIO(base64.b64decode(b64)))
                bio = BytesIO(); img.save(bio, "PNG"); bio.name="classic.png"; bio.seek(0)
                await msg.reply_photo(photo=bio)
            else:
                logger.error("Classic unexpected JSON: %s", r.json())
                await msg.reply_text("Freepik Classic вернул неверные данные.")
        except Exception as e:
            logger.error("Freepik Classic error: %s", e)
            await msg.reply_text("Ошибка Freepik Classic Fast.")
    else:
        await msg.reply_text("Неизвестный провайдер.")

# ─── Регистрация и запуск ─────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",           start))
    app.add_handler(CommandHandler("sarcastic",       set_sarcastic))
    app.add_handler(CommandHandler("philosopher",     set_philosopher))
    app.add_handler(CommandHandler("prompt",          set_custom))
    app.add_handler(CommandHandler("generate",        generate_cmd))
    app.add_handler(CommandHandler("exit_generation", exit_generation))
    app.add_handler(CallbackQueryHandler(on_provider_selected, pattern="^prov:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("🤖 Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
