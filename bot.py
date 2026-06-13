import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from dotenv import load_dotenv
import ccxt

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
MEXC_API_KEY = os.getenv('MEXC_API_KEY')
MEXC_SECRET_KEY = os.getenv('MEXC_SECRET_KEY')
PORT = int(os.environ.get('PORT', 8080))

# БЕРЁМ URL ИЗ ПЕРЕМЕННОЙ, КОТОРУЮ МЫ ДОБАВИЛИ НА РЕНДЕРЕ!
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = f'/webhook/{BOT_TOKEN}'
WEBHOOK_URL = f'{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}'

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_mexc_exchange():
    return ccxt.mexc({
        'apiKey': MEXC_API_KEY,
        'secret': MEXC_SECRET_KEY,
        'enableRateLimit': True,
    })

# ---------- Все ваши обработчики команд остаются без изменений ----------
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "🤖 Привет! Я торговый помощник для MEXC.\n\n"
        "📊 /price BTCUSDT - получить цену с кнопками\n"
        "💰 /balance - проверить баланс USDT"
    )

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

        refresh_btn = InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{symbol}")
        chart_btn = InlineKeyboardButton(text="📈 График", callback_data=f"chart_{symbol}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[refresh_btn, chart_btn]])

        await message.answer(f"💰 Цена {symbol}: {price:.4f} USDT", reply_markup=keyboard)
    except Exception as e:
        await message.answer(f"❌ Ошибка при получении цены: {e}")

@dp.callback_query(F.data.startswith("refresh_"))
async def refresh_price(callback: types.CallbackQuery):
    symbol = callback.data.split("_", 1)[1]
    try:
        exchange = get_mexc_exchange()
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        await callback.message.edit_text(f"💰 Цена {symbol}: {price:.4f} USDT")
        await callback.answer("Цена обновлена ✅")
    except Exception as e:
        await callback.answer("Ошибка при обновлении ❌", show_alert=True)

@dp.callback_query(F.data.startswith("chart_"))
async def show_chart(callback: types.CallbackQuery):
    symbol = callback.data.split("_", 1)[1]
    chart_url = f"https://www.tradingview.com/chart/?symbol=MEXC:{symbol}"
    back_btn = InlineKeyboardButton(text="◀️ Назад к цене", callback_data=f"refresh_{symbol}")
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_btn]])
    await callback.message.edit_text(f"📈 График {symbol}:\n{chart_url}", reply_markup=back_keyboard)
    await callback.answer()

@dp.message(Command("balance"))
async def balance_command(message: types.Message):
    try:
        exchange = get_mexc_exchange()
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('free', 0)
        await message.answer(f"💰 Ваш баланс USDT на MEXC: {usdt_balance:.2f}")
    except Exception as e:
        await message.answer(f"❌ Не удалось получить баланс: {e}")

# ---------- Настройка веб-сервера и webhook ----------
async def on_startup(app: web.Application):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook set to {WEBHOOK_URL}")

async def on_cleanup(app: web.Application):
    await bot.session.close()
    logging.info("Bot session closed")

async def handle_webhook(request: web.Request) -> web.Response:
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return web.Response()

def run():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.router.add_get('/', lambda request: web.Response(text="MOHABOT777 is running"))
    web.run_app(app, host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    run()