# Workout Bot 🤖💪

Telegram-бот для учета тренировок с поддержкой различных упражнений (отжимания, планка и другие).

## Установка

### Требования
- Python 3.10+
- Telegram Bot
- Установленный `uv`
- Сервер с Linux (для systemd)

### 1. Установка через `uv`

```bash
# Клонирование репозитория
git clone https://github.com/yourusername/workout-bot.git
cd workout-bot

# Создание виртуального окружения
python -m venv venv
source venv/bin/activate

# Установка зависимостей через uv
uv pip install -r requirements.txt
```

### 2. Настройка конфигурации

Создайте файл .env в корне проекта:

```ini
TELEGRAM_BOT_TOKEN=ваш_токен_бота
DB_NAME=workouts.db
ADMIN_USER_ID=ваш_telegram_id
GROUP_CHAT_ID=-id_группового_чата

# Цели по умолчанию
PUSHUPS_GOAL=100
PLANK_GOAL=120  # в секундах (2 минуты)
```

### 3. Systemd сервис
Создайте файл /etc/systemd/system/workout-bot.service:
```ini
[Unit]
Description=Workout Tracker Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/path/to/workout-bot
EnvironmentFile=/path/to/workout-bot/.env
ExecStart=/path/to/workout-bot/venv/bin/python -u pushup_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Управление сервисом:
```bash
# Перезагрузка конфигурации
sudo systemctl daemon-reload

# Запуск и автозагрузка
sudo systemctl enable workout-bot
sudo systemctl start workout-bot

# Просмотр статуса
sudo systemctl status workout-bot

# Логи
journalctl -u workout-bot -f
```

## 4. Развитие проекта
### Порядок внесения изменений

#### Добавление нового упражнения:
- Добавить цель в .env: NEW_EXERCISE_GOAL=значение
- Обновить Config.GOALS в коде
- При необходимости создать функцию форматирования format_new_exercise_response()

#### Изменение логики:
- Работать в отдельной ветке
- Тестировать изменения локально
- Обновлять версию в pyproject.toml

```
workout-bot/
├── .env.example          # Шаблон конфига
├── pushup_bot.py         # Основной код бота
├── requirements.txt      # Зависимости
├── README.md            # Этот файл
└── workouts.db           # База данных (создается автоматически)
```

## 5. Взаимодействие с ботом
### Основные команды:
- /start - Начало работы
- /add pushups 25 - Добавить 25 отжиманий
- /add plank 1:30 - Добавить 1 минуту 30 секунд планки
- /report - Текущий прогресс


## 6. Развертывание обновлений

Остановить сервис:
```bash
sudo systemctl stop workout-bot
```

Обновить код:
```bash
git pull origin main
```

Перезапустить:
```bash
sudo systemctl start workout-bot
```