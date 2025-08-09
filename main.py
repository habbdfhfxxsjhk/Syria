"""
main.py
نسخة نهائية وكاملة لبوت تيليجرام بنظام Webhook
يدعم:
- لوحة أدمن داخل البوت (إضافة/تعديل/حذف أزرار - رئيسية وفرعية)
- تعديل نصوص HTML وصور للمحتوى
- جمع معلومات من المستخدمين (طلبات) وإرسالها للأدمن
- ردود حالة الطلب (موافقة/رفض/طلب تعديل)
- بث جماعي نص/صورة/أزرار
- إيقاف وتشغيل البوت من لوحة الأدمن
- إدارة المشرفين وصلاحياتهم
- جدولة رسائل (جدولة بث)
- إحصائيات متقدمة (المستخدمين، الطلبات، أكثر زر استخداماً)
- منع الرسائل الحرة (إرشاد المستخدم لاستخدام الأزرار)
- تخزين كل البيانات في JSON (قابلة للتعديل)
- Webhook عبر Flask
"""

import os
import json
import logging
import uuid
from datetime import datetime, timedelta
from threading import Lock

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from flask import Flask, request, abort
from apscheduler.schedulers.background import BackgroundScheduler

# ----------------------------
#  --- إعداد اللوجينج ----
# ----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------
#  --- ملفات الإعدادات -----
# ----------------------------
CONFIG_FILE = "config.json"
BUTTONS_FILE = "buttons.json"
USERS_FILE = "users.json"
ORDERS_FILE = "orders.json"
ADMINS_FILE = "admins.json"
SCHEDULES_FILE = "schedules.json"

file_lock = Lock()  # لحماية القراءة/الكتابة البسيطة

# ----------------------------
#  --- وظائف مساعدة للـ JSON -
# ----------------------------
def ensure_file(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

def load_json(path, default=None):
    with file_lock:
        if not os.path.exists(path):
            if default is None:
                return None
            ensure_file(path, default)
            return default
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception as e:
                logger.exception("Failed to load JSON %s: %s", path, e)
                return default if default is not None else {}

def save_json(path, data):
    with file_lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# ----------------------------
#  --- إعداد الملفات الافتراضية -
# ----------------------------
DEFAULT_CONFIG = {
    "BOT_TOKEN": "PUT_YOUR_BOT_TOKEN_HERE",
    "WEBHOOK_URL": "https://your-repl-or-domain.repl.co",  # بدون مسار webhook
    "ADMIN_IDS": [],              # ضع ID الأدمن هنا (أرقام صحيحة)
    "BOT_STATUS": "on",           # "on" أو "off"
    "ALLOW_LINKS": False         # False => يمنع الروابط من المستخدمين
}

DEFAULT_BUTTONS = {
    "main_menu": [
        {
            "id": "services",
            "text": "🎮 خدمات الألعاب",
            "type": "submenu",
            "submenu": [
                {"id": "pubg", "text": "شحن شدات PUBG", "type": "request_info", "info_request": "أرسل UserID + المبلغ أو الباقة المطلوبة:"},
                {"id": "ff", "text": "شحن FreeFire", "type": "request_info", "info_request": "أرسل ID اللعبة + الباقة المطلوبة:"}
            ]
        },
        {
            "id": "cards",
            "text": "💳 بطاقات وقسائم",
            "type": "submenu",
            "submenu": [
                {"id": "google", "text": "Google Play", "type": "request_info", "info_request": "أرسل المبلغ/العملة المطلوبة:"},
                {"id": "itunes", "text": "iTunes", "type": "request_info", "info_request": "أرسل المبلغ/العملة المطلوبة:"}
            ]
        },
        {
            "id": "contact",
            "text": "📩 تواصل مع الأدمن",
            "type": "contact_admin"
        }
    ]
}

DEFAULT_USERS = {}
DEFAULT_ORDERS = []
DEFAULT_ADMINS = {
    "admins": []  # كل عنصر: {"id": 12345, "name": "AdminName", "perms": ["all"]} أو perms محددة
}
DEFAULT_SCHEDULES = []

# إنشاء الملفات إذا كانت مفقودة
ensure_file(CONFIG_FILE, DEFAULT_CONFIG)
ensure_file(BUTTONS_FILE, DEFAULT_BUTTONS)
ensure_file(USERS_FILE, DEFAULT_USERS)
ensure_file(ORDERS_FILE, DEFAULT_ORDERS)
ensure_file(ADMINS_FILE, DEFAULT_ADMINS)
ensure_file(SCHEDULES_FILE, DEFAULT_SCHEDULES)

# ----------------------------
#  --- تحميل الإعدادات -----
# ----------------------------
CONFIG = load_json(CONFIG_FILE, DEFAULT_CONFIG)
BUTTONS = load_json(BUTTONS_FILE, DEFAULT_BUTTONS)
USERS = load_json(USERS_FILE, DEFAULT_USERS)
ORDERS = load_json(ORDERS_FILE, DEFAULT_ORDERS)
ADMINS = load_json(ADMINS_FILE, DEFAULT_ADMINS)
SCHEDULES = load_json(SCHEDULES_FILE, DEFAULT_SCHEDULES)

BOT_TOKEN = CONFIG.get("BOT_TOKEN")
WEBHOOK_URL = CONFIG.get("WEBHOOK_URL").rstrip("/")
ADMIN_IDS = set(CONFIG.get("ADMIN_IDS", []))
BOT_STATUS = CONFIG.get("BOT_STATUS", "on")
ALLOW_LINKS = CONFIG.get("ALLOW_LINKS", False)

# ----------------------------
#  --- تهيئة البوت و Flask ---
# ----------------------------
if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
    logger.error("لم يتم وضع BOT_TOKEN في config.json. ضع التوكن ثم أعد التشغيل.")
    raise SystemExit("BOT_TOKEN missing in config.json")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

# ----------------------------
#  --- حالات جلسات الأدمن --- (لحفظ الحالة المؤقتة أثناء إدخال الخطوات)
# ----------------------------
admin_sessions = {}   # key: admin_id -> value: dict {action:, temp:...}
# حالات انتظار المستخدم لإدخال معلومات الطلب محفوظة داخل USERS[user_id]["awaiting"] = {button_id, prompt}

# ----------------------------
#  --- دوال مساعدة عامة -----
# ----------------------------
def save_all():
    save_json(CONFIG_FILE, CONFIG)
    save_json(BUTTONS_FILE, BUTTONS)
    save_json(USERS_FILE, USERS)
    save_json(ORDERS_FILE, ORDERS)
    save_json(ADMINS_FILE, ADMINS)
    save_json(SCHEDULES_FILE, SCHEDULES)

def is_admin(user_id):
    # يستخدم كل من ADMIN_IDS و ملف ADMINS للأذونات المفصلة
    if user_id in ADMIN_IDS:
        return True
    for a in ADMINS.get("admins", []):
        if a.get("id") == user_id:
            return True
    return False

def find_button_by_id(btn_id, btn_list=None):
    if btn_list is None:
        btn_list = BUTTONS.get("main_menu", [])
    for b in btn_list:
        if b.get("id") == btn_id or b.get("text") == btn_id:
            return b
        if b.get("type") == "submenu":
            found = find_button_by_id(btn_id, b.get("submenu", []))
            if found:
                return found
    return None

def build_keyboard_from_buttons(btn_list):
    kb = InlineKeyboardMarkup()
    for b in btn_list:
        kb.add(InlineKeyboardButton(b["text"], callback_data=f"BTN|{b['id']}"))
    # always add a Home button
    kb.add(InlineKeyboardButton("🏠 الرئيسية", callback_data="NAV|home"))
    return kb

def build_submenu_keyboard(submenu):
    kb = InlineKeyboardMarkup()
    for b in submenu:
        kb.add(InlineKeyboardButton(b["text"], callback_data=f"BTN|{b['id']}"))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="NAV|back"))
    kb.add(InlineKeyboardButton("🏠 الرئيسية", callback_data="NAV|home"))
    return kb

def send_to_admins(text, parse_mode="HTML"):
    admin_ids = [a["id"] for a in ADMINS.get("admins", [])] + list(ADMIN_IDS)
    sent = 0
    for aid in set(admin_ids):
        try:
            bot.send_message(aid, text, parse_mode=parse_mode)
            sent += 1
        except Exception as e:
            logger.exception("Failed to send admin notification to %s: %s", aid, e)
    return sent

# ----------------------------
#  --- رسالة البداية -----
# ----------------------------
WELCOME_HTML = CONFIG.get("WELCOME_HTML") or (
    "<b>🎮✨ أهلًا بك في عالمك المفضل للشحن! ✨📱</b>\n\n"
    "مرحبًا بك في <b>[اسم المتجر]</b>، وجهتك الأولى لشحن الألعاب والتطبيقات بسرعة وأمان ⚡💳\n\n"
    "🚀 سرعة شحن فائقة\n🔒 أمان مضمون 100%\n💬 دعم فوري لخدمتك\n\n"
    "اختر أحد الخيارات من القائمة أدناه."
)

# ----------------------------
#  --- الدوال الأساسية للتعامل مع المستخدمين -----
# ----------------------------
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    global USERS
    uid = str(message.chat.id)
    if uid not in USERS:
        USERS[uid] = {
            "id": message.chat.id,
            "name": message.from_user.full_name or message.from_user.first_name,
            "first_seen": datetime.now().isoformat(),
            "awaiting": None,   # info structure when awaiting user input: {"button_id":..., "prompt":...}
            "lang": "ar"
        }
        save_json(USERS_FILE, USERS)
    # bot status check
    if CONFIG.get("BOT_STATUS", "on") == "off" and not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "🚫 البوت متوقف حالياً. تواصل مع الأدمن إذا كنت في حاجة.")
        return
    # send welcome and main menu
    bot.send_message(message.chat.id, WELCOME_HTML, reply_markup=build_keyboard_from_buttons(BUTTONS.get("main_menu", [])))

# منع الروابط أو رسائل حرة عندما لا ننتظر input من المستخدم
@bot.message_handler(func=lambda m: True, content_types=['text', 'photo'])
def catch_all(message):
    uid = str(message.chat.id)
    # admins can send free messages to bot (for admin flows)
    if is_admin(message.chat.id):
        # we check if admin is in a session waiting input
        session = admin_sessions.get(message.chat.id)
        if session:
            handle_admin_session_input(message, session)
            return
        # otherwise allow admin commands through standard handlers (/admin)
    # If user is awaiting info for a previous request, process it
    user = USERS.get(uid)
    if user and user.get("awaiting"):
        # expecting info
        awaiting = user["awaiting"]
        # optionally block links
        if not ALLOW_LINKS:
            txt = message.text or ""
            if (txt.startswith("http://") or txt.startswith("https://")):
                bot.send_message(message.chat.id, "🚫 إرسال الروابط غير مسموح. استخدم النص أو الصورة أو الأرقام فقط.")
                bot.send_message(message.chat.id, "🔁 الرجاء إعادة إرسال المعلومات المطلوبة أو اضغط على 🏠 للعودة.", reply_markup=build_keyboard_from_buttons(BUTTONS.get("main_menu")))
                return
        # Accept photo optionally
        content = None
        if message.content_type == 'photo':
            # get largest photo
            file_id = message.photo[-1].file_id
            content = f"[PHOTO]{file_id}"
        else:
            content = message.text
        # create order
        order = {
            "order_id": str(uuid.uuid4()),
            "user_id": message.chat.id,
            "user_name": user.get("name"),
            "button_id": awaiting.get("button_id"),
            "button_text": awaiting.get("button_text"),
            "info": content,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "notes": ""
        }
        ORDERS.append(order)
        save_json(ORDERS_FILE, ORDERS)
        # clear awaiting
        USERS[uid]["awaiting"] = None
        save_json(USERS_FILE, USERS)
        # notify user and admins
        bot.send_message(message.chat.id, "✅ طلبك قيد المراجعة سيتم إعلامك بالنتيجة بأسرع وقت ممكن ✅")
        send_to_admins(f"📥 طلب جديد\n\n👤 {order['user_name']} (ID: {order['user_id']})\n📦 خدمة: {order['button_text']}\n🆔 OrderID: {order['order_id']}\n📝 المحتوى: {'صورة' if isinstance(order['info'], str) and order['info'].startswith('[PHOTO]') else order['info']}")
        return

    # if not awaiting and user is not admin -> block free text
    if not (user and user.get("awaiting")):
        if not is_admin(message.chat.id):
            bot.send_message(message.chat.id, "⚠️ لا يمكنك إرسال رسائل مباشرة. استخدم الأزرار المتاحة. للتواصل مع الأدمن اضغط زر 'تواصل مع الأدمن'.")
            bot.send_message(message.chat.id, WELCOME_HTML, reply_markup=build_keyboard_from_buttons(BUTTONS.get("main_menu", [])))
            return
    # If admin and not in session, ignore here (admin commands handled elsewhere)

# ----------------------------
#  --- التعامل مع ضغط الأزرار (Callback Query) -----
# ----------------------------
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    uid = call.from_user.id
    # navigation keys
    if data.startswith("NAV|"):
        nav = data.split("|", 1)[1]
        if nav == "home":
            bot.edit_message_text(WELCOME_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=build_keyboard_from_buttons(BUTTONS.get("main_menu", [])))
            bot.answer_callback_query(call.id)
            return
        if nav == "back":
            # simple approach: go to main menu
            bot.edit_message_text(WELCOME_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=build_keyboard_from_buttons(BUTTONS.get("main_menu", [])))
            bot.answer_callback_query(call.id)
            return

    # admin commands inline (prefixed ADMIN|)
    if data.startswith("ADMIN|"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "ممنوع - هذه الوظيفة للأدمن فقط.")
            return
        admin_action = data.split("|",1)[1]
        handle_admin_action_inline(call, admin_action)
        return

    # normal button id
    if data.startswith("BTN|"):
        btn_id = data.split("|",1)[1]
        btn = find_button_by_id(btn_id, BUTTONS.get("main_menu", []))
        if not btn:
            bot.answer_callback_query(call.id, "هذا الزر غير موجود الآن.")
            return
        # if submenu -> show submenu keyboard
        if btn.get("type") == "submenu":
            submenu = btn.get("submenu", [])
            # show as new message or edit message depending on permission
            try:
                bot.edit_message_text(f"<b>{btn.get('text')}</b>\nاختر من القائمة:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=build_submenu_keyboard(submenu))
            except Exception:
                bot.send_message(call.message.chat.id, f"<b>{btn.get('text')}</b>\nاختر من القائمة:", parse_mode="HTML", reply_markup=build_submenu_keyboard(submenu))
            bot.answer_callback_query(call.id)
            return
        # contact admin
        if btn.get("type") == "contact_admin":
            # open a small inline with contact options
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("✉️ إرسال رسالة للأدمن", callback_data=f"CONTACT|send"))
            kb.add(InlineKeyboardButton("🏠 الرئيسية", callback_data="NAV|home"))
            bot.send_message(call.message.chat.id, "اختر طريقة التواصل مع الأدمن:", reply_markup=kb)
            bot.answer_callback_query(call.id)
            return
        # content
        if btn.get("type") == "content":
            text = btn.get("content", "")
            image = btn.get("image", "")
            # send image+text if exists
            if image:
                try:
                    bot.send_photo(call.message.chat.id, image, caption=text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 الرئيسية", callback_data="NAV|home")]]))
                except Exception as e:
                    bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 الرئيسية", callback_data="NAV|home")]]))
            else:
                bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 الرئيسية", callback_data="NAV|home")]]))
            bot.answer_callback_query(call.id)
            return
        # request_info
        if btn.get("type") == "request_info":
            # set user's awaiting
            USERS[str(call.from_user.id)] = USERS.get(str(call.from_user.id), {
                "id": call.from_user.id,
                "name": call.from_user.full_name or call.from_user.first_name,
                "first_seen": datetime.now().isoformat(),
                "awaiting": None
            })
            USERS[str(call.from_user.id)]["awaiting"] = {"button_id": btn.get("id"), "button_text": btn.get("text"), "prompt": btn.get("info_request", "أرسل المعلومات المطلوبة:")}
            save_json(USERS_FILE, USERS)
            bot.send_message(call.message.chat.id, USERS[str(call.from_user.id)]["awaiting"]["prompt"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 الرئيسية", callback_data="NAV|home")]]))
            bot.answer_callback_query(call.id)
            return

    # handle contact sub action: CONTACT|send
    if data.startswith("CONTACT|"):
        sub = data.split("|",1)[1]
        if sub == "send":
            # ask user to send message to admin
            bot.send_message(call.message.chat.id, "✉️ أرسل رسالتك للأدمن الآن (يمكنك كتابة نص أو صورة):")
            # register next step
            bot.register_next_step_handler(call.message, user_send_message_to_admin)
            bot.answer_callback_query(call.id)
            return

    # admin order actions: ORDER|<order_id>|action
    if data.startswith("ORDER|"):
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "ممنوع - للأدمن فقط")
            return
        parts = data.split("|")
        if len(parts) >= 3:
            order_id = parts[1]
            action = parts[2]
            handle_admin_order_action(call, order_id, action)
            bot.answer_callback_query(call.id)
            return

    # unknown callback
    bot.answer_callback_query(call.id, "حدث خطأ أو الزر غير معروف.")

# ----------------------------
#  --- تنفيذ إرسال رسالة من المستخدم إلى الأدمن (CONTACT) ---
# ----------------------------
def user_send_message_to_admin(message):
    try:
        # if photo
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
            text = f"[صورة مرفقة]"
            # send photo to admins with caption
            for a in set(list(ADMIN_IDS) + [adm["id"] for adm in ADMINS.get("admins", [])]):
                try:
                    bot.send_photo(a, file_id, caption=f"📩 رسالة من {message.from_user.full_name} (ID:{message.from_user.id})\n\n{text}")
                except Exception:
                    pass
            bot.send_message(message.chat.id, "✅ تم إرسال رسالتك إلى الأدمن.")
            return
        # else text
        for a in set(list(ADMIN_IDS) + [adm["id"] for adm in ADMINS.get("admins", [])]):
            try:
                bot.send_message(a, f"📩 رسالة من {message.from_user.full_name} (ID:{message.from_user.id}):\n\n{message.text}")
            except Exception:
                pass
        bot.send_message(message.chat.id, "✅ تم إرسال رسالتك إلى الأدمن.")
    except Exception as e:
        logger.exception("user_send_message_to_admin failed: %s", e)
        bot.send_message(message.chat.id, "حدث خطأ أثناء إرسال الرسالة.")

# ----------------------------
#  --- إدارة الطلبات من الأدمن (عرض / قبول /رفض /طلب تعديل) ---
# ----------------------------
def handle_admin_order_action(call, order_id, action):
    # find order
    order = None
    for o in ORDERS:
        if o.get("order_id") == order_id:
            order = o
            break
    if not order:
        bot.send_message(call.message.chat.id, "❌ لم أجد الطلب.")
        return
    if action == "view":
        # send order details with action buttons
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ موافقة", callback_data=f"ORDER|{order_id}|approve"))
        kb.add(InlineKeyboardButton("❌ رفض", callback_data=f"ORDER|{order_id}|reject"))
        kb.add(InlineKeyboardButton("✏️ طلب تعديل", callback_data=f"ORDER|{order_id}|askmore"))
        bot.send_message(call.message.chat.id, f"📦 OrderID: {order_id}\n👤 المستخدم: {order.get('user_name')} ({order.get('user_id')})\n📌 الخدمة: {order.get('button_text')}\n📝 المحتوى: {order.get('info')}\n\nالحالة: {order.get('status')}", reply_markup=kb)
        return
    if action == "approve":
        order["status"] = "approved"
        order["handled_at"] = datetime.now().isoformat()
        save_json(ORDERS_FILE, ORDERS)
        # notify user
        try:
            bot.send_message(order["user_id"], f"✅ تمت الموافقة على طلبك (OrderID: {order_id}). سيتم إتمام الخدمة قريباً. شكراً لتعاملكم.")
        except Exception:
            pass
        bot.send_message(call.message.chat.id, "تمت الموافقة وإشعار المستخدم.")
        return
    if action == "reject":
        order["status"] = "rejected"
        order["handled_at"] = datetime.now().isoformat()
        save_json(ORDERS_FILE, ORDERS)
        try:
            bot.send_message(order["user_id"], f"❌ تم رفض طلبك (OrderID: {order_id}). إذا رغبت بالمساعدة تواصل مع الأدمن.")
        except Exception:
            pass
        bot.send_message(call.message.chat.id, "تم الرفض وإشعار المستخدم.")
        return
    if action == "askmore":
        order["status"] = "needs_more"
        order["handled_at"] = datetime.now().isoformat()
        save_json(ORDERS_FILE, ORDERS)
        # ask admin to send follow-up question text
        bot.send_message(call.message.chat.id, "✏️ أرسل نص السؤال أو الطلب الإضافي الذي سيصل للمستخدم:")
        # create session for admin to input follow-up text and map to order_id
        admin_sessions[call.from_user.id] = {"action": "askmore_input", "order_id": order_id}
        return

# ----------------------------
#  --- التعامل مع جلسات الأدمن (multi-step flows) ---
# ----------------------------
def handle_admin_session_input(message, session):
    aid = message.from_user.id
    act = session.get("action")
    try:
        if act == "askmore_input":
            order_id = session.get("order_id")
            # find order
            order = next((o for o in ORDERS if o["order_id"] == order_id), None)
            if not order:
                bot.send_message(aid, "لم أجد الطلب.")
                admin_sessions.pop(aid, None)
                return
            # send question to user
            try:
                bot.send_message(order["user_id"], f"✏️ من الأدمن: {message.text}\n\nيرجى الرد على هذا الرسالة بالمعلومات المطلوبة.")
            except Exception:
                pass
            bot.send_message(aid, "تم إرسال الطلب الإضافي للمستخدم.")
            admin_sessions.pop(aid, None)
            return

        if act == "add_button_step1":
            # expecting JSON-like input steps: we use sequential prompts
            # session.temp accumulates
            session.setdefault("temp", {})
            session["temp"]["text"] = message.text.strip()
            bot.send_message(aid, "أدخل معرف الزر (id) - استخدم أحرف إنجليزية وبدون مسافات (مثال: new_service):")
            session["action"] = "add_button_step2"
            return
        if act == "add_button_step2":
            session.setdefault("temp", {})
            btn_id = message.text.strip()
            session["temp"]["id"] = btn_id
            bot.send_message(aid, "ما نوع الزر؟ اكتب:\n1) submenu\n2) request_info\n3) content\n4) contact_admin\nأدخل النوع الكلمة فقط (مثال: submenu):")
            session["action"] = "add_button_step3"
            return
        if act == "add_button_step3":
            kind = message.text.strip()
            session["temp"]["type"] = kind
            if kind == "submenu":
                session["temp"]["submenu"] = []
                bot.send_message(aid, "الآن سنضيف عناصر للزر الفرعي. أرسل كل عنصر على صورة 'id|text|type' مثل:\npkg1|اشتراك يومي|request_info\nعندما تنتهي اكتب 'done'")
                session["action"] = "add_button_submenu"
            elif kind == "request_info":
                bot.send_message(aid, "أدخل نص الطلب الذي سيُرسل للمستخدم (مثال: أرسل ID والكمية):")
                session["action"] = "add_button_finish_request"
            elif kind == "content":
                bot.send_message(aid, "أدخل محتوى النص (HTML مسموح):")
                session["action"] = "add_button_finish_content"
            elif kind == "contact_admin":
                # finish quickly
                temp = session["temp"]
                new_btn = {"id": temp["id"], "text": temp["text"], "type": "contact_admin"}
                BUTTONS.setdefault("main_menu", []).append(new_btn)
                save_json(BUTTONS_FILE, BUTTONS)
                bot.send_message(aid, "تم إضافة زر 'تواصل مع الأدمن' بنجاح.")
                admin_sessions.pop(aid, None)
            else:
                bot.send_message(aid, "نوع غير معروف - ألغيت العملية.")
                admin_sessions.pop(aid, None)
            return
        if act == "add_button_submenu":
            if message.text.strip().lower() == "done":
                # finalize
                temp = session.get("temp", {})
                new_btn = {"id": temp["id"], "text": temp["text"], "type": "submenu", "submenu": temp.get("submenu", [])}
                BUTTONS.setdefault("main_menu", []).append(new_btn)
                save_json(BUTTONS_FILE, BUTTONS)
                bot.send_message(aid, "✅ تم إضافة الزر الفرعي بنجاح.")
                admin_sessions.pop(aid, None)
                return
            # parse line: id|text|type (type: request_info/content)
            parts = message.text.split("|")
            if len(parts) < 3:
                bot.send_message(aid, "خطأ في الصيغة. أرسل بالشكل: id|text|type")
                return
            sid, stext, stype = parts[0].strip(), parts[1].strip(), parts[2].strip()
            item = {"id": sid, "text": stext, "type": stype}
            if stype == "request_info":
                # ask for prompt
                bot.send_message(aid, f"أدخل نص الطلب الذي سيراه المستخدم لعنصر {stext}:")
                # save temporary state to fill prompt next
                session.setdefault("pending_subs", []).append(item)
                session["action"] = "add_button_submenu_prompt"
                return
            else:
                # add directly with default prompt blank
                session.setdefault("temp", {}).setdefault("submenu", []).append(item)
                bot.send_message(aid, f"تم إضافة العنصر {stext}. أرسل التالي أو اكتب 'done' للانتهاء.")
                return
        if act == "add_button_submenu_prompt":
            # last pending sub gets prompt
            prompt = message.text
            pending = session.get("pending_subs", [])
            if not pending:
                bot.send_message(aid, "خطأ داخلي - لا يوجد عنصر معلق.")
                admin_sessions.pop(aid, None)
                return
            item = pending.pop(-1)
            item["info_request"] = prompt
            session.setdefault("temp", {}).setdefault("submenu", []).append(item)
            session["action"] = "add_button_submenu"
            bot.send_message(aid, "تم حفظ العنصر الفرعي. أرسل عنصر آخر أو اكتب 'done'.")
            return

        if act == "add_button_finish_request":
            prompt_text = message.text
            temp = session.get("temp", {})
            new_btn = {"id": temp["id"], "text": temp["text"], "type": "request_info", "info_request": prompt_text}
            BUTTONS.setdefault("main_menu", []).append(new_btn)
            save_json(BUTTONS_FILE, BUTTONS)
            bot.send_message(aid, "✅ تم إضافة زر (request_info) بنجاح.")
            admin_sessions.pop(aid, None)
            return

        if act == "add_button_finish_content":
            # message is content text; ask for optional image next
            temp = session.get("temp", {})
            temp["content"] = message.text
            session["action"] = "add_button_finish_content_image"
            bot.send_message(aid, "أضف رابط صورة (أو اكتب 'no' لتخطي):")
            return
        if act == "add_button_finish_content_image":
            temp = session.get("temp", {})
            img = message.text.strip()
            if img.lower() == "no":
                img = ""
            temp["image"] = img
            new_btn = {"id": temp["id"], "text": temp["text"], "type": "content", "content": temp.get("content", ""), "image": temp.get("image", "")}
            BUTTONS.setdefault("main_menu", []).append(new_btn)
            save_json(BUTTONS_FILE, BUTTONS)
            bot.send_message(aid, "✅ تم إضافة زر المحتوى مع الصورة (إن وُجدت).")
            admin_sessions.pop(aid, None)
            return

        if act == "del_button_step1":
            btn_id = message.text.strip()
            # try to remove from main_menu
            removed = False
            for i, b in enumerate(BUTTONS.get("main_menu", [])):
                if b.get("id") == btn_id or b.get("text") == btn_id:
                    BUTTONS["main_menu"].pop(i)
                    removed = True
                    break
            if removed:
                save_json(BUTTONS_FILE, BUTTONS)
                bot.send_message(aid, f"✅ تم حذف الزر {btn_id}.")
            else:
                bot.send_message(aid, "لم أجد هذا المعرف. تأكد وحاول مرة أخرى.")
            admin_sessions.pop(aid, None)
            return

        if act == "broadcast_step1":
            # message contains the broadcast text or 'photo' etc depending on session.temp
            # simple broadcast text-only flow
            text = message.text
            # send preview and ask confirm
            session_temp = session.get("temp", {})
            session_temp["text"] = text
            bot.send_message(aid, "🔁 معاينة البث:\n\n" + text)
            bot.send_message(aid, "هل تريد الإرسال الآن إلى كل المستخدمين؟ اكتب 'yes' للإرسال أو 'no' للإلغاء.")
            session["action"] = "broadcast_confirm"
            return

        if act == "broadcast_confirm":
            if message.text.strip().lower() == "yes":
                text = session.get("temp", {}).get("text", "")
                # send to all users
                sent = 0
                for uid, u in USERS.items():
                    try:
                        bot.send_message(int(uid), text)
                        sent += 1
                    except Exception:
                        pass
                bot.send_message(aid, f"✅ تم الإرسال إلى {sent} مستخدم.")
            else:
                bot.send_message(aid, "تم إلغاء البث.")
            admin_sessions.pop(aid, None)
            return

        if act == "add_admin_step1":
            # expecting new admin id
            try:
                new_id = int(message.text.strip())
            except ValueError:
                bot.send_message(aid, "الـ ID يجب أن يكون رقم. أعد المحاولة.")
                admin_sessions.pop(aid, None)
                return
            name = message.from_user.full_name
            ADMINS.setdefault("admins", []).append({"id": new_id, "name": name, "perms": ["all"]})
            save_json(ADMINS_FILE, ADMINS)
            bot.send_message(aid, f"✅ تم إضافة الأدمن {new_id}")
            admin_sessions.pop(aid, None)
            return

        if act == "del_admin_step1":
            # expecting admin id to delete
            try:
                del_id = int(message.text.strip())
            except ValueError:
                bot.send_message(aid, "الـ ID يجب أن يكون رقم. أعد المحاولة.")
                admin_sessions.pop(aid, None)
                return
            before = len(ADMINS.get("admins", []))
            ADMINS["admins"] = [a for a in ADMINS.get("admins", []) if a.get("id") != del_id]
            save_json(ADMINS_FILE, ADMINS)
            after = len(ADMINS.get("admins", []))
            if after < before:
                bot.send_message(aid, f"✅ تم حذف الأدمن {del_id}")
            else:
                bot.send_message(aid, "لم أجد هذا الأدمن.")
            admin_sessions.pop(aid, None)
            return

        if act == "schedule_step1":
            # expecting text for scheduled message
            session_temp = session.setdefault("temp", {})
            session_temp["text"] = message.text
            bot.send_message(aid, "أدخل التاريخ والوقت للإرسال بصيغة YYYY-MM-DD HH:MM (مثال: 2025-08-10 15:30):")
            session["action"] = "schedule_step2"
            return
        if act == "schedule_step2":
            txt = message.text.strip()
            try:
                send_time = datetime.strptime(txt, "%Y-%m-%d %H:%M")
            except Exception:
                bot.send_message(aid, "صيغة التاريخ غير صحيحة. ألغيت العملية.")
                admin_sessions.pop(aid, None)
                return
            # persist schedule
            sched_id = str(uuid.uuid4())
            entry = {"id": sched_id, "text": session.get("temp", {}).get("text", ""), "time": send_time.isoformat()}
            SCHEDULES.append(entry)
            save_json(SCHEDULES_FILE, SCHEDULES)
            # schedule job
            def job_send(entry):
                txt = entry["text"]
                for uid in list(USERS.keys()):
                    try:
                        bot.send_message(int(uid), txt)
                    except Exception:
                        pass
            scheduler.add_job(job_send, 'date', run_date=send_time, args=[entry], id=sched_id)
            bot.send_message(aid, "✅ تم جدولة الرسالة.")
            admin_sessions.pop(aid, None)
            return

    except Exception as e:
        logger.exception("handle_admin_session_input failed: %s", e)
        bot.send_message(aid, "حدث خطأ أثناء العملية. تم إلغاء الجلسة.")
        admin_sessions.pop(aid, None)


# ----------------------------
#  --- لوحة الأدمن: أوامر /admin و أزرار داخلية -----
# ----------------------------
@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "🚫 ليس لديك صلاحية الوصول لهذه اللوحة.")
        return
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🧭 إدارة الأزرار", callback_data="ADMIN|manage_buttons"))
    kb.add(InlineKeyboardButton("📦 الطلبات", callback_data="ADMIN|manage_orders"))
    kb.add(InlineKeyboardButton("📢 بث / إرسال", callback_data="ADMIN|broadcast"))
    kb.add(InlineKeyboardButton("👥 إدارة المشرفين", callback_data="ADMIN|manage_admins"))
    kb.add(InlineKeyboardButton("📊 إحصائيات", callback_data="ADMIN|stats"))
    kb.add(InlineKeyboardButton("⏯ تشغيل/إيقاف البوت", callback_data="ADMIN|toggle_bot"))
    kb.add(InlineKeyboardButton("⏱ جدولة رسالة", callback_data="ADMIN|schedule"))
    bot.send_message(message.chat.id, "لوحة تحكم الأدمن — اختر خيارًا:", reply_markup=kb)

def handle_admin_action_inline(call, action):
    aid = call.from_user.id
    if action == "manage_buttons":
        # show current buttons + options to add/delete/edit
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("➕ إضافة زر جديد", callback_data="ADMIN|add_button"))
        kb.add(InlineKeyboardButton("🗑 حذف زر", callback_data="ADMIN|del_button"))
        kb.add(InlineKeyboardButton("✏️ تعديل زر", callback_data="ADMIN|edit_button"))
        kb.add(InlineKeyboardButton("🔁 عرض القائمة الحالية", callback_data="ADMIN|show_buttons"))
        kb.add(InlineKeyboardButton("🏠 رجوع", callback_data="NAV|home"))
        bot.send_message(aid, "إدارة الأزرار:", reply_markup=kb)
        return
    if action == "manage_orders":
        # list pending orders
        if not ORDERS:
            bot.send_message(aid, "لا توجد طلبات حتى الآن.")
            return
        kb = InlineKeyboardMarkup()
        # show last 20 orders
        for o in ORDERS[-20:][::-1]:
            kb.add(InlineKeyboardButton(f"{o.get('button_text')} - {o.get('user_name')}", callback_data=f"ORDER|{o.get('order_id')}|view"))
        bot.send_message(aid, "قائمة الطلبات (الأحدث أولاً):", reply_markup=kb)
        return
    if action == "broadcast":
        bot.send_message(aid, "✏️ أرسل نص البث (يمكنك كتابة HTML):")
        admin_sessions[aid] = {"action": "broadcast_step1", "temp": {}}
        return
    if action == "manage_admins":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("➕ إضافة أدمن", callback_data="ADMIN|add_admin"))
        kb.add(InlineKeyboardButton("🗑 حذف أدمن", callback_data="ADMIN|del_admin"))
        kb.add(InlineKeyboardButton("🏠 رجوع", callback_data="NAV|home"))
        bot.send_message(aid, "إدارة المشرفين:", reply_markup=kb)
        return
    if action == "stats":
        users_count = len(USERS)
        orders_count = len(ORDERS)
        # most used button
        counts = {}
        for o in ORDERS:
            key = o.get("button_text", "unknown")
            counts[key] = counts.get(key, 0) + 1
        most_used = max(counts.items(), key=lambda x: x[1])[0] if counts else "لا يوجد"
        bot.send_message(aid, f"📊 إحصائيات:\n\n👥 عدد المستخدمين: {users_count}\n📦 عدد الطلبات: {orders_count}\n⭐ أكثر خدمة استخدامًا: {most_used}")
        return
    if action == "toggle_bot":
        # flip BOT_STATUS
        CONFIG["BOT_STATUS"] = "off" if CONFIG.get("BOT_STATUS", "on") == "on" else "on"
        save_json(CONFIG_FILE, CONFIG)
        bot.send_message(aid, f"🔁 تم تغيير حالة البوت إلى: {CONFIG['BOT_STATUS']}")
        return
    if action == "schedule":
        bot.send_message(aid, "✏️ أرسل نص الرسالة التي تريد جدولتها:")
        admin_sessions[aid] = {"action": "schedule_step1", "temp": {}}
        return

    # other admin actions
    if action == "add_button":
        bot.send_message(aid, "🔰 إدخال اسم الزر (النص الظاهر للمستخدم):")
        admin_sessions[aid] = {"action": "add_button_step1", "temp": {}}
        return
    if action == "del_button":
        bot.send_message(aid, "🗑 أرسل معرف الزر (id) أو نصه لحذفه من القائمة الرئيسية:")
        admin_sessions[aid] = {"action": "del_button_step1"}
        return
    if action == "show_buttons":
        # pretty print buttons tree
        text_lines = ["قائمة الأزرار الحالية:"]
        for b in BUTTONS.get("main_menu", []):
            text_lines.append(f"- {b.get('id')} | {b.get('text')} | {b.get('type')}")
            if b.get("type") == "submenu":
                for s in b.get("submenu", []):
                    text_lines.append(f"    • {s.get('id')} | {s.get('text')} | {s.get('type')}")
        bot.send_message(aid, "\n".join(text_lines))
        return
    if action == "add_admin":
        bot.send_message(aid, "➕ أرسل ID الأدمن الجديد (رقم):")
        admin_sessions[aid] = {"action": "add_admin_step1"}
        return
    if action == "del_admin":
        bot.send_message(aid, "🗑 أرسل ID الأدمن الذي تريد حذفه:")
        admin_sessions[aid] = {"action": "del_admin_step1"}
        return

# ----------------------------
#  --- admin order callback (view/approve/reject/askmore) handler mapping ---
# ----------------------------
# Note: ORDER|{order_id}|view will be created when listing orders
# The ORDER|... callbacks handled in handle_callback -> handle_admin_order_action

# ----------------------------
#  --- جدولة المواعيد عند بدء التشغيل (إعادة تحميل الجداول المحفوظة) -----
# ----------------------------
def restore_schedules():
    global SCHEDULES
    for entry in SCHEDULES:
        try:
            run_at = datetime.fromisoformat(entry["time"])
            if run_at <= datetime.now():
                # if time has passed, skip or send immediately depending policy; we skip
                continue
            def job_send(e=entry):
                # send message to all users
                for uid in list(USERS.keys()):
                    try:
                        bot.send_message(int(uid), e["text"])
                    except Exception:
                        pass
            scheduler.add_job(job_send, 'date', run_date=run_at, args=[entry], id=entry["id"])
        except Exception as e:
            logger.exception("restore_schedules error: %s", e)

# restore at startup
restore_schedules()

# ----------------------------
#  --- Webhook endpoints (Flask) ---
# ----------------------------
@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
    except Exception as e:
        logger.exception("Failed to process webhook: %s", e)
        return "Error", 500
    return "OK", 200

@app.route("/setwebhook")
def set_webhook_endpoint():
    # useful helper to set webhook via code (calls Telegram setWebhook)
    url = f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}"
    try:
        res = bot.set_webhook(url)
        return f"Webhook set: {res}", 200
    except Exception as e:
        logger.exception("set_webhook failed: %s", e)
        return f"Error setting webhook: {e}", 500

@app.route("/", methods=["GET"])
def index():
    return "Telegram Bot (Webhook) is running."

# ----------------------------
#  --- تشغيل الخادم (Flask) ---
# ----------------------------
if __name__ == "__main__":
    # لحفظ أولي للconfigs
    save_all()
    port = int(os.environ.get("PORT", 5000))
    # start flask app
    logger.info("Starting Flask app... Listening on port %s", port)
    app.run(host="0.0.0.0", port=port)
