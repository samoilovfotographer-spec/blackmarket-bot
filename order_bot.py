"""
ORDER BOT — BlackMarket 🧢
=====================================
Механика:
- Кнопка «Заказать» → бот собирает контакт и отправляет заявку админу
- Репост рилса в Instagram → скидка 5% (после ручного /approve)
- Каждый приведённый друг → +1% скидки (без ограничения по кол-ву друзей)
- Максимальная итоговая скидка: 10%

Фото дропа:
- /setphoto — отправить следующее фото как обложку дропа
- /soldout — пометить как распродано (метка держится SOLD_OUT_DAYS дней)
- /newdrop — начать новый дроп (сбросить sold out)

Установка:
    pip install python-telegram-bot==20.7

Запуск:
    python order_bot.py
"""

import json
import os
import logging
import traceback
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes,
)

# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

BOT_TOKEN = "8555954260:AAF3Ek0cSwgxZGljHhU_rCKizzPq7TQXJ6o"
ADMIN_ID       = 246653066
ADMIN_CONTACT  = "@Samoilov_Stanislav"
BOT_USERNAME   = "blackmarket_drop_bot"   # без @

PRODUCT_NAME   = "Бейсболка BlackMarket"
PRODUCT_PRICE  = 19_000                   # тг (число для расчёта скидки)
INSTAGRAM      = "@13bm.kz"
REEL_LINK      = "https://www.instagram.com/reel/ССЫЛКА_НА_РИЛС"

DISCOUNT_REEL  = 5     # % за репост рилса
DISCOUNT_REF   = 1     # % за каждого приведённого друга
MAX_DISCOUNT   = 10    # % максимальная итоговая скидка

SOLD_OUT_DAYS  = 3     # сколько дней показывать пометку SOLD OUT

DATA_FILE = "orders_data.json"

# Состояния ConversationHandler для заказа
ASK_NAME, ASK_PHONE, ASK_ADDRESS = range(3)

# ─── ДАННЫЕ ──────────────────────────────────────────────────────────────────

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"users": {}, "orders": []}

    # Убедиться что секция drop есть
    if "drop" not in data:
        data["drop"] = {
            "photo_file_id": None,
            "sold_out": False,
            "sold_out_at": None,
        }
    return data

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_or_create_user(data, user):
    uid = str(user.id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "id": uid,
            "name": user.full_name,
            "username": user.username or "",
            "referrals": 0,
            "referred_by": None,
            "reel_verified": False,
            "reel_pending": False,
        }
    return data["users"][uid]

def calc_discount(u):
    d = 0
    if u.get("reel_verified"):
        d += DISCOUNT_REEL
    d += u.get("referrals", 0) * DISCOUNT_REF
    return min(d, MAX_DISCOUNT)

def final_price(discount_pct):
    discounted = PRODUCT_PRICE * (1 - discount_pct / 100)
    return round(discounted)

def is_sold_out(data):
    """Проверяет статус sold out с автоматическим снятием по истечении SOLD_OUT_DAYS."""
    drop = data.get("drop", {})
    if not drop.get("sold_out"):
        return False
    sold_at_str = drop.get("sold_out_at")
    if sold_at_str:
        try:
            sold_dt = datetime.fromisoformat(sold_at_str)
            if datetime.now(timezone.utc) - sold_dt > timedelta(days=SOLD_OUT_DAYS):
                # Срок истёк — автоматически снимаем метку
                drop["sold_out"] = False
                drop["sold_out_at"] = None
                save_data(data)
                return False
        except Exception:
            pass
    return True

# ─── КЛАВИАТУРЫ ──────────────────────────────────────────────────────────────

def main_kb(sold_out=False):
    if sold_out:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Распродано", callback_data="soldout_info")],
            [InlineKeyboardButton("🔗 Пригласить друга — скидка 1%", callback_data="ref")],
            [InlineKeyboardButton("💰 Моя скидка", callback_data="discount")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Заказать", callback_data="order")],
        [InlineKeyboardButton("🎬 Репост рилса — скидка 5%", callback_data="reel")],
        [InlineKeyboardButton("🔗 Пригласить друга — скидка 1%", callback_data="ref")],
        [InlineKeyboardButton("💰 Моя скидка", callback_data="discount")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В меню", callback_data="menu")]])

def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]])

# ─── ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: отправить меню ─────────────────────────────────

async def send_menu(update_or_message, context, uid, data=None):
    """
    Отправляет главное меню:
    — Если есть фото дропа, сначала отправляем фото с подписью,
      затем текстовое сообщение с кнопками (два отдельных сообщения).
    — Если фото нет — только текстовое сообщение.
    """
    if data is None:
        data = load_data()

    u = data["users"].get(uid)
    if u is None:
        return

    discount  = calc_discount(u)
    price     = final_price(discount)
    sold_out  = is_sold_out(data)
    drop      = data.get("drop", {})
    photo_id  = drop.get("photo_file_id")

    # ── Фото бейсболки ────────────────────────────────────────────────────────
    if photo_id:
        if sold_out:
            photo_caption = (
                f"🧢 *{PRODUCT_NAME}*\n\n"
                f"🔴 *SOLD OUT* — бейсболки распроданы\n"
                f"_Следи за новыми дропами на {INSTAGRAM}_"
            )
        else:
            photo_caption = f"🧢 *{PRODUCT_NAME}* — дроп активен 🔥"

        target = update_or_message if hasattr(update_or_message, "reply_photo") else update_or_message
        await target.reply_photo(
            photo=photo_id,
            caption=photo_caption,
            parse_mode="Markdown"
        )

    # ── Текстовое меню ────────────────────────────────────────────────────────
    if sold_out:
        text = (
            f"*{PRODUCT_NAME}*\n\n"
            f"🔴 *Эта партия распродана*\n\n"
            f"Скидки копятся — ждут следующего дропа 👇\n"
            f"🎁 Твоя накопленная скидка: *{discount}%*"
        )
    else:
        text = (
            f"*{PRODUCT_NAME}*\n\n"
            f"💰 Цена: *{PRODUCT_PRICE:,} тг*\n"
            f"📦 В наличии\n\n"
            f"🎁 Твоя скидка: *{discount}%*"
            + (f" → итого *{price:,} тг*" if discount > 0 else "") +
            f"\n\nМожешь увеличить скидку:\n"
            f"— Репост рилса в Instagram → *−{DISCOUNT_REEL}%*\n"
            f"— Каждый приведённый друг → *−{DISCOUNT_REF}%*\n"
            f"_(максимум {MAX_DISCOUNT}% итого)_"
        )

    await target.reply_text(text, parse_mode="Markdown", reply_markup=main_kb(sold_out))

# ─── СТАРТ ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = update.effective_user
    uid  = str(user.id)
    is_new = uid not in data["users"]

    u = get_or_create_user(data, user)

    # Реферальная ссылка
    if is_new and context.args:
        ref = context.args[0]
        if ref != uid and ref in data["users"]:
            u["referred_by"] = ref
            data["users"][ref]["referrals"] += 1
            save_data(data)
            ref_discount = calc_discount(data["users"][ref])
            try:
                await context.bot.send_message(
                    chat_id=int(ref),
                    text=(
                        f"🎉 По твоей ссылке пришёл новый друг!\n\n"
                        f"👥 Всего друзей: *{data['users'][ref]['referrals']}*\n"
                        f"💰 Твоя скидка теперь: *{ref_discount}%*"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    save_data(data)
    data = load_data()

    if is_new:
        await update.message.reply_text("👋 Привет! Это официальный бот дропов BlackMarket 🧢")

    await send_menu(update.message, context, uid, data)

# ─── КНОПКИ ГЛАВНОГО МЕНЮ ────────────────────────────────────────────────────

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = load_data()
    uid  = str(q.from_user.id)
    u    = get_or_create_user(data, q.from_user)
    save_data(data)

    sold_out = is_sold_out(data)
    discount = calc_discount(u)
    price    = final_price(discount)

    if q.data == "menu":
        if sold_out:
            text = (
                f"*{PRODUCT_NAME}*\n\n"
                f"🔴 *Эта партия распродана*\n\n"
                f"🎁 Твоя накопленная скидка: *{discount}%*"
            )
        else:
            text = (
                f"*{PRODUCT_NAME}*\n\n"
                f"💰 Цена: *{PRODUCT_PRICE:,} тг*\n"
                f"🎁 Твоя скидка: *{discount}%*"
                + (f" → итого *{price:,} тг*" if discount > 0 else "") +
                f"\n\nМожешь увеличить скидку:\n"
                f"— Репост рилса → *−{DISCOUNT_REEL}%*\n"
                f"— Каждый друг → *−{DISCOUNT_REF}%*\n"
                f"_(максимум {MAX_DISCOUNT}%)_"
            )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb(sold_out))

    elif q.data == "soldout_info":
        await q.answer("🔴 Эта партия распродана. Следи за новыми дропами!", show_alert=True)

    elif q.data == "discount":
        reel_status = (
            "✅ подтверждён" if u.get("reel_verified")
            else ("⏳ на проверке" if u.get("reel_pending") else "❌ не отправлен")
        )
        text = (
            f"💰 *Твоя скидка*\n\n"
            f"🎬 Репост рилса: {reel_status} → *{DISCOUNT_REEL if u.get('reel_verified') else 0}%*\n"
            f"👥 Приглашённых друзей: *{u['referrals']}* → *{min(u['referrals'] * DISCOUNT_REF, MAX_DISCOUNT)}%*\n\n"
            f"📊 Итого: *{discount}%*"
            + (f"\n💵 Цена с учётом скидки: *{price:,} тг*" if discount > 0 and not sold_out else "")
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

    elif q.data == "ref":
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        text = (
            f"🔗 *Пригласи друга — получи −{DISCOUNT_REF}% за каждого*\n\n"
            f"Твоя ссылка:\n`{link}`\n\n"
            f"Скопируй и отправь другу. Как только он нажмёт /start — "
            f"скидка автоматически добавится.\n\n"
            f"👥 Уже приглашено: *{u['referrals']}* чел.\n"
            f"🎁 Скидка за друзей: *{min(u['referrals'] * DISCOUNT_REF, MAX_DISCOUNT)}%*"
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

    elif q.data == "reel":
        if u.get("reel_verified"):
            text = f"✅ *Репост уже подтверждён!*\n\nСкидка *{DISCOUNT_REEL}%* зачислена."
        elif u.get("reel_pending"):
            text = "⏳ *Скрин уже на проверке.*\n\nАдмин рассмотрит в ближайшее время."
        else:
            text = (
                f"🎬 *Репост рилса = скидка {DISCOUNT_REEL}%*\n\n"
                f"Что нужно сделать:\n"
                f"1. Открой рилс: {REEL_LINK}\n"
                f"2. Поделись им в своих сторис с упоминанием *{INSTAGRAM}*\n"
                f"3. Сделай скриншот и отправь его *следующим сообщением*\n\n"
                f"После проверки придёт уведомление ✅"
            )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

# ─── ЗАКАЗ (ConversationHandler) ─────────────────────────────────────────────

async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = load_data()

    # Блокировать заказ если sold out
    if is_sold_out(data):
        await q.answer("🔴 Эта партия распродана. Следи за новыми дропами!", show_alert=True)
        return ConversationHandler.END

    uid = str(q.from_user.id)
    u   = get_or_create_user(data, q.from_user)
    save_data(data)
    discount = calc_discount(u)
    price    = final_price(discount)

    text = (
        f"🛒 *Оформление заказа*\n\n"
        f"Товар: *{PRODUCT_NAME}*\n"
        f"Цена: *{PRODUCT_PRICE:,} тг*"
        + (f"\n🎁 Скидка: *{discount}%* → итого *{price:,} тг*" if discount > 0 else "") +
        f"\n\nКак тебя зовут? _(Имя и фамилия)_"
    )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_name"] = update.message.text.strip()
    await update.message.reply_text(
        "📱 Укажи номер телефона для связи:",
        reply_markup=cancel_kb()
    )
    return ASK_PHONE

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_phone"] = update.message.text.strip()
    await update.message.reply_text(
        "📍 Укажи адрес доставки или напиши «Самовывоз»:",
        reply_markup=cancel_kb()
    )
    return ASK_ADDRESS

async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = update.effective_user
    uid  = str(user.id)
    u    = get_or_create_user(data, user)
    save_data(data)

    discount = calc_discount(u)
    price    = final_price(discount)
    address  = update.message.text.strip()

    order = {
        "user_id":      uid,
        "name":         context.user_data.get("order_name"),
        "phone":        context.user_data.get("order_phone"),
        "address":      address,
        "discount":     discount,
        "price":        price,
        "tg_name":      user.full_name,
        "tg_username":  f"@{user.username}" if user.username else "нет @",
    }
    data["orders"].append(order)
    save_data(data)

    await update.message.reply_text(
        f"✅ *Заявка принята!*\n\n"
        f"📦 {PRODUCT_NAME}\n"
        f"👤 {order['name']}\n"
        f"📱 {order['phone']}\n"
        f"📍 {order['address']}\n"
        f"💰 К оплате: *{price:,} тг*"
        + (f" (скидка {discount}%)" if discount > 0 else "") +
        f"\n\n{ADMIN_CONTACT} свяжется с тобой в ближайшее время 🙌",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

    admin_text = (
        f"🛒 *Новый заказ!*\n\n"
        f"👤 {order['tg_name']} ({order['tg_username']})\n"
        f"🆔 ID: `{uid}`\n"
        f"📋 Имя: {order['name']}\n"
        f"📱 Телефон: {order['phone']}\n"
        f"📍 Адрес: {order['address']}\n"
        f"💰 Цена: *{price:,} тг*"
        + (f" (скидка {discount}%)" if discount > 0 else "")
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="Markdown")
    except Exception:
        pass

    return ConversationHandler.END

async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = load_data()
    uid  = str(q.from_user.id)
    u    = get_or_create_user(data, q.from_user)
    save_data(data)

    sold_out = is_sold_out(data)
    discount = calc_discount(u)
    price    = final_price(discount)

    text = (
        f"*{PRODUCT_NAME}*\n\n"
        f"💰 Цена: *{PRODUCT_PRICE:,} тг*\n"
        f"🎁 Твоя скидка: *{discount}%*"
        + (f" → итого *{price:,} тг*" if discount > 0 else "") +
        f"\n\nМожешь увеличить скидку:\n"
        f"— Репост рилса → *−{DISCOUNT_REEL}%*\n"
        f"— Каждый друг → *−{DISCOUNT_REF}%*\n"
        f"_(максимум {MAX_DISCOUNT}%)_"
    )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb(sold_out))
    return ConversationHandler.END

# ─── СКРИН РИЛСА / ФОТО ──────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    uid   = str(user.id)
    photo = update.message.photo[-1]

    # ── Админ загружает фото дропа ────────────────────────────────────────────
    if user.id == ADMIN_ID and context.user_data.get("awaiting_drop_photo"):
        context.user_data["awaiting_drop_photo"] = False
        data = load_data()
        data["drop"]["photo_file_id"] = photo.file_id
        save_data(data)
        await update.message.reply_text(
            "✅ *Фото дропа сохранено!*\n\n"
            "Теперь оно будет показываться всем пользователям при открытии бота.",
            parse_mode="Markdown"
        )
        return

    # ── Обычный пользователь присылает скрин репоста ──────────────────────────
    data = load_data()
    u    = get_or_create_user(data, user)

    if u.get("reel_verified"):
        await update.message.reply_text(f"✅ Репост уже подтверждён! Скидка {DISCOUNT_REEL}% зачислена.")
        return
    if u.get("reel_pending"):
        await update.message.reply_text("⏳ Скрин уже на проверке, не нужно присылать повторно.")
        return

    u["reel_pending"]  = True
    u["reel_photo_id"] = photo.file_id
    save_data(data)

    await update.message.reply_text(
        "✅ Скрин получен! Ждём подтверждения от админа.\nКак проверят — придёт уведомление.",
        reply_markup=main_kb()
    )

    uname   = f"@{u['username']}" if u["username"] else "нет @"
    caption = (
        f"📸 *Новый скрин репоста*\n\n"
        f"👤 {u['name']} ({uname})\n"
        f"🆔 ID: `{uid}`\n\n"
        f"✅ Подтвердить: `/approve {uid}`\n"
        f"❌ Отклонить: `/decline {uid}`"
    )
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID, photo=photo.file_id,
            caption=caption, parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"Не удалось отправить фото админу: {e}")

# ─── АДМИН — ДРО П ───────────────────────────────────────────────────────────

async def admin_set_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setphoto — следующее фото станет обложкой дропа"""
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data["awaiting_drop_photo"] = True
    await update.message.reply_text(
        "📷 Жду фото бейсболки.\n\nОтправь картинку следующим сообщением — она появится у всех пользователей."
    )

async def admin_sold_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/soldout — пометить партию как распроданную"""
    if update.effective_user.id != ADMIN_ID:
        return
    data = load_data()
    data["drop"]["sold_out"]    = True
    data["drop"]["sold_out_at"] = datetime.now(timezone.utc).isoformat()
    save_data(data)
    await update.message.reply_text(
        f"🔴 *Sold out активирован.*\n\n"
        f"Пользователи увидят метку «Распродано» в течение {SOLD_OUT_DAYS} дней.\n"
        f"Кнопка «Заказать» заблокирована.",
        parse_mode="Markdown"
    )

async def admin_new_drop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/newdrop — начать новый дроп (сбросить sold out и все скидки)"""
    if update.effective_user.id != ADMIN_ID:
        return
    data = load_data()

    # Сбросить sold out
    data["drop"]["sold_out"]    = False
    data["drop"]["sold_out_at"] = None

    # Сбросить скидки у всех пользователей
    reset_count = 0
    for u in data["users"].values():
        u["reel_verified"] = False
        u["reel_pending"]  = False
        u.pop("reel_photo_id", None)
        u["referrals"]     = 0
        u["referred_by"]   = None
        reset_count += 1

    save_data(data)
    await update.message.reply_text(
        f"✅ *Новый дроп запущен!*\n\n"
        f"— Sold out снят\n"
        f"— Скидки сброшены у *{reset_count}* участников\n"
        f"— Реферальные связи обнулены\n\n"
        f"Кнопка «Заказать» снова активна.\n"
        f"Чтобы обновить фото — отправь /setphoto",
        parse_mode="Markdown"
    )

async def admin_drop_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dropstatus — текущее состояние дропа"""
    if update.effective_user.id != ADMIN_ID:
        return
    data     = load_data()
    drop     = data.get("drop", {})
    sold_out = is_sold_out(data)
    has_photo = "✅ загружено" if drop.get("photo_file_id") else "❌ не загружено"

    if sold_out:
        sold_str = f"🔴 Sold out (с {drop.get('sold_out_at', '?')[:10]})"
    else:
        sold_str = "🟢 В продаже"

    text = (
        f"📊 *Статус дропа*\n\n"
        f"🖼 Фото: {has_photo}\n"
        f"📦 Статус: {sold_str}\n\n"
        f"Команды:\n"
        f"/setphoto — загрузить/заменить фото\n"
        f"/soldout — пометить как распродано\n"
        f"/newdrop — начать новый дроп"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

    # Показать текущее фото если есть
    if drop.get("photo_file_id"):
        await update.message.reply_photo(
            photo=drop["photo_file_id"],
            caption="📷 Текущее фото дропа"
        )

# ─── АДМИН — РЕПОСТЫ И ЗАКАЗЫ ────────────────────────────────────────────────

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /approve USER_ID")
        return
    target = context.args[0]
    data   = load_data()
    if target not in data["users"]:
        await update.message.reply_text(f"❌ Пользователь {target} не найден.")
        return
    u = data["users"][target]
    if u.get("reel_verified"):
        await update.message.reply_text("⚠️ Уже подтверждён ранее.")
        return
    u["reel_verified"] = True
    u["reel_pending"]  = False
    save_data(data)
    discount = calc_discount(u)
    price    = final_price(discount)
    await update.message.reply_text(f"✅ Подтверждено! {u['name']} — скидка теперь {discount}%.")
    try:
        await context.bot.send_message(
            chat_id=int(target),
            text=(
                f"🎉 *Репост подтверждён!*\n\n"
                f"Скидка *{DISCOUNT_REEL}%* зачислена.\n"
                f"Твоя итоговая скидка: *{discount}%*"
                + (f"\n💵 Цена с учётом скидки: *{price:,} тг*" if discount > 0 else "") +
                f"\n\nНажми «Заказать» чтобы оформить 🛒"
            ),
            parse_mode="Markdown",
            reply_markup=main_kb()
        )
    except Exception:
        pass

async def admin_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /decline USER_ID")
        return
    target = context.args[0]
    data   = load_data()
    if target not in data["users"]:
        await update.message.reply_text(f"❌ Пользователь {target} не найден.")
        return
    data["users"][target]["reel_pending"] = False
    data["users"][target].pop("reel_photo_id", None)
    save_data(data)
    await update.message.reply_text(f"❌ Скрин отклонён. {data['users'][target]['name']} может прислать новый.")
    try:
        await context.bot.send_message(
            chat_id=int(target),
            text=(
                f"😔 *Скрин не прошёл проверку*\n\n"
                f"Скорее всего не видно упоминания {INSTAGRAM} или сторис уже удалена.\n\n"
                f"Попробуй ещё раз — нажми кнопку «🎬 Репост рилса» и пришли новый скрин."
            ),
            parse_mode="Markdown",
            reply_markup=main_kb()
        )
    except Exception:
        pass

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    data    = load_data()
    pending = [u for u in data["users"].values() if u.get("reel_pending")]
    if not pending:
        await update.message.reply_text("✅ Нет скринов на проверке.")
        return
    text = f"⏳ *Скрины на проверке ({len(pending)}):*\n\n"
    for u in pending:
        uname = f"@{u['username']}" if u["username"] else "нет @"
        text += f"• {u['name']} ({uname}) — `/approve {u['id']}` | `/decline {u['id']}`\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    data   = load_data()
    orders = data.get("orders", [])
    if not orders:
        await update.message.reply_text("Заказов пока нет.")
        return
    text = f"📦 *Заказы ({len(orders)} всего):*\n\n"
    for i, o in enumerate(orders[-20:], 1):
        text += (
            f"{i}. {o['tg_name']} ({o['tg_username']})\n"
            f"   {o['name']} · {o['phone']} · {o['address']}\n"
            f"   💰 {o['price']:,} тг"
            + (f" (−{o['discount']}%)" if o['discount'] > 0 else "") + "\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    data          = load_data()
    users         = list(data["users"].values())
    orders        = data.get("orders", [])
    total_revenue = sum(o["price"] for o in orders)
    verified      = sum(1 for u in users if u.get("reel_verified"))
    pending       = sum(1 for u in users if u.get("reel_pending"))
    text = (
        f"📊 *Статистика*\n\n"
        f"👥 Пользователей: *{len(users)}*\n"
        f"🛒 Заказов: *{len(orders)}*\n"
        f"💵 Выручка: *{total_revenue:,} тг*\n"
        f"🎬 Репостов подтверждено: *{verified}*\n"
        f"⏳ Репостов на проверке: *{pending}*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = (
        f"🛠 *Команды админа:*\n\n"
        f"*— Дроп —*\n"
        f"/setphoto — загрузить фото бейсболки (следующее фото)\n"
        f"/soldout — пометить партию как распроданную\n"
        f"/newdrop — начать новый дроп (сбросить sold out)\n"
        f"/dropstatus — текущее состояние дропа\n\n"
        f"*— Заказы и репосты —*\n"
        f"/stats — статистика\n"
        f"/orders — список заказов\n"
        f"/pending — скрины репостов на проверке\n"
        f"/approve ID — подтвердить репост → скидка {DISCOUNT_REEL}%\n"
        f"/decline ID — отклонить скрин\n"
        f"/broadcast текст — рассылка всем\n\n"
        f"*— Скидки —*\n"
        f"Репост рилса: {DISCOUNT_REEL}% (ручное подтверждение)\n"
        f"Каждый друг: {DISCOUNT_REF}% (авто)\n"
        f"Максимум: {MAX_DISCOUNT}%"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /broadcast текст сообщения")
        return
    msg  = " ".join(context.args)
    data = load_data()
    sent = 0
    for uid in data["users"]:
        try:
            await context.bot.send_message(chat_id=int(uid), text=msg, parse_mode="Markdown")
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Отправлено {sent} участникам")

# ─── ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Ловит все необработанные исключения и отправляет в личку админу."""
    error_text = "".join(traceback.format_exception(
        type(context.error), context.error, context.error.__traceback__
    ))

    # Краткая инфа об update (откуда пришла ошибка)
    if isinstance(update, Update):
        user = update.effective_user
        user_info = f"{user.full_name} (ID: {user.id}, @{user.username})" if user else "неизвестный"
        msg_text  = ""
        if update.message and update.message.text:
            msg_text = f"\nСообщение: `{update.message.text[:200]}`"
        elif update.callback_query:
            msg_text = f"\nКнопка: `{update.callback_query.data}`"
        source = f"Пользователь: {user_info}{msg_text}"
    else:
        source = "Update недоступен"

    # Обрезаем трейсбек если слишком длинный (лимит Telegram — 4096 символов)
    max_tb = 2800
    if len(error_text) > max_tb:
        error_text = error_text[:max_tb] + "\n... (обрезано)"

    report = (
        f"🔴 *Ошибка в боте*\n\n"
        f"{source}\n\n"
        f"```\n{error_text}\n```"
    )

    logging.error(f"Exception:\n{error_text}")

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=report,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить ошибку админу: {e}")

# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    app = Application.builder().token(BOT_TOKEN).build()

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(order_start, pattern="^order$")],
        states={
            ASK_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_address)],
        },
        fallbacks=[CallbackQueryHandler(cancel_order, pattern="^cancel_order$")],
    )

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(order_conv)
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Заказы / репосты
    app.add_handler(CommandHandler("stats",      admin_stats))
    app.add_handler(CommandHandler("orders",     admin_orders))
    app.add_handler(CommandHandler("pending",    admin_pending))
    app.add_handler(CommandHandler("approve",    admin_approve))
    app.add_handler(CommandHandler("decline",    admin_decline))
    app.add_handler(CommandHandler("broadcast",  admin_broadcast))

    # Дроп
    app.add_handler(CommandHandler("setphoto",   admin_set_photo))
    app.add_handler(CommandHandler("soldout",    admin_sold_out))
    app.add_handler(CommandHandler("newdrop",    admin_new_drop))
    app.add_handler(CommandHandler("dropstatus", admin_drop_status))

    app.add_handler(CommandHandler("adminhelp",  admin_help))

    app.add_error_handler(error_handler)

    print("🧢 ORDER BOT запущен! Напиши /adminhelp для команд.")
    app.run_polling()

if __name__ == "__main__":
    main()
