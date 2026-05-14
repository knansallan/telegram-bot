import os
import json
import uuid
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TimedOut, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
DATA_FILE = Path(__file__).parent / "data.json"
OLD_STICKERS_FILE = Path(__file__).parent / "stickers.json"

# ─── Data Model ───────────────────────────────────────────────────────────────
# categories:
#   <cat_id>:
#     name: str
#     items: [{type: sticker|photo|video, file_id: str}]
#     gate: null | {
#       question: str,
#       options: [
#         {label: str, type: text|media, content: str (text only)},
#         {label: str, type: text|media, content: str (text only)}
#       ]
#     }

def default_data() -> dict:
    return {
        "categories": {
            "obaid":   {"name": "ستكرات عبيد",   "items": [], "gate": None},
            "mahawish": {"name": "ستكرات مهاوش", "items": [], "gate": None},
        }
    }


def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load data.json: {e}")

    data = default_data()
    if OLD_STICKERS_FILE.exists():
        try:
            with open(OLD_STICKERS_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
            for cat_id, file_ids in old.items():
                if cat_id in data["categories"] and isinstance(file_ids, list):
                    data["categories"][cat_id]["items"] = [
                        {"type": "sticker", "file_id": fid} for fid in file_ids
                    ]
            logger.info("Migrated stickers from stickers.json")
        except Exception as e:
            logger.error(f"Migration from stickers.json failed: {e}")
    return data


def save_data(data: dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save data: {e}")


def new_cat_id() -> str:
    return uuid.uuid4().hex[:8]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return ADMIN_USER_ID != 0 and user_id == ADMIN_USER_ID


async def send_item(bot, chat_id: int, item: dict) -> None:
    t = item["type"]
    fid = item["file_id"]
    if t == "sticker":
        await bot.send_sticker(chat_id=chat_id, sticker=fid)
    elif t == "photo":
        await bot.send_photo(chat_id=chat_id, photo=fid)
    elif t == "video":
        await bot.send_video(chat_id=chat_id, video=fid)


async def send_category_items(bot, chat_id: int, cat: dict) -> None:
    items = cat.get("items", [])
    if not items:
        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ ما في محتوى مضاف في هذه الفئة بعد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]
            ]),
        )
        return
    for item in items:
        try:
            await send_item(bot, chat_id, item)
        except Exception as e:
            logger.error(f"Failed to send item {item}: {e}")
    await bot.send_message(
        chat_id=chat_id,
        text=f"✅ تم إرسال جميع محتوى *{cat['name']}*!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]
        ]),
    )


ITEM_ICONS = {"sticker": "🎭", "photo": "🖼", "video": "🎬"}


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_keyboard(admin: bool = False) -> InlineKeyboardMarkup:
    data = load_data()
    rows = []
    for cat_id, cat in data["categories"].items():
        rows.append([InlineKeyboardButton(cat["name"], callback_data=f"cat:{cat_id}")])
    if admin:
        rows.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def gate_keyboard(cat_id: str, gate: dict) -> InlineKeyboardMarkup:
    opts = gate["options"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(opts[0]["label"], callback_data=f"gate:{cat_id}:0")],
        [InlineKeyboardButton(opts[1]["label"], callback_data=f"gate:{cat_id}:1")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")],
    ])


def upload_category_keyboard(data: dict) -> InlineKeyboardMarkup:
    rows = []
    for cat_id, cat in data["categories"].items():
        rows.append([InlineKeyboardButton(cat["name"], callback_data=f"upload:{cat_id}")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="upload_cancel")])
    return InlineKeyboardMarkup(rows)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    data = load_data()
    rows = []
    for cat_id, cat in data["categories"].items():
        count = len(cat["items"])
        gate_icon = " 🔀" if cat.get("gate") else ""
        rows.append([InlineKeyboardButton(
            f"🗂 {cat['name']} ({count}){gate_icon}",
            callback_data=f"admin_cat:{cat_id}",
        )])
    rows.append([InlineKeyboardButton("➕ إضافة فئة جديدة", callback_data="admin_new_cat")])
    rows.append([InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def admin_cat_keyboard(cat_id: str, cat: dict) -> InlineKeyboardMarkup:
    gate = cat.get("gate")
    rows = [
        [InlineKeyboardButton("📋 إدارة المحتوى", callback_data=f"admin_items:{cat_id}")],
    ]
    if gate:
        rows.append([InlineKeyboardButton("🔀 تعديل السؤال التمهيدي", callback_data=f"set_gate:{cat_id}")])
        rows.append([InlineKeyboardButton("🗑 حذف السؤال التمهيدي", callback_data=f"rm_gate:{cat_id}")])
    else:
        rows.append([InlineKeyboardButton("🔀 إضافة سؤال تمهيدي", callback_data=f"set_gate:{cat_id}")])
    rows.append([InlineKeyboardButton("🗑 حذف الفئة بالكامل", callback_data=f"del_cat:{cat_id}")])
    rows.append([InlineKeyboardButton("🔙 رجوع للإدارة", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def items_keyboard(cat_id: str, items: list) -> InlineKeyboardMarkup:
    rows = []
    for i, item in enumerate(items):
        icon = ITEM_ICONS.get(item["type"], "📄")
        rows.append([InlineKeyboardButton(
            f"🗑 حذف {icon} #{i + 1} ({item['type']})",
            callback_data=f"del_item:{cat_id}:{i}",
        )])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"admin_cat:{cat_id}")])
    return InlineKeyboardMarkup(rows)


def gate_opt_type_keyboard(opt_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 رسالة نصية", callback_data=f"gopt_type:{opt_idx}:text")],
        [InlineKeyboardButton("🎬 محتوى الفئة (ستكر/صورة/فيديو)", callback_data=f"gopt_type:{opt_idx}:media")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel")],
    ])


def cancel_keyboard(back: str = "admin_panel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=back)]])


# ─── Command Handlers ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    admin = is_admin(user.id)
    context.user_data.pop("admin_state", None)
    text = (
        f"أهلاً وسهلاً {user.first_name}! 👋\n\n"
        "🎁 *مرحباً بك في متجر الستكرات المجاني!*\n\n"
        "اختر الفئة اللي تبي تشوف محتواها:"
    )
    if admin:
        text += "\n\n🔑 _أنت مسجل كمشرف. أرسل أي ستكر/صورة/فيديو لإضافته مباشرة._"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard(admin))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    admin = is_admin(user.id)
    text = "📖 *مساعدة*\n\n• /start — القائمة الرئيسية\n• /help — هذه الرسالة\n"
    if admin:
        text += (
            "\n*أوامر المشرف:*\n"
            "• أرسل ستكر/صورة/فيديو مباشرة لإضافته لفئة\n"
            "• /admin — لوحة الإدارة (إدارة الفئات والمحتوى والأسئلة التمهيدية)\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard(admin))


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ هذا الأمر للمشرف فقط.")
        return
    context.user_data.pop("admin_state", None)
    await update.message.reply_text(
        "⚙️ *لوحة الإدارة*\n\nاختر فئة أو أنشئ فئة جديدة:",
        parse_mode="Markdown",
        reply_markup=admin_panel_keyboard(),
    )


# ─── Media Handler ────────────────────────────────────────────────────────────

async def media_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ فقط المشرف يمكنه إضافة محتوى. استخدم /start.")
        return

    state = context.user_data.get("admin_state")
    if state in ("gate_question", "gate_opt1_label", "gate_opt2_label",
                 "gate_opt1_text", "gate_opt2_text", "new_category_name"):
        return

    msg = update.message
    if msg.sticker:
        media_type, file_id, label = "sticker", msg.sticker.file_id, "ستكر 🎭"
    elif msg.photo:
        media_type, file_id, label = "photo", msg.photo[-1].file_id, "صورة 🖼"
    elif msg.video:
        media_type, file_id, label = "video", msg.video.file_id, "فيديو 🎬"
    else:
        return

    context.user_data["pending_media"] = {"type": media_type, "file_id": file_id}
    data = load_data()
    await update.message.reply_text(
        f"📌 *تم استلام {label}!*\n\nاختر الفئة لإضافته فيها:",
        parse_mode="Markdown",
        reply_markup=upload_category_keyboard(data),
    )


# ─── Text Handler (admin state machine) ───────────────────────────────────────

async def text_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    state = context.user_data.get("admin_state")
    text = update.message.text.strip()

    # ── New category name ──────────────────────────────────────────────────────
    if state == "new_category_name":
        if not text:
            await update.message.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً، أعد المحاولة:")
            return
        cat_id = new_cat_id()
        data = load_data()
        data["categories"][cat_id] = {"name": text, "items": [], "gate": None}
        save_data(data)
        context.user_data.pop("admin_state", None)
        await update.message.reply_text(
            f"✅ *تم إنشاء الفئة:* {text}",
            parse_mode="Markdown",
            reply_markup=admin_panel_keyboard(),
        )
        return

    cat_id = context.user_data.get("gate_cat")

    # ── Gate: question text ────────────────────────────────────────────────────
    if state == "gate_question":
        context.user_data["gate_draft"] = {"question": text, "options": [{}, {}]}
        context.user_data["admin_state"] = "gate_opt1_label"
        await update.message.reply_text(
            f"✏️ *السؤال:* _{text}_\n\nأدخل الآن *عنوان الخيار الأول* (نص الزر):",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return

    # ── Gate: option 1 label ───────────────────────────────────────────────────
    if state == "gate_opt1_label":
        context.user_data["gate_draft"]["options"][0]["label"] = text
        context.user_data["admin_state"] = "gate_opt1_type"
        await update.message.reply_text(
            f"✏️ *عنوان الخيار الأول:* _{text}_\n\nماذا يرسل البوت عند اختيار هذا الخيار؟",
            parse_mode="Markdown",
            reply_markup=gate_opt_type_keyboard(0),
        )
        return

    # ── Gate: option 1 text content ───────────────────────────────────────────
    if state == "gate_opt1_text":
        context.user_data["gate_draft"]["options"][0].update({"type": "text", "content": text})
        context.user_data["admin_state"] = "gate_opt2_label"
        await update.message.reply_text(
            f"✅ *رد الخيار الأول محفوظ.*\n\nأدخل الآن *عنوان الخيار الثاني*:",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return

    # ── Gate: option 2 label ───────────────────────────────────────────────────
    if state == "gate_opt2_label":
        context.user_data["gate_draft"]["options"][1]["label"] = text
        context.user_data["admin_state"] = "gate_opt2_type"
        await update.message.reply_text(
            f"✏️ *عنوان الخيار الثاني:* _{text}_\n\nماذا يرسل البوت عند اختيار هذا الخيار؟",
            parse_mode="Markdown",
            reply_markup=gate_opt_type_keyboard(1),
        )
        return

    # ── Gate: option 2 text content → save ────────────────────────────────────
    if state == "gate_opt2_text":
        context.user_data["gate_draft"]["options"][1].update({"type": "text", "content": text})
        data = load_data()
        data["categories"][cat_id]["gate"] = context.user_data.pop("gate_draft")
        save_data(data)
        cat_name = data["categories"][cat_id]["name"]
        context.user_data.pop("admin_state", None)
        context.user_data.pop("gate_cat", None)
        await update.message.reply_text(
            f"✅ *تم حفظ السؤال التمهيدي للفئة:* {cat_name}",
            parse_mode="Markdown",
            reply_markup=admin_panel_keyboard(),
        )
        return


# ─── Callback Button Handler ──────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        logger.warning(f"Network error (will retry): {err}")
        return
    if isinstance(err, BadRequest) and "query is too old" in str(err).lower():
        return
    logger.error(f"Unhandled error: {err}", exc_info=err)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        if "query is too old" in str(e).lower() or "query id is invalid" in str(e).lower():
            return
        raise
    cb = query.data
    user = query.from_user
    admin = is_admin(user.id)

    # ── Main menu ──────────────────────────────────────────────────────────────
    if cb == "main_menu":
        context.user_data.pop("admin_state", None)
        await query.edit_message_text(
            "🏠 *القائمة الرئيسية*\n\nاختر الفئة:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(admin),
        )
        return

    # ── Browse category ────────────────────────────────────────────────────────
    if cb.startswith("cat:"):
        cat_id = cb[4:]
        data = load_data()
        cat = data["categories"].get(cat_id)
        if not cat:
            await query.edit_message_text("⚠️ الفئة غير موجودة.", reply_markup=main_menu_keyboard(admin))
            return
        gate = cat.get("gate")
        if gate:
            await query.edit_message_text(
                f"📂 *{cat['name']}*\n\n❓ {gate['question']}",
                parse_mode="Markdown",
                reply_markup=gate_keyboard(cat_id, gate),
            )
        else:
            items = cat.get("items", [])
            if not items:
                await query.edit_message_text(
                    f"📂 *{cat['name']}*\n\n⚠️ ما في محتوى مضاف بعد.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
                    ]),
                )
                return
            await query.edit_message_text(
                f"📂 *{cat['name']}*\n\n{len(items)} عنصر — جاري الإرسال 🚀",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
                ]),
            )
            await send_category_items(context.bot, query.message.chat_id, cat)
        return

    # ── Gate option tapped ─────────────────────────────────────────────────────
    if cb.startswith("gate:"):
        _, cat_id, opt_idx_str = cb.split(":")
        opt_idx = int(opt_idx_str)
        data = load_data()
        cat = data["categories"].get(cat_id)
        if not cat or not cat.get("gate"):
            await query.edit_message_text("⚠️ خطأ، حاول مجدداً.", reply_markup=main_menu_keyboard(admin))
            return
        option = cat["gate"]["options"][opt_idx]
        if option["type"] == "text":
            await query.edit_message_text(
                option["content"],
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
                ]),
            )
        else:
            items = cat.get("items", [])
            await query.edit_message_text(
                f"📂 *{cat['name']}*\n\n{len(items)} عنصر — جاري الإرسال 🚀",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
                ]),
            )
            await send_category_items(context.bot, query.message.chat_id, cat)
        return

    # ── Upload: pick category ──────────────────────────────────────────────────
    if cb.startswith("upload:"):
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        cat_id = cb[7:]
        pending = context.user_data.get("pending_media")
        if not pending:
            await query.edit_message_text("⚠️ انتهت صلاحية الملف، أرسله مجدداً.")
            return
        data = load_data()
        cat = data["categories"].get(cat_id)
        if not cat:
            await query.edit_message_text("⚠️ الفئة غير موجودة.")
            return
        existing_ids = [i["file_id"] for i in cat["items"]]
        if pending["file_id"] not in existing_ids:
            cat["items"].append(pending)
            save_data(data)
            added = True
        else:
            added = False
        context.user_data.pop("pending_media", None)
        icon = ITEM_ICONS.get(pending["type"], "📄")
        if added:
            await query.edit_message_text(
                f"✅ *تمت الإضافة!* {icon}\n\nالفئة: *{cat['name']}*\nالإجمالي: {len(cat['items'])} عنصر",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(admin),
            )
        else:
            await query.edit_message_text(
                f"⚠️ هذا العنصر موجود مسبقاً في *{cat['name']}*.",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(admin),
            )
        return

    if cb == "upload_cancel":
        context.user_data.pop("pending_media", None)
        await query.edit_message_text("❌ تم الإلغاء.", reply_markup=main_menu_keyboard(admin))
        return

    # ── Admin panel ────────────────────────────────────────────────────────────
    if cb == "admin_panel":
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        context.user_data.pop("admin_state", None)
        await query.edit_message_text(
            "⚙️ *لوحة الإدارة*",
            parse_mode="Markdown",
            reply_markup=admin_panel_keyboard(),
        )
        return

    if cb == "admin_new_cat":
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        context.user_data["admin_state"] = "new_category_name"
        await query.edit_message_text(
            "✏️ *إنشاء فئة جديدة*\n\nأرسل اسم الفئة الجديدة:",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return

    if cb.startswith("admin_cat:"):
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        cat_id = cb[10:]
        data = load_data()
        cat = data["categories"].get(cat_id)
        if not cat:
            await query.edit_message_text("⚠️ الفئة غير موجودة.", reply_markup=admin_panel_keyboard())
            return
        gate = cat.get("gate")
        gate_text = f"\n🔀 *السؤال:* _{gate['question']}_" if gate else "\n⬜ لا يوجد سؤال تمهيدي"
        await query.edit_message_text(
            f"🗂 *{cat['name']}*\n📦 {len(cat['items'])} عنصر{gate_text}",
            parse_mode="Markdown",
            reply_markup=admin_cat_keyboard(cat_id, cat),
        )
        return

    if cb.startswith("admin_items:"):
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        cat_id = cb[12:]
        data = load_data()
        cat = data["categories"].get(cat_id)
        if not cat:
            await query.edit_message_text("⚠️ الفئة غير موجودة.")
            return
        items = cat.get("items", [])
        if not items:
            await query.edit_message_text(
                f"📂 *{cat['name']}*\n\nما في محتوى. أرسل ستكر/صورة/فيديو للبوت لإضافته.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع", callback_data=f"admin_cat:{cat_id}")]
                ]),
            )
        else:
            await query.edit_message_text(
                f"📂 *{cat['name']}* — {len(items)} عنصر\n\nاضغط على أي عنصر لحذفه:",
                parse_mode="Markdown",
                reply_markup=items_keyboard(cat_id, items),
            )
        return

    if cb.startswith("del_item:"):
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        _, cat_id, idx_str = cb.split(":")
        idx = int(idx_str)
        data = load_data()
        cat = data["categories"].get(cat_id)
        if cat and 0 <= idx < len(cat["items"]):
            cat["items"].pop(idx)
            save_data(data)
            items = cat["items"]
            if items:
                await query.edit_message_text(
                    f"🗑 *تم الحذف!* المتبقي: {len(items)} عنصر",
                    parse_mode="Markdown",
                    reply_markup=items_keyboard(cat_id, items),
                )
            else:
                await query.edit_message_text(
                    f"🗑 *تم الحذف!* الفئة *{cat['name']}* أصبحت فارغة.",
                    parse_mode="Markdown",
                    reply_markup=admin_panel_keyboard(),
                )
        else:
            await query.edit_message_text("⚠️ لم يتم العثور على العنصر.")
        return

    if cb.startswith("del_cat:"):
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        cat_id = cb[8:]
        data = load_data()
        cat = data["categories"].pop(cat_id, None)
        if cat:
            save_data(data)
            await query.edit_message_text(
                f"🗑 *تم حذف الفئة:* {cat['name']}",
                parse_mode="Markdown",
                reply_markup=admin_panel_keyboard(),
            )
        else:
            await query.edit_message_text("⚠️ الفئة غير موجودة.")
        return

    # ── Gate setup: start ──────────────────────────────────────────────────────
    if cb.startswith("set_gate:"):
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        cat_id = cb[9:]
        data = load_data()
        cat_name = data["categories"].get(cat_id, {}).get("name", cat_id)
        context.user_data["gate_cat"] = cat_id
        context.user_data["gate_draft"] = {"question": "", "options": [{}, {}]}
        context.user_data["admin_state"] = "gate_question"
        await query.edit_message_text(
            f"🔀 *إعداد السؤال التمهيدي للفئة:* {cat_name}\n\n"
            "أرسل *نص السؤال* الذي سيظهر للمستخدم قبل عرض المحتوى:",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return

    if cb.startswith("rm_gate:"):
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        cat_id = cb[8:]
        data = load_data()
        cat = data["categories"].get(cat_id)
        if cat:
            cat["gate"] = None
            save_data(data)
            await query.edit_message_text(
                f"✅ *تم حذف السؤال التمهيدي* من: {cat['name']}",
                parse_mode="Markdown",
                reply_markup=admin_panel_keyboard(),
            )
        return

    # ── Gate option type selected ──────────────────────────────────────────────
    if cb.startswith("gopt_type:"):
        if not admin:
            await query.edit_message_text("⛔ غير مصرح.")
            return
        _, opt_idx_str, opt_type = cb.split(":")
        opt_idx = int(opt_idx_str)
        cat_id = context.user_data.get("gate_cat")

        if opt_type == "media":
            context.user_data["gate_draft"]["options"][opt_idx]["type"] = "media"
            if opt_idx == 0:
                context.user_data["admin_state"] = "gate_opt2_label"
                await query.edit_message_text(
                    "✅ *الخيار الأول:* سيرسل محتوى الفئة.\n\nأدخل الآن *عنوان الخيار الثاني*:",
                    parse_mode="Markdown",
                    reply_markup=cancel_keyboard(),
                )
            else:
                data = load_data()
                data["categories"][cat_id]["gate"] = context.user_data.pop("gate_draft")
                save_data(data)
                cat_name = data["categories"][cat_id]["name"]
                context.user_data.pop("admin_state", None)
                context.user_data.pop("gate_cat", None)
                await query.edit_message_text(
                    f"✅ *تم حفظ السؤال التمهيدي للفئة:* {cat_name}",
                    parse_mode="Markdown",
                    reply_markup=admin_panel_keyboard(),
                )
        else:
            context.user_data["admin_state"] = f"gate_opt{opt_idx + 1}_text"
            await query.edit_message_text(
                f"✏️ أرسل *نص الرد* للخيار {opt_idx + 1}:",
                parse_mode="Markdown",
                reply_markup=cancel_keyboard(),
            )
        return


# ─── Keep-Alive HTTP Server ───────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"online")

    def log_message(self, format, *args):
        pass


def run_http_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"HTTP keep-alive server started on port {port}")
    server.serve_forever()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set!")

    if ADMIN_USER_ID == 0:
        logger.warning("ADMIN_USER_ID not set — admin features disabled.")
    else:
        logger.info(f"Admin user ID: {ADMIN_USER_ID}")

    data = load_data()
    save_data(data)

    app = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(
        (filters.Sticker.ALL | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND,
        media_received,
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_received))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
