import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiohttp import web
from dotenv import load_dotenv
import ccxt

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
MEXC_API_KEY = os.getenv('MEXC_API_KEY')
MEXC_SECRET_KEY = os.getenv('MEXC_SECRET_KEY')
PORT = int(os.environ.get('PORT', 8080))
DB_PATH = 'bot_database.db'

# БЕРЁМ URL ИЗ ПЕРЕМЕННОЙ, КОТОРУЮ МЫ ДОБАВИЛИ НА РЕНДЕРЕ!
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = f'/webhook/{BOT_TOKEN}'
WEBHOOK_URL = f'{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}'

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- Database ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            mode TEXT DEFAULT 'FAST',
            trading_pair TEXT DEFAULT 'BTCUSDT',
            position TEXT DEFAULT 'LONG',
            deposit REAL DEFAULT 100,
            leverage INTEGER DEFAULT 5,
            orders_count INTEGER DEFAULT 5,
            price_overlap REAL DEFAULT 30,
            price_coeff REAL DEFAULT 1.5,
            volume_coeff REAL DEFAULT 1.2,
            exit_type TEXT DEFAULT 'limit',
            profit_percent REAL DEFAULT 0.5,
            bot_active INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            position TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            roi REAL,
            timestamp DATETIME,
            status TEXT DEFAULT 'closed'
        )
    ''')
    conn.commit()
    conn.close()

def get_user_settings(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'mode': row[1],
            'trading_pair': row[2],
            'position': row[3],
            'deposit': row[4],
            'leverage': row[5],
            'orders_count': row[6],
            'price_overlap': row[7],
            'price_coeff': row[8],
            'volume_coeff': row[9],
            'exit_type': row[10],
            'profit_percent': row[11],
            'bot_active': row[12]
        }
    return None

def create_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()

def update_user_setting(user_id, setting, value):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(f'UPDATE users SET {setting} = ? WHERE user_id = ?', (value, user_id))
    conn.commit()
    conn.close()

def add_trade(user_id, symbol, position, entry_price, exit_price, pnl, roi):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trades (user_id, symbol, position, entry_price, exit_price, pnl, roi, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, symbol, position, entry_price, exit_price, pnl, roi, datetime.now()))
    conn.commit()
    conn.close()

def get_user_trades(user_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT symbol, position, pnl, roi, timestamp FROM trades 
        WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
    ''', (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_user_stats(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*), SUM(pnl), AVG(roi) FROM trades WHERE user_id = ? AND status = 'closed'
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    total_trades = row[0] or 0
    total_pnl = row[1] or 0
    avg_roi = row[2] or 0
    return {'total_trades': total_trades, 'total_pnl': total_pnl, 'avg_roi': avg_roi}

# ---------- Trading Logic ----------
async def place_market_order(symbol, side, amount):
    try:
        exchange = get_mexc_exchange()
        order = exchange.create_market_order(symbol, side, amount)
        return order
    except Exception as e:
        logging.error(f"Error placing market order: {e}")
        return None

async def place_limit_order(symbol, side, amount, price):
    try:
        exchange = get_mexc_exchange()
        order = exchange.create_limit_order(symbol, side, amount, price)
        return order
    except Exception as e:
        logging.error(f"Error placing limit order: {e}")
        return None

async def get_current_price(symbol):
    try:
        exchange = get_mexc_exchange()
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except Exception as e:
        logging.error(f"Error getting price: {e}")
        return None

async def calculate_position_size(deposit, leverage, current_price):
    position_value = deposit * leverage
    amount = position_value / current_price
    return amount

async def execute_trade(user_id):
    settings = get_user_settings(user_id)
    if not settings or not settings['bot_active']:
        return
    
    symbol = settings['trading_pair']
    position = settings['position']
    deposit = settings['deposit']
    leverage = settings['leverage']
    
    current_price = await get_current_price(symbol)
    if not current_price:
        return
    
    side = 'buy' if position == 'LONG' else 'sell'
    amount = await calculate_position_size(deposit, leverage, current_price)
    
    entry_order = await place_market_order(symbol, side, amount)
    if not entry_order:
        return
    
    entry_price = entry_order['average'] if entry_order.get('average') else entry_order['price']
    
    # Calculate exit price based on profit percentage
    profit_percent = settings['profit_percent'] / 100
    if position == 'LONG':
        exit_price = entry_price * (1 + profit_percent)
    else:
        exit_price = entry_price * (1 - profit_percent)
    
    # Place exit order
    exit_side = 'sell' if position == 'LONG' else 'buy'
    exit_order = await place_limit_order(symbol, exit_side, amount, exit_price)
    
    if exit_order:
        # Calculate PNL and ROI
        if position == 'LONG':
            pnl = (exit_price - entry_price) * amount
        else:
            pnl = (entry_price - exit_price) * amount
        
        roi = (pnl / deposit) * 100
        
        # Save trade to database
        add_trade(user_id, symbol, position, entry_price, exit_price, pnl, roi)
        
        logging.info(f"Trade executed: {symbol} {position} PNL: {pnl:.2f} USDT ROI: {roi:.2f}%")

async def trading_loop():
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM users WHERE bot_active = 1')
            active_users = cursor.fetchall()
            conn.close()
            
            for (user_id,) in active_users:
                await execute_trade(user_id)
        except Exception as e:
            logging.error(f"Error in trading loop: {e}")
        
        await asyncio.sleep(60)  # Check every minute

def get_mexc_exchange():
    return ccxt.mexc({
        'apiKey': MEXC_API_KEY,
        'secret': MEXC_SECRET_KEY,
        'enableRateLimit': True,
    })

# ---------- Keyboards ----------
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="� Статистика")],
            [KeyboardButton(text="⚙️ Настройки бота")],
            [KeyboardButton(text="�� Цена")],
            [KeyboardButton(text="📊 Баланс")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_dashboard_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Настройки бота", callback_data="settings")],
        [InlineKeyboardButton(text="📜 История сделок", callback_data="history")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_dashboard")]
    ])
    return keyboard

def get_settings_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Режим бота", callback_data="set_mode")],
        [InlineKeyboardButton(text="💱 Торговая пара", callback_data="set_pair")],
        [InlineKeyboardButton(text="📈 Позиция", callback_data="set_position")],
        [InlineKeyboardButton(text="💵 Депозит", callback_data="set_deposit")],
        [InlineKeyboardButton(text="⚡ Плечо", callback_data="set_leverage")],
        [InlineKeyboardButton(text="📊 Стратегия", callback_data="set_strategy")],
        [InlineKeyboardButton(text="🚪 Выход из сделки", callback_data="set_exit")],
        [InlineKeyboardButton(text="▶️ Запустить бота", callback_data="start_bot")],
        [InlineKeyboardButton(text="⏹️ Остановить бота", callback_data="stop_bot")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_dashboard")]
    ])
    return keyboard

def get_mode_keyboard(current_mode):
    fast_style = "✅" if current_mode == "FAST" else ""
    pro_style = "✅" if current_mode == "PRO" else ""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚡ FAST {fast_style}", callback_data="mode_FAST")],
        [InlineKeyboardButton(text=f"🔧 PRO {pro_style}", callback_data="mode_PRO")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="settings")]
    ])
    return keyboard

def get_position_keyboard(current_position):
    long_style = "✅" if current_position == "LONG" else ""
    short_style = "✅" if current_position == "SHORT" else ""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📈 LONG {long_style}", callback_data="position_LONG")],
        [InlineKeyboardButton(text=f"📉 SHORT {short_style}", callback_data="position_SHORT")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="settings")]
    ])
    return keyboard

def get_exit_type_keyboard(current_type):
    limit_style = "✅" if current_type == "limit" else ""
    trailing_style = "✅" if current_type == "trailing" else ""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📊 Лимитный {limit_style}", callback_data="exit_limit")],
        [InlineKeyboardButton(text=f"📈 Скользящий {trailing_style}", callback_data="exit_trailing")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="set_exit")]
    ])
    return keyboard

@dp.message(Command("start"))
async def start_command(message: types.Message):
    create_user(message.from_user.id)
    await show_dashboard(message)

async def show_dashboard(message):
    settings = get_user_settings(message.from_user.id)
    if not settings:
        create_user(message.from_user.id)
        settings = get_user_settings(message.from_user.id)
    
    stats = get_user_stats(message.from_user.id)
    
    try:
        exchange = get_mexc_exchange()
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('free', 0)
    except:
        usdt_balance = 0
    
    status = "🟢 Активен" if settings['bot_active'] else "🔴 Остановлен"
    
    text = f"""🤖 **Trading Bot**

💰 **ОБЩИЙ БАЛАНС**
${usdt_balance:.2f} USDT

📊 **СТАТИСТИКА**
{status}
PNL: {stats['total_pnl']:+.2f} USDT
ROI: {stats['avg_roi']:+.2f}%
Сделок: {stats['total_trades']}

⚙️ **ТЕКУЩИЕ НАСТРОЙКИ**
Режим: {settings['mode']}
Пара: {settings['trading_pair']}
Позиция: {settings['position']}
Депозит: {settings['deposit']} USDT
Плечо: {settings['leverage']}x
"""
    
    if isinstance(message, types.CallbackQuery):
        await message.message.edit_text(text, reply_markup=get_dashboard_keyboard(), parse_mode="Markdown")
        await message.answer()
    else:
        await message.answer(text, reply_markup=get_dashboard_keyboard(), parse_mode="Markdown")

@dp.message(F.text == "📊 Статистика")
async def statistics_button_handler(message: types.Message):
    await show_dashboard(message)

@dp.message(F.text == "⚙️ Настройки бота")
async def settings_button_handler(message: types.Message):
    await show_settings(message)

async def show_settings(message):
    settings = get_user_settings(message.from_user.id)
    if not settings:
        create_user(message.from_user.id)
        settings = get_user_settings(message.from_user.id)
    
    text = f"""⚙️ **НАСТРОЙКА БОТА**

🚀 **Режим:** {settings['mode']}
💱 **Пара:** {settings['trading_pair']}
📈 **Позиция:** {settings['position']}
💵 **Депозит:** {settings['deposit']} USDT
⚡ **Плечо:** {settings['leverage']}x

📊 **Стратегия:**
Ордеров: {settings['orders_count']}
Перекрытие цены: {settings['price_overlap']}%
Коэф. цены: {settings['price_coeff']}
Коэф. объёма: {settings['volume_coeff']}

🚪 **Выход:**
Тип: {settings['exit_type']}
Профит: {settings['profit_percent']}%

Статус: {'🟢 Активен' if settings['bot_active'] else '🔴 Остановлен'}
"""
    
    if isinstance(message, types.CallbackQuery):
        await message.message.edit_text(text, reply_markup=get_settings_keyboard(), parse_mode="Markdown")
        await message.answer()
    else:
        await message.answer(text, reply_markup=get_settings_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "settings")
async def settings_callback(callback: types.CallbackQuery):
    await show_settings(callback)

@dp.callback_query(F.data == "back_to_dashboard")
async def back_to_dashboard_callback(callback: types.CallbackQuery):
    await show_dashboard(callback)

@dp.callback_query(F.data == "refresh_dashboard")
async def refresh_dashboard_callback(callback: types.CallbackQuery):
    await show_dashboard(callback)

@dp.callback_query(F.data == "set_mode")
async def set_mode_callback(callback: types.CallbackQuery):
    settings = get_user_settings(callback.from_user.id)
    await callback.message.edit_text("🚀 **Выберите режим бота:**", reply_markup=get_mode_keyboard(settings['mode']), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("mode_"))
async def mode_selected_callback(callback: types.CallbackQuery):
    mode = callback.data.split("_")[1]
    update_user_setting(callback.from_user.id, "mode", mode)
    await show_settings(callback)

@dp.callback_query(F.data == "set_position")
async def set_position_callback(callback: types.CallbackQuery):
    settings = get_user_settings(callback.from_user.id)
    await callback.message.edit_text("📈 **Выберите позицию:**", reply_markup=get_position_keyboard(settings['position']), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("position_"))
async def position_selected_callback(callback: types.CallbackQuery):
    position = callback.data.split("_")[1]
    update_user_setting(callback.from_user.id, "position", position)
    await show_settings(callback)

@dp.callback_query(F.data == "set_exit")
async def set_exit_callback(callback: types.CallbackQuery):
    settings = get_user_settings(callback.from_user.id)
    await callback.message.edit_text("🚪 **Тип выхода из сделки:**", reply_markup=get_exit_type_keyboard(settings['exit_type']), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("exit_"))
async def exit_selected_callback(callback: types.CallbackQuery):
    exit_type = callback.data.split("_")[1]
    update_user_setting(callback.from_user.id, "exit_type", exit_type)
    await show_settings(callback)

@dp.callback_query(F.data == "history")
async def history_callback(callback: types.CallbackQuery):
    trades = get_user_trades(callback.from_user.id, limit=10)
    
    if not trades:
        text = "📜 **История сделок пуста**"
    else:
        text = "📜 **История сделок:**\n\n"
        for trade in trades:
            symbol, position, pnl, roi, timestamp = trade
            pnl_emoji = "🟢" if pnl > 0 else "🔴"
            text += f"{pnl_emoji} {symbol} {position}\n"
            text += f"   PNL: {pnl:+.2f} USDT | ROI: {roi:+.2f}%\n"
            text += f"   {timestamp}\n\n"
    
    back_btn = InlineKeyboardButton(text="◀️ Назад", callback_data="refresh_dashboard")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_btn]])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "start_bot")
async def start_bot_callback(callback: types.CallbackQuery):
    update_user_setting(callback.from_user.id, "bot_active", 1)
    await show_settings(callback)
    await callback.answer("Бот запущен 🟢")

@dp.callback_query(F.data == "stop_bot")
async def stop_bot_callback(callback: types.CallbackQuery):
    update_user_setting(callback.from_user.id, "bot_active", 0)
    await show_settings(callback)
    await callback.answer("Бот остановлен 🔴")

@dp.callback_query(F.data == "set_pair")
async def set_pair_callback(callback: types.CallbackQuery):
    await callback.message.edit_text("💱 **Введите торговую пару:**\nПример: BTCUSDT", reply_markup=None, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "set_deposit")
async def set_deposit_callback(callback: types.CallbackQuery):
    await callback.message.edit_text("💵 **Введите депозит в USDT:**", reply_markup=None, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "set_leverage")
async def set_leverage_callback(callback: types.CallbackQuery):
    await callback.message.edit_text("⚡ **Введите плечо (1-20x):**", reply_markup=None, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "set_strategy")
async def set_strategy_callback(callback: types.CallbackQuery):
    await callback.message.edit_text("📊 **Введите параметры стратегии через запятую:**\nПример: 5,30,1.5,1.2\n(ордеров, перекрытие%, коэф.цены, коэф.объёма)", reply_markup=None, parse_mode="Markdown")
    await callback.answer()

@dp.message()
async def handle_text_input(message: types.Message):
    text = message.text.strip()
    user_id = message.from_user.id
    
    if text in ["💰 Цена", "📊 Баланс", "📊 Статистика", "⚙️ Настройки бота"]:
        return
    
    try:
        exchange = get_mexc_exchange()
        
        if text.upper().endswith('USDT') or len(text) >= 3 and text.isalpha():
            symbol = text.upper() if text.upper().endswith('USDT') else text.upper() + 'USDT'
            try:
                ticker = exchange.fetch_ticker(symbol)
                price = ticker['last']
                refresh_btn = InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{symbol}")
                chart_btn = InlineKeyboardButton(text="📈 График", callback_data=f"chart_{symbol}")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[refresh_btn, chart_btn]])
                await message.answer(f"💰 Цена {symbol}: {price:.4f} USDT", reply_markup=keyboard)
                return
            except:
                pass
        
        if text.replace('.', '', 1).isdigit():
            value = float(text)
            if value < 1000:
                update_user_setting(user_id, "deposit", value)
                await message.answer(f"💵 Депозит установлен: {value} USDT", reply_markup=get_main_keyboard())
            elif value <= 20:
                update_user_setting(user_id, "leverage", int(value))
                await message.answer(f"⚡ Плечо установлено: {int(value)}x", reply_markup=get_main_keyboard())
            else:
                await message.answer("❌ Неверное значение", reply_markup=get_main_keyboard())
            return
        
        if ',' in text:
            parts = text.split(',')
            if len(parts) == 4:
                orders, overlap, price_c, volume_c = parts
                update_user_setting(user_id, "orders_count", int(orders))
                update_user_setting(user_id, "price_overlap", float(overlap))
                update_user_setting(user_id, "price_coeff", float(price_c))
                update_user_setting(user_id, "volume_coeff", float(volume_c))
                await message.answer("📊 Стратегия обновлена!", reply_markup=get_main_keyboard())
                return
        
        await message.answer("❌ Неверный формат ввода", reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}", reply_markup=get_main_keyboard())

@dp.message(F.text == "💰 Цена")
async def price_button_handler(message: types.Message):
    await message.answer(
        "Введите валютную пару для получения цены.\n"
        "Пример: BTCUSDT или просто BTC",
        reply_markup=get_main_keyboard()
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

@dp.message()
async def handle_symbol_input(message: types.Message):
    # Обработка ввода валютной пары
    text = message.text.strip().upper()
    if text in ["💰 Цена", "📊 Баланс", "/start", "/price", "/balance"]:
        return  # Пропускаем команды и кнопки меню
    
    # Проверяем, это похоже на валютную пару
    if len(text) >= 3 and text.isalpha():
        symbol = text if text.endswith('USDT') else text + 'USDT'
        try:
            exchange = get_mexc_exchange()
            ticker = exchange.fetch_ticker(symbol)
            price = ticker['last']

            refresh_btn = InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_{symbol}")
            chart_btn = InlineKeyboardButton(text="📈 График", callback_data=f"chart_{symbol}")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[refresh_btn, chart_btn]])

            await message.answer(f"💰 Цена {symbol}: {price:.4f} USDT", reply_markup=keyboard)
        except Exception as e:
            await message.answer(f"❌ Ошибка при получении цены для {symbol}: {e}")

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

@dp.message(F.text == "📊 Баланс")
async def balance_button_handler(message: types.Message):
    try:
        exchange = get_mexc_exchange()
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('free', 0)
        await message.answer(f"💰 Ваш баланс USDT на MEXC: {usdt_balance:.2f}", reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"❌ Не удалось получить баланс: {e}", reply_markup=get_main_keyboard())

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
    init_db()
    # Delete old webhook to avoid conflict
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook set to {WEBHOOK_URL}")
    # Start trading loop in background
    asyncio.create_task(trading_loop())
    logging.info("Trading loop started")

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