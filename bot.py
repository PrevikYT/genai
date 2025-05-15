import os
# Принудительно указываем регион для Google GenAI (может потребоваться, если API недоступен в вашем регионе)
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from google import genai
from google.genai import types

# Токены (замените на свои реальные в проде)
TELEGRAM_TOKEN = "5203334054:AAGJGZePmEK0FFGJxSVNoAoHNUUdFVd1lrk"
GENAI_API_KEY = "AIzaSyCNtiVyVWvIDRmtplc1i7wv_Gel3OoSN68"

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализируем клиент Google GenAI
genai_client = genai.Client(api_key=GENAI_API_KEY)

# Хранилище стилей ответов (память по chat_id)
user_styles = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я AI-бот. Выберите стиль ответа:\n"
        "/sarcastic - саркастичный AI\n"
        "/philosopher - доброжелательный философ\n"
        "или задайте свой стиль через /prompt <ваш стиль>."
    )

async def set_sarcastic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_styles[update.effective_chat.id] = "саркастичный AI"
    await update.message.reply_text("Стиль установлен: саркастичный AI.")

async def set_philosopher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_styles[update.effective_chat.id] = "доброжелательный философ"
    await update.message.reply_text("Стиль установлен: доброжелательный философ.")

async def set_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.args:
        style = ' '.join(context.args)
        user_styles[chat_id] = style
        await update.message.reply_text(f"Стиль установлен: {style}")
    else:
        await update.message.reply_text("Используйте: /prompt <ваш стиль>.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_styles:
        await update.message.reply_text(
            "Сначала выберите стиль: /sarcastic, /philosopher или /prompt <ваш стиль>."
        )
        return

    user_input = update.message.text
    style = user_styles[chat_id]

    try:
        # Генерация ответа через Google GenAI
        response = genai_client.models.generate_content(
            model="gemini-2.0-flash-001",
            contents=user_input,
            config=types.GenerateContentConfig(
                system_instruction=f"Отвечай как {style}."
            )
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"GenAI error: {e}")
        # Если ошибка из-за региона или биллинга, информируем пользователя
        if 'User location is not supported' in str(e):
            await update.message.reply_text(
                "Извините, сервис недоступен в вашем регионе. "
                "Попробуйте указать регион US (us-central1) переменной окружения или включить биллинг."  
            )
        else:
            await update.message.reply_text("Ошибка при генерации ответа. Попробуйте позже.")

def main():
    # Создаем и запускаем приложение
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Регистрация хендлеров
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sarcastic", set_sarcastic))
    app.add_handler(CommandHandler("philosopher", set_philosopher))
    app.add_handler(CommandHandler("prompt", set_custom))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Запуск бота (поллинг)
    app.run_polling()

if __name__ == '__main__':
    main()
