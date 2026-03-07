import asyncio
import sqlite3
import time
import re
import logging
import os
import sys
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, FSInputFile, InputMediaPhoto
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# ================= КОНФИГУРАЦИЯ =================
TOKEN = "8518608816:AAE2sq4E2ZqWPcPhec_DrIvM-DUllyzJZOY"
MAIN_ADMIN_ID = 5413256595
PROXY_NAME = "SalutProxy"
PROXY_FILE_PATH = "proxy.txt"

# Названия файлов изображений
IMG_PRIVET = "privet.png"   # Старт
IMG_PROFILE = "profile.png" # Профиль
IMG_PROXY = "proxy.png"     # Прокси, Локации, Инфо, Поддержка
IMG_REFKA = "refka.png"     # Рефералка
IMG_ADMIN = "admin.png"     # Админка
IMG_INFA = "infa.png"       # Рассылка

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================= УТИЛИТЫ =================
def ensure_files_exist():
    """Создает заглушки для картинок и proxy.txt, если их нет, чтобы бот не падал."""
    images = [IMG_PRIVET, IMG_PROFILE, IMG_PROXY, IMG_REFKA, IMG_ADMIN, IMG_INFA]
    for img in images:
        if not os.path.exists(img):
            with open(img, 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    
    if not os.path.exists(PROXY_FILE_PATH):
        with open(PROXY_FILE_PATH, 'w') as f:
            f.write("") # Создаем пустой файл

def get_media(filename):
    return FSInputFile(filename)

def format_vip_time(vip_end_str):
    if not vip_end_str:
        return "Не активен"
    try:
        end_date = datetime.strptime(vip_end_str, "%Y-%m-%d %H:%M:%S")
        if end_date < datetime.now():
            return "Истек"
        
        delta = end_date - datetime.now()
        days = delta.days
        hours = delta.seconds // 3600
        return f"{days} дн. {hours} ч."
    except:
        return "Ошибка даты"

def guess_country(ip):
    if ip.startswith("85."): return "🇩🇪 Германия"
    if ip.startswith("176."): return "🇳🇱 Нидерланды"
    if ip.startswith("83."): return "🇫🇷 Франция"
    if ip.startswith("5."): return "🇬🇧 Великобритания"
    if ip.startswith("91."): return "🇺🇸 США"
    return "🇪🇺 Европа"

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect("salut_proxy.db")
    cursor = conn.cursor()
    # Таблица пользователей
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        referrer_id INTEGER,
                        refs_count INTEGER DEFAULT 0,
                        is_vip_permanent INTEGER DEFAULT 0,
                        vip_end_date TIMESTAMP DEFAULT NULL
                    )''')
    # Таблица прокси
    cursor.execute('''CREATE TABLE IF NOT EXISTS proxies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server TEXT,
                        port INTEGER,
                        secret TEXT,
                        country TEXT,
                        ping_ms INTEGER DEFAULT 9999,
                        UNIQUE(server, port)
                    )''')
    
    # Миграция (на случай старой БД)
    cursor.execute("PRAGMA table_info(proxies)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'country' not in columns:
        cursor.execute("ALTER TABLE proxies ADD COLUMN country TEXT DEFAULT 'Европа'")
    
    conn.commit()
    conn.close()

def db_query(query, args=(), fetchone=False, fetchall=False, commit=False):
    conn = sqlite3.connect("salut_proxy.db")
    cursor = conn.cursor()
    try:
        cursor.execute(query, args)
        if commit: conn.commit()
        if fetchone: return cursor.fetchone()
        if fetchall: return cursor.fetchall()
    except Exception as e:
        logging.error(f"DB Error: {e}")
    finally:
        conn.close()

def add_proxy_to_db(link):
    match = re.search(r'server=([^&]+)&port=(\d+)&secret=([^&]+)', link)
    if match:
        server, port, secret = match.groups()
        country = guess_country(server)
        conn = sqlite3.connect("salut_proxy.db")
        try:
            conn.execute("INSERT OR IGNORE INTO proxies (server, port, secret, country) VALUES (?, ?, ?, ?)", 
                         (server, int(port), secret, country))
            conn.commit()
            return True
        except: return False
        finally: conn.close()
    return False

def load_proxies_from_file():
    if not os.path.exists(PROXY_FILE_PATH): return 0
    count = 0
    with open(PROXY_FILE_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            if "proxy?" in line:
                if add_proxy_to_db(line.strip()):
                    count += 1
    return count

# ================= ЛОГИКА СТАТУСОВ =================
def get_user_status(user_id):
    user = db_query("SELECT is_vip_permanent, vip_end_date, refs_count FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if not user: return "Базовый", False
    
    is_perm, vip_end, refs = user
    
    # 1. Админ / Вечный VIP
    if user_id == MAIN_ADMIN_ID or is_perm:
        return "Администратор", True

    # 2. Временный VIP (за рефералов)
    if vip_end:
        try:
            end_date = datetime.strptime(vip_end, "%Y-%m-%d %H:%M:%S")
            if end_date > datetime.now():
                return "Премиум (Активен)", True
        except: pass
    
    # 3. Базовый
    return "Базовый", False

def add_vip_days(user_id, days=1):
    user = db_query("SELECT vip_end_date FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    current_end = user[0] if user else None
    
    now = datetime.now()
    
    if current_end:
        try:
            current_date = datetime.strptime(current_end, "%Y-%m-%d %H:%M:%S")
            if current_date > now:
                new_date = current_date + timedelta(days=days)
            else:
                new_date = now + timedelta(days=days)
        except:
            new_date = now + timedelta(days=days)
    else:
        new_date = now + timedelta(days=days)
        
    db_query("UPDATE users SET vip_end_date = ? WHERE user_id = ?", (new_date.strftime("%Y-%m-%d %H:%M:%S"), user_id), commit=True)

# ================= ПИНГАТОР =================
async def ping_proxy(server, port):
    try:
        start_time = time.time()
        reader, writer = await asyncio.wait_for(asyncio.open_connection(server, port), timeout=2.0)
        ping_ms = int((time.time() - start_time) * 1000)
        writer.close()
        await writer.wait_closed()
        return ping_ms
    except:
        return 9999

async def update_all_pings():
    proxies = db_query("SELECT id, server, port FROM proxies", fetchall=True)
    if not proxies: return
    for p_id, server, port in proxies:
        ping = await ping_proxy(server, port)
        db_query("UPDATE proxies SET ping_ms = ? WHERE id = ?", (ping, p_id), commit=True)

async def background_ping_task():
    while True:
        await asyncio.sleep(180) # Каждые 3 минуты
        try:
            await update_all_pings()
        except Exception as e:
            logging.error(f"Ping Error: {e}")

# ================= МАШИНА СОСТОЯНИЙ =================
class States(StatesGroup):
    waiting_proxy_text = State()
    waiting_del_id = State()
    waiting_vip_id = State()
    waiting_broadcast = State()
    waiting_support_msg = State()
    waiting_support_reply = State()

# ================= КЛАВИАТУРЫ =================
def start_kb():
    return InlineKeyboardBuilder().button(text="Продолжить", callback_data="open_profile").as_markup()

def profile_kb(is_admin):
    b = InlineKeyboardBuilder()
    b.button(text="⚡️ Подключить прокси", callback_data="get_proxy")
    b.button(text="🌍 Локации", callback_data="locations")
    b.button(text="👥 Партнерская программа", callback_data="referrals")
    b.button(text="❓ О сервисе", callback_data="about")
    b.button(text="📩 Поддержка", callback_data="support")
    if is_admin:
        b.button(text="⚙️ Панель админа", callback_data="admin_panel")
    b.adjust(1, 2, 2, 1)
    return b.as_markup()

def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Ввод текста", callback_data="admin_add_text")
    b.button(text="📂 Из proxy.txt", callback_data="admin_load_file")
    b.button(text="🗑 Удалить", callback_data="admin_del")
    b.button(text="🔄 Обновить пинг", callback_data="admin_ping")
    b.button(text="📊 Статистика", callback_data="admin_stats")
    b.button(text="👑 Выдать права", callback_data="admin_vip")
    b.button(text="📢 Рассылка", callback_data="admin_broadcast")
    b.button(text="🔙 В профиль", callback_data="open_profile")
    b.adjust(2, 2, 1, 2, 1)
    return b.as_markup()

def back_kb(to="open_profile"):
    return InlineKeyboardBuilder().button(text="🔙 Назад", callback_data=to).as_markup()

async def safe_edit(call: CallbackQuery, media, kb):
    try: await call.message.edit_media(media=media, reply_markup=kb)
    except TelegramBadRequest: pass

# ================= ХЕНДЛЕРЫ: СТАРТ И ПРОФИЛЬ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or "Пользователь"
    
    # Регистрация
    if not db_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetchone=True):
        args = message.text.split()
        referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() and int(args[1]) != user_id else None
        
        db_query("INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)", (user_id, username, referrer_id), commit=True)
        
        # Начисление бонуса рефереру
        if referrer_id:
            db_query("UPDATE users SET refs_count = refs_count + 1 WHERE user_id = ?", (referrer_id,), commit=True)
            add_vip_days(referrer_id, 1)
            try:
                await bot.send_message(referrer_id, "🎉 <b>Новый реферал!</b>\nВам начислен +1 день Премиум доступа.", parse_mode="HTML")
            except: pass

    try: await message.delete()
    except: pass
    
    text = f"👋 Привет, {username}!\nДобро пожаловать в <b>{PROXY_NAME}</b>."
    await message.answer_photo(photo=get_media(IMG_PRIVET), caption=text, reply_markup=start_kb(), parse_mode="HTML")

@router.callback_query(F.data == "open_profile")
async def open_profile(call: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = call.from_user.id
    status_text, is_vip = get_user_status(user_id)
    is_admin_rights = (user_id == MAIN_ADMIN_ID) or (status_text == "Администратор")
    
    user_data = db_query("SELECT refs_count, vip_end_date FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    refs = user_data[0]
    vip_time = format_vip_time(user_data[1])
    
    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"💎 Статус: <b>{status_text}</b>\n"
        f"⏳ Действует: <b>{vip_time}</b>\n"
        f"👥 Рефералов: <b>{refs}</b>\n\n"
        f"<i>Приглашайте друзей, чтобы получить бесплатный Премиум!</i>"
    )
    
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_PROFILE), caption=text, parse_mode="HTML"), profile_kb(is_admin_rights))

# ================= ХЕНДЛЕРЫ: ПРОКСИ И ИНФО (PROXY.PNG) =================
@router.callback_query(F.data == "get_proxy")
async def get_proxy(call: CallbackQuery):
    user_id = call.from_user.id
    status, is_vip = get_user_status(user_id)
    
    proxies = db_query("SELECT server, port, secret, country, ping_ms FROM proxies WHERE ping_ms < 9999 ORDER BY ping_ms ASC", fetchall=True)
    
    if not proxies:
        return await call.answer("Серверы обновляются, попробуйте позже.", show_alert=True)
    
    # Алгоритм выдачи
    if status == "Администратор":
        selected = proxies[0] # Топ 1
    elif is_vip: # Премиум
        # Топ 30%
        idx = min(len(proxies)-1, int(len(proxies) * 0.3))
        selected = proxies[idx]
    else: # Базовый
        selected = proxies[-1] # Самый медленный (но рабочий)

    server, port, secret, country, ping = selected
    link = f"https://t.me/proxy?server={server}&port={port}&secret={secret}"
    
    text = (
        f"✅ <b>Сервер подобран</b>\n\n"
        f"📍 Локация: <b>{country}</b>\n"
        f"⚡️ Пинг: <b>{ping} ms</b>\n"
        f"🔐 Шифрование: <b>MTProto</b>\n\n"
        f"<i>Нажмите кнопку ниже для подключения.</i>"
    )
    kb = InlineKeyboardBuilder().button(text="⚡️ Подключить", url=link).button(text="🔙 Назад", callback_data="open_profile").as_markup()
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_PROXY), caption=text, parse_mode="HTML"), kb)

@router.callback_query(F.data == "locations")
async def locations(call: CallbackQuery):
    data = db_query("SELECT country, MIN(ping_ms) FROM proxies WHERE ping_ms < 9999 GROUP BY country", fetchall=True)
    list_text = "\n".join([f"• {r[0]} — <b>{r[1]} ms</b>" for r in data]) if data else "Нет данных"
    text = f"🌍 <b>Локации и лучший пинг:</b>\n\n{list_text}"
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_PROXY), caption=text, parse_mode="HTML"), back_kb())

@router.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    text = f"ℹ️ <b>О сервисе {PROXY_NAME}</b>\n\nАвтоматическая система выдачи MTProxy.\nМы мониторим доступность серверов каждые 3 минуты.\nВаш статус определяет скорость соединения."
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_PROXY), caption=text, parse_mode="HTML"), back_kb())

# ================= ХЕНДЛЕРЫ: РЕФЕРАЛКА (REFKA.PNG) =================
@router.callback_query(F.data == "referrals")
async def referrals(call: CallbackQuery):
    user_id = call.from_user.id
    refs = db_query("SELECT refs_count FROM users WHERE user_id = ?", (user_id,), fetchone=True)[0]
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    text = (
        f"🔗 <b>Партнерская программа</b>\n\n"
        f"Приглашай друзей и получай <b>+1 день Премиума</b> за каждого!\n\n"
        f"👥 Приглашено: <b>{refs}</b>\n"
        f"🔗 Твоя ссылка:\n<code>{link}</code>"
    )
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_REFKA), caption=text, parse_mode="HTML"), back_kb())

# ================= ХЕНДЛЕРЫ: ПОДДЕРЖКА (PROXY.PNG) =================
@router.callback_query(F.data == "support")
async def support(call: CallbackQuery, state: FSMContext):
    text = "📩 <b>Поддержка</b>\n\nОпишите вашу проблему следующим сообщением.\nАдминистратор ответит вам."
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_PROXY), caption=text, parse_mode="HTML"), back_kb())
    await state.set_state(States.waiting_support_msg)

@router.message(StateFilter(States.waiting_support_msg))
async def support_msg(message: Message, state: FSMContext):
    # Отправка админу
    admin_text = f"📩 <b>Тикет от пользователя</b>\nID: <code>{message.from_user.id}</code>\n@{message.from_user.username}\n\nТекст:\n{message.text}"
    kb = InlineKeyboardBuilder().button(text="Ответить", callback_data=f"reply_{message.from_user.id}").as_markup()
    try:
        await bot.send_message(MAIN_ADMIN_ID, admin_text, reply_markup=kb, parse_mode="HTML")
        await message.answer("✅ Ваше сообщение отправлено! Ожидайте ответа.")
    except:
        await message.answer("Ошибка отправки.")
    await state.clear()

@router.callback_query(F.data.startswith("reply_"))
async def admin_reply_start(call: CallbackQuery, state: FSMContext):
    user_id = int(call.data.split("_")[1])
    await call.message.answer(f"Введите ответ для пользователя {user_id}:")
    await state.update_data(reply_to_id=user_id)
    await state.set_state(States.waiting_support_reply)
    await call.answer()

@router.message(StateFilter(States.waiting_support_reply))
async def admin_reply_send(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("reply_to_id")
    try:
        await bot.send_message(target_id, f"📩 <b>Ответ от поддержки:</b>\n\n{message.text}", parse_mode="HTML")
        await message.answer("✅ Ответ отправлен.")
    except:
        await message.answer("❌ Не удалось отправить (пользователь заблокировал бота).")
    await state.clear()

# ================= ХЕНДЛЕРЫ: АДМИНКА (ADMIN.PNG) =================
@router.callback_query(F.data == "admin_panel")
async def admin_main(call: CallbackQuery):
    if not get_user_status(call.from_user.id)[1]: return
    text = "⚙️ <b>Панель управления</b>\n\nВыберите действие:"
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_ADMIN), caption=text, parse_mode="HTML"), admin_kb())

@router.callback_query(F.data == "admin_load_file")
async def load_file(call: CallbackQuery):
    count = load_proxies_from_file()
    await call.answer(f"Загружено из файла: {count} шт.", show_alert=True)

@router.callback_query(F.data == "admin_add_text")
async def add_text(call: CallbackQuery, state: FSMContext):
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_ADMIN), caption="Отправьте прокси списком:", parse_mode="HTML"), back_kb("admin_panel"))
    await state.set_state(States.waiting_proxy_text)

@router.message(StateFilter(States.waiting_proxy_text))
async def add_text_process(message: Message, state: FSMContext):
    await message.delete()
    links = re.findall(r'(?:tg://|https://t\.me/)proxy\?[^\s]+', message.text)
    added = sum(1 for l in links if add_proxy_to_db(l))
    msg = await message.answer(f"✅ Добавлено: {added}")
    await state.clear()
    await asyncio.sleep(3)
    try: await msg.delete()
    except: pass

@router.callback_query(F.data == "admin_del")
async def del_proxy(call: CallbackQuery, state: FSMContext):
    proxies = db_query("SELECT id, server FROM proxies", fetchall=True)
    if not proxies: return await call.answer("Пусто", show_alert=True)
    text = "Введите ID для удаления:\n\n" + "\n".join([f"ID: {p[0]} | IP: {p[1]}" for p in proxies])
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_ADMIN), caption=text[:1000], parse_mode="HTML"), back_kb("admin_panel"))
    await state.set_state(States.waiting_del_id)

@router.message(StateFilter(States.waiting_del_id))
async def del_process(message: Message, state: FSMContext):
    await message.delete()
    if message.text.isdigit():
        db_query("DELETE FROM proxies WHERE id = ?", (int(message.text),), commit=True)
        msg = await message.answer("✅ Удалено")
    else: msg = await message.answer("Ошибка")
    await state.clear()
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass

@router.callback_query(F.data == "admin_ping")
async def force_ping(call: CallbackQuery):
    await call.answer("Обновляю...", show_alert=False)
    await update_all_pings()
    await call.answer("✅ Готово!", show_alert=True)

@router.callback_query(F.data == "admin_stats")
async def stats(call: CallbackQuery):
    u = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    p = db_query("SELECT COUNT(*) FROM proxies", fetchone=True)[0]
    pa = db_query("SELECT COUNT(*) FROM proxies WHERE ping_ms < 9999", fetchone=True)[0]
    await call.answer(f"Юзеров: {u}\nПрокси: {p}\nЖивых: {pa}", show_alert=True)

@router.callback_query(F.data == "admin_vip")
async def give_vip(call: CallbackQuery, state: FSMContext):
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_ADMIN), caption="Введите ID пользователя для вечного VIP:", parse_mode="HTML"), back_kb("admin_panel"))
    await state.set_state(States.waiting_vip_id)

@router.message(StateFilter(States.waiting_vip_id))
async def vip_process(message: Message, state: FSMContext):
    await message.delete()
    if message.text.isdigit():
        db_query("UPDATE users SET is_vip_permanent = 1 WHERE user_id = ?", (int(message.text),), commit=True)
        msg = await message.answer("✅ Права выданы")
    else: msg = await message.answer("Ошибка")
    await state.clear()
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass

@router.callback_query(F.data == "admin_broadcast")
async def broadcast(call: CallbackQuery, state: FSMContext):
    await safe_edit(call, InputMediaPhoto(media=get_media(IMG_INFA), caption="Введите текст рассылки (будет использована картинка infa.png):", parse_mode="HTML"), back_kb("admin_panel"))
    await state.set_state(States.waiting_broadcast)

@router.message(StateFilter(States.waiting_broadcast))
async def broadcast_process(message: Message, state: FSMContext):
    await message.delete()
    users = db_query("SELECT user_id FROM users", fetchall=True)
    msg = await message.answer("🚀 Рассылка началась...")
    count = 0
    photo = get_media(IMG_INFA)
    for u in users:
        try:
            await bot.send_photo(u[0], photo=photo, caption=message.text, parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await msg.edit_text(f"✅ Рассылка завершена: {count} получено.")
    await state.clear()

# ================= ЗАПУСК =================
async def main():
    print("Запуск SalutProxy...")
    ensure_files_exist()
    init_db()
    
    # Загружаем прокси из файла при старте
    loaded = load_proxies_from_file()
    print(f"Загружено из proxy.txt: {loaded}")
    
    # Первичный пинг
    print("Пинг серверов...")
    await update_all_pings()
    
    # Фоновая задача
    asyncio.create_task(background_ping_task())
    
    print("Бот в сети!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен")