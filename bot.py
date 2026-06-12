import os
import threading
import logging
import asyncio
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv
import ccxt

# --- Настройка логирования (для отслеживания работы) ---
logging.basicConfig(level=logging.INFO)

# --- Загрузка переменных окружения ---
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
MEXC_API_KEY = os.getenv('MEXC_API_KEY')
MEXC_SECRET_KEY = os.getenv('MEXC_SECRET_KEY')

# --- Настройка Telegram бота ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Настройка подключения к MEXC ---
def get_mexc_exchange():
    return ccxt.mexc({
        'apiKey': MEXC_API_KEY,
        'secret': MEXC_SECRET_KEY,
        'enableRateLimit': True, # Важно для соблюдения лимитов запросов к бирже
    })

# --- Обработчики команд телеграм-бота ---
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "🤖 Привет! Я торговый помощник MEXC.\n"
        "Вот что я умею:\n"
        "/price BTCUSDT - Узнать цену BTC\n"
        "/balance - Узнать баланс USDT"
    )

@dp.message(Command("price"))
async def price_command(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Укажите валютную пару. Например: /price BTCUSDT")
        return

    symbol = args[1].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'

    try:
        exchange = get_mexc_exchange()
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        await message.answer(f"💰 Цена {symbol}: {price:.4f} USDT")
    except Exception as e:
        logging.error(f"Ошибка получения цены: {e}")
        await message.answer(f"❌ Не удалось получить цену для {symbol}.")

@dp.message(Command("balance"))
async def balance_command(message: types.Message):
    try:
        exchange = get_mexc_exchange()
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('free', 0)
        await message.answer(f"💰 Ваш баланс USDT на MEXC: {usdt_balance:.2f}")
    except Exception as e:
        logging.error(f"Ошибка получения баланса: {e}")
        await message.answer("❌ Не удалось получить баланс. Проверьте API-ключи.")

# --- Функция для запуска бота ---
async def run_bot():
    await dp.start_polling(bot)

# --- Flask веб-сервер (нужен для Render, чтобы сервис не уснул) ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Бот работает!"

# --- Главная функция, которая запускает всё вместе ---
if __name__ == '__main__':
    # Запускаем Telegram-бота в отдельном потоке
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    threading.Thread(target=lambda: loop.run_until_complete(run_bot())).start()

    # Запускаем Flask-сервер в основном потоке
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)