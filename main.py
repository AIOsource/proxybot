import asyncio
import sqlite3
import logging
import os
import sys
import re
import time
import random
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import (
    Message, 
    CallbackQuery, 
    FSInputFile, 
    InputMediaPhoto
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# ==============================================================================
#                               CONFIG & CONSTANTS
# ==============================================================================

# ОСНОВНЫЕ НАСТРОЙКИ
TOKEN = "8518608816:AAE2sq4E2ZqWPcPhec_DrIvM-DUllyzJZOY" # Лучше вынести в .env в будущем
ADMIN_ID = 5413256595
PROXY_NAME = "SalutProxy"
PROXY_FILE = "proxy.txt"
DB_NAME = "salut_enterprise.db"
PING_INTERVAL = 180   # Интервал проверки прокси (сек)
VIP_REWARD_DAYS = 1   # Сколько дней VIP давать за реферала

ASSETS = {
    "START": "privet.png",
    "PROFILE": "profile.png",
    "PROXY": "proxy.png",
    "REF": "refka.png",
    "ADMIN": "admin.png",
    "BROADCAST": "infa.png"
}

TIERS = {
    "BRONZE": {"min": 0, "name": "Базовый", "speed": "Стандартная"},
    "SILVER": {"min": 3, "name": "Продвинутый", "speed": "Повышенная"},
    "GOLD": {"min": 10, "name": "Профессиональный", "speed": "Высокая"},
    "PLATINUM": {"min": 25, "name": "Элитный", "speed": "Максимальная"},
    "DIAMOND": {"min": 50, "name": "Премиум", "speed": "Безлимитная"}
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SalutProxySystem")

# ==============================================================================
#                               DATABASE MANAGER
# ==============================================================================

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def connect(self):
        return sqlite3.connect(self.db_path)

    def init_tables(self):
        conn = self.connect()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                referrer_id INTEGER,
                refs_count INTEGER DEFAULT 0,
                is_vip_permanent INTEGER DEFAULT 0,
                vip_expires_at TIMESTAMP DEFAULT NULL,
                country_pref TEXT DEFAULT 'Мир',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS proxies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server TEXT NOT NULL,
                port INTEGER NOT NULL,
                secret TEXT NOT NULL,
                country TEXT DEFAULT 'Европа',
                ping INTEGER DEFAULT 9999,
                is_active INTEGER DEFAULT 1,
                UNIQUE(server, port)
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("База данных успешно инициализирована.")

    def execute(self, query: str, args: tuple = (), commit: bool = False):
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(query, args)
            if commit:
                conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"SQL Execute Error: {e}")
            return False
        finally:
            conn.close()

    def fetch_all(self, query: str, args: tuple = ()):
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(query, args)
            return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"SQL FetchAll Error: {e}")
            return []
        finally:
            conn.close()

    def fetch_one(self, query: str, args: tuple = ()):
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(query, args)
            return cursor.fetchone()
        except sqlite3.Error as e:
            logger.error(f"SQL FetchOne Error: {e}")
            return None
        finally:
            conn.close()

# ==============================================================================
#                               UTILS & HELPERS
# ==============================================================================

class Utils:
    @staticmethod
    def check_files():
        missing = []
        for name in ASSETS.values():
            if not os.path.exists(name):
                missing.append(name)
        if missing:
            logger.warning(f"ОТСУТСТВУЮТ ФАЙЛЫ: {', '.join(missing)}. Убедитесь, что они лежат в папке с ботом!")
        
        if not os.path.exists(PROXY_FILE):
            with open(PROXY_FILE, "w", encoding="utf-8") as f:
                f.write("")

    @staticmethod
    def guess_country(ip: str) -> str:
        if ip.startswith(("85.", "46.", "78.", "31.")): return "🇩🇪 Германия"
        if ip.startswith(("176.", "185.", "95.")): return "🇳🇱 Нидерланды"
        if ip.startswith(("83.", "163.", "51.")): return "🇫🇷 Франция"
        if ip.startswith(("5.", "178.")): return "🇬🇧 Великобритания"
        if ip.startswith(("45.", "92.", "193.", "194.")): return "🇷🇺 Россия"
        if ip.startswith(("91.", "104.")): return "🇺🇸 США"
        return "🇪🇺 Европа"

    @staticmethod
    def format_vip_time(vip_str: str) -> str:
        if not vip_str: return "Не активен"
        try:
            end_date = datetime.strptime(vip_str, "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            if end_date < now: return "Истек"
            delta = end_date - now
            return f"{delta.days}д {delta.seconds // 3600}ч"
        except: return "Ошибка"

# ==============================================================================
#                               PROXY MANAGER
# ==============================================================================

class ProxyManager:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def add_from_link(self, link: str) -> bool:
        match = re.search(r'server=([^&#\s]+)&port=(\d+)&secret=([^&#\s]+)', link)
        if match:
            server, port, secret = match.groups()
            country = Utils.guess_country(server)
            return self.db.execute(
                "INSERT OR IGNORE INTO proxies (server, port, secret, country) VALUES (?, ?, ?, ?)",
                (server, int(port), secret, country),
                commit=True
            )
        return False

    def load_from_file(self) -> int:
        if not os.path.exists(PROXY_FILE): return 0
        count = 0
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if "proxy?" in line:
                    if self.add_from_link(line.strip()):
                        count += 1
        return count

    async def ping_single(self, host: str, port: int) -> int:
        try:
            start_time = time.time()
            future = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(future, timeout=1.5)
            ping_ms = int((time.time() - start_time) * 1000)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return ping_ms
        except Exception: 
            return 9999

    async def update_all_pings(self):
        proxies = self.db.fetch_all("SELECT id, server, port FROM proxies")
        if not proxies: return
        tasks = [self._process_ping(pid, server, port) for pid, server, port in proxies]
        chunk_size = 50
        for i in range(0, len(tasks), chunk_size):
            await asyncio.gather(*tasks[i:i + chunk_size])

    async def _process_ping(self, pid, server, port):
        ping = await self.ping_single(server, port)
        self.db.execute("UPDATE proxies SET ping = ? WHERE id = ?", (ping, pid), commit=True)

    def get_best_proxy(self, tier_name: str, country_pref: str) -> tuple:
        query = "SELECT server, port, secret, country, ping FROM proxies WHERE ping < 9999"
        params = []
        if country_pref != "Мир":
            query += " AND country LIKE ?"
            params.append(f"%{country_pref}%")
        query += " ORDER BY ping ASC"
        
        proxies = self.db.fetch_all(query, tuple(params))
        note = ""
        
        if not proxies:
            proxies = self.db.fetch_all("SELECT server, port, secret, country, ping FROM proxies WHERE ping < 9999 ORDER BY ping ASC")
            note = "\n<i>Внимание: в выбранном регионе нет активных узлов. Назначен альтернативный маршрут.</i>"
            if not proxies: return None, "В данный момент активные узлы недоступны."

        total = len(proxies)
        index = -1 
        
        if tier_name in ["Premium+ 👑", "Премиум"]: index = 0
        elif tier_name == "Элитный": index = min(total - 1, int(total * 0.05))
        elif tier_name == "Профессиональный": index = min(total - 1, int(total * 0.20))
        elif tier_name == "Продвинутый": index = min(total - 1, int(total * 0.50))
        else:
            start_bad = int(total * 0.8)
            index = min(total - 1, start_bad + random.randint(0, total - start_bad - 1) if total - start_bad - 1 > 0 else 0)

        return proxies[index], note

# ==============================================================================
#                               USER MANAGER
# ==============================================================================

class UserManager:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def register(self, user_id: int, username: str, referrer_id: int = None):
        if not self.db.fetch_one("SELECT user_id FROM users WHERE user_id = ?", (user_id,)):
            if referrer_id == user_id: referrer_id = None
            self.db.execute(
                "INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)",
                (user_id, username, referrer_id), commit=True
            )
            if referrer_id: self.process_referral_reward(referrer_id)
            return True
        return False

    def process_referral_reward(self, referrer_id: int):
        self.db.execute("UPDATE users SET refs_count = refs_count + 1 WHERE user_id = ?", (referrer_id,), commit=True)
        user = self.db.fetch_one("SELECT vip_expires_at FROM users WHERE user_id = ?", (referrer_id,))
        if user:
            current_vip = user[0]
            now = datetime.now()
            if current_vip:
                try:
                    curr_date = datetime.strptime(current_vip, "%Y-%m-%d %H:%M:%S")
                    new_date = max(now, curr_date) + timedelta(days=VIP_REWARD_DAYS)
                except: new_date = now + timedelta(days=VIP_REWARD_DAYS)
            else:
                new_date = now + timedelta(days=VIP_REWARD_DAYS)
            
            self.db.execute("UPDATE users SET vip_expires_at = ? WHERE user_id = ?", 
                           (new_date.strftime("%Y-%m-%d %H:%M:%S"), referrer_id), commit=True)

    def get_info(self, user_id: int):
        return self.db.fetch_one(
            "SELECT refs_count, is_vip_permanent, vip_expires_at, country_pref FROM users WHERE user_id = ?", 
            (user_id,)
        )

    def get_tier_info(self, user_id: int):
        if user_id == ADMIN_ID:
            return {"name": "Premium+ 👑", "speed": "Абсолютная (Unrestricted)"}
            
        info = self.get_info(user_id)
        if not info: return TIERS["BRONZE"]
        refs, is_perm, vip_end, _ = info
        
        is_vip = False
        if is_perm: is_vip = True
        elif vip_end:
            try:
                if datetime.strptime(vip_end, "%Y-%m-%d %H:%M:%S") > datetime.now(): is_vip = True
            except: pass

        if is_vip: return TIERS["DIAMOND"]
        for t_key in reversed(list(TIERS.keys())):
            if refs >= TIERS[t_key]["min"]: return TIERS[t_key]
        return TIERS["BRONZE"]

    def set_pref(self, user_id: int, country: str):
        self.db.execute("UPDATE users SET country_pref = ? WHERE user_id = ?", (country, user_id), commit=True)

# ==============================================================================
#                               KEYBOARDS & HANDLERS
# ==============================================================================

class Keyboards:
    @staticmethod
    def start():
        kb = InlineKeyboardBuilder()
        kb.button(text="Вход в систему", callback_data="profile")
        return kb.as_markup()

    @staticmethod
    def profile(is_admin: bool, country: str):
        kb = InlineKeyboardBuilder()
        kb.button(text="🚀 Подключить Proxy", callback_data="get_proxy")
        kb.button(text=f"🌐 Локация: {country}", callback_data="filter_menu")
        kb.button(text="💎 Привилегии", callback_data="privileges")
        kb.button(text="🛒 Купить Premium", callback_data="buy_premium")
        kb.button(text="👥 Партнерская сеть", callback_data="referrals")
        kb.button(text="💬 Поддержка", callback_data="support")
        kb.button(text="👑 Администрация", callback_data="admins_list")
        kb.button(text="ℹ️ Информация", callback_data="about")
        if is_admin: 
            kb.button(text="⚙️ Управление (Админ)", callback_data="admin_panel")
            kb.adjust(1, 1, 2, 2, 2, 1)
        else:
            kb.adjust(1, 1, 2, 2, 2)
        return kb.as_markup()

    @staticmethod
    def locations():
        kb = InlineKeyboardBuilder()
        locs = ["Мир", "Германия", "Нидерланды", "Франция", "Россия", "США", "Великобритания"]
        for loc in locs: kb.button(text=loc, callback_data=f"set_loc_{loc}")
        kb.button(text="Вернуться", callback_data="profile")
        kb.adjust(2)
        return kb.as_markup()

    @staticmethod
    def payment():
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Я оплатил(а)", callback_data="support")
        kb.button(text="Вернуться", callback_data="profile")
        kb.adjust(1)
        return kb.as_markup()

    @staticmethod
    def admin():
        kb = InlineKeyboardBuilder()
        kb.button(text="📂 Загрузить из TXT", callback_data="adm_load")
        kb.button(text="➕ Добавить текстом", callback_data="adm_add")
        kb.button(text="🗑 Очистить базу", callback_data="adm_del")
        kb.button(text="📢 Рассылка", callback_data="adm_broadcast")
        kb.button(text="📊 Статистика", callback_data="adm_stats")
        kb.button(text="Вернуться", callback_data="profile")
        kb.adjust(2, 2, 1, 1)
        return kb.as_markup()

    @staticmethod
    def back(to="profile"):
        return InlineKeyboardBuilder().button(text="Вернуться", callback_data=to).as_markup()

    @staticmethod
    def proxy_result(url):
        kb = InlineKeyboardBuilder()
        kb.button(text="🔗 Соединиться", url=url)
        kb.button(text="Вернуться", callback_data="profile")
        return kb.as_markup()

class States(StatesGroup):
    support_msg = State()
    support_reply = State()
    admin_add_proxy = State()
    admin_broadcast = State()

db = DatabaseManager(DB_NAME)
pm = ProxyManager(db)
um = UserManager(db)
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

@router.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    username = message.from_user.username or "User"
    args = message.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    
    if um.register(uid, username, ref_id) and ref_id:
        try: await bot.send_message(ref_id, f"✦ <b>Новый участник в вашей сети!</b> +1 день Premium.")
        except: pass

    try: await message.delete()
    except: pass
    
    await message.answer_photo(
        photo=FSInputFile(ASSETS["START"]),
        caption=f"🛡 <b>{PROXY_NAME}</b>\n\nПриветствуем, <b>{message.from_user.first_name}</b>!",
        reply_markup=Keyboards.start(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "profile")
async def show_profile(call: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = call.from_user.id
    info = um.get_info(uid)
    if not info:
        um.register(uid, call.from_user.username)
        info = um.get_info(uid)

    refs, _, vip_end, pref = info
    tier = um.get_tier_info(uid)
    vip_status = Utils.format_vip_time(vip_end)
    if uid == ADMIN_ID: vip_status = "Бессрочный"
    is_admin = (uid == ADMIN_ID)
    
    text = (f"👤 <b>КАБИНЕТ ПОЛЬЗОВАТЕЛЯ</b>\n\n"
            f"⌗ ID: <code>{uid}</code>\n"
            f"✦ Статус: <b>{tier['name']}</b>\n"
            f"🛡 Привилегии: <b>{vip_status}</b>\n"
            f"👥 Партнерская сеть: <b>{refs}</b>\n"
            f"🌐 Маршрутизация: <b>{pref}</b>\n\n"
            f"⚡ Скорость соединения: <i>{tier['speed']}</i>")
    
    await try_edit(call, ASSETS["PROFILE"], text, Keyboards.profile(is_admin, pref))

@router.callback_query(F.data == "get_proxy")
async def get_proxy_handler(call: CallbackQuery):
    uid = call.from_user.id
    info = um.get_info(uid)
    proxy, error = pm.get_best_proxy(um.get_tier_info(uid)["name"], info[3])
    
    if not proxy: return await call.answer(error, show_alert=True)
    server, port, secret, country, ping = proxy
    
    text = (f"🛡 <b>СОЕДИНЕНИЕ УСТАНОВЛЕНО</b>\n\n"
            f"▻ Локация: <b>{country}</b>\n"
            f"▻ Отклик: <b>{ping} ms</b>\n"
            f"▻ Протокол: <b>MTProto Secret</b>\n"
            f"{error}\n\n"
            f"<i>Используйте кнопку ниже для автоматической настройки клиента:</i>")
    
    await try_edit(call, ASSETS["PROXY"], text, Keyboards.proxy_result(f"https://t.me/proxy?server={server}&port={port}&secret={secret}"))

@router.callback_query(F.data == "filter_menu")
async def filter_handler(call: CallbackQuery):
    await try_edit(call, ASSETS["PROXY"], "🌐 <b>Конфигурация маршрутов</b>\nУкажите предпочитаемый регион подключения:", Keyboards.locations())

@router.callback_query(F.data.startswith("set_loc_"))
async def set_location(call: CallbackQuery):
    um.set_pref(call.from_user.id, call.data.split("_")[2])
    await call.answer(f"✓ Конфигурация обновлена: {call.data.split('_')[2]}")
    await show_profile(call, None)

@router.callback_query(F.data == "privileges")
async def privileges_handler(call: CallbackQuery):
    uid = call.from_user.id
    tier = um.get_tier_info(uid)
    text = (f"💎 <b>СИСТЕМА ПРИВИЛЕГИЙ</b>\n\n"
            f"Ваш текущий статус: <b>{tier['name']}</b>\n\n"
            f"<b>Уровни доступа:</b>\n"
            f"▫️ <b>Базовый</b> (0 рефералов) - <i>Стандартная скорость</i>\n"
            f"▫️ <b>Продвинутый</b> (3 реф.) - <i>Повышенная скорость</i>\n"
            f"▫️ <b>Профессиональный</b> (10 реф.) - <i>Высокая скорость</i>\n"
            f"▫️ <b>Элитный</b> (25 реф.) - <i>Максимальная скорость</i>\n"
            f"▫️ <b>Премиум</b> (50 реф. или покупка) - <i>Безлимитная скорость</i>\n"
            f"👑 <b>Premium+</b> - <i>Высший уровень (Только Администрация)</i>\n\n"
            f"<i>Приглашайте друзей, чтобы повышать свой статус автоматически!</i>")
    await try_edit(call, ASSETS["PROFILE"], text, Keyboards.back())

@router.callback_query(F.data == "buy_premium")
async def buy_premium_handler(call: CallbackQuery):
    text = (f"🛒 <b>ПОКУПКА PREMIUM ДОСТУПА</b>\n\n"
            f"Получите максимальную скорость и приоритет без приглашения рефералов!\n\n"
            f"Стоимость: <b>$5 / месяц</b>\n"
            f"Способ оплаты: <b>CryptoBot (USDT/TON)</b>\n\n"
            f"<i>Интеграция бота автоматической оплаты в процессе разработки...</i>\n\n"
            f"Для ручной оплаты переведите средства на следующий TRC20 адрес:\n"
            f"<code>Txxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>\n\n"
            f"<i>После оплаты нажмите кнопку ниже или отправьте скриншот в поддержку.</i>")
    await try_edit(call, ASSETS["PROXY"], text, Keyboards.payment())

@router.callback_query(F.data == "admins_list")
async def admins_list_handler(call: CallbackQuery):
    text = (f"👑 <b>АДМИНИСТРАЦИЯ ПРОЕКТА</b>\n\n"
            f"Наша команда следит за стабильностью работы серверов и высоким качеством предоставляемых услуг.\n\n"
            f"<b>Владелец и Главный Администратор:</b>\n"
            f"👤 <a href='tg://user?id={ADMIN_ID}'>Xenon (Premium+)</a>\n\n"
            f"<i>По всем деловым предложениям обращайтесь напрямую. Для решения технических проблем используйте раздел \"Поддержка\".</i>")
    await try_edit(call, ASSETS["ADMIN"], text, Keyboards.back())

@router.callback_query(F.data == "referrals")
async def referrals_handler(call: CallbackQuery):
    uid = call.from_user.id
    info = um.get_info(uid)
    bot_info = await bot.get_me()
    text = (f"🔗 <b>ПАРТНЕРСКАЯ СЕТЬ</b>\n\n"
            f"Условия: <b>1 приглашенный = +1 день Premium</b>\n\n"
            f"Персональная ссылка:\n<code>https://t.me/{bot_info.username}?start={uid}</code>\n\n"
            f"Привлечено участников: <b>{info[0]}</b>")
    await try_edit(call, ASSETS["REF"], text, Keyboards.back())

@router.callback_query(F.data == "support")
async def support_handler(call: CallbackQuery, state: FSMContext):
    await try_edit(call, ASSETS["PROXY"], "💬 <b>СЛУЖБА ПОДДЕРЖКИ</b>\n\nСформулируйте ваше обращение (вопрос, проблема или подтверждение оплаты) <b>одним сообщением</b>:", Keyboards.back())
    await state.set_state(States.support_msg)

@router.message(StateFilter(States.support_msg))
async def support_receive(message: Message, state: FSMContext):
    uid = message.from_user.id
    kb = InlineKeyboardBuilder().button(text="Ответить", callback_data=f"reply_{uid}").as_markup()
    try:
        await bot.send_message(ADMIN_ID, f"📞 <b>Обращение {uid} (@{message.from_user.username}):</b>\n\n{message.text}", reply_markup=kb, parse_mode="HTML")
        await message.answer("✓ <b>Запрос зарегистрирован.</b> Ожидайте ответа специалиста.", parse_mode="HTML")
    except Exception as e: 
        logger.error(f"Support msg error: {e}")
    await state.clear()
    
@router.callback_query(F.data.startswith("reply_"))
async def admin_reply_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    user_to_reply = call.data.split('_')[1]
    await call.message.answer(f"Напишите ответ для пользователя {user_to_reply}:")
    await state.update_data(tid=int(user_to_reply))
    await state.set_state(States.support_reply)

@router.message(StateFilter(States.support_reply))
async def admin_reply_send(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['tid'], f"📩 <b>Ответ поддержки:</b>\n\n{message.text}", parse_mode="HTML")
        await message.answer("✅ Сообщение успешно доставлено пользователю.")
    except Exception: 
        await message.answer("❌ Ошибка доставки. Возможно пользователь заблокировал бота.")
    await state.clear()

@router.callback_query(F.data == "admin_panel")
async def admin_panel(call: CallbackQuery):
    if call.from_user.id == ADMIN_ID:
        await try_edit(call, ASSETS["ADMIN"], "⚙️ <b>СИСТЕМНАЯ ПАНЕЛЬ</b>\nДоступ: Утвержден", Keyboards.admin())

@router.callback_query(F.data == "adm_load")
async def adm_load(call: CallbackQuery):
    c = pm.load_from_file()
    await call.answer(f"✅ Успешно считано и добавлено: {c} прокси", show_alert=True)

@router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    uc = db.fetch_one("SELECT COUNT(*) FROM users")[0]
    pc = db.fetch_one("SELECT COUNT(*) FROM proxies")[0]
    pa = db.fetch_one("SELECT COUNT(*) FROM proxies WHERE ping < 9999")[0]
    await call.answer(f"📊 СТАТИСТИКА:\n\nПользователей: {uc}\nВсего прокси: {pc}\nЖивых прокси: {pa}", show_alert=True)

@router.callback_query(F.data == "adm_add")
async def adm_add(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Отправь список прокси (можно просто скопировать и вставить ссылки tg:// или https://):")
    await state.set_state(States.admin_add_proxy)

@router.message(StateFilter(States.admin_add_proxy))
async def adm_add_proc(message: Message, state: FSMContext):
    ls = re.findall(r'(?:tg://|https://t\.me/)proxy\?[^\s]+', message.text)
    c = sum(1 for l in ls if pm.add_from_link(l))
    await message.answer(f"✅ Успешно добавлено {c} прокси из текста.")
    await state.clear()

@router.callback_query(F.data == "adm_broadcast")
async def adm_cast(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Отправь текст для рассылки всем пользователям (поддерживается HTML-разметка):")
    await state.set_state(States.admin_broadcast)

@router.message(StateFilter(States.admin_broadcast))
async def adm_cast_proc(message: Message, state: FSMContext):
    users = db.fetch_all("SELECT user_id FROM users")
    c = 0
    ph = FSInputFile(ASSETS["BROADCAST"])
    msg = await message.answer("Рассылка началась...")
    
    for u in users:
        try:
            await bot.send_photo(u[0], ph, caption=message.text, parse_mode="HTML")
            c += 1
            await asyncio.sleep(0.05) # Защита от спам-блока Telegram (20 сообщений в секунду макс)
        except Exception: 
            pass # Пользователь заблокировал бота
            
    await msg.edit_text(f"✅ Рассылка завершена!\nУспешно доставлено: {c} пользователям.")
    await state.clear()

@router.callback_query(F.data == "adm_del")
async def adm_del(call: CallbackQuery):
    db.execute("DELETE FROM proxies", commit=True)
    await call.answer("✓ База прокси очищена.", show_alert=True)

@router.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    await try_edit(call, ASSETS["PROXY"], "ℹ️ <b>О СИСТЕМЕ</b>\n\nПлатформа предоставляет доступ к защищенным MTProto серверам.\nРазвивайте партнерскую сеть для получения привилегированного доступа к элитным узлам без ограничений скорости.", Keyboards.back())

async def try_edit(call: CallbackQuery, photo: str, cap: str, kb):
    try: 
        await call.message.edit_media(
            media=InputMediaPhoto(media=FSInputFile(photo), caption=cap, parse_mode="HTML"), 
            reply_markup=kb
        )
    except TelegramBadRequest as e: 
        if "message is not modified" not in str(e):
            logger.error(f"Edit Media Error: {e}")

async def ping_loop():
    logger.info("Запущен фоновый пинг прокси...")
    while True:
        try:
            await asyncio.sleep(PING_INTERVAL)
            logger.info("Обновление пинга...")
            await pm.update_all_pings()
        except Exception as e: 
            logger.error(f"Ping loop error: {e}")

async def main():
    print(f"\n🚀 SALUT PROXY STARTED | ADMIN: {ADMIN_ID}")
    
    # Проверка наличия картинок
    Utils.check_files()
    
    # Инициализация БД
    db.init_tables()
    
    # Загрузка прокси из файла при старте (игнорирует дубликаты)
    c = pm.load_from_file()
    if c > 0:
        logger.info(f"Loaded {c} new proxies from file.")
        
    logger.info("Initial ping check...")
    await pm.update_all_pings()
    
    # Запуск фонового пинга
    asyncio.create_task(ping_loop())
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: 
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
    except Exception as e:
        logger.error(f"CRASH: {e}")