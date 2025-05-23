import os
import sqlite3
from datetime import datetime, time
import pytz
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
import logging
from telegram.error import Forbidden


# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# Загружаем конфиг из .env
load_dotenv()


class Config:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    DB_NAME = os.getenv("DB_NAME", "pushups.db")
    ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
    GOAL = int(os.getenv("GOAL", 100))
    GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))


if not Config.TOKEN:
    raise ValueError("Токен бота не найден в .env!")

# Константы
TIMEZONE = pytz.timezone('Europe/Moscow')  # Жестко задаем московское время


# --- База данных ---
def init_db():
    with sqlite3.connect(Config.DB_NAME) as conn:
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


def add_user(user_id: int, username: str, first_name: str, last_name: str):
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, last_name),
        )
        conn.commit()


def add_pushups(user_id: int, count: int):
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO pushups (user_id, date, count) VALUES (?, ?, ?)",
            (user_id, today, count),
        )
        conn.commit()


def get_today_pushups(user_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SUM(count) FROM pushups WHERE user_id = ? AND date = ?",
            (user_id, today),
        )
        result = cursor.fetchone()[0] or 0
    return result


# --- Обработчики команд ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n"
        f"Я бот для учёта отжиманий. Цель — {Config.GOAL} в день.\n"
        f"Просто напиши число, например: «25» или «сделал 30».\n\n"
        f"ID этого чата: `{update.message.chat.id}`",
        parse_mode="Markdown"
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


# --- Напоминания ---
async def remind_pushups(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TIMEZONE)
    if 9 <= now.hour < 21:  # Только с 9:00 до 21:00
        try:
            with sqlite3.connect(Config.DB_NAME) as conn:
                cursor = conn.cursor()
                # Получаем только пользователей, которые начали диалог с ботом
                cursor.execute("""
                    SELECT 
                        u.user_id,
                        u.username
                    FROM users u
                    JOIN pushups p ON p.user_id = u.user_id
                """)
                users = cursor.fetchall()

                for user_id, username in users:
                    today_pushups = get_today_pushups(user_id)
                    if today_pushups < Config.GOAL:
                        try:
                            await context.bot.send_message(
                                chat_id=Config.GROUP_CHAT_ID,
                                text=f"⏰ Напоминание! Сегодня {username} сделал {today_pushups}/{Config.GOAL}. Давай, ещё немного!",
                            )
                        except Forbidden:
                            logger.warning(f"Не удалось отправить сообщение пользователю {user_id} (чат запрещен)")
                        except Exception as e:
                            logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка в remind_pushups: {e}")

# --- Ежедневный отчет ---
async def generate_report() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT 
                           u.user_id, 
                           u.first_name, 
                           COALESCE(SUM(p.count), 0) as total
                       FROM users u
                       LEFT JOIN pushups p ON u.user_id = p.user_id AND p.date = ?
                       GROUP BY 
                           u.user_id, 
                           u.first_name
                       """, (today,))
        results = cursor.fetchall()

    achievers = []
    underachievers = []

    for user_id, first_name, total in results:
        if total >= Config.GOAL:
            achievers.append(f"{first_name} - {total} ✅")
        else:
            underachievers.append(f"{first_name} - {total} ❌ (осталось {Config.GOAL - total})")

    report_message = "📊 *Текущий прогресс:*\n\n"

    if achievers:
        report_message += "*Молодцы!*\n" + "\n".join(achievers) + "\n\n"

    if underachievers:
        report_message += "*Нужно стараться больше:*\n" + "\n".join(underachievers)

    return report_message


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /report"""
    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    report = await generate_report()
    await update.message.reply_text(report, parse_mode="Markdown")


async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    try:
        report = await generate_report()
        if Config.GROUP_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=Config.GROUP_CHAT_ID,
                    text=report.replace("Текущий", "Итоговый"),
                    parse_mode="Markdown"
                )
            except Forbidden:
                logger.error("Нет доступа к групповому чату")
                if Config.ADMIN_USER_ID:
                    await context.bot.send_message(
                        chat_id=Config.GROUP_CHAT_ID,
                        text="⚠️ Нет доступа к групповому чату для отправки отчета",
                        parse_mode="Markdown"
                    )
        elif Config.ADMIN_USER_ID:
            await context.bot.send_message(
                chat_id=Config.GROUP_CHAT_ID,
                text="⚠️ GROUP_CHAT_ID не указан\n\n" + report.replace("Текущий", "Итоговый"),
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Ошибка в send_daily_report: {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логируем ошибки"""
    logger.error(msg="Исключение при обработке обновления:", exc_info=context.error)

    if isinstance(context.error, Forbidden):
        logger.warning(f"Боту запрещено писать пользователю")
    elif isinstance(context.error, Exception):
        logger.error(f"Необработанное исключение: {context.error}")


# --- Запуск бота ---
def main():
    init_db()

    # Создаем Application с JobQueue
    application = Application.builder().token(Config.TOKEN).job_queue(JobQueue()).build()

    # Обработчики
    application.add_error_handler(error_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pushups))

    # Напоминания каждые 2 часа (9:00-21:00)
    application.job_queue.run_repeating(
        callback=remind_pushups,
        interval=7200,
        first=10,
    )

    # Ежедневный отчет в 22:00
    application.job_queue.run_daily(
        callback=send_daily_report,
        time=time(hour=22, minute=0),
        days=tuple(range(7)),  # Все дни недели
    )

    application.run_polling()


if __name__ == "__main__":
    main()
