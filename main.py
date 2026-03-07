import asyncio
import sqlite3
import time
import re
import logging
import os
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

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================= ГЕНЕРАТОР КАРТИНОК-ЗАГЛУШЕК =================
def ensure_image_exists(filename):
    if not os.path.exists(filename):
        with open(filename, 'wb') as f:
            # 1x1 прозрачный PNG пиксель (заглушка от ошибок, если картинки нет)
            f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    return FSInputFile(filename)

# Названия всех картинок по разделам
IMG_PRIVET = "privet.png"  # Приветствие /start
IMG_PROFILE = "profile.png" # Главный профиль
IMG_PROXY = "proxy.png"     # Подключение, локации, информация
IMG_INFA = "infa.png"       # Рассылка /everyone
IMG_ADMIN = "admin.png"     # Вся панель администратора
IMG_REFKA = "refka.png"     # Раздел партнерской программы (рефералы)

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect("salut_proxy.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        referrer_id INTEGER,
                        refs_count INTEGER DEFAULT 0,
                        is_vip INTEGER DEFAULT 0
                    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS proxies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server TEXT,
                        port INTEGER,
                        secret TEXT,
                        country TEXT,
                        ping_ms INTEGER DEFAULT 9999,
                        UNIQUE(server, port)
                    )''')
    
    cursor.execute("PRAGMA table_info(proxies)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'country' not in columns:
        cursor.execute("ALTER TABLE proxies ADD COLUMN country TEXT DEFAULT 'Европа'")
    
    conn.commit()
    conn.close()

def guess_country(ip):
    if ip.startswith("85."): return "Германия"
    if ip.startswith("176."): return "Нидерланды"
    if ip.startswith("83."): return "Франция"
    return "Европа"

def add_proxy_to_db(link):
    conn = sqlite3.connect("salut_proxy.db")
    cursor = conn.cursor()
    match = re.search(r'server=([^&]+)&port=(\d+)&secret=([^&]+)', link)
    success = False
    if match:
        server, port, secret = match.groups()
        country = guess_country(server)
        try:
            cursor.execute("INSERT OR IGNORE INTO proxies (server, port, secret, country) VALUES (?, ?, ?, ?)", 
                           (server, int(port), secret, country))
            if cursor.rowcount > 0: success = True
            conn.commit()
        except: pass
    conn.close()
    return success

def db_query(query, args=(), fetchone=False, fetchall=False, commit=False):
    conn = sqlite3.connect("salut_proxy.db")
    cursor = conn.cursor()
    cursor.execute(query, args)
    res = None
    if fetchone: res = cursor.fetchone()
    if fetchall: res = cursor.fetchall()
    if commit: conn.commit()
    conn.close()
    return res

def is_admin(user_id):
    if user_id == MAIN_ADMIN_ID: return True
    res = db_query("SELECT is_vip FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    return res[0] == 1 if res else False

# ================= ПИНГАТОР =================
async def ping_proxy(server, port):
    try:
        start_time = time.time()
        reader, writer = await asyncio.wait_for(asyncio.open_connection(server, port), timeout=1.5)
        ping_ms = int((time.time() - start_time) * 1000)
        writer.close()
        await writer.wait_closed()
        return ping_ms
    except Exception:
        return 9999

async def update_all_pings():
    proxies = db_query("SELECT id, server, port FROM proxies", fetchall=True)
    for p_id, server, port in proxies:
        ping = await ping_proxy(server, port)
        db_query("UPDATE proxies SET ping_ms = ? WHERE id = ?", (ping, p_id), commit=True)

async def background_ping_task():
    while True:
        await asyncio.sleep(180) # Авто-обновление пинга каждые 3 минуты
        await update_all_pings()

# ================= МАШИНА СОСТОЯНИЙ =================
class AdminState(StatesGroup):
    waiting_for_proxy = State()
    waiting_for_delete_id = State()
    waiting_for_vip = State()

# ================= ИНТЕРФЕЙС / КЛАВИАТУРЫ =================
def start_kb():
    b = InlineKeyboardBuilder()
    b.button(text="Продолжить", callback_data="open_profile")
    return b.as_markup()

def profile_kb(admin=False):
    b = InlineKeyboardBuilder()
    b.button(text="Подключить прокси", callback_data="get_proxy")
    b.button(text="Локации серверов", callback_data="locations")
    b.button(text="Партнерская программа", callback_data="referrals")
    b.button(text="О сервисе", callback_data="about")
    if admin:
        b.button(text="Панель администратора", callback_data="admin_panel")
    b.adjust(1, 2, 1, 1) if admin else b.adjust(1, 2, 1)
    return b.as_markup()

def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="Добавить", callback_data="admin_add_proxy")
    b.button(text="Удалить", callback_data="admin_del_proxy")
    b.button(text="Обновить пинг", callback_data="admin_update_ping")
    b.button(text="Статистика", callback_data="admin_stats")
    b.button(text="Выдать права", callback_data="admin_add_vip")
    b.button(text="Назад в профиль", callback_data="open_profile")
    b.adjust(2, 1, 2, 1)
    return b.as_markup()

def back_kb(to="open_profile"):
    b = InlineKeyboardBuilder()
    b.button(text="Назад", callback_data=to)
    return b.as_markup()

async def safe_edit_media(callback: CallbackQuery, media: InputMediaPhoto, reply_markup):
    try:
        await callback.message.edit_media(media=media, reply_markup=reply_markup)
    except TelegramBadRequest:
        pass 

# ================= ЛОГИКА: СТАРТ И ПРОФИЛЬ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Пользователь"
    args = message.text.split()[1] if len(message.text.split()) > 1 else None

    user = db_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if not user:
        referrer_id = int(args) if args and args.isdigit() and int(args) != user_id else None
        db_query("INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)", 
                 (user_id, username, referrer_id), commit=True)
        if referrer_id:
            db_query("UPDATE users SET refs_count = refs_count + 1 WHERE user_id = ?", (referrer_id,), commit=True)

    try: await message.delete()
    except: pass

    text = (
        f"👋 Добро пожаловать в <b>{PROXY_NAME}</b>!\n\n"
        f"Мы предоставляем надежные и быстрые MTProto прокси для Telegram. "
        f"Нажмите кнопку ниже, чтобы открыть ваш профиль и получить настройки."
    )
    # Использует PRIVET.PNG
    await message.answer_photo(photo=ensure_image_exists(IMG_PRIVET), caption=text, reply_markup=start_kb(), parse_mode="HTML")

@router.callback_query(F.data == "open_profile")
async def process_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.first_name or "Пользователь"
    
    admin_status = is_admin(user_id)
    refs_count = db_query("SELECT refs_count FROM users WHERE user_id = ?", (user_id,), fetchone=True)[0]
    
    if admin_status:
        lvl = "Администратор"
        greet = "Здравствуйте! У вас полный доступ к управлению сервисом."
    elif refs_count > 0:
        lvl = "Премиум"
        greet = "Отличная работа! Вам доступны серверы с повышенной скоростью."
    else:
        lvl = "Базовый"
        greet = "Используйте наши прокси для комфортного и безопасного общения."

    text = (
        f"{greet}\n\n"
        f"👤 <b>Ваш профиль:</b> {username}\n"
        f"💎 <b>Статус:</b> {lvl}\n"
        f"👥 <b>Приглашено друзей:</b> {refs_count}\n\n"
        f"<i>💡 Совет: приглашайте друзей, чтобы система автоматически выделяла вам серверы с минимальным откликом.</i>"
    )

    # Использует PROFILE.PNG
    media = InputMediaPhoto(media=ensure_image_exists(IMG_PROFILE), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, profile_kb(admin_status))

# ================= ЛОГИКА: ПРОКСИ И ИНФО (ИСПОЛЬЗУЕТ PROXY.PNG) =================
@router.callback_query(F.data == "get_proxy")
async def process_get_proxy(callback: CallbackQuery):
    user_id = callback.from_user.id
    admin_status = is_admin(user_id)
    refs_count = db_query("SELECT refs_count FROM users WHERE user_id = ?", (user_id,), fetchone=True)[0]

    proxies = db_query("SELECT server, port, secret, country, ping_ms FROM proxies WHERE ping_ms < 9999 ORDER BY ping_ms ASC", fetchall=True)
    if not proxies:
        return await callback.answer("В данный момент нет доступных серверов. Пожалуйста, подождите.", show_alert=True)

    if admin_status: selected = proxies[0]
    elif refs_count > 0: selected = proxies[min(len(proxies)-1, max(0, len(proxies) // 3))]
    else: selected = proxies[-1]

    server, port, secret, country, ping = selected
    proxy_url = f"https://t.me/proxy?server={server}&port={port}&secret={secret}"
    
    text = (
        f"✅ <b>Ваш прокси готов к работе!</b>\n\n"
        f"📍 Локация: <b>{country}</b>\n"
        f"⚡️ Скорость отклика: <b>{ping} ms</b>\n"
        f"🛡 Протокол: <b>MTProto</b>\n\n"
        f"<i>Нажмите кнопку ниже, чтобы применить настройки в Telegram.</i>"
    )
    b = InlineKeyboardBuilder()
    b.button(text="Подключить", url=proxy_url)
    b.button(text="Назад", callback_data="open_profile")
    
    media = InputMediaPhoto(media=ensure_image_exists(IMG_PROXY), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, b.as_markup())

@router.callback_query(F.data == "locations")
async def process_locations(callback: CallbackQuery):
    countries = db_query("SELECT country, MIN(ping_ms) FROM proxies WHERE ping_ms < 9999 GROUP BY country", fetchall=True)
    
    if countries:
        c_list = "\n".join([f"• {c[0]} — <b>{c[1]} ms</b>" for c in countries])
    else:
        c_list = "Нет активных локаций."
        
    text = (
        f"🌍 <b>Активные локации серверов:</b>\n\n"
        f"{c_list}\n\n"
        f"<i>Система автоматически подбирает для вас лучший сервер в зависимости от вашего статуса. Данные обновляются в реальном времени.</i>"
    )
    media = InputMediaPhoto(media=ensure_image_exists(IMG_PROXY), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, back_kb())

@router.callback_query(F.data == "about")
async def process_about(callback: CallbackQuery):
    text = (
        f"ℹ️ <b>О сервисе SalutProxy</b>\n\n"
        f"Мы предоставляем стабильные узлы MTProto для обеспечения безопасного соединения с серверами Telegram.\n\n"
        f"Ваш трафик защищен сквозным шифрованием, а система мониторинга круглосуточно проверяет скорость каждого сервера, чтобы вы получали лучшее качество связи."
    )
    media = InputMediaPhoto(media=ensure_image_exists(IMG_PROXY), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, back_kb())

# ================= ЛОГИКА: ПАРТНЕРСКАЯ ПРОГРАММА (ИСПОЛЬЗУЕТ REFKA.PNG) =================
@router.callback_query(F.data == "referrals")
async def process_referrals(callback: CallbackQuery):
    user_id = callback.from_user.id
    refs = db_query("SELECT refs_count FROM users WHERE user_id = ?", (user_id,), fetchone=True)[0]
    bot_info = await bot.get_me()
    text = (
        f"🔗 <b>Партнерская программа</b>\n\n"
        f"Скорость выдаваемых вам серверов зависит от вашей активности. Поделитесь ссылкой с друзьями, чтобы получить статус «Премиум».\n\n"
        f"👥 Приглашено друзей: <b>{refs}</b>\n\n"
        f"<b>Ваша персональная ссылка:</b>\n<code>https://t.me/{bot_info.username}?start={user_id}</code>"
    )
    # Использует REFKA.PNG
    media = InputMediaPhoto(media=ensure_image_exists(IMG_REFKA), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, back_kb())

# ================= ЛОГИКА: АДМИНКА (ИСПОЛЬЗУЕТ ADMIN.PNG) =================
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    text = "⚙️ <b>Панель управления SalutProxy</b>\n\nВыберите нужное действие:"
    # Использует ADMIN.PNG
    media = InputMediaPhoto(media=ensure_image_exists(IMG_ADMIN), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, admin_kb())

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    u = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    p = db_query("SELECT COUNT(*) FROM proxies", fetchone=True)[0]
    p_a = db_query("SELECT COUNT(*) FROM proxies WHERE ping_ms < 9999", fetchone=True)[0]
    await callback.answer(f"📊 Статистика:\n\nПользователей: {u}\nВсего серверов: {p}\nВ сети: {p_a}", show_alert=True)

@router.callback_query(F.data == "admin_update_ping")
async def admin_ping(callback: CallbackQuery):
    await callback.answer("Запущено обновление серверов...", show_alert=False)
    await update_all_pings()
    text = "✅ <b>Пинг серверов успешно обновлен.</b>"
    media = InputMediaPhoto(media=ensure_image_exists(IMG_ADMIN), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, admin_kb())

@router.callback_query(F.data == "admin_add_proxy")
async def admin_add(callback: CallbackQuery, state: FSMContext):
    text = "📡 <b>Добавление серверов</b>\n\nОтправьте в чат ссылки на MTProxy (можно списком)."
    media = InputMediaPhoto(media=ensure_image_exists(IMG_ADMIN), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, back_kb("admin_panel"))
    await state.set_state(AdminState.waiting_for_proxy)

@router.message(StateFilter(AdminState.waiting_for_proxy))
async def process_new_proxy(message: Message, state: FSMContext):
    await message.delete()
    links = re.findall(r'(?:tg://|https://t\.me/)proxy\?[^\s]+', message.text)
    if not links: return
    added = sum(1 for link in links if add_proxy_to_db(link))
    await state.clear()
    msg = await message.answer(f"✅ Успешно добавлено серверов: {added} из {len(links)}")
    await asyncio.sleep(3)
    try: await msg.delete()
    except: pass

@router.callback_query(F.data == "admin_del_proxy")
async def admin_del(callback: CallbackQuery, state: FSMContext):
    proxies = db_query("SELECT id, server, ping_ms FROM proxies", fetchall=True)
    if not proxies:
        return await callback.answer("В базе нет серверов.", show_alert=True)
    
    p_list = "\n".join([f"ID: <code>{p[0]}</code> | IP: {p[1]} | Пинг: {p[2]} ms" for p in proxies])
    text = f"🗑 <b>Удаление сервера</b>\n\nТекущие серверы:\n{p_list}\n\nОтправьте ID сервера, который хотите удалить."
    media = InputMediaPhoto(media=ensure_image_exists(IMG_ADMIN), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, back_kb("admin_panel"))
    await state.set_state(AdminState.waiting_for_delete_id)

@router.message(StateFilter(AdminState.waiting_for_delete_id))
async def process_del_proxy(message: Message, state: FSMContext):
    await message.delete()
    if message.text.isdigit():
        db_query("DELETE FROM proxies WHERE id = ?", (int(message.text),), commit=True)
        msg = await message.answer(f"✅ Сервер успешно удален.")
    else:
        msg = await message.answer("❌ Ошибка: ID должен быть числом.")
    await state.clear()
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass

@router.callback_query(F.data == "admin_add_vip")
async def admin_vip(callback: CallbackQuery, state: FSMContext):
    text = "👑 <b>Выдача прав администратора</b>\n\nОтправьте Telegram ID пользователя."
    media = InputMediaPhoto(media=ensure_image_exists(IMG_ADMIN), caption=text, parse_mode="HTML")
    await safe_edit_media(callback, media, back_kb("admin_panel"))
    await state.set_state(AdminState.waiting_for_vip)

@router.message(StateFilter(AdminState.waiting_for_vip))
async def process_new_vip(message: Message, state: FSMContext):
    await message.delete()
    if message.text.isdigit():
        db_query("UPDATE users SET is_vip = 1 WHERE user_id = ?", (int(message.text),), commit=True)
        msg = await message.answer("✅ Права администратора успешно выданы.")
    await state.clear()
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass

# ================= ЛОГИКА: РАССЫЛКА (ИСПОЛЬЗУЕТ INFA.PNG) =================
@router.message(Command("everyone"))
async def cmd_everyone(message: Message):
    if not is_admin(message.from_user.id): return
    text = message.text.replace("/everyone", "").strip()
    if not text:
        return await message.answer("Использование: /everyone [текст сообщения]")
    
    await message.delete()
    users = db_query("SELECT user_id FROM users", fetchall=True)
    sent = 0
    
    # Использует INFA.PNG
    photo = ensure_image_exists(IMG_INFA)
    
    status_msg = await message.answer("Начинаю рассылку...")
    for (uid,) in users:
        try:
            await bot.send_photo(chat_id=uid, photo=photo, caption=f"📣 <b>Уведомление от сервиса:</b>\n\n{text}", parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05) 
        except: pass
    
    await status_msg.edit_text(f"✅ Рассылка завершена. Успешно доставлено: {sent} чел.")
    await asyncio.sleep(5)
    try: await status_msg.delete()
    except: pass

# ================= ЗАПУСК =================
async def main():
    print("Инициализация БД...")
    init_db()
    print("Выполняется первичная проверка серверов...")
    await update_all_pings()
    
    asyncio.create_task(background_ping_task())
    
    print("Бот SalutProxy успешно запущен и готов к работе.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())