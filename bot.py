import asyncio
import re
import sqlite3
import time
from datetime import datetime
from collections import defaultdict
from typing import Optional, Dict, List, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton, FSInputFile
)

# === ВСТАВЬ СВОИ ДАННЫЕ ЗДЕСЬ ===
BOT_TOKEN = "8721549582:AAEjWx3asUMppBgriWu_ppnFegDdPAlAATU"
ADMIN_GROUP_ID = -5219194459  # ID группы (с минусом если группа)
MAIN_ADMIN_ID = 8721549582
# ===============================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# База данных
conn = sqlite3.connect("anonymous_chat.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    phone TEXT,
    username TEXT,
    age INTEGER,
    gender TEXT,
    search_gender TEXT,
    search_age_min INTEGER,
    search_age_max INTEGER,
    is_searching INTEGER DEFAULT 0,
    current_chat_num INTEGER DEFAULT NULL,
    warnings INTEGER DEFAULT 0,
    banned_until INTEGER DEFAULT 0,
    perm_banned INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS active_chats (
    chat_num INTEGER PRIMARY KEY AUTOINCREMENT,
    user1 INTEGER,
    user2 INTEGER,
    created_at TIMESTAMP,
    messages TEXT DEFAULT ''
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_num INTEGER,
    reporter_id INTEGER,
    reported_id INTEGER,
    reason TEXT,
    created_at TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    is_main INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS banned_ids (
    user_id INTEGER PRIMARY KEY,
    reason TEXT,
    until INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('admin_group_id', ?)", (str(ADMIN_GROUP_ID),))
cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('next_chat_num', '1')")
conn.commit()

cursor.execute("INSERT OR IGNORE INTO admins (user_id, is_main) VALUES (?, 1)", (MAIN_ADMIN_ID,))
conn.commit()

STOP_WORDS = re.compile(r"(реклама|скидка|магазин|цена|купить|продам|наркотики|закладка|меф|скорость|соль|трава|сигна|бот|заработок|пассивный доход|работа на дому)", re.IGNORECASE)

user_msg_history: Dict[int, List[Tuple[str, float]]] = defaultdict(list)

class RegisterState(StatesGroup):
    waiting_phone = State()
    waiting_age = State()
    waiting_gender = State()
    waiting_search_gender = State()
    waiting_search_age_min = State()
    waiting_search_age_max = State()

class ReportState(StatesGroup):
    waiting_reason = State()
    waiting_photos = State()

class AppealState(StatesGroup):
    waiting_text = State()

class BroadcastState(StatesGroup):
    waiting_text = State()
    waiting_photo = State()

def get_admin_group() -> int:
    cursor.execute("SELECT value FROM config WHERE key = 'admin_group_id'")
    res = cursor.fetchone()
    return int(res[0]) if res else ADMIN_GROUP_ID

def get_next_chat_num() -> int:
    cursor.execute("SELECT value FROM config WHERE key = 'next_chat_num'")
    res = cursor.fetchone()
    return int(res[0]) if res else 1

def increment_chat_num() -> int:
    num = get_next_chat_num()
    cursor.execute("UPDATE config SET value = ? WHERE key = 'next_chat_num'", (str(num + 1),))
    conn.commit()
    return num

def is_admin(user_id: int) -> bool:
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

def is_main_admin(user_id: int) -> bool:
    cursor.execute("SELECT is_main FROM admins WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    return res and res[0] == 1

def is_banned(user_id: int):
    cursor.execute("SELECT reason, until FROM banned_ids WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if res:
        reason, until = res
        if until == 0 or until > time.time():
            return True, reason
    cursor.execute("SELECT banned_until, perm_banned FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if res:
        banned_until, perm_banned = res
        if perm_banned:
            return True, "перманентно забанен"
        if banned_until > time.time():
            return True, f"забанен до {datetime.fromtimestamp(banned_until)}"
    return False, ""

def check_spam(user_id: int, text: str) -> bool:
    now = time.time()
    history = user_msg_history[user_id]
    user_msg_history[user_id] = [(msg, t) for msg, t in history if now - t < 6]
    same_count = sum(1 for msg, _ in user_msg_history[user_id] if msg == text)
    if same_count >= 3:
        user_msg_history[user_id].clear()
        return True
    user_msg_history[user_id].append((text, now))
    return False

def save_chat_history(chat_num: int, user1: int, user2: int, messages: str):
    filename = f"chat_{chat_num}_{user1}_{user2}_{int(time.time())}.txt"
    async def send():
        admin_group = get_admin_group()
        if admin_group:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"ЧАТ #{chat_num}\nПользователь1: {user1}\nПользователь2: {user2}\n\n{messages}")
            await bot.send_document(admin_group, FSInputFile(filename), caption=f"📝 История чата #{chat_num}")
            import os
            os.remove(filename)
    asyncio.create_task(send())

def create_chat(user1: int, user2: int) -> int:
    chat_num = get_next_chat_num()
    increment_chat_num()
    cursor.execute("INSERT INTO active_chats (chat_num, user1, user2, created_at, messages) VALUES (?, ?, ?, ?, '')",
                   (chat_num, user1, user2, datetime.now()))
    cursor.execute("UPDATE users SET current_chat_num = ?, is_searching = 0 WHERE user_id IN (?, ?)", 
                   (chat_num, user1, user2))
    conn.commit()
    return chat_num

def get_chat_partner(user_id: int):
    cursor.execute("SELECT chat_num, user1, user2 FROM active_chats WHERE user1 = ? OR user2 = ?", (user_id, user_id))
    res = cursor.fetchone()
    if res:
        chat_num, user1, user2 = res
        return (user2 if user1 == user_id else user1), chat_num
    return None, None

def add_message_to_chat(chat_num: int, user_id: int, text: str):
    cursor.execute("SELECT messages FROM active_chats WHERE chat_num = ?", (chat_num,))
    res = cursor.fetchone()
    if res:
        new_msg = f"[{datetime.now().strftime('%H:%M')}] {user_id}: {text}\n"
        cursor.execute("UPDATE active_chats SET messages = messages || ? WHERE chat_num = ?", (new_msg, chat_num))
        conn.commit()

def end_chat(chat_num: int):
    cursor.execute("SELECT user1, user2, messages FROM active_chats WHERE chat_num = ?", (chat_num,))
    res = cursor.fetchone()
    if res:
        user1, user2, messages = res
        cursor.execute("UPDATE users SET current_chat_num = NULL, is_searching = 1 WHERE user_id IN (?, ?)", (user1, user2))
        cursor.execute("DELETE FROM active_chats WHERE chat_num = ?", (chat_num,))
        conn.commit()
        save_chat_history(chat_num, user1, user2, messages)

def find_match(user_id: int) -> Optional[int]:
    cursor.execute("SELECT age, gender, search_gender, search_age_min, search_age_max FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        return None
    age, gender, search_gender, search_age_min, search_age_max = user
    banned, _ = is_banned(user_id)
    if banned:
        return None
    cursor.execute("""
        SELECT user_id FROM users 
        WHERE user_id != ? 
        AND is_searching = 1 
        AND current_chat_num IS NULL
        AND gender = ?
        AND age BETWEEN ? AND ?
        AND (search_gender = ? OR search_gender = 'any')
        AND (search_gender = ? OR ? = 'any')
    """, (user_id, search_gender, search_age_min, search_age_max, gender, gender, gender))
    match = cursor.fetchone()
    if match:
        banned2, _ = is_banned(match[0])
        if not banned2:
            return match[0]
    return None

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    banned, reason = is_banned(user_id)
    if banned:
        await message.answer(f"❌ Вы заблокированы: {reason}\n/appeal")
        return
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        await message.answer("✅ Вы зарегистрированы! /search — искать, /help")
        return
    await message.answer("🔐 Добро пожаловать! Поделитесь номером (+7949):",
                         reply_markup=ReplyKeyboardMarkup(
                             keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
                             resize_keyboard=True))
    await state.set_state(RegisterState.waiting_phone)

@dp.message(RegisterState.waiting_phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    if not phone.startswith("+7949"):
        await message.answer("❌ Только +7949 (Енакиево). /start")
        await state.clear()
        return
    await state.update_data(phone=phone, username=message.from_user.username or "")
    await message.answer("📅 Ваш возраст (10-40):")
    await state.set_state(RegisterState.waiting_age)

@dp.message(RegisterState.waiting_age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or not (10 <= int(message.text) <= 40):
        await message.answer("❌ Число от 10 до 40")
        return
    await state.update_data(age=int(message.text))
    kb = ReplyKeyboardMarkup(keyboard=[["Мужской", "Женский"]], resize_keyboard=True)
    await message.answer("🚻 Ваш пол:", reply_markup=kb)
    await state.set_state(RegisterState.waiting_gender)

@dp.message(RegisterState.waiting_gender, F.text.in_(["Мужской", "Женский"]))
async def process_gender(message: types.Message, state: FSMContext):
    gender = "male" if message.text == "Мужской" else "female"
    await state.update_data(gender=gender)
    kb = ReplyKeyboardMarkup(keyboard=[["Мужской", "Женский", "Любой"]], resize_keyboard=True)
    await message.answer("🔍 Кого ищете?", reply_markup=kb)
    await state.set_state(RegisterState.waiting_search_gender)

@dp.message(RegisterState.waiting_search_gender, F.text.in_(["Мужской", "Женский", "Любой"]))
async def process_search_gender(message: types.Message, state: FSMContext):
    text = message.text
    search_gender = "male" if text == "Мужской" else "female" if text == "Женский" else "any"
    await state.update_data(search_gender=search_gender)
    await message.answer("🔢 Мин. возраст (10-40):")
    await state.set_state(RegisterState.waiting_search_age_min)

@dp.message(RegisterState.waiting_search_age_min)
async def process_search_age_min(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or not (10 <= int(message.text) <= 40):
        await message.answer("❌ Число от 10 до 40")
        return
    await state.update_data(search_age_min=int(message.text))
    await message.answer("🔢 Макс. возраст (10-40):")
    await state.set_state(RegisterState.waiting_search_age_max)

@dp.message(RegisterState.waiting_search_age_max)
async def process_search_age_max(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or not (10 <= int(message.text) <= 40):
        await message.answer("❌ Число от 10 до 40")
        return
    data = await state.get_data()
    if int(message.text) < data["search_age_min"]:
        await message.answer("❌ Макс. возраст меньше мин.")
        return
    await state.update_data(search_age_max=int(message.text))
    data = await state.get_data()
    cursor.execute("""
        INSERT INTO users (user_id, phone, username, age, gender, search_gender, search_age_min, search_age_max, is_searching)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (message.from_user.id, data["phone"], data["username"], data["age"], data["gender"],
          data["search_gender"], data["search_age_min"], data["search_age_max"]))
    conn.commit()
    await message.answer("✅ Регистрация завершена! /search", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("/search — поиск\n/stop — выйти\n/next — след.\n/report — жалоба\n/help — это меню")

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    user_id = message.from_user.id
    banned, reason = is_banned(user_id)
    if banned:
        await message.answer(f"❌ Блокировка: {reason}")
        return
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        await message.answer("❌ Сначала /start")
        return
    cursor.execute("UPDATE users SET is_searching = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    await message.answer("⏳ Ищу... (15 сек)")
    for _ in range(15):
        await asyncio.sleep(1)
        partner = find_match(user_id)
        if partner:
            chat_num = create_chat(user_id, partner)
            await bot.send_message(user_id, "🎉 Собеседник найден!\n/next /report /stop")
            await bot.send_message(partner, "🎉 Собеседник найден!\n/next /report /stop")
            return
    cursor.execute("UPDATE users SET is_searching = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    await message.answer("😔 Не найдено. Попробуйте позже")

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    user_id = message.from_user.id
    partner, chat_num = get_chat_partner(user_id)
    if chat_num:
        if partner:
            await bot.send_message(partner, "👋 Собеседник покинул чат. /search")
        end_chat(chat_num)
    await message.answer("✅ Вы вышли. /search")

@dp.message(Command("next"))
async def cmd_next(message: types.Message):
    await cmd_stop(message)
    await cmd_search(message)

@dp.message(Command("report"))
async def cmd_report(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    partner, chat_num = get_chat_partner(user_id)
    if not chat_num:
        await message.answer("❌ Вы не в чате")
        return
    await state.update_data(report_chat_num=chat_num, report_partner=partner)
    await message.answer("📝 Причина жалобы:")
    await state.set_state(ReportState.waiting_reason)

@dp.message(ReportState.waiting_reason)
async def process_report_reason(message: types.Message, state: FSMContext):
    if len(message.text) < 5:
        await message.answer("❌ Минимум 5 символов")
        return
    await state.update_data(report_reason=message.text, report_photos=[])
    await message.answer("📸 Фото (0-15 шт). /готово")
    await state.set_state(ReportState.waiting_photos)

@dp.message(ReportState.waiting_photos, F.photo)
async def process_report_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("report_photos", [])
    if len(photos) >= 15:
        await message.answer("❌ Макс. 15 фото. /готово")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(report_photos=photos)
    await message.answer(f"✅ {len(photos)}/15. Ещё или /готово")

@dp.message(ReportState.waiting_photos, Command("готово"))
async def finalize_report(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if "report_reason" not in data:
        await message.answer("❌ Сначала причина")
        return
    chat_num = data["report_chat_num"]
    partner = data["report_partner"]
    reason = data["report_reason"]
    photos = data.get("report_photos", [])
    cursor.execute("INSERT INTO reports (chat_num, reporter_id, reported_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                   (chat_num, message.from_user.id, partner, reason, datetime.now()))
    conn.commit()
    cursor.execute("SELECT messages FROM active_chats WHERE chat_num = ?", (chat_num,))
    res = cursor.fetchone()
    history = res[0] if res else ""
    admin_group = get_admin_group()
    if admin_group:
        filename = f"report_{chat_num}_{int(time.time())}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"ЖАЛОБА #{chat_num}\nОт: {message.from_user.id}\nНа: {partner}\nПричина: {reason}\n\nИСТОРИЯ:\n{history}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔨 Бан нарушителя", callback_data=f"ban_{partner}_{chat_num}")],
            [InlineKeyboardButton(text="⚠️ Бан отправителя", callback_data=f"ban_{message.from_user.id}_{chat_num}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{chat_num}")]
        ])
        await bot.send_document(admin_group, FSInputFile(filename), caption=f"⚠️ #reports #id={partner} #chat_id={chat_num}\n{reason}", reply_markup=kb)
        import os
        os.remove(filename)
        for photo in photos:
            await bot.send_photo(admin_group, photo)
    await message.answer("✅ Жалоба отправлена")
    await state.clear()

@dp.message(Command("appeal"))
async def cmd_appeal(message: types.Message, state: FSMContext):
    await message.answer("📝 Текст апелляции:")
    await state.set_state(AppealState.waiting_text)

@dp.message(AppealState.waiting_text)
async def process_appeal(message: types.Message, state: FSMContext):
    admin_group = get_admin_group()
    if admin_group:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Разбанить", callback_data=f"unban_{message.from_user.id}")]])
        await bot.send_message(admin_group, f"📨 Апелляция от {message.from_user.id}:\n{message.text}", reply_markup=kb)
    await message.answer("✅ Апелляция отправлена")
    await state.clear()

@dp.message()
async def handle_chat_messages(message: types.Message):
    user_id = message.from_user.id
    partner, chat_num = get_chat_partner(user_id)
    if not chat_num:
        return
    if check_spam(user_id, message.text):
        cursor.execute("UPDATE users SET banned_until = ? WHERE user_id = ?", (int(time.time() + 3600), user_id))
        conn.commit()
        await bot.send_message(user_id, "⛔ Бан 1 час за спам")
        if partner:
            await bot.send_message(partner, "👋 Собеседник забанен. /search")
        end_chat(chat_num)
        admin_group = get_admin_group()
        if admin_group:
            await bot.send_message(admin_group, f"⚠️ Авто-бан спамера: {user_id}")
        return
    if STOP_WORDS.search(message.text):
        await message.answer("⚠️ Подозрительное сообщение. /report если это реклама")
    if "@" in message.text:
        await message.answer("⚠️ @ деанонимизирует вас!")
    add_message_to_chat(chat_num, user_id, message.text)
    if partner:
        try:
            await bot.send_message(partner, f"💬 {message.text}")
        except:
            pass

@dp.message(Command("help_admin"))
async def cmd_help_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("/profile [ID] — пробив\n/forcechat [ID] — чат с пользователем\n/broadcast — рассылка (гл.админ)\n/setname — имя бота (гл.админ)\n/preban — пребан (гл.админ)")

@dp.message(Command("add_admin_group"))
async def cmd_add_admin_group(message: types.Message):
    if not is_main_admin(message.from_user.id):
        return
    admin_group = message.chat.id
    cursor.execute("UPDATE config SET value = ? WHERE key = 'admin_group_id'", (str(admin_group),))
    conn.commit()
    awa
@dp.message(Command("add_admin_group"))
async def cmd_add_admin_group(message: types.Message):
    if not is_main_admin(message.from_user.id):
        return
    set_admin_group(message.chat.id)
    await message.answer(f"✅ Группа привязана")

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ /profile ID")
        return
    target_id = int(parts[1])
    cursor.execute("SELECT phone, username, age, gender, warnings, banned_until, perm_banned FROM users WHERE user_id = ?", (target_id,))
    user = cursor.fetchone()
    if not user:
        await message.answer("❌ Не найден")
        return
    phone, username, age, gender, warnings, banned_until, perm_banned = user
    cursor.execute("SELECT messages FROM active_chats WHERE user1 = ? OR user2 = ?", (target_id, target_id))
    chats = cursor.fetchall()
    word_count = defaultdict(int)
    for (msg,) in chats:
        words = re.findall(r'\b\w+\b', msg.lower())
        for w in words:
            if len(w) > 2:
                word_count[w] += 1
    top_words = sorted(word_count.items(), key=lambda x: x[1], reverse=True)[:10]
    text = f"📊 ПРОФИЛЬ {target_id}\n📞 {phone}\n👤 @{username}\n🎂 {age}\n🚻 {'Мужской' if gender=='male' else 'Женский'}\n⚠️ Предупреждения: {warnings}/3\nСлова: {', '.join([f'{w}({c})' for w,c in top_words])}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📜 История чатов", callback_data=f"get_chats_{target_id}")]])
    await message.answer(text, reply_markup=kb)

@dp.message(Command("forcechat"))
async def cmd_forcechat(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ /forcechat ID")
        return
    target_id = int(parts[1])
    admin_id = message.from_user.id
    partner, chat_num = get_chat_partner(admin_id)
    if chat_num:
        await message.answer("❌ Вы уже в чате. /stop")
        return
    cursor.execute("SELECT is_searching, current_chat_num FROM users WHERE user_id = ?", (target_id,))
    res = cursor.fetchone()
    if not res:
        await message.answer("❌ Пользователь не найден")
        return
    is_searching, current_chat = res
    if current_chat:
        await message.answer("⏳ Пользователь в чате, жду...")
        while True:
            await asyncio.sleep(2)
            cursor.execute("SELECT current_chat_num FROM users WHERE user_id = ?", (target_id,))
            res = cursor.fetchone()
            if not res or not res[0]:
                break
    cursor.execute("UPDATE users SET is_searching = 1 WHERE user_id = ?", (target_id,))
    conn.commit()
    chat_num = create_chat(admin_id, target_id)
    await bot.send_message(admin_id, f"✅ Чат с {target_id} установлен")
    await bot.send_message(target_id, "🎉 Собеседник найден!")

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if not is_main_admin(message.from_user.id):
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("❌ /broadcast Текст")
        return
    await state.update_data(broadcast_text=text)
    await message.answer("Пришлите фото или /skip")
    await state.set_state(BroadcastState.waiting_photo)

@dp.message(BroadcastState.waiting_photo, F.photo)
async def process_broadcast_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    text = data["broadcast_text"]
    photo = message.photo[-1].file_id
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    count = 0
    for (uid,) in users:
        try:
            await bot.send_photo(uid, photo, caption=text)
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"✅ Отправлено {count} пользователям")
    await state.clear()

@dp.message(BroadcastState.waiting_photo, Command("skip"))
async def skip_broadcast_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    text = data["broadcast_text"]
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    count = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, f"📢 {text}")
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"✅ Отправлено {count} пользователям")
    await state.clear()

@dp.message(Command("setname"))
async def cmd_setname(message: types.Message):
    if not is_main_admin(message.from_user.id):
        return
    new_name = message.text.replace("/setname", "").strip()
    if not new_name:
        await message.answer("❌ /setname Имя")
        return
    await bot.set_my_name(new_name)
    await message.answer(f"✅ Имя изменено: {new_name}")

@dp.message(Command("preban"))
async def cmd_preban(message: types.Message):
    if not is_main_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("❌ /preban ID [причина]")
        return
    target_id = int(parts[1])
    reason = parts[2] if len(parts) > 2 else "без причины"
    cursor.execute("INSERT OR REPLACE INTO banned_ids (user_id, reason, until) VALUES (?, ?, 0)", (target_id, reason))
    conn.commit()
    await message.answer(f"✅ {target_id} забанен: {reason}")

@dp.callback_query()
async def handle_callbacks(callback: types.CallbackQuery):
    data = callback.data
    if data.startswith("ban_"):
        parts = data.split("_")
        user_id = int(parts[1])
        chat_num = int(parts[2])
        cursor.execute("UPDATE users SET perm_banned = 1, is_searching = 0, current_chat_num = NULL WHERE user_id = ?", (user_id,))
        cursor.execute("SELECT user1, user2 FROM active_chats WHERE chat_num = ?", (chat_num,))
        res = cursor.fetchone()
        if res:
            user1, user2 = res
            cursor.execute("UPDATE users SET current_chat_num = NULL WHERE user_id IN (?, ?)", (user1, user2))
            cursor.execute("DELETE FROM active_chats WHERE chat_num = ?", (chat_num,))
            try:
                await bot.send_message(user1, "⛔ Чат завершён админом")
                await bot.send_message(user2, "⛔ Чат завершён админом")
            except:
                pass
        conn.commit()
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ Нарушитель забанен")
        await callback.answer()
    elif data.startswith("reject_"):
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ Отклонено")
        await callback.answer()
    elif data.startswith("unban_"):
        user_id = int(data.split("_")[1])
        cursor.execute("UPDATE users SET banned_until = 0, warnings = 0, perm_banned = 0 WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM banned_ids WHERE user_id = ?", (user_id,))
        conn.commit()
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ Разбанен")
        try:
            await bot.send_message(user_id, "✅ Бан снят по апелляции")
        except:
            pass
        await callback.answer()
    elif data.startswith("get_chats_"):
        target_id = int(data.split("_")[2])
        cursor.execute("SELECT chat_num, user1, user2, messages FROM active_chats WHERE user1 = ? OR user2 = ? ORDER BY chat_num DESC LIMIT 5", (target_id, target_id))
        chats = cursor.fetchall()
        if not chats:
            await callback.answer("Нет истории")
            return
        for chat_num, user1, user2, messages in chats:
            filename = f"history_{target_id}_{chat_num}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"Чат #{chat_num}\n{user1} — {user2}\n\n{messages}")
            await callback.message.answer_document(FSInputFile(filename), caption=f"Чат #{chat_num}")
            os.remove(filename)
        await callback.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
