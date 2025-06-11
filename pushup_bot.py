import os
import sqlite3
from datetime import datetime, time
import pytz
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
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
    GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
    GOALS = {
        'pushups': int(os.getenv("PUSHUPS_GOAL", 100)),
        'plank': int(os.getenv("PLANK_GOAL", 120))
    }


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
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                exercise_type TEXT,
                date TEXT,
                value INTEGER,
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


def add_workout(user_id: int, exercise_type: str, value: int):
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO workouts (user_id, exercise_type, date, value) VALUES (?, ?, ?, ?)",
            (user_id, exercise_type, today, value),
        )
        conn.commit()


def get_today_workouts(user_id: int, exercise_type: str) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SUM(value) FROM workouts WHERE user_id = ? AND exercise_type = ? AND date = ?",
            (user_id, exercise_type, today),
        )
        result = cursor.fetchone()[0] or 0
    return result


# --- Обработчики команд ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    exercises_list = "\n".join([f"- {ex} (цель: {Config.GOALS[ex]})" for ex in Config.GOALS])
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n"
        f"Я бот для учёта тренировок. Доступные упражнения:\n"
        f"{exercises_list}\n\n"
        f"Используй /add <упражнение> <значение>",
        parse_mode="Markdown"
    )


async def add_workout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Определяем тип сообщения (новое или измененное)
    is_edit = update.edited_message is not None
    message = update.edited_message if is_edit else update.message
    if not message:
        logger.warning("Не удалось получить сообщение")
        return

    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)

    if len(context.args) < 2:
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="Используйте: /add <упражнение> <значение>\nПримеры:\n/add pushups 25\n/add plank 1:30",
            reply_to_message_id=message.message_id
        )
        return

    exercise_type = context.args[0].lower()
    value_str = context.args[1]

    try:
        if exercise_type == 'pushups':
            value = int(value_str)
        elif exercise_type == 'plank':
            if ':' in value_str:
                minutes, seconds = map(int, value_str.split(':'))
                value = minutes * 60 + seconds
            else:
                value = int(value_str)
        else:
            await context.bot.send_message(
                chat_id=message.chat.id,
                text="Неизвестное упражнение",
                reply_to_message_id=message.message_id
            )
            return
    except ValueError:
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="Некорректное значение",
            reply_to_message_id=message.message_id
        )
        return

    # Для измененных сообщений сначала находим и удаляем предыдущую запись
    if is_edit:
        with sqlite3.connect(Config.DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                           DELETE
                           FROM workouts
                           WHERE user_id = ?
                             AND exercise_type = ?
                             AND date = ?
                             AND id = (
                               SELECT id FROM workouts
                               WHERE user_id = ?
                             AND exercise_type = ?
                             AND date = ?
                               ORDER BY id DESC LIMIT 1
                               )
                           """, (user.id, exercise_type, datetime.now().strftime("%Y-%m-%d"),
                                 user.id, exercise_type, datetime.now().strftime("%Y-%m-%d")))
            conn.commit()

    # Добавляем новую запись (или обновленную)
    add_workout(user.id, exercise_type, value)
    today_total = get_today_workouts(user.id, exercise_type)
    goal = Config.GOALS[exercise_type]

    if exercise_type == 'pushups':
        message_text = format_pushups_response(user.username, value, today_total, goal)
    else:
        message_text = format_plank_response(user.username, value, today_total, goal)

    # Добавляем пометку об изменении если нужно
    if is_edit:
        message_text = "✏️ " + message_text

    await context.bot.send_message(
        chat_id=message.chat.id,
        text=message_text,
        parse_mode="Markdown",
        reply_to_message_id=message.message_id
    )

    # Уведомление в группу только для новых сообщений
    if not is_edit and str(message.chat.id) == str(Config.GROUP_CHAT_ID):
        await context.bot.send_message(
            chat_id=Config.GROUP_CHAT_ID,
            text=f"@{user.username} добавил {value_str} к упражнению {exercise_type}!",
            parse_mode="Markdown"
        )


def format_pushups_response(username: str, value: int, total: int, goal: int) -> str:
    if total >= goal:
        return (
            f"🏆 @{username}, ты выполнил норму отжиманий!\n"
            f"Всего сегодня: {total} из {goal}\n"
            f"Последние: {value}"
        )
    return (
        f"💪 @{username}, хорошая работа!\n"
        f"Добавлено: {value} отжиманий\n"
        f"Прогресс: {total}/{goal}\n"
        f"Осталось: {goal - total}"
    )


def format_plank_response(username: str, value: int, total: int, goal: int) -> str:
    def sec_to_str(seconds):
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins}:{secs:02d}"

    if total >= goal:
        return (
            f"🏆 @{username}, ты выполнил норму планки!\n"
            f"Общее время сегодня: {sec_to_str(total)}\n"
            f"Последний подход: {sec_to_str(value)}"
        )
    return (
        f"💪 @{username}, хорошая работа!\n"
        f"Добавлено: {sec_to_str(value)} планки\n"
        f"Прогресс: {sec_to_str(total)}/{sec_to_str(goal)}\n"
        f"Осталось: {sec_to_str(goal - total)}"
    )


# --- Напоминания ---
async def remind_workouts(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TIMEZONE)
    today = now.strftime("%Y-%m-%d")
    if 9 <= now.hour < 21:  # Только с 9:00 до 21:00
        try:
            with sqlite3.connect(Config.DB_NAME) as conn:
                cursor = conn.cursor()

                # Для каждого типа упражнения формируем список отстающих
                reminders = []

                for exercise, goal in Config.GOALS.items():
                    cursor.execute("""
                                   SELECT u.user_id,
                                          u.username,
                                          COALESCE(SUM(w.value), 0) AS total
                                   FROM users u
                                            LEFT JOIN workouts w ON
                                       w.user_id = u.user_id AND
                                       w.exercise_type = ? AND
                                       w.date = ?
                                   GROUP BY u.user_id
                                   HAVING total < ?
                                   ORDER BY total DESC
                                   """, (exercise, today, goal))

                    underachievers = []
                    for row in cursor.fetchall():
                        user_id, username, total = row
                        if exercise == 'plank':
                            total_str = f"{total // 60}:{total % 60:02d}"
                            goal_str = f"{goal // 60}:{goal % 60:02d}"
                        else:
                            total_str = str(total)
                            goal_str = str(goal)

                        underachievers.append(f"@{username} - {total_str}/{goal_str}")

                    if underachievers:
                        reminders.append(
                            f"*{exercise.capitalize()}:*\n" +
                            "\n".join(underachievers)
                        )

                if reminders:
                    message = "⏰ *Напоминание!*\n" + \
                              "\n\n".join(reminders)

                    await context.bot.send_message(
                        chat_id=Config.GROUP_CHAT_ID,
                        text=message,
                        parse_mode="Markdown"
                    )

        except Exception as e:
            logger.error(f"Ошибка в remind_workouts: {e}")


# --- Ежедневный отчет ---
async def generate_report() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(Config.DB_NAME) as conn:
        cursor = conn.cursor()

        report_sections = []

        for exercise, goal in Config.GOALS.items():
            cursor.execute("""
                           SELECT u.user_id,
                                  u.first_name,
                                  COALESCE(SUM(w.value), 0) as total
                           FROM users u
                           LEFT JOIN workouts w ON
                               u.user_id = w.user_id AND
                               w.exercise_type = ? AND
                               w.date = ?
                           GROUP BY u.user_id
                                  """, (exercise, today))
            results = cursor.fetchall()
            achievers = []
            underachievers = []

            for user_id, first_name, total in results:
                if exercise == 'plank':
                    total_str = f"{total // 60}:{total % 60:02d}"
                    goal_str = f"{goal // 60}:{goal % 60:02d}"
                else:
                    total_str = str(total)
                    goal_str = str(goal)

                if total >= goal:
                    achievers.append(f"{first_name} - {total_str} ✅")
                else:
                    underachievers.append(f"{first_name} - {total_str} ❌ (осталось {goal_str})")

            section = f"*{exercise.capitalize()} ({goal_str}):*\n"
            if achievers:
                section += "Молодцы:\n" + "\n".join(achievers) + "\n"
            if underachievers:
                section += "Нужно стараться:\n" + "\n".join(underachievers)

            report_sections.append(section)

    report_message = "📊 *Итоги дня:*\n\n" + "\n\n".join(report_sections)
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
    application.add_handler(CommandHandler("add", add_workout_command))

    # Напоминания каждые 2 часа (9:00-21:00)
    application.job_queue.run_repeating(
        callback=remind_workouts,
        interval=7200,
        first=10,
    )

    # Ежедневный отчет в 22:00
    application.job_queue.run_daily(
        callback=send_daily_report,
        time=time(hour=22, minute=0, tzinfo=TIMEZONE),
        days=tuple(range(7)),
    )

    application.run_polling()


if __name__ == "__main__":
    main()
