"""
ORDER BOT — BlackMarket 🧢  v2.0
=====================================
Что нового в v2.0:
- Несколько дропов одновременно (каждый со своим фото, ценой, статусом)
- Поддержка альбомов: можно загрузить сразу несколько фото к дропу
- Пользователь выбирает дроп из списка если их несколько

Команды админа:
  /newdrop Название | Цена   — создать новый дроп (цена необязательна)
  /listdrops                  — список всех дропов с их ID
  /setphoto ID                — загрузить фото(альбом) для дропа
  /soldout ID                 — пометить дроп как распроданный
  /undosoldout ID             — снять sold out
  /closedrop ID               — закрыть/скрыть дроп
  /dropstatus ID              — статус конкретного дропа
  /stats, /orders, /pending   — как раньше
  /approve ID, /decline ID    — подтвердить/отклонить репост
  /broadcast текст            — рассылка всем

Установка:
    pip install python-telegram-bot==20.7

Запуск:
    python drop_bot.py
"""

import json, os, logging, traceback, asyncio
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes,
)

# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

BOT_TOKEN      = "8555954260:AAF3Ek0cSwgxZGljHhU_rCKizzPq7TQXJ6o"
ADMIN_ID       = 246653066
ADMIN_CONTACT  = "@Samoilov_Stanislav"
BOT_USERNAME   = "blackmarket_drop_bot"

INSTAGRAM      = "@13bm.kz"
REEL_LINK      = "https://www.instagram.com/reel/ССЫЛКА_НА_РИЛС"

DISCOUNT_REEL  = 5
DISCOUNT_REF   = 1
MAX_DISCOUNT   = 10
SOLD_OUT_DAYS  = 3

DATA_FILE = "orders_data.json"

# Состояния ConversationHandler
ASK_NAME, ASK_PHONE, ASK_ADDRESS = range(3)

# ─── ДАННЫЕ ──────────────────────────────────────────────────────────────────

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"users": {}, "orders": []}

    # Миграция старого формата: один drop → список drops
    if "drop" in data and "drops" not in data:
        old = data.pop("drop")
        migrated = {
            "id":            "drop_1",
            "name":          "BlackMarket Drop",
            "price":         19000,
            "photo_file_ids": [old["photo_file_id"]] if old.get("photo_file_id") else [],
            "sold_out":      old.get("sold_out", False),
            "sold_out_at":   old.get("sold_out_at"),
            "active":        True,
        }
        data["drops"] = [migrated]

    if "drops" not in data:
        data["drops"] = []

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
    d = (DISCOUNT_REEL if u.get("reel_verified") else 0) + u.get("referrals", 0) * DISCOUNT_REF
    return min(d, MAX_DISCOUNT)

def final_price(base_price, discount_pct):
    return round(base_price * (1 - discount_pct / 100))

def is_sold_out(drop):
    if not drop.get("sold_out"):
        return False
    sold_at_str = drop.get("sold_out_at")
    if sold_at_str:
        try:
            sold_dt = datetime.fromisoformat(sold_at_str)
            if datetime.now(timezone.utc) - sold_dt > timedelta(days=SOLD_OUT_DAYS):
                drop["sold_out"] = False
                drop["sold_out_at"] = None
                return False
        except Exception:
            pass
    return True

def get_active_drops(data):
    """Возвращает только активные (не скрытые) дропы."""
    return [d for d in data.get("drops", []) if d.get("active", True)]

def find_drop(data, drop_id):
    for d in data.get("drops", []):
        if d["id"] == drop_id:
            return d
    return None

def next_drop_id(data):
    ids = [d["id"] for d in data.get("drops", [])]
    n = len(ids) + 1
    while f"drop_{n}" in ids:
        n += 1
    return f"drop_{n}"

# ─── КЛАВИАТУРЫ ──────────────────────────────────────────────────────────────

def drops_kb(drops, uid):
    """Клавиатура выбора дропа."""
    rows = []
    for d in drops:
        sold = is_sold_out(d)
        label = f"{'🔴 ' if sold else '🔥 '}{d['name']} — {d['price']:,} тг"
        rows.append([InlineKeyboardButton(label, callback_data=f"drop_{d['id']}")])
    return InlineKeyboardMarkup(rows)

def main_kb(drop, sold_out=False):
    did = drop["id"]
    if sold_out:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Распродано", callback_data=f"soldout_info_{did}")],
            [InlineKeyboardButton("🔗 Пригласить друга — скидка 1%", callback_data="ref")],
            [InlineKeyboardButton("💰 Моя скидка", callback_data="discount")],
            [InlineKeyboardButton("⬅️ Все дропы", callback_data="drops_list")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Заказать", callback_data=f"order_{did}")],
        [InlineKeyboardButton("🎬 Репост рилса — скидка 5%", callback_data="reel")],
        [InlineKeyboardButton("🔗 Пригласить друга — скидка 1%", callback_data="ref")],
        [InlineKeyboardButton("💰 Моя скидка", callback_data="discount")],
        [InlineKeyboardButton("⬅️ Все дропы", callback_data="drops_list")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="drops_list")]])

def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]])

# ─── ВСПОМОГАТЕЛЬНЫЕ ─────────────────────────────────────────────────────────

async def send_drop_menu(target, context, uid, drop, data):
    """Отправляет меню конкретного дропа с фото(альбомом)."""
    u        = data["users"].get(uid)
    if not u:
        return
    sold_out = is_sold_out(drop)
    discount = calc_discount(u)
    price    = final_price(drop["price"], discount)

    photo_ids = drop.get("photo_file_ids", [])

    # Отправить альбом если несколько фото
    if len(photo_ids) > 1:
        from telegram import InputMediaPhoto
        media = [InputMediaPhoto(pid) for pid in photo_ids]
        await target.reply_media_group(media=media)
    elif len(photo_ids) == 1:
        if sold_out:
            cap = f"🧢 *{drop['name']}*\n\n🔴 *SOLD OUT*\n_Следи за новыми дропами на {INSTAGRAM}_"
        else:
            cap = f"🧢 *{drop['name']}* — дроп активен 🔥"
        await target.reply_photo(photo=photo_ids[0], caption=cap, parse_mode="Markdown")

    # Текстовое меню
    if sold_out:
        text = (
            f"*{drop['name']}*\n\n"
            f"🔴 *Эта партия распродана*\n\n"
            f"Скидки копятся — ждут следующего дропа 👇\n"
            f"🎁 Твоя накопленная скидка: *{discount}%*"
        )
    else:
        text = (
            f"*{drop['name']}*\n\n"
            f"💰 Цена: *{drop['price']:,} тг*\n"
            f"📦 В наличии\n\n"
            f"🎁 Твоя скидка: *{discount}%*"
            + (f" → итого *{price:,} тг*" if discount > 0 else "") +
            f"\n\nУвеличить скидку:\n"
            f"— Репост рилса в Instagram → *−{DISCOUNT_REEL}%*\n"
            f"— Каждый приведённый друг → *−{DISCOUNT_REF}%*\n"
            f"_(максимум {MAX_DISCOUNT}% итого)_"
        )

    await target.reply_text(text, parse_mode="Markdown", reply_markup=main_kb(drop, sold_out))

async def send_drops_list(target, context, uid, data):
    """Главный экран: список активных дропов."""
    active = get_active_drops(data)

    if not active:
        await target.reply_text(
            "🧢 *BlackMarket*\n\nАктивных дропов пока нет.\n"
            f"Следи за анонсами в Instagram: {INSTAGRAM}",
            parse_mode="Markdown"
        )
        return

    # Если дроп один — сразу показываем его меню
    if len(active) == 1:
        await send_drop_menu(target, context, uid, active[0], data)
        return

    # Несколько дропов — показываем список
    text = "🧢 *BlackMarket Drops*\n\nВыбери дроп:"
    await target.reply_text(text, parse_mode="Markdown", reply_markup=drops_kb(active, uid))

# ─── СТАРТ ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = update.effective_user
    uid  = str(user.id)
    is_new = uid not in data["users"]

    u = get_or_create_user(data, user)

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

    await send_drops_list(update.message, context, uid, data)

# ─── КНОПКИ ──────────────────────────────────────────────────────────────────

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = load_data()
    uid  = str(q.from_user.id)
    u    = get_or_create_user(data, q.from_user)
    save_data(data)

    cb = q.data

    # Список дропов
    if cb == "drops_list":
        active = get_active_drops(data)
        if not active:
            await q.edit_message_text("Активных дропов пока нет. Следи за анонсами!")
            return
        if len(active) == 1:
            # Один дроп — редактируем текст меню
            drop     = active[0]
            sold_out = is_sold_out(drop)
            discount = calc_discount(u)
            price    = final_price(drop["price"], discount)
            if sold_out:
                text = f"*{drop['name']}*\n\n🔴 *Распродано*\n\n🎁 Скидка: *{discount}%*"
            else:
                text = (
                    f"*{drop['name']}*\n\n💰 Цена: *{drop['price']:,} тг*\n"
                    f"🎁 Скидка: *{discount}%*"
                    + (f" → итого *{price:,} тг*" if discount > 0 else "")
                )
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb(drop, sold_out))
        else:
            await q.edit_message_text("🧢 *Выбери дроп:*", parse_mode="Markdown",
                                      reply_markup=drops_kb(active, uid))
        return

    # Выбор конкретного дропа
    if cb.startswith("drop_drop_"):
        drop_id = cb[len("drop_"):]   # убираем prefix "drop_"
        drop = find_drop(data, drop_id)
        if not drop:
            await q.answer("Дроп не найден", show_alert=True)
            return
        sold_out = is_sold_out(drop)
        discount = calc_discount(u)
        price    = final_price(drop["price"], discount)
        if sold_out:
            text = f"*{drop['name']}*\n\n🔴 *Распродано*\n\n🎁 Накопленная скидка: *{discount}%*"
        else:
            text = (
                f"*{drop['name']}*\n\n"
                f"💰 Цена: *{drop['price']:,} тг*\n"
                f"🎁 Твоя скидка: *{discount}%*"
                + (f" → итого *{price:,} тг*" if discount > 0 else "") +
                f"\n\nУвеличить скидку:\n"
                f"— Репост рилса → *−{DISCOUNT_REEL}%*\n"
                f"— Каждый друг → *−{DISCOUNT_REF}%*"
            )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb(drop, sold_out))
        return

    # Sold out инфо
    if cb.startswith("soldout_info_"):
        await q.answer("🔴 Эта партия распродана. Следи за новыми дропами!", show_alert=True)
        return

    # Скидка
    if cb == "discount":
        discount = calc_discount(u)
        active = get_active_drops(data)
        reel_status = (
            "✅ подтверждён" if u.get("reel_verified")
            else ("⏳ на проверке" if u.get("reel_pending") else "❌ не отправлен")
        )
        text = (
            f"💰 *Твоя скидка*\n\n"
            f"🎬 Репост рилса: {reel_status} → *{DISCOUNT_REEL if u.get('reel_verified') else 0}%*\n"
            f"👥 Приглашённых друзей: *{u['referrals']}* → *{min(u['referrals'] * DISCOUNT_REF, MAX_DISCOUNT)}%*\n\n"
            f"📊 Итого скидка: *{discount}%*"
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())
        return

    # Реферальная ссылка
    if cb == "ref":
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        text = (
            f"🔗 *Пригласи друга — получи −{DISCOUNT_REF}% за каждого*\n\n"
            f"Твоя ссылка:\n`{link}`\n\n"
            f"👥 Уже приглашено: *{u['referrals']}* чел.\n"
            f"🎁 Скидка за друзей: *{min(u['referrals'] * DISCOUNT_REF, MAX_DISCOUNT)}%*"
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())
        return

    # Репост рилса
    if cb == "reel":
        if u.get("reel_verified"):
            text = f"✅ *Репост уже подтверждён!*\n\nСкидка *{DISCOUNT_REEL}%* зачислена."
        elif u.get("reel_pending"):
            text = "⏳ *Скрин уже на проверке.*\n\nАдмин рассмотрит в ближайшее время."
        else:
            text = (
                f"🎬 *Репост рилса = скидка {DISCOUNT_REEL}%*\n\n"
                f"1. Открой рилс: {REEL_LINK}\n"
                f"2. Поделись в сторис с упоминанием *{INSTAGRAM}*\n"
                f"3. Сделай скриншот и отправь *следующим сообщением*\n\n"
                f"После проверки придёт уведомление ✅"
            )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())
        return

# ─── ЗАКАЗ (ConversationHandler) ─────────────────────────────────────────────

async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # Извлечь drop_id из callback: "order_drop_1"
    drop_id = q.data[len("order_"):]
    data    = load_data()
    drop    = find_drop(data, drop_id)

    if not drop or is_sold_out(drop):
        await q.answer("🔴 Эта партия распродана!", show_alert=True)
        return ConversationHandler.END

    uid = str(q.from_user.id)
    u   = get_or_create_user(data, q.from_user)
    save_data(data)

    context.user_data["order_drop_id"] = drop_id
    discount = calc_discount(u)
    price    = final_price(drop["price"], discount)

    text = (
        f"🛒 *Оформление заказа*\n\n"
        f"Товар: *{drop['name']}*\n"
        f"Цена: *{drop['price']:,} тг*"
        + (f"\n🎁 Скидка: *{discount}%* → итого *{price:,} тг*" if discount > 0 else "") +
        f"\n\nКак тебя зовут? _(Имя и фамилия)_"
    )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_name"] = update.message.text.strip()
    await update.message.reply_text("📱 Укажи номер телефона для связи:", reply_markup=cancel_kb())
    return ASK_PHONE

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_phone"] = update.message.text.strip()
    await update.message.reply_text(
        "📍 Укажи адрес доставки или напиши «Самовывоз»:", reply_markup=cancel_kb()
    )
    return ASK_ADDRESS

async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data    = load_data()
    user    = update.effective_user
    uid     = str(user.id)
    u       = get_or_create_user(data, user)
    drop_id = context.user_data.get("order_drop_id")
    drop    = find_drop(data, drop_id) or {}

    discount = calc_discount(u)
    price    = final_price(drop.get("price", 0), discount)
    address  = update.message.text.strip()

    order = {
        "user_id":      uid,
        "drop_id":      drop_id,
        "drop_name":    drop.get("name", ""),
        "name":         context.user_data.get("order_name"),
        "phone":        context.user_data.get("order_phone"),
        "address":      address,
        "discount":     discount,
        "price":        price,
        "tg_name":      user.full_name,
        "tg_username":  f"@{user.username}" if user.username else "нет @",
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
    data["orders"].append(order)
    save_data(data)

    await update.message.reply_text(
        f"✅ *Заявка принята!*\n\n"
        f"📦 {drop.get('name', '')}\n"
        f"👤 {order['name']}\n"
        f"📱 {order['phone']}\n"
        f"📍 {order['address']}\n"
        f"💰 К оплате: *{price:,} тг*"
        + (f" (скидка {discount}%)" if discount > 0 else "") +
        f"\n\n{ADMIN_CONTACT} свяжется с тобой в ближайшее время 🙌",
        parse_mode="Markdown"
    )

    # Реферальная ссылка после заказа
    referral_link = f"https://t.me/{BOT_USERNAME}?start={uid}"
    current_discount = calc_discount(u)
    remaining = MAX_DISCOUNT - current_discount
    if remaining > 0:
        ref_msg = (
            f"🔗 *Поделись с друзьями — получи скидку на следующий дроп!*\n\n"
            f"За каждого друга — *−{DISCOUNT_REF}%* к цене.\n"
            f"Твоя ссылка:\n`{referral_link}`\n\n"
            f"👥 Уже приглашено: *{u['referrals']}* чел. · "
            f"Скидка: *{current_discount}%* (можно ещё *+{remaining}%*)"
        )
    else:
        ref_msg = (
            f"🔗 *Твоя реферальная ссылка:*\n\n"
            f"`{referral_link}`\n\n"
            f"🎁 Скидка максимальная — *{current_discount}%*. Молодец! 🔥"
        )
    await update.message.reply_text(ref_msg, parse_mode="Markdown")

    admin_text = (
        f"🛒 *Новый заказ!*\n\n"
        f"📦 Дроп: *{drop.get('name', '?')}*\n"
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
    q    = update.callback_query
    await q.answer()
    data = load_data()
    uid  = str(q.from_user.id)
    get_or_create_user(data, q.from_user)
    save_data(data)
    active = get_active_drops(data)
    if active:
        await q.edit_message_text("🧢 Выбери дроп:", reply_markup=drops_kb(active, uid))
    else:
        await q.edit_message_text("Заказ отменён.")
    return ConversationHandler.END

# ─── ФОТО — АЛЬБОМЫ И СКРИНЫ ─────────────────────────────────────────────────

# Буфер для сборки альбомов: { media_group_id: [file_id, ...] }
_album_buffer: dict[str, list[str]] = {}
_album_tasks: dict[str, asyncio.Task] = {}

async def _process_album(media_group_id: str, context: ContextTypes.DEFAULT_TYPE,
                          user_id: int, chat_id: int, is_admin_drop: bool,
                          drop_id: str | None):
    """Вызывается через 1.5 сек после получения первого фото в альбоме."""
    await asyncio.sleep(1.5)

    file_ids = _album_buffer.pop(media_group_id, [])
    _album_tasks.pop(media_group_id, None)

    if not file_ids:
        return

    if is_admin_drop and drop_id:
        # Сохранить альбом в дроп
        data = load_data()
        drop = find_drop(data, drop_id)
        if drop:
            drop["photo_file_ids"] = file_ids
            save_data(data)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ *Сохранено {len(file_ids)} фото* для дропа *{drop['name']}*",
                parse_mode="Markdown"
            )
        context.bot_data.pop("awaiting_drop_photo_id", None)
        return

    # Обычный пользователь — скрин репоста (берём только первое фото)
    uid = str(user_id)
    data = load_data()
    u = data["users"].get(uid)
    if not u:
        return

    if u.get("reel_verified"):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Репост уже подтверждён! Скидка {DISCOUNT_REEL}% зачислена."
        )
        return

    if u.get("reel_pending"):
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Скрин уже на проверке, не нужно присылать повторно."
        )
        return

    u["reel_pending"]  = True
    u["reel_photo_id"] = file_ids[0]
    save_data(data)

    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Скрин получен! Ждём подтверждения от админа.\nКак проверят — придёт уведомление.",
    )

    uname   = f"@{u['username']}" if u.get("username") else "нет @"
    caption = (
        f"📸 *Новый скрин репоста*\n\n"
        f"👤 {u['name']} ({uname})\n"
        f"🆔 ID: `{uid}`\n\n"
        f"✅ `/approve {uid}`  ❌ `/decline {uid}`"
    )
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID, photo=file_ids[0],
            caption=caption, parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"Не удалось отправить фото админу: {e}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    uid   = str(user.id)
    msg   = update.message
    photo = msg.photo[-1]   # лучшее качество
    mgid  = msg.media_group_id   # None если одиночное фото

    is_admin = (user.id == ADMIN_ID)
    awaiting_drop_id = context.bot_data.get("awaiting_drop_photo_id") if is_admin else None

    # ── Одиночное фото ────────────────────────────────────────────────────────
    if mgid is None:
        if is_admin and awaiting_drop_id:
            data = load_data()
            drop = find_drop(data, awaiting_drop_id)
            if drop:
                drop["photo_file_ids"] = [photo.file_id]
                save_data(data)
                context.bot_data.pop("awaiting_drop_photo_id", None)
                await msg.reply_text(
                    f"✅ *Фото сохранено* для дропа *{drop['name']}*",
                    parse_mode="Markdown"
                )
            return

        # Пользователь — скрин репоста
        await _process_album(
            f"single_{uid}_{msg.message_id}", context,
            user.id, msg.chat_id,
            is_admin_drop=False, drop_id=None
        )
        # Немедленная обработка (без задержки)
        _album_buffer[f"single_{uid}_{msg.message_id}"] = [photo.file_id]
        return

    # ── Альбом (media group) ──────────────────────────────────────────────────
    # Добавляем фото в буфер
    if mgid not in _album_buffer:
        _album_buffer[mgid] = []
    _album_buffer[mgid].append(photo.file_id)

    # Если задача уже запущена для этой группы — просто добавили фото, выходим
    if mgid in _album_tasks:
        return

    # Запускаем отложенную обработку
    task = asyncio.create_task(
        _process_album(
            mgid, context,
            user.id, msg.chat_id,
            is_admin_drop=(is_admin and bool(awaiting_drop_id)),
            drop_id=awaiting_drop_id
        )
    )
    _album_tasks[mgid] = task


# ─── АДМИН — ДРОПЫ ───────────────────────────────────────────────────────────

async def admin_new_drop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/newdrop Название | Цена  — создать новый дроп"""
    if update.effective_user.id != ADMIN_ID:
        return

    args_text = " ".join(context.args).strip()
    if not args_text:
        await update.message.reply_text(
            "Использование:\n`/newdrop Название`\n`/newdrop Название | 19000`",
            parse_mode="Markdown"
        )
        return

    # Парсим название и цену
    if "|" in args_text:
        parts = args_text.split("|", 1)
        name  = parts[0].strip()
        try:
            price = int(parts[1].strip().replace(" ", "").replace(",", ""))
        except ValueError:
            price = 19000
    else:
        name  = args_text
        price = 19000

    data    = load_data()
    drop_id = next_drop_id(data)
    drop    = {
        "id":            drop_id,
        "name":          name,
        "price":         price,
        "photo_file_ids": [],
        "sold_out":      False,
        "sold_out_at":   None,
        "active":        True,
        "created_at":    datetime.now(timezone.utc).isoformat(),
    }
    data["drops"].append(drop)
    save_data(data)

    await update.message.reply_text(
        f"✅ *Дроп создан!*\n\n"
        f"🆔 ID: `{drop_id}`\n"
        f"📦 Название: *{name}*\n"
        f"💰 Цена: *{price:,} тг*\n\n"
        f"Теперь загрузи фото:\n`/setphoto {drop_id}`",
        parse_mode="Markdown"
    )

async def admin_set_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setphoto DROP_ID — загрузить фото(альбом) для дропа"""
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        data = load_data()
        drops = data.get("drops", [])
        if not drops:
            await update.message.reply_text("Сначала создай дроп: /newdrop Название | Цена")
            return
        # Показать список ID
        lines = "\n".join([f"• `{d['id']}` — {d['name']}" for d in drops])
        await update.message.reply_text(
            f"Укажи ID дропа:\n{lines}\n\nПример: `/setphoto drop_1`",
            parse_mode="Markdown"
        )
        return

    drop_id = context.args[0]
    data    = load_data()
    drop    = find_drop(data, drop_id)

    if not drop:
        await update.message.reply_text(f"❌ Дроп `{drop_id}` не найден. Проверь /listdrops", parse_mode="Markdown")
        return

    context.bot_data["awaiting_drop_photo_id"] = drop_id
    await update.message.reply_text(
        f"📷 Жду фото для дропа *{drop['name']}*\n\n"
        f"Можешь отправить одно фото или сразу альбом — все сохранятся.",
        parse_mode="Markdown"
    )

async def admin_list_drops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/listdrops — список всех дропов"""
    if update.effective_user.id != ADMIN_ID:
        return
    data  = load_data()
    drops = data.get("drops", [])
    if not drops:
        await update.message.reply_text("Дропов пока нет. Создай: /newdrop Название | Цена")
        return

    text = "📦 *Все дропы:*\n\n"
    for d in drops:
        sold  = is_sold_out(d)
        photos = len(d.get("photo_file_ids", []))
        status = "🔴 Sold out" if sold else ("🟢 Активен" if d.get("active") else "⚫️ Скрыт")
        text += (
            f"*{d['name']}*\n"
            f"  🆔 `{d['id']}` · {status} · {d['price']:,} тг · 📷 {photos} фото\n\n"
        )

    text += (
        "Команды:\n"
        "`/setphoto ID` — загрузить фото\n"
        "`/soldout ID` — sold out\n"
        "`/undosoldout ID` — снять sold out\n"
        "`/closedrop ID` — скрыть дроп"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_sold_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/soldout DROP_ID"""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: `/soldout drop_1`", parse_mode="Markdown")
        return
    data = load_data()
    drop = find_drop(data, context.args[0])
    if not drop:
        await update.message.reply_text("❌ Дроп не найден. Проверь /listdrops")
        return
    drop["sold_out"]    = True
    drop["sold_out_at"] = datetime.now(timezone.utc).isoformat()
    save_data(data)
    await update.message.reply_text(f"🔴 *{drop['name']}* — sold out активирован.", parse_mode="Markdown")

async def admin_undo_sold_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/undosoldout DROP_ID"""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: `/undosoldout drop_1`", parse_mode="Markdown")
        return
    data = load_data()
    drop = find_drop(data, context.args[0])
    if not drop:
        await update.message.reply_text("❌ Дроп не найден.")
        return
    drop["sold_out"]    = False
    drop["sold_out_at"] = None
    save_data(data)
    await update.message.reply_text(f"🟢 *{drop['name']}* — sold out снят.", parse_mode="Markdown")

async def admin_close_drop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/closedrop DROP_ID — скрыть дроп от пользователей"""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: `/closedrop drop_1`", parse_mode="Markdown")
        return
    data = load_data()
    drop = find_drop(data, context.args[0])
    if not drop:
        await update.message.reply_text("❌ Дроп не найден.")
        return
    drop["active"] = False
    save_data(data)
    await update.message.reply_text(f"⚫️ *{drop['name']}* скрыт от пользователей.", parse_mode="Markdown")

async def admin_drop_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dropstatus DROP_ID"""
    if update.effective_user.id != ADMIN_ID:
        return
    data = load_data()

    if not context.args:
        await admin_list_drops(update, context)
        return

    drop = find_drop(data, context.args[0])
    if not drop:
        await update.message.reply_text("❌ Дроп не найден. Проверь /listdrops")
        return

    sold    = is_sold_out(drop)
    photos  = len(drop.get("photo_file_ids", []))
    status  = "🔴 Sold out" if sold else ("🟢 Активен" if drop.get("active") else "⚫️ Скрыт")

    text = (
        f"📊 *{drop['name']}*\n\n"
        f"🆔 ID: `{drop['id']}`\n"
        f"💰 Цена: {drop['price']:,} тг\n"
        f"📷 Фото: {photos} шт.\n"
        f"📦 Статус: {status}\n\n"
        f"/setphoto {drop['id']} — загрузить фото\n"
        f"/soldout {drop['id']} — sold out\n"
        f"/undosoldout {drop['id']} — снять sold out\n"
        f"/closedrop {drop['id']} — скрыть"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

    if drop.get("photo_file_ids"):
        await update.message.reply_photo(
            photo=drop["photo_file_ids"][0],
            caption=f"📷 Первое фото дропа «{drop['name']}»"
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
    await update.message.reply_text(f"✅ Подтверждено! {u['name']} — скидка {discount}%.")
    try:
        await context.bot.send_message(
            chat_id=int(target),
            text=f"🎉 *Репост подтверждён!*\n\nСкидка *{DISCOUNT_REEL}%* зачислена. Твоя итоговая скидка: *{discount}%*",
            parse_mode="Markdown"
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
    await update.message.reply_text(f"❌ Скрин отклонён.")
    try:
        await context.bot.send_message(
            chat_id=int(target),
            text=(
                f"😔 *Скрин не прошёл проверку*\n\n"
                f"Скорее всего не видно упоминания {INSTAGRAM} или сторис удалена.\n"
                f"Попробуй ещё раз — нажми «🎬 Репост рилса» и пришли новый скрин."
            ),
            parse_mode="Markdown"
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
        uname = f"@{u['username']}" if u.get("username") else "нет @"
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
            f"{i}. [{o.get('drop_name','?')}] {o['tg_name']} ({o['tg_username']})\n"
            f"   {o['name']} · {o['phone']} · {o['address']}\n"
            f"   💰 {o['price']:,} тг" + (f" (−{o['discount']}%)" if o['discount'] > 0 else "") + "\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    data          = load_data()
    users         = list(data["users"].values())
    orders        = data.get("orders", [])
    drops         = data.get("drops", [])
    total_revenue = sum(o["price"] for o in orders)
    verified      = sum(1 for u in users if u.get("reel_verified"))
    pending       = sum(1 for u in users if u.get("reel_pending"))
    active_drops  = sum(1 for d in drops if d.get("active"))
    text = (
        f"📊 *Статистика*\n\n"
        f"🧢 Дропов всего: *{len(drops)}* (активных: *{active_drops}*)\n"
        f"👥 Пользователей: *{len(users)}*\n"
        f"🛒 Заказов: *{len(orders)}*\n"
        f"💵 Выручка: *{total_revenue:,} тг*\n"
        f"🎬 Репостов подтверждено: *{verified}*\n"
        f"⏳ Репостов на проверке: *{pending}*"
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

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = (
        f"🛠 *Команды админа v2.0:*\n\n"
        f"*— Дропы —*\n"
        f"`/newdrop Название | Цена` — создать дроп\n"
        f"`/listdrops` — список всех дропов\n"
        f"`/setphoto ID` — загрузить фото(альбом)\n"
        f"`/soldout ID` — sold out\n"
        f"`/undosoldout ID` — снять sold out\n"
        f"`/closedrop ID` — скрыть дроп\n"
        f"`/dropstatus ID` — статус дропа\n\n"
        f"*— Репосты —*\n"
        f"`/pending` — скрины на проверке\n"
        f"`/approve ID` — подтвердить → скидка {DISCOUNT_REEL}%\n"
        f"`/decline ID` — отклонить\n\n"
        f"*— Заказы —*\n"
        f"`/stats` — статистика\n"
        f"`/orders` — последние 20 заказов\n"
        f"`/broadcast текст` — рассылка всем\n\n"
        f"*— Скидки —*\n"
        f"Репост: {DISCOUNT_REEL}% · Друг: {DISCOUNT_REF}% · Макс: {MAX_DISCOUNT}%"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── ОШИБКИ ──────────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error_text = "".join(traceback.format_exception(
        type(context.error), context.error, context.error.__traceback__
    ))
    if isinstance(update, Update):
        user = update.effective_user
        user_info = f"{user.full_name} (ID: {user.id})" if user else "неизвестный"
    else:
        user_info = "—"

    if len(error_text) > 2800:
        error_text = error_text[:2800] + "\n...(обрезано)"

    logging.error(f"Exception:\n{error_text}")
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔴 *Ошибка*\n\n{user_info}\n\n```\n{error_text}\n```",
            parse_mode="Markdown"
        )
    except Exception:
        pass

# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    app = Application.builder().token(BOT_TOKEN).build()

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(order_start, pattern="^order_drop_")],
        states={
            ASK_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_address)],
        },
        fallbacks=[CallbackQueryHandler(cancel_order, pattern="^cancel_order$")],
    )

    app.add_handler(CommandHandler("start",         start))
    app.add_handler(order_conv)
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.PHOTO,   handle_photo))

    # Дропы
    app.add_handler(CommandHandler("newdrop",       admin_new_drop))
    app.add_handler(CommandHandler("listdrops",     admin_list_drops))
    app.add_handler(CommandHandler("setphoto",      admin_set_photo))
    app.add_handler(CommandHandler("soldout",       admin_sold_out))
    app.add_handler(CommandHandler("undosoldout",   admin_undo_sold_out))
    app.add_handler(CommandHandler("closedrop",     admin_close_drop))
    app.add_handler(CommandHandler("dropstatus",    admin_drop_status))

    # Заказы / репосты
    app.add_handler(CommandHandler("stats",         admin_stats))
    app.add_handler(CommandHandler("orders",        admin_orders))
    app.add_handler(CommandHandler("pending",       admin_pending))
    app.add_handler(CommandHandler("approve",       admin_approve))
    app.add_handler(CommandHandler("decline",       admin_decline))
    app.add_handler(CommandHandler("broadcast",     admin_broadcast))
    app.add_handler(CommandHandler("adminhelp",     admin_help))

    app.add_error_handler(error_handler)

    print("🧢 BlackMarket Bot v2.0 запущен! /adminhelp — команды.")
    app.run_polling()

if __name__ == "__main__":
    main()
