import os
# Задаём регион для Google GenAI
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

# Патч APScheduler (надо до импорта telegram.ext)
import pytz, apscheduler.util
apscheduler.util.astimezone = lambda tz=None: pytz.utc

import logging
import tempfile
from io import BytesIO
from PIL import Image

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from google import genai
from google.genai import types

# ─── Ваши токены (не публикуйте!) ─────────────────────────────────────────────
TELEGRAM_TOKEN = "5203334054:AAGJGZePmEK0FFGJxSVNoAoHNUUdFVd1lrk"
GENAI_API_KEY  = "AIzaSyCNtiVyVWvIDRmtplc1i7wv_Gel3OoSN68"
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализируем клиент GenAI
genai_client = genai.Client(api_key=GENAI_API_KEY)

# Храним стиль и историю по chat_id
auth_styles    = {}  # chat_id -> style_name
user_histories = {}  # chat_id -> list of (role, text)

# ─── Команды выбора стиля ─────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Выбери стиль ответа:\n"
        "/sarcastic — саркастичный AI\n"
        "/philosopher — доброжелательный философ\n"
        "/prompt <твой стиль> — свой вариант\n\n"
        "• Отправь любое фото — я опишу его в твоем стиле.\n"
        "• Напиши «создай изображение <тема>» — сгенерирую картинку."
    )

async def set_sarcastic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    auth_styles[cid] = "саркастичный AI"
    user_histories[cid] = []
    await update.message.reply_text("Стиль установлен: саркастичный AI. История очищена.")

async def set_philosopher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    auth_styles[cid] = "доброжелательный философ"
    user_histories[cid] = []
    await update.message.reply_text("Стиль установлен: доброжелательный философ. История очищена.")

async def set_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if not context.args:
        return await update.message.reply_text("Используй: /prompt <твой стиль>")
    style = " ".join(context.args)
    auth_styles[cid] = style
    user_histories[cid] = []
    await update.message.reply_text(f"Стиль установлен: {style}. История очищена.")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    user_histories.pop(cid, None)
    await update.message.reply_text("История диалога очищена.")

# ─── Обработка текстовых сообщений и генерации изображений ─────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid  = update.effective_chat.id
    text = update.message.text.strip()

    if cid not in auth_styles:
        return await update.message.reply_text(
            "Сначала выбери стиль: /sarcastic, /philosopher или /prompt <твой стиль>."
        )

    # Генерация нового изображения
    if text.lower().startswith("создай изображение"):
        topic = text[len("создай изображение"):].strip()
        if not topic:
            return await update.message.reply_text("Укажи тему: «создай изображение <тема>»")
        await update.message.reply_text("Генерирую изображение…")

        try:
            # Интеграция из официального примера Google
            response = genai_client.models.generate_content(
                model="gemini-2.0-flash-preview-image-generation",
                contents=topic,
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])
            )
            # Обрабатываем части ответа
            for part in response.candidates[0].content.parts:
                if part.text is not None:
                    await update.message.reply_text(part.text)
                elif part.inline_data is not None:
                    img = Image.open(BytesIO(part.inline_data.data))
                    bio = BytesIO()
                    img.save(bio, format="PNG")
                    bio.name = "generated.png"
                    bio.seek(0)
                    await update.message.reply_photo(photo=bio)
            return
        except Exception as e:
            logger.error("GenAI image-gen error: %s", e)
            return await update.message.reply_text(
                "Не удалось сгенерировать изображение."
            )

    # Обычный ролевой чат
    if cid not in user_histories:
        user_histories[cid] = []

    # Убираем префикс «роль:» если пользователь его добавил
    style = auth_styles[cid]
    prefix = f"{style}:".lower()
    if text.lower().startswith(prefix):
        user_text = text[len(prefix):].strip()
    else:
        user_text = text

    user_histories[cid].append(("User", user_text))
    system_inst = f"Отвечай как {style}."
    history     = "\n".join(f"{r}: {t}" for r, t in user_histories[cid])
    prompt      = f"{system_inst}\n{history}\nAI:"

    try:
        resp = genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[prompt],
            config=types.GenerateContentConfig(temperature=0.7)
        )
        reply = resp.text.strip()
        user_histories[cid].append(("AI", reply))
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error("GenAI text error: %s", e)
        await update.message.reply_text("Ошибка при генерации ответа. Попробуйте позже.")

# ─── Обработка фотографий — описание в стиле роли ───────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid not in auth_styles:
        return await update.message.reply_text(
            "Сначала выбери стиль: /sarcastic, /philosopher или /prompt <твой стиль>."
        )

    style = auth_styles[cid]
    photo = update.message.photo[-1]

    # Скачиваем фото во временный файл
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        path = tmp.name
    file = await photo.get_file()
    await file.download_to_drive(path)

    await update.message.reply_text("Анализирую изображение…")

    try:
        with open(path, "rb") as img_file:
            data = img_file.read()
        image_part = types.Part.from_bytes(data=data, mime_type="image/jpeg")
        text_part  = types.Part(text=f"Опиши, что на этом фото, в стиле: {style}.")

        resp = genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=[image_part, text_part],
            config=types.GenerateContentConfig(temperature=0.5)
        )
        desc = resp.text.strip()
        user_histories[cid].append(("AI(Image)", desc))
        await update.message.reply_text(desc)
    except Exception as e:
        logger.error("GenAI image-desc error: %s", e)
        await update.message.reply_text("Не удалось проанализировать изображение.")

# ─── Запуск бота ─────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("sarcastic",   set_sarcastic))
    app.add_handler(CommandHandler("philosopher", set_philosopher))
    app.add_handler(CommandHandler("prompt",      set_custom))
    app.add_handler(CommandHandler("clear",       clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO,          handle_photo))
    print("🤖 Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
