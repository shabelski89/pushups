import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue,
)

# Загружаем конфиг из .env
load_dotenv()

class Config:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    DB_NAME = os.getenv("DB_NAME", "pushups.db")
    ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
    GOAL = int(os.getenv("GOAL"))

if not Config.TOKEN:
    raise ValueError("Токен бота не найден в .env!")

# --- База данных ---
def init_db():
    conn = sqlite3.connect(Config.DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pushups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            count INTEGER,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    conn.commit()
    conn.close()

def add_user(user_id: int, username: str, first_name: str, last_name: str):
    conn = sqlite3.connect(Config.DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, last_name),
    )
    conn.commit()
    conn.close()

def add_pushups(user_id: int, count: int):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(Config.DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO pushups (user_id, date, count) VALUES (?, ?, ?)",
        (user_id, today, count),
    )
    conn.commit()
    conn.close()

def get_today_pushups(user_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(Config.DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT SUM(count) FROM pushups WHERE user_id = ? AND date = ?",
        (user_id, today),
    )
    result = cursor.fetchone()[0] or 0
    conn.close()
    return result

# --- Обработчики команд ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(f"Этот чат имеет ID: `{update.message.chat.id}`", parse_mode="Markdown")
    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n"
        f"Я бот для учёта отжиманий. Цель — {Config.GOAL} в день.\n"
        f"Просто напиши число, например: «25» или «сделал 30»."
    )

async def handle_pushups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    try:
        count = int("".join(filter(str.isdigit, text)))
    except ValueError:
        await update.message.reply_text("Не понял. Напиши число, например: «25»")
        return

    today_pushups = get_today_pushups(user_id)
    total = today_pushups + count

    add_pushups(user_id, count)

    if total >= Config.GOAL:
        await update.message.reply_text(
            f"🔥 Отлично! Ты достиг {Config.GOAL} отжиманий за сегодня!\n"
            f"Последние {count} — и ты молодец!"
        )
    else:
        await update.message.reply_text(
            f"✅ Добавил {count} отжиманий. За сегодня: {total}/{Config.GOAL}.\n"
            f"Осталось {Config.GOAL - total}!"
        )

# --- Напоминания (асинхронные) ---
async def remind_pushups(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(Config.DB_NAME)
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    for (user_id,) in users:
        today_pushups = get_today_pushups(user_id)
        if today_pushups < Config.GOAL:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⏰ Напоминание! Сегодня ты сделал {today_pushups}/{Config.GOAL}. Давай, ещё немного!",
            )
    conn.close()

# --- Запуск бота ---
def main():
    init_db()
    app = Application.builder().token(Config.TOKEN).job_queue(JobQueue()).build()

    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pushups))

    # Напоминания (каждые 2 часа)
    HOUR_IN_SECONDS = 60 * 60 * 24
    REMIND_HOURS = HOUR_IN_SECONDS * 2
    job_queue = app.job_queue
    job_queue.run_repeating(remind_pushups, interval=REMIND_HOURS, first=10)

    app.run_polling()

if __name__ == "__main__":
    main()