import asyncio
import logging
import os
import threading
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
import ccxt

# Загружаем переменные окружения из файла .env
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MEXC_API_KEY = os.getenv('MEXC_API_KEY')
MEXC_SECRET_KEY = os.getenv('MEXC_SECRET_KEY')

logging.basicConfig(level=logging.INFO)

# --- Веб-сервер для Render (будет работать в отдельном потоке) ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Бот работает!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- Создаём бота и диспетчер ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_mexc_exchange():
    """Возвращает объект биржи MEXC с API ключами"""
    return ccxt.mexc({
        'apiKey': MEXC_API_KEY,
        'secret': MEXC_SECRET_KEY,
        'enableRateLimit': True,
    })

# ---------- Команда /start ----------
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "🤖 Привет! Я торговый помощник для MEXC.\n\n"
        "📊 /price BTCUSDT - получить цену с кнопками\n"
        "💰 /balance - проверить баланс USDT"
    )

# ---------- Команда /price с инлайн-кнопками ----------
@dp.message(Command("price"))
async def price_command(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Укажите валютную пару. Пример: /price BTCUSDT")
        return

    symbol = args[1].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'

    try:
        exchange = get_mexc_exchange()
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']

        # Создаём инлайн-кнопки
        refresh_btn = InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"refresh_{symbol}"
        )
        chart_btn = InlineKeyboardButton(
            text="📈 График",
            callback_data=f"chart_{symbol}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[refresh_btn, chart_btn]])

        await message.answer(
            f"💰 Цена {symbol}: {price:.4f} USDT",
            reply_markup=keyboard
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при получении цены: {e}")

# ---------- Обработчик нажатия на кнопку "Обновить" ----------
@dp.callback_query(F.data.startswith("refresh_"))
async def refresh_price(callback: types.CallbackQuery):
    symbol = callback.data.split("_", 1)[1]  # Получаем символ из callback_data
    try:
        exchange = get_mexc_exchange()
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        # Редактируем текущее сообщение, обновляя цену
        await callback.message.edit_text(
            f"💰 Цена {symbol}: {price:.4f} USDT"
        )
        await callback.answer("Цена обновлена ✅")
    except Exception as e:
        await callback.answer("Ошибка при обновлении ❌", show_alert=True)

# ---------- Обработчик нажатия на кнопку "График" ----------
@dp.callback_query(F.data.startswith("chart_"))
async def show_chart(callback: types.CallbackQuery):
    symbol = callback.data.split("_", 1)[1]
    chart_url = f"https://www.tradingview.com/chart/?symbol=MEXC:{symbol}"
    # Кнопка "Назад" – она использует тот же refresh, чтобы вернуться к цене
    back_btn = InlineKeyboardButton(
        text="◀️ Назад к цене",
        callback_data=f"refresh_{symbol}"
    )
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_btn]])
    await callback.message.edit_text(
        f"📈 График {symbol}:\n{chart_url}",
        reply_markup=back_keyboard
    )
    await callback.answer()

# ---------- Команда /balance ----------
@dp.message(Command("balance"))
async def balance_command(message: types.Message):
    try:
        exchange = get_mexc_exchange()
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('free', 0)
        await message.answer(f"💰 Ваш баланс USDT на MEXC: {usdt_balance:.2f}")
    except Exception as e:
        await message.answer(f"❌ Не удалось получить баланс: {e}")

# ---------- Запуск всего приложения ----------
if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Запускаем бота в основном потоке
    try:
        asyncio.run(dp.start_polling(bot))
    except KeyboardInterrupt:
        print("Бот остановлен.")