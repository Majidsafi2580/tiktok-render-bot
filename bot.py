import os
import re
import json
import time
import uuid
import shutil
import asyncio
import subprocess
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DateRange
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

FALLBACK_ADMIN_IDS = [1853431053]

BOT_NAME = "TikSave Pro"
BOT_LOGO = "⚡🎬"
ADMIN_USERNAME = "@madjid_d14"

LIVE_TASKS = {}
ACTIVE_USERS = set()
LIVE_SEGMENT_SECONDS = 240
DEFAULT_LIVE_MINUTES = 60
MAX_LIVE_MINUTES = 180

BASE_DIR = Path("tiktok_bot_data")
DOWNLOAD_DIR = BASE_DIR / "downloads"

USERS_FILE = BASE_DIR / "users.json"
VIP_FILE = BASE_DIR / "vip.json"
CODES_FILE = BASE_DIR / "vip_codes.json"
WATCH_FILE = BASE_DIR / "watchlist.json"
HISTORY_FILE = BASE_DIR / "history.json"
DAILY_FILE = BASE_DIR / "daily_usage.json"
ADS_FILE = BASE_DIR / "ads.json"
BAN_FILE = BASE_DIR / "banned.json"
SETTINGS_FILE = BASE_DIR / "settings.json"
LOGS_FILE = BASE_DIR / "logs.json"
BOT_CONFIG_FILE = BASE_DIR / "bot_config.json"

BASE_DIR.mkdir(exist_ok=True)
DOWNLOAD_DIR.mkdir(exist_ok=True)

MAX_FILE_MB = 49
CHECK_INTERVAL_SECONDS = 3600
CLEANUP_INTERVAL_SECONDS = 24 * 3600
DOWNLOADS_TTL_SECONDS = 24 * 3600
DAILY_KEEP_DAYS = 7
USED_CODES_KEEP_DAYS = 30
MAX_HISTORY_ITEMS_PER_CHAT = 300

DEFAULT_BOT_CONFIG = {
    "required_channel": "@NATOU_DZ",
    "required_channel_link": "https://t.me/NATOU_DZ",
    "force_sub_enabled": True,

    "free_user_daily_limit": 3,
    "free_watch_limit": 1,
    "free_quality": "normal",

    "default_vip_days": 30,
    "default_vip_user_limit": 15,
    "default_vip_watch_limit": 5,
    "default_vip_link_quality": "best",
    "default_vip_user_quality": "best",
    "default_vip_date_filter": True,
}

DEFAULT_SETTINGS = {
    "send_as": "document",          # document / video
    "preferred_quality": "normal",  # normal / best
}


# =========================================================
# JSON
# =========================================================

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_bot_config():
    cfg = load_json(BOT_CONFIG_FILE, DEFAULT_BOT_CONFIG.copy())
    changed = False
    for k, v in DEFAULT_BOT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
            changed = True
    if changed:
        save_json(BOT_CONFIG_FILE, cfg)
    return cfg


def set_bot_config(key, value):
    cfg = get_bot_config()
    cfg[key] = value
    save_json(BOT_CONFIG_FILE, cfg)


# =========================================================
# USERS / ADMIN / BAN
# =========================================================

def register_user(user):
    users = load_json(USERS_FILE, {})
    uid = str(user.id)

    if uid not in users:
        users[uid] = {
            "id": user.id,
            "name": user.full_name,
            "username": user.username,
            "joined_at": int(time.time()),
        }
        save_json(USERS_FILE, users)


def is_admin(user_id):
    try:
        uid = int(user_id)
        return uid in ADMIN_IDS or uid in FALLBACK_ADMIN_IDS
    except Exception:
        return False


def is_banned(user_id):
    data = load_json(BAN_FILE, {})
    return str(user_id) in data


def ban_user(user_id):
    data = load_json(BAN_FILE, {})
    data[str(user_id)] = {"banned_at": int(time.time())}
    save_json(BAN_FILE, data)


def unban_user(user_id):
    data = load_json(BAN_FILE, {})
    data.pop(str(user_id), None)
    save_json(BAN_FILE, data)


# =========================================================
# SETTINGS
# =========================================================

def get_settings(chat_id):
    data = load_json(SETTINGS_FILE, {})
    key = str(chat_id)

    if key not in data:
        data[key] = DEFAULT_SETTINGS.copy()

    for k, v in DEFAULT_SETTINGS.items():
        data[key].setdefault(k, v)

    save_json(SETTINGS_FILE, data)
    return data[key]


def set_setting(chat_id, name, value):
    data = load_json(SETTINGS_FILE, {})
    key = str(chat_id)

    if key not in data:
        data[key] = DEFAULT_SETTINGS.copy()

    data[key][name] = value
    save_json(SETTINGS_FILE, data)


# =========================================================
# VIP
# =========================================================

def get_vip_data(user_id):
    vip = load_json(VIP_FILE, {})
    uid = str(user_id)

    if uid not in vip:
        return None

    data = vip[uid]
    expires_at = data.get("expires_at", 0)

    if expires_at and int(time.time()) > expires_at:
        vip.pop(uid, None)
        save_json(VIP_FILE, vip)
        return None

    return data


def is_vip(user_id):
    return get_vip_data(user_id) is not None


def vip_days_left(user_id):
    data = get_vip_data(user_id)
    if not data:
        return "لا يوجد"

    remaining = data.get("expires_at", 0) - int(time.time())
    if remaining <= 0:
        return "منتهي"

    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    return f"{days} يوم و {hours} ساعة"


def add_vip(user_id, days, user_limit, watch_limit, link_quality, user_quality, date_filter):
    vip = load_json(VIP_FILE, {})
    now = int(time.time())

    vip[str(user_id)] = {
        "added_at": now,
        "expires_at": now + int(days) * 86400,
        "days": int(days),
        "user_limit": int(user_limit),
        "watch_limit": int(watch_limit),
        "link_quality": link_quality,
        "user_quality": user_quality,
        "date_filter": bool(date_filter),
    }

    save_json(VIP_FILE, vip)


def remove_vip(user_id):
    vip = load_json(VIP_FILE, {})
    vip.pop(str(user_id), None)
    save_json(VIP_FILE, vip)


def extend_vip(user_id, extra_days):
    vip = load_json(VIP_FILE, {})
    uid = str(user_id)
    now = int(time.time())

    data = vip.get(uid)
    if not data:
        cfg = get_bot_config()
        add_vip(
            user_id=user_id,
            days=int(extra_days),
            user_limit=cfg["default_vip_user_limit"],
            watch_limit=cfg["default_vip_watch_limit"],
            link_quality=cfg["default_vip_link_quality"],
            user_quality=cfg["default_vip_user_quality"],
            date_filter=cfg["default_vip_date_filter"],
        )
        return

    current_exp = int(data.get("expires_at", now))
    if current_exp < now:
        current_exp = now

    data["expires_at"] = current_exp + int(extra_days) * 86400
    data["days"] = int(data.get("days", 0)) + int(extra_days)
    vip[uid] = data
    save_json(VIP_FILE, vip)


def get_plan(user_id):
    vip = get_vip_data(user_id)
    cfg = get_bot_config()

    if not vip:
        return {
            "name": "Free",
            "user_limit": int(cfg["free_user_daily_limit"]),
            "watch_limit": int(cfg["free_watch_limit"]),
            "link_quality": cfg["free_quality"],
            "user_quality": cfg["free_quality"],
            "date_filter": False,
        }

    return {
        "name": "VIP",
        "user_limit": int(vip.get("user_limit", cfg["default_vip_user_limit"])),
        "watch_limit": int(vip.get("watch_limit", cfg["default_vip_watch_limit"])),
        "link_quality": vip.get("link_quality", cfg["default_vip_link_quality"]),
        "user_quality": vip.get("user_quality", cfg["default_vip_user_quality"]),
        "date_filter": bool(vip.get("date_filter", cfg["default_vip_date_filter"])),
    }


def list_vip_text(limit=30):
    vip = load_json(VIP_FILE, {})
    users = load_json(USERS_FILE, {})
    if not vip:
        return "💎 لا يوجد VIP حاليًا."

    lines = ["💎 قائمة VIP:\n"]
    shown = 0
    for uid, data in vip.items():
        if shown >= limit:
            lines.append(f"\n... وباقي {len(vip) - limit}")
            break
        name = users.get(uid, {}).get("name", "Unknown")
        expires = data.get("expires_at", 0)
        if expires:
            remaining = expires - int(time.time())
            days = max(0, remaining // 86400)
            exp_txt = f"{days} يوم"
        else:
            exp_txt = "دائم"
        lines.append(f"{shown+1}. {uid} | {name} | باقي: {exp_txt}")
        shown += 1
    return "\n".join(lines)


def list_users_text(limit=30):
    users = load_json(USERS_FILE, {})
    if not users:
        return "لا يوجد مستخدمون بعد."

    items = list(users.items())[-limit:]
    lines = ["<b>آخر المستخدمين</b>\n"]

    for i, (uid, data) in enumerate(reversed(items), 1):
        name = data.get("name") or "Unknown"
        username = data.get("username")
        username_text = f"@{username}" if username else "-"
        plan = "VIP" if is_vip(uid) else "Free"
        banned = " | محظور" if is_banned(uid) else ""
        lines.append(f"{i}. <b>{name}</b>\nID: <code>{uid}</code>\nUsername: {username_text}\nPlan: {plan}{banned}\n")

    return "\n".join(lines)


# =========================================================
# VIP CODES
# =========================================================

def generate_vip_code(days, user_limit, watch_limit, link_quality, user_quality, date_filter, created_by):
    codes = load_json(CODES_FILE, {})
    code = "VIP-" + uuid.uuid4().hex[:10].upper()

    codes[code] = {
        "created_at": int(time.time()),
        "created_by": int(created_by),
        "used": False,
        "used_by": None,
        "used_at": None,
        "days": int(days),
        "user_limit": int(user_limit),
        "watch_limit": int(watch_limit),
        "link_quality": link_quality,
        "user_quality": user_quality,
        "date_filter": bool(date_filter),
    }

    save_json(CODES_FILE, codes)
    return code


def redeem_code(user_id, code):
    codes = load_json(CODES_FILE, {})
    code = code.strip().upper()

    if code not in codes:
        return False, "❌ الكود غير صحيح."

    item = codes[code]

    if item.get("used"):
        return False, "❌ هذا الكود مستعمل من قبل."

    add_vip(
        user_id=user_id,
        days=item["days"],
        user_limit=item["user_limit"],
        watch_limit=item["watch_limit"],
        link_quality=item["link_quality"],
        user_quality=item["user_quality"],
        date_filter=item["date_filter"],
    )

    item["used"] = True
    item["used_by"] = int(user_id)
    item["used_at"] = int(time.time())
    codes[code] = item
    save_json(CODES_FILE, codes)

    return True, f"""
💎 تم تفعيل VIP بنجاح

⏳ المدة: {item["days"]} يوم
📥 تحميل من يوزر: {item["user_limit"]} فيديو
👁 مراقبة: {item["watch_limit"]} حساب
🔗 جودة الرابط: {item["link_quality"]}
📥 جودة اليوزر: {item["user_quality"]}
📆 من تاريخ إلى تاريخ: {"نعم" if item["date_filter"] else "لا"}
"""


def list_codes_text(limit=30):
    codes = load_json(CODES_FILE, {})
    if not codes:
        return "🎟 لا توجد أكواد VIP."

    items = list(codes.items())[-limit:]
    lines = ["🎟 آخر أكواد VIP:\n"]

    for i, (code, item) in enumerate(reversed(items), 1):
        status = "✅ مستعمل" if item.get("used") else "🆕 غير مستعمل"
        used_by = item.get("used_by") or "-"
        lines.append(
            f"{i}. `{code}`\n"
            f"   {status} | مستخدم: {used_by}\n"
            f"   {item.get('days')} يوم | يوزر {item.get('user_limit')} | مراقبة {item.get('watch_limit')}\n"
        )
    return "\n".join(lines)


# =========================================================
# ADS / BROADCAST
# =========================================================

def get_ads():
    return load_json(ADS_FILE, {
        "enabled": False,
        "text": "📢 إعلان: فعّل VIP للحصول على جودة أفضل ومزايا أكثر."
    })


def set_ad(enabled, text=None):
    ads = get_ads()
    ads["enabled"] = bool(enabled)
    if text is not None:
        ads["text"] = text
    save_json(ADS_FILE, ads)


async def send_ad_if_enabled(message, user_id):
    if is_vip(user_id):
        return

    ads = get_ads()
    if ads.get("enabled"):
        await message.reply_text(ads.get("text", ""))


async def broadcast_to_users(bot, text):
    users = load_json(USERS_FILE, {})
    sent = 0
    failed = 0

    for uid in list(users.keys()):
        try:
            await bot.send_message(chat_id=int(uid), text=text)
            sent += 1
            await asyncio.sleep(0.06)
        except Exception:
            failed += 1

    return sent, failed



def add_log(action, user_id=None, detail=""):
    logs = load_json(LOGS_FILE, [])
    logs.append({
        "time": int(time.time()),
        "action": action,
        "user_id": int(user_id) if str(user_id).isdigit() else str(user_id),
        "detail": str(detail)[:500],
    })
    if len(logs) > 200:
        logs = logs[-200:]
    save_json(LOGS_FILE, logs)


def logs_text(limit=30):
    logs = load_json(LOGS_FILE, [])
    if not logs:
        return "<b>السجلات</b>\n\nلا توجد سجلات بعد."

    lines = ["<b>آخر السجلات</b>\n"]
    for item in reversed(logs[-limit:]):
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.get("time", 0)))
        lines.append(
            f"{t}\n"
            f"User: <code>{item.get('user_id')}</code>\n"
            f"Action: <b>{item.get('action')}</b>\n"
            f"{item.get('detail', '')}\n"
        )
    return "\n".join(lines)

# =========================================================
# DAILY USAGE
# =========================================================

def today_key():
    return time.strftime("%Y-%m-%d")


def get_daily_usage(user_id):
    data = load_json(DAILY_FILE, {})
    return int(data.get(str(user_id), {}).get(today_key(), 0))


def add_daily_usage(user_id, amount):
    data = load_json(DAILY_FILE, {})
    uid = str(user_id)
    day = today_key()

    data.setdefault(uid, {})
    data[uid][day] = int(data[uid].get(day, 0)) + int(amount)
    save_json(DAILY_FILE, data)


def remaining_user_downloads(user_id):
    plan = get_plan(user_id)
    used = get_daily_usage(user_id)
    return max(0, plan["user_limit"] - used)


# =========================================================
# FORCE SUB
# =========================================================

async def is_user_subscribed(bot, user_id):
    cfg = get_bot_config()
    if not cfg.get("force_sub_enabled", True):
        return True

    if is_admin(user_id):
        return True

    try:
        member = await bot.get_chat_member(cfg["required_channel"], user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False


def subscribe_menu():
    cfg = get_bot_config()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 اشترك في القناة", url=cfg["required_channel_link"])],
        [InlineKeyboardButton("✅ تحقّق من الاشتراك", callback_data="check_subscription")],
    ])


def force_sub_text():
    cfg = get_bot_config()
    return f"""
🔒 الاشتراك إجباري

لاستعمال البوت لازم تشترك في القناة:

{cfg["required_channel_link"]}

بعد الاشتراك اضغط:
✅ تحقّق من الاشتراك
"""


# =========================================================
# TIKTOK HELPERS
# =========================================================

def clean_username(text):
    text = text.strip()
    text = text.replace("@", "")
    text = text.replace("https://www.tiktok.com/@", "")
    text = text.replace("https://tiktok.com/@", "")
    text = text.replace("www.tiktok.com/@", "")
    text = text.replace("tiktok.com/@", "")
    text = text.split("/")[0].split("?")[0]
    return re.sub(r"[^A-Za-z0-9._-]", "", text)


def is_tiktok_url(text):
    return "tiktok.com" in text.lower()


def file_size_mb(path):
    return path.stat().st_size / (1024 * 1024)


def file_id_from_path(path):
    parts = path.stem.split("_")
    return parts[-1] if parts else path.stem


def quality_format(quality):
    if quality == "best":
        return "bestvideo*+bestaudio/best"
    if quality == "normal":
        return "mp4/best"
    if quality == "light":
        return "worst[ext=mp4]/worst"
    return "mp4/best"


def build_opts(folder, quality, count=None, date_after=None, date_before=None):
    opts = {
        "outtmpl": str(folder / "%(uploader)s_%(id)s.%(ext)s"),
        "format": quality_format(quality),
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "restrictfilenames": True,
        "merge_output_format": "mp4",
    }

    if count:
        opts["playlist_items"] = f"1-{count}"

    if date_after or date_before:
        start = date_after.replace("-", "") if date_after else None
        end = date_before.replace("-", "") if date_before else None
        opts["daterange"] = DateRange(start, end)

    return opts


def download_tiktok(url, folder, quality, count=None, date_after=None, date_before=None):
    before = set(folder.glob("*"))

    with yt_dlp.YoutubeDL(build_opts(folder, quality, count, date_after, date_before)) as ydl:
        ydl.download([url])

    after = set(folder.glob("*"))
    new_files = [f for f in after - before if f.is_file()]
    files = [f for f in new_files if f.suffix.lower() in [".mp4", ".mov", ".mkv", ".webm"]]
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files


# =========================================================
# HISTORY / WATCH
# =========================================================

def load_history():
    return load_json(HISTORY_FILE, {})


def is_downloaded(chat_id, item_id):
    data = load_history()
    return item_id in data.get(str(chat_id), [])


def add_history(chat_id, item_id):
    data = load_history()
    key = str(chat_id)
    data.setdefault(key, [])

    if item_id not in data[key]:
        data[key].append(item_id)

    save_json(HISTORY_FILE, data)


def clear_history(chat_id):
    data = load_history()
    data[str(chat_id)] = []
    save_json(HISTORY_FILE, data)


def load_watchlist():
    return load_json(WATCH_FILE, {})


def save_watchlist(data):
    save_json(WATCH_FILE, data)



# =========================================================
# STATE SYSTEM
# =========================================================

STATE_KEY = "waiting_for"


def set_state(context, state):
    context.user_data[STATE_KEY] = state


def get_state(context):
    return context.user_data.get(STATE_KEY)


def clear_state(context):
    context.user_data.pop(STATE_KEY, None)


# =========================================================
# MENUS
# =========================================================

def main_menu(user_id=None):
    buttons = [
        [
            InlineKeyboardButton("تحميل فيديو", callback_data="download_link"),
            InlineKeyboardButton("تحميل من حساب", callback_data="download_user"),
        ],
        [
            InlineKeyboardButton("المراقبة", callback_data="watch_menu"),
            InlineKeyboardButton("VIP", callback_data="vip_menu"),
        ],
        [
            InlineKeyboardButton("الإعدادات", callback_data="settings"),
            InlineKeyboardButton("الحالة", callback_data="status"),
        ],
        [
            InlineKeyboardButton("المساعدة", callback_data="help"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def settings_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("إرسال كملف", callback_data="send_document"),
            InlineKeyboardButton("إرسال كفيديو", callback_data="send_video"),
        ],
        [
            InlineKeyboardButton("جودة عادية", callback_data="quality_normal"),
            InlineKeyboardButton("أفضل جودة", callback_data="quality_best"),
        ],
        [
            InlineKeyboardButton("حذف سجل التحميل", callback_data="clear_history"),
        ],
        [
            InlineKeyboardButton("رجوع", callback_data="back_main"),
        ],
    ])


def vip_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("تفعيل كود", callback_data="redeem_code"),
            InlineKeyboardButton("تواصل مع الأدمن", callback_data="contact_admin"),
        ],
        [
            InlineKeyboardButton("تسجيل لايف", callback_data="download_live"),
            InlineKeyboardButton("إيقاف لايف", callback_data="stop_live"),
        ],
        [
            InlineKeyboardButton("رجوع", callback_data="back_main"),
        ],
    ])


def watch_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("إضافة حساب", callback_data="watch_add"),
            InlineKeyboardButton("حذف حساب", callback_data="watch_remove"),
        ],
        [
            InlineKeyboardButton("قائمة الحسابات", callback_data="watchlist"),
        ],
        [
            InlineKeyboardButton("رجوع", callback_data="back_main"),
        ],
    ])


def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("VIP", callback_data="admin_vip_section"),
            InlineKeyboardButton("الإعلانات", callback_data="admin_ads_section"),
        ],
        [
            InlineKeyboardButton("المستخدمون", callback_data="admin_users_section"),
            InlineKeyboardButton("إعدادات البوت", callback_data="admin_config"),
        ],
        [
            InlineKeyboardButton("السجلات", callback_data="admin_logs"),
            InlineKeyboardButton("الإحصائيات", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("رجوع", callback_data="back_main"),
        ],
    ])


def admin_vip_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("إنشاء كود VIP", callback_data="admin_create_code"),
            InlineKeyboardButton("الأكواد", callback_data="admin_codes"),
        ],
        [
            InlineKeyboardButton("قائمة VIP", callback_data="admin_vips"),
            InlineKeyboardButton("حذف VIP", callback_data="admin_remove_vip"),
        ],
        [
            InlineKeyboardButton("إضافة VIP", callback_data="admin_add_vip"),
            InlineKeyboardButton("تمديد VIP", callback_data="admin_extend_vip"),
        ],
        [
            InlineKeyboardButton("رجوع للأدمن", callback_data="admin_panel"),
        ],
    ])


def admin_ads_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("تفعيل إعلان", callback_data="admin_ad_on"),
            InlineKeyboardButton("إيقاف إعلان", callback_data="admin_ad_off"),
        ],
        [
            InlineKeyboardButton("تغيير الإعلان", callback_data="admin_set_ad"),
            InlineKeyboardButton("إعلان جماعي", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("رجوع للأدمن", callback_data="admin_panel"),
        ],
    ])


def admin_users_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("آخر المستخدمين", callback_data="admin_users_list"),
        ],
        [
            InlineKeyboardButton("حظر", callback_data="admin_ban"),
            InlineKeyboardButton("فك الحظر", callback_data="admin_unban"),
        ],
        [
            InlineKeyboardButton("رجوع للأدمن", callback_data="admin_panel"),
        ],
    ])


def admin_config_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Free يوزر 3", callback_data="cfg_free_user_3"),
            InlineKeyboardButton("Free يوزر 5", callback_data="cfg_free_user_5"),
            InlineKeyboardButton("Free يوزر 10", callback_data="cfg_free_user_10"),
        ],
        [
            InlineKeyboardButton("Free مراقبة 1", callback_data="cfg_free_watch_1"),
            InlineKeyboardButton("Free مراقبة 2", callback_data="cfg_free_watch_2"),
            InlineKeyboardButton("Free مراقبة 5", callback_data="cfg_free_watch_5"),
        ],
        [
            InlineKeyboardButton("🔒 تفعيل اشتراك", callback_data="cfg_force_on"),
            InlineKeyboardButton("🔓 إيقاف اشتراك", callback_data="cfg_force_off"),
        ],
        [InlineKeyboardButton("📢 تغيير قناة الاشتراك", callback_data="cfg_set_channel")],
        [InlineKeyboardButton("⬅️ رجوع للأدمن", callback_data="admin_panel")],
    ])


def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("إلغاء والرجوع", callback_data="cancel_current")]
    ])

# =========================================================
# HTML MESSAGE HELPERS
# =========================================================

async def send_html(message, text, reply_markup=None):
    await message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def edit_html(query, text, reply_markup=None):
    await query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# =========================================================
# TEXTS
# =========================================================

def account_label(user_id):
    return "💎 VIP" if is_vip(user_id) else "🆓 Free"


def welcome_text(user_id):
    account = "VIP" if is_vip(user_id) else "Free"
    admin_note = "\n<b>أمر الأدمن:</b> /admin" if is_admin(user_id) else ""

    return f"""
<b>TikSave Pro</b>

مرحبًا بك.
اختر الخدمة التي تريدها من القائمة.

<b>الخطة:</b> {account}{admin_note}
"""


def help_text():
    return """
<b>المساعدة</b>

<b>تحميل فيديو</b>
أرسل رابط فيديو TikTok وسيتم تحميله.

<b>تحميل من حساب</b>
أرسل اسم المستخدم بهذا الشكل:
<code>@username</code>

<b>المراقبة</b>
أضف حسابًا ليتم فحصه تلقائيًا وإرسال الفيديوهات الجديدة.

<b>VIP</b>
جودة أفضل، حدود أكبر، تحميل حسب التاريخ، وتسجيل لايف.

<b>الأوامر</b>
<code>/start</code> تحديث القائمة
<code>/admin</code> لوحة الأدمن
<code>/live رابط_اللايف 60</code> تسجيل لايف
<code>/stoplive</code> إيقاف اللايف
"""


def status_text(user_id, chat_id):
    plan = get_plan(user_id)
    used = get_daily_usage(user_id)
    remaining = remaining_user_downloads(user_id)
    watch = load_watchlist().get(str(chat_id), [])
    settings = get_settings(chat_id)

    send_text = "ملف" if settings["send_as"] == "document" else "فيديو"
    quality_text = "أفضل جودة" if settings["preferred_quality"] == "best" else "عادية"
    account = "VIP" if is_vip(user_id) else "Free"

    return f"""
<b>الحالة</b>

<b>الحساب</b>
الخطة: <b>{account}</b>
انتهاء VIP: <b>{vip_days_left(user_id)}</b>

<b>الاستخدام اليومي</b>
المستعمل: <b>{used}</b>
الباقي: <b>{remaining}</b>
الحد: <b>{plan["user_limit"]}</b>

<b>المراقبة</b>
الحسابات: <b>{len(watch)}</b> / <b>{plan["watch_limit"]}</b>

<b>الإعدادات</b>
الإرسال: <b>{send_text}</b>
الجودة: <b>{quality_text}</b>
"""


def settings_text(user_id, chat_id):
    settings = get_settings(chat_id)
    plan = get_plan(user_id)

    send_text = "ملف" if settings["send_as"] == "document" else "فيديو"
    quality_text = "أفضل جودة" if settings["preferred_quality"] == "best" else "عادية"

    return f"""
<b>الإعدادات</b>

طريقة الإرسال: <b>{send_text}</b>
الجودة المفضلة: <b>{quality_text}</b>

<b>صلاحياتك</b>
جودة الرابط: <b>{plan["link_quality"]}</b>
جودة الحساب: <b>{plan["user_quality"]}</b>
حد الحساب: <b>{plan["user_limit"]}</b>
حد المراقبة: <b>{plan["watch_limit"]}</b>
"""


def vip_info_text(user_id):
    return f"""
<b>VIP</b>

<b>المزايا</b>
• جودة أفضل
• تحميل أكثر من الحساب
• مراقبة حسابات أكثر
• تحميل حسب التاريخ
• تسجيل لايف وإرسال مقطع كل 4 دقائق
• بدون إعلانات

<b>حالتك</b>
الخطة: <b>{"VIP" if is_vip(user_id) else "Free"}</b>
انتهاء VIP: <b>{vip_days_left(user_id)}</b>

<b>تسجيل لايف</b>
<code>/live رابط_اللايف 60</code>
"""


def contact_admin_text():
    return f"""
<b>التواصل مع الأدمن</b>

لشراء VIP أو طلب الدعم:
<b>{ADMIN_USERNAME}</b>
"""


def watch_panel_text(user_id, chat_id):
    plan = get_plan(user_id)
    items = load_watchlist().get(str(chat_id), [])
    return f"""
<b>المراقبة</b>

أضف حساب TikTok ليتم فحصه تلقائيًا.

الحسابات: <b>{len(items)}</b> / <b>{plan["watch_limit"]}</b>
"""


def admin_vip_text():
    return """
<b>إدارة VIP</b>

أنشئ أكواد VIP، اعرض الأكواد، راجع قائمة VIP أو احذف VIP من مستخدم.
"""


def admin_ads_text():
    return """
<b>إدارة الإعلانات</b>

تحكم في الإعلان الذي يظهر للمستخدمين المجانيين أو أرسل إعلانًا جماعيًا لكل المستخدمين.
"""


def admin_users_text():
    return """
<b>إدارة المستخدمين</b>

اعرض آخر المستخدمين، احظر مستخدمًا أو فك الحظر عنه.
"""


def admin_panel_text():
    return """
<b>لوحة الأدمن</b>

اختر القسم الذي تريد إدارته.
"""


def admin_config_text():
    cfg = get_bot_config()
    return f"""
⚙️ إعدادات البوت

📢 قناة الاشتراك: {cfg["required_channel"]}
🔗 رابط القناة: {cfg["required_channel_link"]}
🔒 الاشتراك الإجباري: {"مفعل" if cfg["force_sub_enabled"] else "متوقف"}

Free:
📥 حد اليوزر اليومي: {cfg["free_user_daily_limit"]}
👁 حد المراقبة: {cfg["free_watch_limit"]}
🎚 الجودة: {cfg["free_quality"]}

اختر التعديل:
"""


# =========================================================
# COMMANDS
# =========================================================

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(context)
    await send_html(
        update.message,
        "تم إلغاء العملية الحالية.\n\n" + welcome_text(update.effective_user.id),
        reply_markup=main_menu(update.effective_user.id),
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(context)
    register_user(update.effective_user)
    user_id = update.effective_user.id

    if is_banned(user_id) and not is_admin(user_id):
        await update.message.reply_text("🚫 تم حظرك من استعمال البوت.")
        return

    if not await is_user_subscribed(context.bot, user_id):
        await update.message.reply_text(force_sub_text(), reply_markup=subscribe_menu())
        return

    await send_html(update.message, welcome_text(user_id), reply_markup=main_menu(user_id))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_html(update.message, help_text(), reply_markup=main_menu(update.effective_user.id))


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text(f"غير مسموح.\n\nID تاعك هو:\n{user_id}")
        return

    await send_html(update.message, admin_panel_text(), reply_markup=admin_menu())


async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استعمل:\n/redeem CODE")
        return

    ok, msg = redeem_code(update.effective_user.id, context.args[0])
    await update.message.reply_text(msg, reply_markup=main_menu(update.effective_user.id))


async def gencode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("غير مسموح.")
        return

    if len(context.args) < 6:
        await update.message.reply_text(
            "استعمل:\n/gencode days user_limit watch_limit link_quality user_quality date_filter\n\n"
            "مثال:\n/gencode 30 15 5 best best yes"
        )
        return

    try:
        days = int(context.args[0])
        user_limit = int(context.args[1])
        watch_limit = int(context.args[2])
        link_quality = context.args[3]
        user_quality = context.args[4]
        date_filter = context.args[5].lower() in ["yes", "true", "1", "نعم"]
    except Exception:
        await update.message.reply_text("❌ الصيغة خطأ.")
        return

    code = generate_vip_code(days, user_limit, watch_limit, link_quality, user_quality, date_filter, user_id)

    await update.message.reply_text(
        f"🎟 كود VIP جديد:\n\n`{code}`\n\n"
        f"⏳ المدة: {days} يوم\n"
        f"📥 تحميل يوزر: {user_limit}\n"
        f"👁 مراقبة: {watch_limit}\n"
        f"🔗 جودة الرابط: {link_quality}\n"
        f"📥 جودة اليوزر: {user_quality}\n"
        f"📆 من تاريخ إلى تاريخ: {'نعم' if date_filter else 'لا'}",
        parse_mode="Markdown"
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = q.from_user.id
    chat_id = q.message.chat_id
    data = q.data

    register_user(q.from_user)

    if data in ["cancel_current", "back_main"]:
        clear_state(context)
        await edit_html(q, welcome_text(user_id), reply_markup=main_menu(user_id))
        return


    if is_banned(user_id) and not is_admin(user_id):
        await q.answer("تم حظرك.", show_alert=True)
        return

    if data == "check_subscription":
        if await is_user_subscribed(context.bot, user_id):
            await q.edit_message_text("✅ تم التحقق من الاشتراك.", reply_markup=main_menu(user_id))
        else:
            await q.edit_message_text(force_sub_text(), reply_markup=subscribe_menu())
        return

    if not await is_user_subscribed(context.bot, user_id):
        await q.edit_message_text(force_sub_text(), reply_markup=subscribe_menu())
        return


    if data == "stop_live":
        task = LIVE_TASKS.get(chat_id)
        if not task:
            await q.answer("لا يوجد تسجيل لايف يعمل الآن.", show_alert=True)
            return
        task.cancel()
        LIVE_TASKS.pop(chat_id, None)
        await q.answer("تم طلب إيقاف اللايف.", show_alert=True)
        await edit_html(q, vip_info_text(user_id), reply_markup=vip_menu())
        return

    if data == "download_live":
        if not is_vip(user_id) and not is_admin(user_id):
            await q.answer("تسجيل اللايف متاح لـ VIP فقط.", show_alert=True)
            return

        set_state(context, "download_live")
        await edit_html(
            q,
            "أرسل رابط اللايف والمدة بالدقائق.\n\nمثال:\n<code>https://www.tiktok.com/@username/live 60</code>",
            reply_markup=back_menu()
        )
        return

    if data == "vip_menu":
        await edit_html(q, vip_info_text(user_id), reply_markup=vip_menu())
        return

    if data == "contact_admin":
        await edit_html(q, contact_admin_text(), reply_markup=vip_menu())
        return

    if data == "watch_menu":
        await edit_html(q, watch_panel_text(user_id, chat_id), reply_markup=watch_menu())
        return

    if data == "admin_vip_section":
        if is_admin(user_id):
            await edit_html(q, admin_vip_text(), reply_markup=admin_vip_menu())
        return

    if data == "admin_ads_section":
        if is_admin(user_id):
            await edit_html(q, admin_ads_text(), reply_markup=admin_ads_menu())
        return

    if data == "admin_users_section":
        if is_admin(user_id):
            await edit_html(q, admin_users_text(), reply_markup=admin_users_menu())
        return

    if data == "admin_users_list":
        if is_admin(user_id):
            await q.edit_message_text(list_users_text(), reply_markup=admin_users_menu(), parse_mode="HTML")
        return

    if data == "download_link":
        set_state(context, "download_link")
        await q.edit_message_text("أرسل رابط فيديو TikTok:", reply_markup=back_menu())
        return

    if data == "download_user":
        set_state(context, "download_user")
        await q.edit_message_text("أرسل يوزر TikTok:\nمثال: @username", reply_markup=back_menu())
        return

    if data == "download_fromto":
        if not get_plan(user_id)["date_filter"]:
            await q.answer("الميزة متاحة لـ VIP فقط.", show_alert=True)
            return
        set_state(context, "download_fromto")
        await q.edit_message_text("📆 أرسل هكذا:\n\n@username 2026-04-01 2026-05-01", reply_markup=back_menu())
        return

    if data == "watch_add":
        set_state(context, "watch_add")
        await q.edit_message_text("أرسل يوزر الحساب لمراقبته:", reply_markup=back_menu())
        return

    if data == "watch_remove":
        set_state(context, "watch_remove")
        await q.edit_message_text("أرسل يوزر الحساب لإيقاف المراقبة:", reply_markup=back_menu())
        return

    if data == "watchlist":
        await show_watchlist(q.message, chat_id, user_id)
        return

    if data == "status":
        await edit_html(q, status_text(user_id, chat_id), reply_markup=main_menu(user_id))
        return

    if data == "settings":
        await edit_html(q, settings_text(user_id, chat_id), reply_markup=settings_menu())
        return

    if data == "send_document":
        set_setting(chat_id, "send_as", "document")
        await edit_html(q, settings_text(user_id, chat_id), reply_markup=settings_menu())
        return

    if data == "send_video":
        set_setting(chat_id, "send_as", "video")
        await edit_html(q, settings_text(user_id, chat_id), reply_markup=settings_menu())
        return

    if data == "quality_normal":
        set_setting(chat_id, "preferred_quality", "normal")
        await edit_html(q, settings_text(user_id, chat_id), reply_markup=settings_menu())
        return

    if data == "quality_best":
        if not is_vip(user_id):
            await q.answer("أفضل جودة متاحة لـ VIP فقط.", show_alert=True)
            return
        set_setting(chat_id, "preferred_quality", "best")
        await edit_html(q, settings_text(user_id, chat_id), reply_markup=settings_menu())
        return

    if data == "clear_history":
        clear_history(chat_id)
        await q.edit_message_text("🧹 تم حذف سجل التحميل.", reply_markup=settings_menu())
        return

    if data == "help":
        await edit_html(q, help_text(), reply_markup=main_menu(user_id))
        return

    if data == "redeem_code":
        set_state(context, "redeem_code")
        await q.edit_message_text("أرسل كود VIP الآن:", reply_markup=back_menu())
        return

    # ADMIN
    if data == "admin_panel":
        if not is_admin(user_id):
            await q.answer("أنت لست أدمن.", show_alert=True)
            return
        await edit_html(q, admin_panel_text(), reply_markup=admin_menu())
        return

    if data == "admin_create_code":
        if not is_admin(user_id):
            return
        set_state(context, "admin_create_code")
        await q.edit_message_text(
            "🎟 أرسل إعدادات الكود هكذا:\n\n"
            "days user_limit watch_limit link_quality user_quality date_filter\n\n"
            "مثال:\n30 15 5 best best yes",
            reply_markup=admin_menu()
        )
        return

    if data == "admin_ad_on":
        if is_admin(user_id):
            set_ad(True)
            await q.edit_message_text("📢 تم تفعيل الإعلان.", reply_markup=admin_menu())
        return

    if data == "admin_ad_off":
        if is_admin(user_id):
            set_ad(False)
            await q.edit_message_text("🚫 تم إيقاف الإعلان.", reply_markup=admin_menu())
        return

    if data == "admin_set_ad":
        if is_admin(user_id):
            set_state(context, "admin_set_ad")
            await q.edit_message_text("✏️ أرسل نص الإعلان الجديد:", reply_markup=admin_menu())
        return

    if data == "admin_broadcast":
        if is_admin(user_id):
            set_state(context, "admin_broadcast")
            await q.edit_message_text("📣 أرسل نص الإعلان الجماعي:", reply_markup=admin_menu())
        return

    if data == "admin_codes":
        if is_admin(user_id):
            await q.edit_message_text(list_codes_text(), reply_markup=admin_menu(), parse_mode="Markdown")
        return

    if data == "admin_vips":
        if is_admin(user_id):
            await q.edit_message_text(list_vip_text(), reply_markup=admin_menu())
        return

    if data == "admin_config":
        if is_admin(user_id):
            await q.edit_message_text(admin_config_text(), reply_markup=admin_config_menu())
        return

    if data.startswith("cfg_free_user_") and is_admin(user_id):
        value = int(data.split("_")[-1])
        set_bot_config("free_user_daily_limit", value)
        await q.edit_message_text(admin_config_text(), reply_markup=admin_config_menu())
        return

    if data.startswith("cfg_free_watch_") and is_admin(user_id):
        value = int(data.split("_")[-1])
        set_bot_config("free_watch_limit", value)
        await q.edit_message_text(admin_config_text(), reply_markup=admin_config_menu())
        return

    if data == "cfg_force_on" and is_admin(user_id):
        set_bot_config("force_sub_enabled", True)
        await q.edit_message_text(admin_config_text(), reply_markup=admin_config_menu())
        return

    if data == "cfg_force_off" and is_admin(user_id):
        set_bot_config("force_sub_enabled", False)
        await q.edit_message_text(admin_config_text(), reply_markup=admin_config_menu())
        return

    if data == "cfg_set_channel" and is_admin(user_id):
        set_state(context, "cfg_set_channel")
        await q.edit_message_text(
            "📢 أرسل القناة والرابط هكذا:\n\n@CHANNEL https://t.me/CHANNEL",
            reply_markup=admin_config_menu()
        )
        return

    if data == "admin_ban":
        if is_admin(user_id):
            set_state(context, "admin_ban")
            await q.edit_message_text("🚫 أرسل ID المستخدم لحظره:", reply_markup=admin_menu())
        return

    if data == "admin_unban":
        if is_admin(user_id):
            set_state(context, "admin_unban")
            await q.edit_message_text("✅ أرسل ID المستخدم لفك الحظر:", reply_markup=admin_menu())
        return


    if data == "admin_add_vip":
        if is_admin(user_id):
            set_state(context, "admin_add_vip")
            await q.edit_message_text(
                "أرسل بيانات VIP بهذا الشكل:\n\nID days user_limit watch_limit link_quality user_quality date_filter\n\nمثال:\n1853431053 30 15 5 best best yes",
                reply_markup=admin_vip_menu()
            )
        return

    if data == "admin_extend_vip":
        if is_admin(user_id):
            set_state(context, "admin_extend_vip")
            await q.edit_message_text(
                "أرسل ID والمدة الإضافية بالأيام:\n\n1853431053 30",
                reply_markup=admin_vip_menu()
            )
        return

    if data == "admin_remove_vip":
        if is_admin(user_id):
            set_state(context, "admin_remove_vip")
            await q.edit_message_text("➖ أرسل ID المستخدم لحذف VIP:", reply_markup=admin_menu())
        return


    if data == "admin_cleanup":
        if not is_admin(user_id):
            return

        report = await asyncio.to_thread(cleanup_old_data)
        text = f"""<b>تنظيف البيانات</b>

تم تنظيف البيانات المؤقتة.

الملفات المؤقتة المحذوفة: <b>{report["downloads_removed"]}</b>
مستخدمو daily_usage المنظفون: <b>{report["daily_users_cleaned"]}</b>
الأكواد المستعملة المحذوفة: <b>{report["codes_removed"]}</b>
السجلات المختصرة: <b>{report["history_trimmed"]}</b>
"""
        try:
            await edit_html(q, text, reply_markup=admin_menu())
        except Exception:
            await q.edit_message_text(text, reply_markup=admin_menu(), parse_mode="HTML")
        return


    if data == "admin_logs":
        if is_admin(user_id):
            try:
                await edit_html(q, logs_text(), reply_markup=admin_menu())
            except Exception:
                await q.edit_message_text(logs_text(), reply_markup=admin_menu(), parse_mode="HTML")
        return

    if data == "admin_stats":
        if not is_admin(user_id):
            return

        users = load_json(USERS_FILE, {})
        vip = load_json(VIP_FILE, {})
        codes = load_json(CODES_FILE, {})
        watch = load_json(WATCH_FILE, {})
        banned = load_json(BAN_FILE, {})
        history = load_json(HISTORY_FILE, {})

        used_codes = sum(1 for c in codes.values() if c.get("used"))
        total_downloads = sum(len(v) for v in history.values())

        text = f"""
📊 إحصائيات البوت

👥 المستخدمين: {len(users)}
💎 VIP: {len(vip)}
🎟 الأكواد: {len(codes)}
✅ أكواد مستعملة: {used_codes}
📥 مجموع التحميلات: {total_downloads}
👁 مجموع المراقبة: {sum(len(v) for v in watch.values())}
🚫 المحظورين: {len(banned)}
"""
        await q.edit_message_text(text, reply_markup=admin_menu())
        return


# =========================================================
# TEXT HANDLER
# =========================================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    text = update.message.text.strip()
    user_id = update.effective_user.id
    waiting = get_state(context)

    if text in ["/cancel", "cancel", "رجوع", "إلغاء", "الغاء"]:
        clear_state(context)
        await send_html(update.message, welcome_text(user_id), reply_markup=main_menu(user_id))
        return


    if is_banned(user_id) and not is_admin(user_id):
        await update.message.reply_text("🚫 تم حظرك من استعمال البوت.")
        return

    if not await is_user_subscribed(context.bot, user_id):
        await update.message.reply_text(force_sub_text(), reply_markup=subscribe_menu())
        return


    if waiting == "download_live":
        clear_state(context)

        if not is_vip(user_id) and not is_admin(user_id):
            await update.message.reply_text("تسجيل اللايف متاح لـ VIP فقط.")
            return

        parts = text.split()
        if not parts or not is_tiktok_url(parts[0]):
            await update.message.reply_text("أرسل رابط TikTok Live صحيح.", reply_markup=main_menu(user_id))
            return

        live_url = parts[0]
        total_minutes = DEFAULT_LIVE_MINUTES

        if len(parts) >= 2:
            try:
                total_minutes = int(parts[1])
            except Exception:
                total_minutes = DEFAULT_LIVE_MINUTES

        total_minutes = max(4, min(total_minutes, MAX_LIVE_MINUTES))
        await start_live_recording(update, context, live_url, total_minutes)
        return

    if waiting == "download_link":
        clear_state(context)
        if not is_tiktok_url(text):
            await update.message.reply_text("❌ هذا ليس رابط TikTok.", reply_markup=main_menu(user_id))
            return
        await process_link(update, text)
        return

    if waiting == "download_user":
        clear_state(context)
        await process_username(update, clean_username(text))
        return

    if waiting == "download_fromto":
        clear_state(context)
        parts = text.split()
        if len(parts) < 3:
            await update.message.reply_text("❌ الصيغة خطأ.\nمثال:\n@username 2026-04-01 2026-05-01")
            return
        await process_username(update, clean_username(parts[0]), parts[1], parts[2])
        return

    if waiting == "watch_add":
        clear_state(context)
        await add_watch(update, clean_username(text))
        return

    if waiting == "watch_remove":
        clear_state(context)
        await remove_watch(update, clean_username(text))
        return

    if waiting == "redeem_code":
        clear_state(context)
        ok, msg = redeem_code(user_id, text)
        await update.message.reply_text(msg, reply_markup=main_menu(user_id))
        return


    if waiting == "admin_add_vip":
        clear_state(context)
        if not is_admin(user_id):
            return

        parts = text.split()
        if len(parts) < 7:
            await update.message.reply_text(
                "الصيغة خطأ.\nمثال:\n1853431053 30 15 5 best best yes",
                reply_markup=admin_vip_menu()
            )
            return

        try:
            target_id = int(parts[0])
            days = int(parts[1])
            user_limit = int(parts[2])
            watch_limit = int(parts[3])
            link_quality = parts[4]
            user_quality = parts[5]
            date_filter = parts[6].lower() in ["yes", "true", "1", "نعم"]
        except Exception:
            await update.message.reply_text("الأرقام غير صحيحة.", reply_markup=admin_vip_menu())
            return

        add_vip(target_id, days, user_limit, watch_limit, link_quality, user_quality, date_filter)
        add_log("admin_add_vip", user_id, f"target={target_id}, days={days}")
        await update.message.reply_text(
            f"تمت إضافة VIP للمستخدم: {target_id}\nالمدة: {days} يوم",
            reply_markup=admin_vip_menu()
        )
        return

    if waiting == "admin_extend_vip":
        clear_state(context)
        if not is_admin(user_id):
            return

        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text("الصيغة خطأ.\nمثال:\n1853431053 30", reply_markup=admin_vip_menu())
            return

        try:
            target_id = int(parts[0])
            days = int(parts[1])
        except Exception:
            await update.message.reply_text("الأرقام غير صحيحة.", reply_markup=admin_vip_menu())
            return

        extend_vip(target_id, days)
        add_log("admin_extend_vip", user_id, f"target={target_id}, days={days}")
        await update.message.reply_text(
            f"تم تمديد VIP للمستخدم: {target_id}\nالمدة الإضافية: {days} يوم",
            reply_markup=admin_vip_menu()
        )
        return

    if waiting == "admin_create_code":
        clear_state(context)
        if not is_admin(user_id):
            return

        parts = text.split()
        if len(parts) < 6:
            await update.message.reply_text("❌ الصيغة خطأ.\nمثال:\n30 15 5 best best yes", reply_markup=admin_menu())
            return

        try:
            days = int(parts[0])
            user_limit = int(parts[1])
            watch_limit = int(parts[2])
            link_quality = parts[3]
            user_quality = parts[4]
            date_filter = parts[5].lower() in ["yes", "true", "1", "نعم"]
        except Exception:
            await update.message.reply_text("❌ الأرقام غير صحيحة.", reply_markup=admin_menu())
            return

        code = generate_vip_code(days, user_limit, watch_limit, link_quality, user_quality, date_filter, user_id)

        await update.message.reply_text(
            f"🎟 كود VIP جديد:\n\n`{code}`\n\n"
            f"⏳ المدة: {days} يوم\n"
            f"📥 تحميل يوزر: {user_limit}\n"
            f"👁 مراقبة: {watch_limit}\n"
            f"🔗 جودة الرابط: {link_quality}\n"
            f"📥 جودة اليوزر: {user_quality}\n"
            f"📆 من تاريخ إلى تاريخ: {'نعم' if date_filter else 'لا'}",
            parse_mode="Markdown",
            reply_markup=admin_menu()
        )
        return

    if waiting == "admin_set_ad":
        clear_state(context)
        if is_admin(user_id):
            set_ad(True, text)
            await update.message.reply_text("✅ تم تغيير الإعلان وتفعيله.", reply_markup=admin_menu())
        return

    if waiting == "admin_broadcast":
        clear_state(context)
        if is_admin(user_id):
            msg = await update.message.reply_text("📣 جاري إرسال الإعلان الجماعي...")
            sent, failed = await broadcast_to_users(context.bot, text)
            await msg.edit_text(f"✅ انتهى الإرسال.\n📤 وصل: {sent}\n❌ فشل: {failed}", reply_markup=admin_menu())
        return

    if waiting == "cfg_set_channel":
        clear_state(context)
        if is_admin(user_id):
            parts = text.split()
            if len(parts) < 2 or not parts[0].startswith("@"):
                await update.message.reply_text("❌ الصيغة خطأ.\nمثال:\n@NATOU_DZ https://t.me/NATOU_DZ", reply_markup=admin_config_menu())
                return
            set_bot_config("required_channel", parts[0])
            set_bot_config("required_channel_link", parts[1])
            await update.message.reply_text("✅ تم تغيير قناة الاشتراك.", reply_markup=admin_config_menu())
        return

    if waiting == "admin_ban":
        clear_state(context)
        if is_admin(user_id):
            ban_user(text)
            await update.message.reply_text(f"🚫 تم حظر: {text}", reply_markup=admin_menu())
        return

    if waiting == "admin_unban":
        clear_state(context)
        if is_admin(user_id):
            unban_user(text)
            await update.message.reply_text(f"✅ تم فك الحظر: {text}", reply_markup=admin_menu())
        return

    if waiting == "admin_remove_vip":
        clear_state(context)
        if is_admin(user_id):
            remove_vip(text)
            await update.message.reply_text(f"➖ تم حذف VIP من: {text}", reply_markup=admin_menu())
        return

    if is_tiktok_url(text):
        await process_link(update, text)
        return

    username = clean_username(text)
    if username:
        await process_username(update, username)
    else:
        await update.message.reply_text("استعمل القائمة:", reply_markup=main_menu(user_id))


# =========================================================
# DOWNLOAD
# =========================================================

def get_effective_quality(user_id, chat_id, download_type):
    if not is_vip(user_id):
        return get_bot_config()["free_quality"]

    settings = get_settings(chat_id)
    plan = get_plan(user_id)

    if settings.get("preferred_quality") == "best":
        return plan["link_quality"] if download_type == "link" else plan["user_quality"]

    return get_bot_config()["free_quality"]


async def process_link(update, url):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if user_id in ACTIVE_USERS:
        await update.message.reply_text("طلبك قيد المعالجة. انتظر حتى يكتمل.")
        return
    ACTIVE_USERS.add(user_id)
    quality = get_effective_quality(user_id, chat_id, "link")

    folder = DOWNLOAD_DIR / f"link_{chat_id}_{int(time.time())}"
    folder.mkdir(parents=True, exist_ok=True)

    msg = await update.message.reply_text(f"⏳ جاري تحميل الرابط بجودة: {quality}...")

    try:
        files = await asyncio.to_thread(download_tiktok, url, folder, quality)

        if not files:
            await msg.edit_text("❌ ما قدرتش نحمل هذا الرابط.")
            return

        file = files[0]
        fid = file_id_from_path(file)
        history_id = f"link_{fid}"

        if is_downloaded(chat_id, history_id):
            await msg.edit_text("✅ هذا الفيديو سبق وتحمل.")
            return

        ok = await send_file(update, file, "TikTok")

        if ok:
            add_history(chat_id, history_id)
            add_log("download_link", user_id, url)
            await msg.edit_text("✅ تم التحميل والإرسال.")
        else:
            await msg.edit_text("❌ لم يتم الإرسال.")

        await send_ad_if_enabled(update.message, user_id)
        await update.message.reply_text("القائمة:", reply_markup=main_menu(user_id))

    except Exception as e:
        await msg.edit_text(f"❌ صار خطأ.\n\nجرّب تحديث yt-dlp.\n\n{str(e)[:600]}")
    finally:
        ACTIVE_USERS.discard(user_id)
        shutil.rmtree(folder, ignore_errors=True)


async def process_username(update, username, date_after=None, date_before=None):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if user_id in ACTIVE_USERS:
        await update.message.reply_text("طلبك قيد المعالجة. انتظر حتى يكتمل.")
        return
    ACTIVE_USERS.add(user_id)
    plan = get_plan(user_id)

    remaining = remaining_user_downloads(user_id)

    if remaining <= 0:
        await update.message.reply_text(
            f"⏳ وصلت للحد اليومي: {plan['user_limit']} فيديو.",
            reply_markup=main_menu(user_id)
        )
        return

    if date_after or date_before:
        if not plan["date_filter"]:
            await update.message.reply_text("❌ التحميل من تاريخ إلى تاريخ متاح لـ VIP فقط.")
            return

    count = remaining
    quality = get_effective_quality(user_id, chat_id, "user")

    folder = DOWNLOAD_DIR / f"user_{chat_id}_{int(time.time())}"
    folder.mkdir(parents=True, exist_ok=True)

    url = f"https://www.tiktok.com/@{username}"

    if date_after or date_before:
        msg_text = f"⏳ تحميل @{username}\nمن {date_after} إلى {date_before}\nالجودة: {quality}"
    else:
        msg_text = f"⏳ تحميل آخر {count} فيديو من @{username}\nالجودة: {quality}"

    msg = await update.message.reply_text(msg_text)

    try:
        files = await asyncio.to_thread(download_tiktok, url, folder, quality, count, date_after, date_before)

        if not files:
            await msg.edit_text("❌ ما قدرتش نحمل. الحساب private أو اليوزر غلط أو ما كاينش فيديوهات.")
            return

        sent = 0
        skipped = 0

        for file in files:
            if sent >= count:
                break

            fid = file_id_from_path(file)
            history_id = f"{username}_{fid}"

            if is_downloaded(chat_id, history_id):
                skipped += 1
                continue

            ok = await send_file(update, file, f"@{username}")

            if ok:
                add_history(chat_id, history_id)
                sent += 1
            else:
                skipped += 1

        if sent > 0:
            add_daily_usage(user_id, sent)
            add_log("download_user", user_id, f"@{username} | sent={sent}")

        await msg.edit_text(f"✅ انتهى.\n📤 أُرسل: {sent}\n⏭ تخطى: {skipped}")
        await send_ad_if_enabled(update.message, user_id)
        await update.message.reply_text("القائمة:", reply_markup=main_menu(user_id))

    except Exception as e:
        await msg.edit_text(f"❌ صار خطأ.\n\n{str(e)[:600]}")
    finally:
        ACTIVE_USERS.discard(user_id)
        shutil.rmtree(folder, ignore_errors=True)


async def send_file(update, file, caption):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)

    size = file_size_mb(file)

    if size > MAX_FILE_MB:
        await update.message.reply_text(f"⚠️ الملف كبير بزاف: {size:.1f}MB")
        return False

    if settings.get("send_as") == "video":
        with open(file, "rb") as f:
            await update.message.reply_video(video=f, caption=f"🎬 {caption}")
        return True

    with open(file, "rb") as f:
        await update.message.reply_document(document=f, caption=f"📁 {caption}")

    return True


# =========================================================
# WATCH
# =========================================================

async def add_watch(update, username):
    chat_id = str(update.effective_chat.id)
    user_id = update.effective_user.id
    plan = get_plan(user_id)

    data = load_watchlist()
    data.setdefault(chat_id, [])

    if username not in data[chat_id] and len(data[chat_id]) >= plan["watch_limit"]:
        await update.message.reply_text(f"⚠️ حد المراقبة عندك: {plan['watch_limit']} حساب.", reply_markup=main_menu(user_id))
        return

    if username not in data[chat_id]:
        data[chat_id].append(username)
        save_watchlist(data)
        await update.message.reply_text(f"👁 تم إضافة @{username} للمراقبة ✅", reply_markup=main_menu(user_id))
    else:
        await update.message.reply_text("هذا الحساب موجود من قبل.", reply_markup=main_menu(user_id))


async def remove_watch(update, username):
    chat_id = str(update.effective_chat.id)
    user_id = update.effective_user.id
    data = load_watchlist()

    if chat_id in data and username in data[chat_id]:
        data[chat_id].remove(username)
        save_watchlist(data)
        await update.message.reply_text(f"🛑 تم إيقاف مراقبة @{username}", reply_markup=main_menu(user_id))
    else:
        await update.message.reply_text("هذا الحساب غير موجود في المراقبة.", reply_markup=main_menu(user_id))


async def show_watchlist(message, chat_id, user_id):
    data = load_watchlist()
    items = data.get(str(chat_id), [])

    if not items:
        await message.reply_text("📜 لا توجد حسابات مراقبة.", reply_markup=main_menu(user_id))
        return

    text = "👁 الحسابات المراقبة:\n\n"
    for i, username in enumerate(items, 1):
        text += f"{i}. @{username}\n"

    await message.reply_text(text, reply_markup=main_menu(user_id))


async def check_watchlist_job(context: ContextTypes.DEFAULT_TYPE):
    data = load_watchlist()

    if not data:
        return

    for chat_id_str, usernames in data.items():
        chat_id = int(chat_id_str)

        for username in usernames:
            folder = DOWNLOAD_DIR / f"watch_{chat_id}_{username}_{int(time.time())}"
            folder.mkdir(parents=True, exist_ok=True)

            try:
                url = f"https://www.tiktok.com/@{username}"
                files = await asyncio.to_thread(download_tiktok, url, folder, get_bot_config()["free_quality"], 1)

                if not files:
                    continue

                file = files[0]
                fid = file_id_from_path(file)
                history_id = f"watch_{username}_{fid}"

                if is_downloaded(chat_id, history_id):
                    continue

                await context.bot.send_message(chat_id=chat_id, text=f"🔔 جديد من @{username}")

                with open(file, "rb") as f:
                    await context.bot.send_document(chat_id=chat_id, document=f, caption=f"📁 @{username}")

                add_history(chat_id, history_id)

            except Exception as e:
                print(f"Watch error @{username}: {e}")

            finally:
                shutil.rmtree(folder, ignore_errors=True)


# =========================================================
# LIVE RECORDING
# =========================================================

def extract_live_stream_url(live_url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "best",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(live_url, download=False)

    if info.get("url"):
        return info["url"]

    formats = info.get("formats") or []
    formats = [f for f in formats if f.get("url")]

    if not formats:
        raise RuntimeError("لم أجد رابط مباشر للبث. ممكن اللايف انتهى أو TikTok حابس التحميل.")

    return formats[-1]["url"]


def record_live_segment(stream_url, output_path):
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        stream_url,
        "-t",
        str(LIVE_SEGMENT_SECONDS),
        "-c",
        "copy",
        str(output_path),
    ]

    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=LIVE_SEGMENT_SECONDS + 90,
    )


def compress_if_needed(input_path):
    size_mb = file_size_mb(input_path)
    if size_mb <= MAX_FILE_MB:
        return input_path

    compressed_path = input_path.with_name(input_path.stem + "_small.mp4")

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        "scale='min(720,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(compressed_path),
    ]

    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )

    if compressed_path.exists() and file_size_mb(compressed_path) <= MAX_FILE_MB:
        return compressed_path

    return input_path


async def start_live_recording(update, context, live_url, total_minutes):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id in LIVE_TASKS:
        await update.message.reply_text(
            "كاين تسجيل لايف يخدم بالفعل. استعمل /stoplive لإيقافه.",
            reply_markup=main_menu(user_id)
        )
        return

    task = asyncio.create_task(
        live_recording_loop(context, chat_id, user_id, live_url, total_minutes)
    )
    LIVE_TASKS[chat_id] = task

    await update.message.reply_text(
        f"بدأ تسجيل اللايف.\n\nالمدة: {total_minutes} دقيقة\nكل مقطع: 4 دقائق\n\nللإيقاف: /stoplive",
        reply_markup=main_menu(user_id)
    )


async def live_recording_loop(context, chat_id, user_id, live_url, total_minutes):
    folder = DOWNLOAD_DIR / f"live_{chat_id}_{int(time.time())}"
    folder.mkdir(parents=True, exist_ok=True)

    end_time = time.time() + total_minutes * 60
    part = 1

    try:
        while time.time() < end_time:
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"جاري تسجيل مقطع اللايف رقم {part}...")

                stream_url = await asyncio.to_thread(extract_live_stream_url, live_url)
                output_path = folder / f"live_part_{part}.mp4"

                await asyncio.to_thread(record_live_segment, stream_url, output_path)

                if not output_path.exists() or output_path.stat().st_size == 0:
                    await context.bot.send_message(chat_id=chat_id, text="تعذر تسجيل المقطع. ربما توقف اللايف.")
                    break

                send_path = await asyncio.to_thread(compress_if_needed, output_path)

                if file_size_mb(send_path) > MAX_FILE_MB:
                    await context.bot.send_message(chat_id=chat_id, text=f"المقطع رقم {part} كبير جدًا ولم يتم إرساله.")
                else:
                    with open(send_path, "rb") as f:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            caption=f"Live segment #{part} — 4 دقائق"
                        )

                part += 1

            except asyncio.CancelledError:
                await context.bot.send_message(chat_id=chat_id, text="تم إيقاف تسجيل اللايف.")
                break

            except Exception as e:
                await context.bot.send_message(chat_id=chat_id, text=f"خطأ في تسجيل اللايف:\n{str(e)[:500]}")
                break

        await context.bot.send_message(chat_id=chat_id, text="انتهى تسجيل اللايف.")

    finally:
        LIVE_TASKS.pop(chat_id, None)
        shutil.rmtree(folder, ignore_errors=True)


async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_vip(user_id) and not is_admin(user_id):
        await update.message.reply_text("تسجيل اللايف متاح لـ VIP فقط.")
        return

    if not context.args:
        await update.message.reply_text("استعمل:\n/live رابط_اللايف 60")
        return

    live_url = context.args[0]
    total_minutes = DEFAULT_LIVE_MINUTES

    if len(context.args) >= 2:
        try:
            total_minutes = int(context.args[1])
        except Exception:
            total_minutes = DEFAULT_LIVE_MINUTES

    total_minutes = max(4, min(total_minutes, MAX_LIVE_MINUTES))
    await start_live_recording(update, context, live_url, total_minutes)


async def stop_live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    task = LIVE_TASKS.get(chat_id)

    if not task:
        await update.message.reply_text("ما كاين حتى تسجيل لايف يخدم حاليًا.")
        return

    task.cancel()
    LIVE_TASKS.pop(chat_id, None)
    await update.message.reply_text("تم إيقاف تسجيل اللايف.")



# =========================================================
# DATA CLEANUP
# =========================================================

def cleanup_old_data():
    now = int(time.time())
    report = {
        "downloads_removed": 0,
        "daily_users_cleaned": 0,
        "codes_removed": 0,
        "history_trimmed": 0,
    }

    # 1) حذف ملفات ومجلدات التحميل المؤقتة
    if DOWNLOAD_DIR.exists():
        for item in list(DOWNLOAD_DIR.iterdir()):
            try:
                age = now - int(item.stat().st_mtime)
                if age > DOWNLOADS_TTL_SECONDS:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                    report["downloads_removed"] += 1
            except Exception:
                pass

    # 2) تنظيف daily_usage: نترك آخر 7 أيام فقط
    daily = load_json(DAILY_FILE, {})
    keep_days = {
        time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
        for i in range(DAILY_KEEP_DAYS)
    }

    for uid in list(daily.keys()):
        if not isinstance(daily.get(uid), dict):
            daily.pop(uid, None)
            report["daily_users_cleaned"] += 1
            continue

        old_len = len(daily[uid])
        daily[uid] = {
            day: count for day, count in daily[uid].items()
            if day in keep_days
        }

        if len(daily[uid]) != old_len:
            report["daily_users_cleaned"] += 1

        if not daily[uid]:
            daily.pop(uid, None)

    save_json(DAILY_FILE, daily)

    # 3) حذف الأكواد المستعملة القديمة بعد 30 يوم
    codes = load_json(CODES_FILE, {})
    for code_key in list(codes.keys()):
        item = codes.get(code_key, {})
        if item.get("used") and item.get("used_at"):
            try:
                if now - int(item["used_at"]) > USED_CODES_KEEP_DAYS * 86400:
                    codes.pop(code_key, None)
                    report["codes_removed"] += 1
            except Exception:
                pass

    save_json(CODES_FILE, codes)

    # 4) تقصير سجل التحميل لكل شات
    history = load_json(HISTORY_FILE, {})
    for chat_id in list(history.keys()):
        items = history.get(chat_id)
        if isinstance(items, list) and len(items) > MAX_HISTORY_ITEMS_PER_CHAT:
            history[chat_id] = items[-MAX_HISTORY_ITEMS_PER_CHAT:]
            report["history_trimmed"] += 1

    save_json(HISTORY_FILE, history)

    # 5) تقصير logs
    logs = load_json(LOGS_FILE, [])
    if isinstance(logs, list) and len(logs) > 200:
        save_json(LOGS_FILE, logs[-200:])

    return report


async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    report = await asyncio.to_thread(cleanup_old_data)
    print(f"Cleanup done: {report}")


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN غير موجود.")
        return

    if not ADMIN_IDS:
        print("⚠️ ADMIN_IDS غير موجود، نستعمل FALLBACK_ADMIN_IDS.")

    print(f"Admin IDs: {ADMIN_IDS + FALLBACK_ADMIN_IDS}")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("gencode", gencode_cmd))
    app.add_handler(CommandHandler("live", live_cmd))
    app.add_handler(CommandHandler("stoplive", stop_live_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    if app.job_queue:
        app.job_queue.run_repeating(
            check_watchlist_job,
            interval=CHECK_INTERVAL_SECONDS,
            first=60
        )
        app.job_queue.run_repeating(
            cleanup_job,
            interval=CLEANUP_INTERVAL_SECONDS,
            first=300
        )
    else:
        print('⚠️ ثبّت JobQueue: pip install -U "python-telegram-bot[job-queue]"')

    startup_report = cleanup_old_data()
    print(f"Startup cleanup: {startup_report}")

    print(f"{BOT_LOGO} {BOT_NAME} is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
