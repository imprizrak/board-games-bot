import os
import io
import uuid
import asyncio
import logging
import hashlib
import hmac
import json
import time
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from threading import Thread, Lock
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote, parse_qsl, urlparse, unquote, parse_qs

import requests
from flask import Flask, request, jsonify, send_from_directory, send_file
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message, CallbackQuery, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logging.basicConfig(level=logging.INFO)

# ==== НАЛАШТУВАННЯ (беруться зі змінних середовища на Render) ====
API_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
TELEGRAM_APP_SHORT_NAME = os.environ.get("TELEGRAM_APP_SHORT_NAME", "").strip().strip("/")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "Stylogames_bot").strip().lstrip("@")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
BUCKET = "files"
EVENT_TIMEZONE = os.environ.get("EVENT_TIMEZONE", "Europe/Berlin")
MONOBANK_ACQUIRING_TOKEN = os.environ.get("MONOBANK_ACQUIRING_TOKEN", "").strip()
MONOBANK_BASE_URL = "https://api.monobank.ua/api/merchant/invoice"

HEADERS = {"apikey": SUPABASE_KEY}
# Старі ключі (JWT) потребують ще й заголовка Authorization
if SUPABASE_KEY.startswith("eyJ"):
    HEADERS["Authorization"] = f"Bearer {SUPABASE_KEY}"

REST = f"{SUPABASE_URL}/rest/v1/games"
HISTORY_REST = f"{SUPABASE_URL}/rest/v1/game_history"
WISHLIST_REST = f"{SUPABASE_URL}/rest/v1/wishlist"
EVENTS_REST = f"{SUPABASE_URL}/rest/v1/events"
RSVPS_REST = f"{SUPABASE_URL}/rest/v1/event_rsvps"
EVENT_GROUP_POSTS_REST = f"{SUPABASE_URL}/rest/v1/event_group_posts"
MONOBANK_EVENT_PAYMENTS_REST = f"{SUPABASE_URL}/rest/v1/event_monobank_payments"
MONOBANK_CONFIRM_RPC = f"{SUPABASE_URL}/rest/v1/rpc/confirm_event_monobank_payment"
MONOBANK_RESERVE_RPC = f"{SUPABASE_URL}/rest/v1/rpc/reserve_event_monobank_payment"
EVENT_GAME_PICKS_REST = f"{SUPABASE_URL}/rest/v1/event_game_picks"
EVENT_CHECKINS_REST = f"{SUPABASE_URL}/rest/v1/event_checkins"
QUESTS_CHECKINS_REST = f"{SUPABASE_URL}/rest/v1/event_quests_checkins"
EVENT_CHECKIN_RPC = f"{SUPABASE_URL}/rest/v1/rpc/styloteka_confirm_event_checkin"
ADMINS_REST = f"{SUPABASE_URL}/rest/v1/admins"
MASTERCLASS_REST = f"{SUPABASE_URL}/rest/v1/masterclass_bookings"
TELEGRAM_GROUPS_REST = f"{SUPABASE_URL}/rest/v1/telegram_groups"
PROFILES_REST = f"{SUPABASE_URL}/rest/v1/profiles"
ACTIVITY_REST = f"{SUPABASE_URL}/rest/v1/activity_feed"
PROFILE_TOOL_STATS_REST = f"{SUPABASE_URL}/rest/v1/profile_tool_stats"
PROFILE_TOOL_EVENT_RPC = f"{SUPABASE_URL}/rest/v1/rpc/record_profile_tool_event"
DUNGEON_RUNS_REST = f"{SUPABASE_URL}/rest/v1/dungeon_runs"
DUNGEON_SETTLE_RPC = f"{SUPABASE_URL}/rest/v1/rpc/settle_dungeon_run"
GAME_SUBMISSIONS_REST = f"{SUPABASE_URL}/rest/v1/game_submissions"
COIN_RUSH_CLAIM_RPC = f"{SUPABASE_URL}/rest/v1/rpc/claim_coin_catcher_run"
TREASURE_MEMORY_CLAIM_RPC = f"{SUPABASE_URL}/rest/v1/rpc/claim_treasure_memory_run"
STORAGE = f"{SUPABASE_URL}/storage/v1/object"




# ==================== GOOGLE MAPS LOCATION ====================
_GOOGLE_MAP_HOSTS = {
    "maps.app.goo.gl",
    "goo.gl",
    "google.com",
    "www.google.com",
    "maps.google.com",
}


def _is_google_maps_url(value):
    value = str(value or "").strip()
    if not value:
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and parsed.hostname and parsed.hostname.lower() in _GOOGLE_MAP_HOSTS
    except Exception:
        return False


def _extract_google_maps_coords(url):
    url = str(url or "")
    patterns = [
        r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
        r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            try:
                return float(match.group(1)), float(match.group(2))
            except Exception:
                pass

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        for key in ("query", "q", "ll"):
            raw = (params.get(key) or [""])[0]
            match = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", raw)
            if match:
                return float(match.group(1)), float(match.group(2))
    except Exception:
        pass

    return None, None


def _google_maps_label_from_url(url):
    try:
        parsed = urlparse(url)
        match = re.search(r"/place/([^/]+)", parsed.path or "")
        if match:
            label = unquote(match.group(1)).replace("+", " ").strip()
            if label and label.lower() not in ("maps", "google maps"):
                return label
    except Exception:
        pass
    return ""


def _normalize_event_location(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return {
            "location_text": "",
            "location_map_url": "",
            "location_lat": None,
            "location_lng": None,
        }

    # Звичайна адреса / назва місця.
    if not _is_google_maps_url(raw):
        return {
            "location_text": raw,
            "location_map_url": f"https://www.google.com/maps/search/?api=1&query={quote(raw)}",
            "location_lat": None,
            "location_lng": None,
        }

    # Короткі maps.app.goo.gl посилання резолвимо тільки всередині Google-доменів.
    final_url = raw
    try:
        response = requests.get(
            raw,
            allow_redirects=True,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 Styloteka/1.0"},
        )
        if response.url and _is_google_maps_url(response.url):
            final_url = response.url
    except Exception:
        logging.info("Google Maps short link не вдалося розгорнути; використовую оригінальний URL")

    lat, lng = _extract_google_maps_coords(final_url)
    label = _google_maps_label_from_url(final_url)

    return {
        "location_text": label or raw,
        "location_map_url": final_url,
        "location_lat": lat,
        "location_lng": lng,
    }


def _event_google_maps_url(event):
    saved = str((event or {}).get("location_map_url") or "").strip()
    if saved:
        return saved
    location = str((event or {}).get("location_text") or "").strip()
    if not location:
        return ""
    if _is_google_maps_url(location):
        return location
    return f"https://www.google.com/maps/search/?api=1&query={quote(location)}"


def normalize_rules_url(value):
    """Нормалізує зовнішнє HTTP(S)-посилання на правила. Повертає None, якщо URL некоректний."""
    value = str(value or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, flags=re.IGNORECASE):
        value = "https://" + value
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return value


# ==================== Робота зі сховищем Supabase ====================
def upload_file(file_storage, folder):
    """Завантажує файл у Supabase Storage, повертає публічне посилання."""
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    path = f"{folder}/{uuid.uuid4().hex}{ext}"
    content_type = file_storage.mimetype or "application/octet-stream"
    resp = requests.post(
        f"{STORAGE}/{BUCKET}/{path}",
        headers={**HEADERS, "Content-Type": content_type, "x-upsert": "true"},
        data=file_storage.read(),
        timeout=60,
    )
    resp.raise_for_status()
    return f"{STORAGE}/public/{BUCKET}/{quote(path)}"


def delete_file(url):
    """Видаляє файл зі сховища (якщо не вдалось - просто ігноруємо)."""
    if not url:
        return
    marker = f"/public/{BUCKET}/"
    if marker not in url:
        return
    path = url.split(marker, 1)[1]
    try:
        requests.delete(
            f"{STORAGE}/{BUCKET}",
            headers={**HEADERS, "Content-Type": "application/json"},
            json={"prefixes": [path]},
            timeout=30,
        )
    except Exception:
        logging.exception("Не вдалось видалити файл")


# ==================== ГЕНЕРАТОР PDF ЧАРНИКА ====================
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
pdfmetrics.registerFont(TTFont("PlexSerif", f"{FONT_DIR}/IBMPlexSerif-Regular.ttf"))
pdfmetrics.registerFont(TTFont("PlexSerif-Bold", f"{FONT_DIR}/IBMPlexSerif-Bold.ttf"))

GOLD = colors.HexColor("#a9852a")
DARK = colors.HexColor("#241333")
MUTED = colors.HexColor("#6b6070")
LIGHT_BG = colors.HexColor("#f7f3ea")

SKILLS = [
    ("Акробатика", "dex"), ("Аналіз поведінки", "wis"), ("Атлетика", "str"),
    ("Виживання", "wis"), ("Виступ", "cha"), ("Залякування", "cha"),
    ("Історія", "int"), ("Магія", "int"), ("Медицина", "wis"),
    ("Обман", "cha"), ("Переконання", "cha"), ("Поводження з тваринами", "wis"),
    ("Природа", "int"), ("Релігія", "int"), ("Розслідування", "int"),
    ("Спритність рук", "dex"), ("Непомітність", "dex"), ("Уважність", "wis"),
]
ABILITY_LABELS = {"str": "СИЛА", "dex": "СПРИТНІСТЬ", "con": "ТІЛОБУДОВА",
                  "int": "ІНТЕЛЕКТ", "wis": "МУДРІСТЬ", "cha": "ХАРИЗМА"}


def mod(score):
    return (score - 10) // 2


def mod_str(m):
    return f"+{m}" if m >= 0 else str(m)


def prof_bonus(level):
    return 2 + (max(level, 1) - 1) // 4


def draw_section_title(c, x, y, text):
    c.setFont("PlexSerif-Bold", 9)
    c.setFillColor(GOLD)
    c.drawString(x, y, text.upper())
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.6)
    c.line(x, y - 3, x + 250, y - 3)
    c.setFillColor(DARK)


def wrapped_text(c, text, x, y, max_width, font="PlexSerif", size=8.5, leading=11):
    c.setFont(font, size)
    lines = []
    for raw_line in (text or "").split("\n"):
        words = raw_line.split(" ")
        current = ""
        for w in words:
            trial = (current + " " + w).strip()
            if pdfmetrics.stringWidth(trial, font, size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = w
        lines.append(current)
    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def generate_character_pdf(ch):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    abilities = ch.get("abilities", {})
    scores = {k: int(abilities.get(k, 10) or 10) for k in ["str", "dex", "con", "int", "wis", "cha"]}
    mods = {k: mod(v) for k, v in scores.items()}
    level = int(ch.get("level") or 1)
    pb = prof_bonus(level)
    saves = ch.get("savingThrows", {}) or {}
    skill_profs = ch.get("skillProfs", {}) or {}

    margin = 15 * mm
    x = margin
    y = H - margin

    # ---- Фон сторінки ----
    c.setFillColor(LIGHT_BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(DARK)

    # ---- Заголовок ----
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.line(x, y, W - margin, y)
    y -= 7 * mm
    c.setFont("PlexSerif-Bold", 20)
    c.setFillColor(DARK)
    c.drawString(x, y, ch.get("name", "Без імені"))
    c.setFont("PlexSerif", 9)
    c.setFillColor(MUTED)
    c.drawRightString(W - margin, y, "Аркуш персонажа")
    y -= 6 * mm

    header_fields = [
        ("Клас та рівень", f"{ch.get('class','')} {level}".strip()),
        ("Раса", ch.get("race", "")),
        ("Передісторія", ch.get("background_title", "")),
        ("Світогляд", ch.get("alignment", "")),
        ("Гравець", ch.get("playerName", "")),
        ("Досвід", str(ch.get("experience", ""))),
    ]
    col_w = (W - 2 * margin) / 3
    row_h = 10 * mm
    for i, (label, value) in enumerate(header_fields):
        col = i % 3
        row = i // 3
        fx = x + col * col_w
        fy = y - row * row_h
        c.setFont("PlexSerif", 7.5)
        c.setFillColor(MUTED)
        c.drawString(fx, fy, label)
        c.setFont("PlexSerif-Bold", 10)
        c.setFillColor(DARK)
        c.drawString(fx, fy - 4.5 * mm, value or "—")
    y -= (2 * row_h + 4 * mm)

    # ---- Ліва колонка: характеристики ----
    left_w = 42 * mm
    ab_y = y
    for key in ["str", "dex", "con", "int", "wis", "cha"]:
        box_h = 20 * mm
        c.setStrokeColor(GOLD)
        c.setLineWidth(1)
        c.roundRect(x, ab_y - box_h, left_w, box_h, 4, fill=0, stroke=1)
        c.setFont("PlexSerif", 7)
        c.setFillColor(MUTED)
        c.drawCentredString(x + left_w / 2, ab_y - 5 * mm, ABILITY_LABELS[key])
        c.setFont("PlexSerif-Bold", 16)
        c.setFillColor(DARK)
        c.drawCentredString(x + left_w / 2, ab_y - 11 * mm, str(scores[key]))
        c.setFont("PlexSerif-Bold", 9)
        c.setFillColor(GOLD)
        c.drawCentredString(x + left_w / 2, ab_y - 16.5 * mm, mod_str(mods[key]))
        ab_y -= box_h + 3 * mm

    # ---- Рятункові кидки (під характеристиками) ----
    draw_section_title(c, x, ab_y - 5 * mm, "Рятункові кидки")
    ab_y -= 11 * mm
    for key in ["str", "dex", "con", "int", "wis", "cha"]:
        prof = bool(saves.get(key))
        val = mods[key] + (pb if prof else 0)
        c.setFillColor(GOLD if prof else MUTED)
        c.circle(x + 2 * mm, ab_y - 1 * mm, 1.3 * mm, fill=1 if prof else 0, stroke=1)
        c.setFillColor(DARK)
        c.setFont("PlexSerif", 8.5)
        c.drawString(x + 6 * mm, ab_y - 2 * mm, f"{mod_str(val)}  {ABILITY_LABELS[key].capitalize()}")
        ab_y -= 5 * mm

    # ---- Середня колонка: бойові показники + навички ----
    mid_x = x + left_w + 8 * mm
    mid_w = 55 * mm
    combat_y = y

    combat_boxes = [
        ("КЗ", str(ch.get("ac", "") or "10")),
        ("ІНІЦІАТИВА", mod_str(mods["dex"])),
        ("ШВИДКІСТЬ", str(ch.get("speed", "") or "30")),
    ]
    box_w = mid_w / 3 - 2 * mm
    for i, (label, value) in enumerate(combat_boxes):
        bx = mid_x + i * (box_w + 3 * mm)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1)
        c.roundRect(bx, combat_y - 16 * mm, box_w, 16 * mm, 4, fill=0, stroke=1)
        c.setFont("PlexSerif-Bold", 13)
        c.setFillColor(DARK)
        c.drawCentredString(bx + box_w / 2, combat_y - 8 * mm, value)
        c.setFont("PlexSerif", 6.5)
        c.setFillColor(MUTED)
        c.drawCentredString(bx + box_w / 2, combat_y - 13.5 * mm, label)
    combat_y -= 20 * mm

    hp_h = 16 * mm
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.roundRect(mid_x, combat_y - hp_h, mid_w, hp_h, 4, fill=0, stroke=1)
    c.setFont("PlexSerif", 7)
    c.setFillColor(MUTED)
    c.drawString(mid_x + 3 * mm, combat_y - 5 * mm, "ХІТИ (поточні / максимум)")
    c.setFont("PlexSerif-Bold", 14)
    c.setFillColor(DARK)
    hp_cur = ch.get("hpCurrent") or ch.get("hp") or "10"
    hp_max = ch.get("hp") or "10"
    c.drawString(mid_x + 3 * mm, combat_y - 12 * mm, f"{hp_cur} / {hp_max}")
    combat_y -= hp_h + 4 * mm

    hd_h = 10 * mm
    c.setLineWidth(1)
    c.roundRect(mid_x, combat_y - hd_h, mid_w, hd_h, 4, fill=0, stroke=1)
    c.setFont("PlexSerif", 7)
    c.setFillColor(MUTED)
    c.drawString(mid_x + 3 * mm, combat_y - 4 * mm, "КУБИКИ ЗДОРОВ'Я")
    c.setFont("PlexSerif-Bold", 11)
    c.setFillColor(DARK)
    c.drawString(mid_x + 3 * mm, combat_y - 8.5 * mm, f"{level}к{ch.get('hitDie', '8')}")
    combat_y -= hd_h + 6 * mm

    draw_section_title(c, mid_x, combat_y, "Навички")
    combat_y -= 6 * mm
    for skill_name, ability in SKILLS:
        prof = bool(skill_profs.get(skill_name))
        val = mods[ability] + (pb if prof else 0)
        c.setFillColor(GOLD if prof else MUTED)
        c.circle(mid_x + 1.5 * mm, combat_y - 1 * mm, 1.1 * mm, fill=1 if prof else 0, stroke=1)
        c.setFillColor(DARK)
        c.setFont("PlexSerif", 7.5)
        c.drawString(mid_x + 5 * mm, combat_y - 1.8 * mm,
                      f"{mod_str(val)}  {skill_name} ({ABILITY_LABELS[ability][:3].capitalize()})")
        combat_y -= 4.3 * mm

    passive_perception = 10 + mods["wis"] + (pb if skill_profs.get("Уважність") else 0)
    combat_y -= 3 * mm
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.roundRect(mid_x, combat_y - 10 * mm, mid_w, 10 * mm, 4, fill=0, stroke=1)
    c.setFont("PlexSerif", 7)
    c.setFillColor(MUTED)
    c.drawCentredString(mid_x + mid_w / 2, combat_y - 4 * mm, "ПАСИВНА УВАЖНІСТЬ")
    c.setFont("PlexSerif-Bold", 12)
    c.setFillColor(DARK)
    c.drawCentredString(mid_x + mid_w / 2, combat_y - 8.5 * mm, str(passive_perception))

    # ---- Права колонка: атаки, спорядження, риси ----
    right_x = mid_x + mid_w + 8 * mm
    right_w = W - margin - right_x
    right_y = y

    draw_section_title(c, right_x, right_y, "Атаки та заклинання")
    right_y -= 6 * mm
    right_y = wrapped_text(c, ch.get("attacks", "") or "—", right_x, right_y, right_w)
    right_y -= 6 * mm

    draw_section_title(c, right_x, right_y, "Спорядження")
    right_y -= 6 * mm
    right_y = wrapped_text(c, ch.get("notes", "") or "—", right_x, right_y, right_w)
    right_y -= 6 * mm

    draw_section_title(c, right_x, right_y, "Уміння та особливості")
    right_y -= 6 * mm
    right_y = wrapped_text(c, ch.get("features", "") or "—", right_x, right_y, right_w)

    # ---- Низ сторінки: передісторія / особистість ----
    bottom_y = 30 * mm
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.line(margin, bottom_y + 6 * mm, W - margin, bottom_y + 6 * mm)
    draw_section_title(c, margin, bottom_y, "Особистість, ідеали, прив'язаності, слабкості")
    wrapped_text(c, ch.get("background", "") or "—", margin, bottom_y - 6 * mm, W - 2 * margin, size=8.5)

    c.setFont("PlexSerif", 6.5)
    c.setFillColor(MUTED)
    c.drawCentredString(W / 2, 10 * mm, "Створено в Бібліотеці настільних ігор")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


# ==================== FLASK (веб-сторінка + API) ====================
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 МБ


@app.route("/")
def index():
    response = send_from_directory(".", "index.html", conditional=False)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response



# Styloteka offline-first PWA: local tools and cached public library; no payment writes offline.
@app.route("/offline")
@app.route("/offline.html")
def offline_library():
    response = send_from_directory(".", "offline.html", conditional=False)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/manifest.webmanifest")
def pwa_manifest():
    response = send_from_directory(".", "manifest.webmanifest", mimetype="application/manifest+json")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/sw.js")
def pwa_service_worker():
    response = send_from_directory(".", "sw.js", mimetype="text/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/offline.js")
@app.route("/offline-tools.js")
@app.route("/offline-bridge.js")
def pwa_js_assets():
    filename = request.path.lstrip("/")
    response = send_from_directory(".", filename, mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/icons/<filename>")
def pwa_icon_assets(filename):
    if filename not in {"icon-192.png", "icon-512.png", "icon-512-maskable.png"}:
        return jsonify({"error": "not_found"}), 404
    return send_from_directory("icons", filename, mimetype="image/png")


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


# Перегляд бібліотеки та правил доступний усім користувачам Mini App.
@app.route("/api/games", methods=["GET"])
def get_games():
    resp = requests.get(
        REST,
        headers=HEADERS,
        params={"select": "*", "order": "name.asc"},
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify(resp.json())


# Додавання гри — тільки адміністратор/власник підключеної Telegram-групи.
@app.route("/api/games", methods=["POST"])
def add_game():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    description = request.form.get("description", "")
    added_by = request.form.get("added_by", "невідомо")
    tags = request.form.get("tags", "")
    rules_link = normalize_rules_url(request.form.get("rules_url", ""))
    if rules_link is None:
        return jsonify({"error": "Некоректне посилання на правила. Використай адресу сайту http:// або https://"}), 400

    cover_file = request.files.get("cover")
    rules_file = request.files.get("rules")
    cover_url = upload_file(cover_file, "covers") if cover_file else None
    rules_url = upload_file(rules_file, "rules") if rules_file else (rules_link or None)

    is_group_admin = _telegram_user_is_group_admin(user.get("id"))
    if not is_group_admin:
        # Не даємо одному користувачу створити однакову незавершену заявку двічі.
        try:
            dup = requests.get(
                GAME_SUBMISSIONS_REST,
                headers=HEADERS,
                params={
                    "submitted_by_telegram_user_id": f"eq.{int(user.get('id'))}",
                    "status": "eq.pending",
                    "name": f"ilike.{name}",
                    "select": "id",
                    "limit": 1,
                },
                timeout=20,
            )
            if dup.ok and dup.json():
                if cover_url: delete_file(cover_url)
                if rules_file and rules_url: delete_file(rules_url)
                return jsonify({"error": "submission_already_pending"}), 409
        except Exception:
            logging.exception("Не вдалося перевірити дубль заявки гри")

        username = (user.get("username") or "").lstrip("@")
        display_name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x).strip() or username or "Учасник"
        payload = {
            "submitted_by_telegram_user_id": int(user.get("id")),
            "submitted_by_username": username or None,
            "submitted_by_display_name": display_name,
            "name": name,
            "description": description,
            "tags": tags,
            "cover_url": cover_url,
            "rules_url": rules_url,
            "status": "pending",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = requests.post(
            GAME_SUBMISSIONS_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
            json=payload,
            timeout=30,
        )
        if not resp.ok:
            logging.warning("Не вдалося зберегти заявку гри: %s", resp.text[:500])
            if cover_url: delete_file(cover_url)
            if rules_file and rules_url: delete_file(rules_url)
            return jsonify({"error": "submission_create_failed"}), 500
        return jsonify({"status": "pending", "submission": (resp.json() or [None])[0]}), 202

    # Адміністратор публікує гру одразу.
    resp = requests.post(
        REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        json={
            "name": name,
            "description": description,
            "cover_url": cover_url,
            "rules_url": rules_url,
            "added_by": added_by,
            "tags": tags,
            "is_favorite": False,
        },
        timeout=30,
    )
    resp.raise_for_status()

    notify_text = f"🎲 Нова гра в бібліотеці: {name}"
    if description:
        notify_text += f"\n\n{description}"
    notify_subscribers_async(notify_text, photo_url=cover_url)
    created_game = (resp.json() or [{}])[0]
    notify_game_groups_async(name, description=description, cover_url=cover_url,
                             game_id=created_game.get("id"))
    log_activity(
        "game_added",
        f"У бібліотеці з’явилась нова гра: {name}",
        description,
        actor_name=added_by,
        image_url=cover_url,
        metadata={"game_name": name},
    )
    return jsonify({"status": "ok"})


# Редагування гри — тільки адміністратор/власник підключеної Telegram-групи.
@app.route("/api/games/<int:game_id>", methods=["PUT"])
def update_game(game_id):
    denied = _admin_required_response()
    if denied:
        return denied
    name = request.form.get("name")
    description = request.form.get("description", "")
    tags = request.form.get("tags", "")
    rules_link_raw = request.form.get("rules_url", None)
    rules_link = normalize_rules_url(rules_link_raw) if rules_link_raw is not None else None
    if rules_link_raw is not None and str(rules_link_raw).strip() and rules_link is None:
        return jsonify({"error": "Некоректне посилання на правила. Використай адресу сайту http:// або https://"}), 400

    cover_file = request.files.get("cover")
    rules_file = request.files.get("rules")

    current = requests.get(
        REST,
        headers=HEADERS,
        params={"id": f"eq.{game_id}", "select": "cover_url,rules_url"},
        timeout=30,
    )
    current.raise_for_status()
    rows = current.json()
    if not rows:
        return jsonify({"status": "not_found"}), 404

    cover_url = rows[0].get("cover_url")
    rules_url = rows[0].get("rules_url")

    if cover_file:
        delete_file(cover_url)
        cover_url = upload_file(cover_file, "covers")
    if rules_file:
        delete_file(rules_url)
        rules_url = upload_file(rules_file, "rules")
    elif rules_link_raw is not None and str(rules_link_raw).strip():
        # Нове зовнішнє посилання замінює попередній PDF/URL.
        if rules_link != rules_url:
            delete_file(rules_url)
            rules_url = rules_link

    resp = requests.patch(
        REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        params={"id": f"eq.{game_id}"},
        json={
            "name": name,
            "description": description,
            "cover_url": cover_url,
            "rules_url": rules_url,
            "tags": tags,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify({"status": "updated"})


@app.route("/api/games/<int:game_id>/favorite", methods=["POST"])
def toggle_favorite(game_id):
    denied = _admin_required_response()
    if denied:
        return denied
    is_favorite = bool(request.get_json(silent=True, force=True).get("is_favorite"))
    resp = requests.patch(
        REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        params={"id": f"eq.{game_id}"},
        json={"is_favorite": is_favorite},
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify({"status": "ok"})


# Видалення гри — тільки адміністратор/власник підключеної Telegram-групи.
@app.route("/api/games/<int:game_id>", methods=["DELETE"])
def delete_game(game_id):
    denied = _admin_required_response()
    if denied:
        return denied
    current = requests.get(
        REST,
        headers=HEADERS,
        params={"id": f"eq.{game_id}", "select": "cover_url,rules_url"},
        timeout=30,
    )
    if current.ok and current.json():
        delete_file(current.json()[0].get("cover_url"))
        delete_file(current.json()[0].get("rules_url"))

    resp = requests.delete(REST, headers=HEADERS, params={"id": f"eq.{game_id}"}, timeout=30)
    resp.raise_for_status()
    return jsonify({"status": "deleted"})


# ==================== ІСТОРІЯ ІГРОВИХ ВЕЧОРІВ ====================
@app.route("/api/history", methods=["GET"])
def get_history():
    resp = requests.get(
        HISTORY_REST,
        headers=HEADERS,
        params={"select": "*", "order": "played_at.desc,id.desc"},
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify(resp.json())


@app.route("/api/history", methods=["POST"])
def add_history():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    data = request.get_json(silent=True, force=True) or {}

    raw_ids = data.get("player_telegram_ids") or []
    if not isinstance(raw_ids, list):
        raw_ids = []
    player_ids = []
    for value in raw_ids:
        try:
            pid = int(value)
            if pid > 0 and pid not in player_ids:
                player_ids.append(pid)
        except Exception:
            pass

    winner_user_id = None
    try:
        if data.get("winner_telegram_user_id"):
            winner_user_id = int(data.get("winner_telegram_user_id"))
    except Exception:
        winner_user_id = None

    # Переможець із профілем мусить бути серед учасників цієї партії.
    if winner_user_id and winner_user_id not in player_ids:
        return jsonify({"error": "winner_must_be_player"}), 400

    payload = {
        "game_name": data.get("game_name", ""),
        "played_at": data.get("played_at"),
        "winner": data.get("winner", ""),
        "note": data.get("note", ""),
        "added_by": data.get("added_by", "невідомо"),
        "players": data.get("players", ""),
        "duration_minutes": int(data.get("duration_minutes") or 0),
        "player_telegram_ids": player_ids,
        "winner_telegram_user_id": winner_user_id,
        "added_by_telegram_user_id": int(user.get("id")),
    }

    resp = requests.post(
        HISTORY_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        json=payload,
        timeout=30,
    )

    # Старі таблиці не повинні ламати базовий функціонал до виконання міграції.
    if resp.status_code >= 400 and any(
        field in resp.text
        for field in (
            "players",
            "duration_minutes",
            "player_telegram_ids",
            "winner_telegram_user_id",
            "added_by_telegram_user_id",
        )
    ):
        fallback = dict(payload)
        for field in (
            "players",
            "duration_minutes",
            "player_telegram_ids",
            "winner_telegram_user_id",
            "added_by_telegram_user_id",
        ):
            if field in resp.text:
                fallback.pop(field, None)
        resp = requests.post(
            HISTORY_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json=fallback,
            timeout=30,
        )

    resp.raise_for_status()

    history_title = f"Зіграно партію: {payload.get('game_name') or 'Настільна гра'}"
    details = []
    if payload.get("winner"):
        details.append(f"Переможець: {payload.get('winner')}")
    if payload.get("players"):
        details.append(f"Гравці: {payload.get('players')}")

    log_activity(
        "game_played",
        history_title,
        " · ".join(details),
        actor_name=payload.get("added_by") or "",
        metadata={
            "game_name": payload.get("game_name"),
            "winner": payload.get("winner"),
            "player_telegram_ids": player_ids,
            "winner_telegram_user_id": winner_user_id,
        },
    )
    return jsonify({"status": "ok"})


@app.route("/api/history/<int:entry_id>", methods=["DELETE"])
def delete_history(entry_id):
    resp = requests.delete(HISTORY_REST, headers=HEADERS, params={"id": f"eq.{entry_id}"}, timeout=30)
    resp.raise_for_status()
    return jsonify({"status": "deleted"})


# ==================== СПИСОК БАЖАНЬ ====================
@app.route("/api/wishlist", methods=["GET"])
def get_wishlist():
    resp = requests.get(
        WISHLIST_REST,
        headers=HEADERS,
        params={"select": "*", "order": "id.desc"},
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify(resp.json())


@app.route("/api/wishlist", methods=["POST"])
def add_wishlist():
    data = request.get_json(silent=True, force=True) or {}
    resp = requests.post(
        WISHLIST_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        json={
            "name": data.get("name", ""),
            "note": data.get("note", ""),
            "added_by": data.get("added_by", "невідомо"),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify({"status": "ok"})


@app.route("/api/wishlist/<int:item_id>", methods=["DELETE"])
def delete_wishlist(item_id):
    resp = requests.delete(WISHLIST_REST, headers=HEADERS, params={"id": f"eq.{item_id}"}, timeout=30)
    resp.raise_for_status()
    return jsonify({"status": "deleted"})


# ==================== PDF ЧАРНИКА ====================
@app.route("/api/character-pdf", methods=["POST"])
def character_pdf():
    ch = request.get_json(silent=True, force=True) or {}
    pdf_buf = generate_character_pdf(ch)
    safe_name = "".join(c for c in (ch.get("name") or "character") if c.isalnum() or c in " -_").strip() or "character"
    return send_file(
        pdf_buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{safe_name}.pdf",
    )


# ==================== ПРАВА АДМІНІСТРАТОРА TELEGRAM-ГРУПИ ====================
def _verified_telegram_webapp_user(init_data):
    """Перевіряє підпис Telegram WebApp initData і повертає user або None."""
    if not init_data or not API_TOKEN:
        return None
    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={values[k]}" for k in sorted(values))
        secret_key = hmac.new(b"WebAppData", API_TOKEN.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        auth_date = int(values.get("auth_date") or 0)
        if auth_date and abs(int(time.time()) - auth_date) > 7 * 24 * 60 * 60:
            return None

        raw_user = values.get("user")
        if not raw_user:
            return None
        user = json.loads(raw_user)
        return user if user.get("id") else None
    except Exception:
        logging.exception("Не вдалося перевірити Telegram WebApp initData")
        return None


def _request_telegram_user():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        init_data = request.form.get("_tg_init_data", "")
    if not init_data and request.is_json:
        body = request.get_json(silent=True) or {}
        init_data = body.get("_tg_init_data", "")
    return _verified_telegram_webapp_user(init_data)


def _active_telegram_group_ids():
    try:
        resp = requests.get(
            TELEGRAM_GROUPS_REST,
            headers=HEADERS,
            params={"select": "chat_id", "active": "eq.true"},
            timeout=20,
        )
        resp.raise_for_status()
        return [int(row["chat_id"]) for row in resp.json() if row.get("chat_id")]
    except Exception:
        logging.exception("Не вдалося отримати список підключених Telegram-груп")
        return []


def _telegram_user_is_group_admin(user_id):
    """Адмін, якщо користувач є creator/administrator хоча б в одній активній групі бота."""
    if not user_id or not API_TOKEN:
        return False
    for chat_id in _active_telegram_group_ids():
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{API_TOKEN}/getChatMember",
                json={"chat_id": chat_id, "user_id": int(user_id)},
                timeout=20,
            )
            data = resp.json() if resp.content else {}
            status = ((data.get("result") or {}).get("status") or "").lower()
            if resp.ok and data.get("ok") and status in {"administrator", "creator"}:
                return True
        except Exception:
            logging.exception("Не вдалося перевірити адміністратора групи %s", chat_id)
    return False


def _request_is_group_admin():
    user = _request_telegram_user()
    return bool(user and _telegram_user_is_group_admin(user.get("id")))


def _admin_required_response():
    if not _request_is_group_admin():
        return jsonify({"error": "group_admin_required"}), 403
    return None


@app.route("/api/admin/game-submissions", methods=["GET"])
def admin_game_submissions():
    denied = _admin_required_response()
    if denied:
        return denied
    status = (request.args.get("status") or "pending").strip().lower()
    if status not in {"pending", "approved", "rejected", "all"}:
        status = "pending"
    params = {"select": "*", "order": "created_at.desc"}
    if status != "all":
        params["status"] = f"eq.{status}"
    try:
        resp = requests.get(GAME_SUBMISSIONS_REST, headers=HEADERS, params=params, timeout=30)
        if not resp.ok:
            logging.warning("Не вдалося завантажити заявки: %s", resp.text[:500])
            return jsonify({"error": "moderation_load_failed"}), 500
        return jsonify(resp.json())
    except Exception:
        logging.exception("Не вдалося завантажити заявки ігор")
        return jsonify({"error": "moderation_load_failed"}), 500


def _admin_get_game_submission(submission_id):
    try:
        resp = requests.get(
            GAME_SUBMISSIONS_REST,
            headers=HEADERS,
            params={"id": f"eq.{int(submission_id)}", "select": "*", "limit": 1},
            timeout=20,
        )
        if resp.ok and resp.json():
            return resp.json()[0]
    except Exception:
        logging.exception("Не вдалося отримати заявку %s", submission_id)
    return None


@app.route("/api/admin/game-submissions/<int:submission_id>", methods=["PUT"])
def admin_edit_game_submission(submission_id):
    denied = _admin_required_response()
    if denied:
        return denied
    current = _admin_get_game_submission(submission_id)
    if not current:
        return jsonify({"error": "submission_not_found"}), 404
    if current.get("status") != "pending":
        return jsonify({"error": "submission_already_reviewed"}), 409

    name = (request.form.get("name") or current.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    description = request.form.get("description", current.get("description") or "")
    tags = request.form.get("tags", current.get("tags") or "")
    cover_url = current.get("cover_url")
    rules_url = current.get("rules_url")
    cover_file = request.files.get("cover")
    rules_file = request.files.get("rules")
    rules_link_raw = request.form.get("rules_url", None)

    if cover_file:
        delete_file(cover_url)
        cover_url = upload_file(cover_file, "covers")
    if rules_file:
        delete_file(rules_url)
        rules_url = upload_file(rules_file, "rules")
    elif rules_link_raw is not None:
        normalized = normalize_rules_url(rules_link_raw)
        if str(rules_link_raw).strip() and normalized is None:
            return jsonify({"error": "Некоректне посилання на правила."}), 400
        if normalized != rules_url:
            delete_file(rules_url)
            rules_url = normalized or None

    patch = {
        "name": name,
        "description": description,
        "tags": tags,
        "cover_url": cover_url,
        "rules_url": rules_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = requests.patch(
        GAME_SUBMISSIONS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        params={"id": f"eq.{submission_id}", "status": "eq.pending"},
        json=patch,
        timeout=30,
    )
    if not resp.ok:
        return jsonify({"error": "submission_update_failed"}), 500
    return jsonify({"status": "updated", "submission": (resp.json() or [None])[0]})


@app.route("/api/admin/game-submissions/<int:submission_id>/approve", methods=["POST"])
def admin_approve_game_submission(submission_id):
    denied = _admin_required_response()
    if denied:
        return denied
    admin = _request_telegram_user()
    current = _admin_get_game_submission(submission_id)
    if not current:
        return jsonify({"error": "submission_not_found"}), 404
    if current.get("status") != "pending":
        return jsonify({"error": "submission_already_reviewed"}), 409

    game_payload = {
        "name": current.get("name") or "Без назви",
        "description": current.get("description") or "",
        "cover_url": current.get("cover_url"),
        "rules_url": current.get("rules_url"),
        "added_by": current.get("submitted_by_display_name") or current.get("submitted_by_username") or "учасник",
        "tags": current.get("tags") or "",
        "is_favorite": False,
    }
    created = requests.post(
        REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        json=game_payload,
        timeout=30,
    )
    if not created.ok:
        logging.warning("Не вдалося опублікувати схвалену гру: %s", created.text[:500])
        return jsonify({"error": "game_publish_failed"}), 500

    patch = {
        "status": "approved",
        "reviewed_by_telegram_user_id": int(admin.get("id")),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    reviewed = requests.patch(
        GAME_SUBMISSIONS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        params={"id": f"eq.{submission_id}", "status": "eq.pending"},
        json=patch,
        timeout=30,
    )
    if not reviewed.ok:
        logging.warning("Гру опубліковано, але заявку не позначено схваленою: %s", reviewed.text[:500])

    name = game_payload["name"]
    description = game_payload["description"]
    notify_text = f"🎲 Нова гра в бібліотеці: {name}"
    if description:
        notify_text += f"\n\n{description}"
    notify_subscribers_async(notify_text, photo_url=game_payload.get("cover_url"))
    created_game = (created.json() or [{}])[0]
    notify_game_groups_async(name, description=description,
                             cover_url=game_payload.get("cover_url"), game_id=created_game.get("id"))
    log_activity("game_added", f"У бібліотеці з’явилась нова гра: {name}", description,
                 actor_name=game_payload.get("added_by"), image_url=game_payload.get("cover_url"),
                 metadata={"game_name": name, "moderated": True})
    return jsonify({"status": "approved", "game": (created.json() or [None])[0]})


@app.route("/api/admin/game-submissions/<int:submission_id>/reject", methods=["POST"])
def admin_reject_game_submission(submission_id):
    denied = _admin_required_response()
    if denied:
        return denied
    admin = _request_telegram_user()
    current = _admin_get_game_submission(submission_id)
    if not current:
        return jsonify({"error": "submission_not_found"}), 404
    if current.get("status") != "pending":
        return jsonify({"error": "submission_already_reviewed"}), 409
    data = request.get_json(silent=True) or {}
    patch = {
        "status": "rejected",
        "review_note": str(data.get("note") or "").strip() or None,
        "reviewed_by_telegram_user_id": int(admin.get("id")),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = requests.patch(
        GAME_SUBMISSIONS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        params={"id": f"eq.{submission_id}", "status": "eq.pending"},
        json=patch,
        timeout=30,
    )
    if not resp.ok:
        return jsonify({"error": "submission_reject_failed"}), 500
    return jsonify({"status": "rejected"})






# ==================== TELEGRAM-АВАТАРКИ ====================
# WebApp initData не гарантує поле photo_url. Тому аватар беремо
# через Bot API getUserProfilePhotos і віддаємо клієнту через наш backend,
# не розкриваючи BOT_TOKEN у URL Telegram-файлу.
_TELEGRAM_AVATAR_CACHE = {}
_TELEGRAM_AVATAR_CACHE_TTL = 15 * 60


def _profile_avatar_url(telegram_user_id):
    try:
        return f"/api/profile/avatar/{int(telegram_user_id)}"
    except Exception:
        return ""


def _telegram_avatar_placeholder():
    # Нейтральна SVG-заглушка, якщо в Telegram немає доступної фотографії.
    return b"""<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<rect width="256" height="256" rx="48" fill="#14253a"/>
<circle cx="128" cy="94" r="46" fill="#6f9fe8"/>
<path d="M44 230c7-56 36-84 84-84s77 28 84 84" fill="#6f9fe8"/>
</svg>"""


def _telegram_profile_avatar_bytes(telegram_user_id):
    """Повертає (bytes, mimetype, found_real_photo)."""
    try:
        user_id = int(telegram_user_id)
    except Exception:
        return _telegram_avatar_placeholder(), "image/svg+xml", False

    now = time.time()
    cached = _TELEGRAM_AVATAR_CACHE.get(user_id)
    if cached and now - cached["ts"] < _TELEGRAM_AVATAR_CACHE_TTL:
        return cached["data"], cached["mimetype"], cached["real"]

    if not API_TOKEN:
        data = _telegram_avatar_placeholder()
        _TELEGRAM_AVATAR_CACHE[user_id] = {
            "ts": now, "data": data, "mimetype": "image/svg+xml", "real": False
        }
        return data, "image/svg+xml", False

    try:
        photos_resp = requests.get(
            f"https://api.telegram.org/bot{API_TOKEN}/getUserProfilePhotos",
            params={"user_id": user_id, "offset": 0, "limit": 1},
            timeout=20,
        )
        photos_data = photos_resp.json() if photos_resp.content else {}

        photos = ((photos_data.get("result") or {}).get("photos") or [])
        if photos_resp.ok and photos_data.get("ok") and photos:
            # Telegram повертає кілька розмірів однієї фотографії; останній — найбільший.
            sizes = photos[0] or []
            if sizes:
                file_id = sizes[-1].get("file_id")
                if file_id:
                    file_resp = requests.get(
                        f"https://api.telegram.org/bot{API_TOKEN}/getFile",
                        params={"file_id": file_id},
                        timeout=20,
                    )
                    file_data = file_resp.json() if file_resp.content else {}
                    file_path = ((file_data.get("result") or {}).get("file_path") or "")

                    if file_resp.ok and file_data.get("ok") and file_path:
                        image_resp = requests.get(
                            f"https://api.telegram.org/file/bot{API_TOKEN}/{file_path}",
                            timeout=30,
                        )
                        image_resp.raise_for_status()

                        mimetype = (
                            image_resp.headers.get("Content-Type")
                            or ("image/png" if file_path.lower().endswith(".png") else "image/jpeg")
                        )
                        data = image_resp.content
                        _TELEGRAM_AVATAR_CACHE[user_id] = {
                            "ts": now,
                            "data": data,
                            "mimetype": mimetype,
                            "real": True,
                        }
                        return data, mimetype, True

    except Exception:
        logging.exception("Не вдалося завантажити Telegram-аватар користувача %s", user_id)

    data = _telegram_avatar_placeholder()
    _TELEGRAM_AVATAR_CACHE[user_id] = {
        "ts": now, "data": data, "mimetype": "image/svg+xml", "real": False
    }
    return data, "image/svg+xml", False


@app.route("/api/profile/avatar/<int:telegram_user_id>", methods=["GET"])
def get_profile_avatar(telegram_user_id):
    # Не даємо використовувати endpoint як довільний Telegram photo proxy:
    # аватар віддається лише для користувача, який уже має профіль у Styloteka.
    try:
        check = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={
                "telegram_user_id": f"eq.{int(telegram_user_id)}",
                "select": "telegram_user_id",
                "limit": 1,
            },
            timeout=15,
        )
        if not check.ok or not check.json():
            return "", 404
    except Exception:
        return "", 404

    data, mimetype, real = _telegram_profile_avatar_bytes(telegram_user_id)
    response = send_file(
        io.BytesIO(data),
        mimetype=mimetype,
        download_name=f"avatar-{telegram_user_id}",
        max_age=900,
    )
    response.headers["Cache-Control"] = "public, max-age=900"
    response.headers["X-Styloteka-Telegram-Avatar"] = "1" if real else "0"
    return response


# ==================== ПРОФІЛЬ УЧАСНИКА ====================
def _profile_norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lstrip("@").lower())


def _profile_history_stats(user, telegram_user_id=None):
    username = _profile_norm(user.get("username"))
    full_name = _profile_norm(" ".join(x for x in [user.get("first_name"), user.get("last_name")] if x))
    first_name = _profile_norm(user.get("first_name"))
    aliases = {x for x in [username, full_name, first_name] if x}

    try:
        telegram_user_id = int(telegram_user_id or user.get("id") or 0) or None
    except Exception:
        telegram_user_id = None

    games_played = 0
    wins = 0
    game_counts = {}

    try:
        resp = requests.get(
            HISTORY_REST,
            headers=HEADERS,
            params={
                "select": "game_name,players,winner,played_at,player_telegram_ids,winner_telegram_user_id"
            },
            timeout=30,
        )

        # Сумісність зі старою схемою до міграції.
        if resp.status_code >= 400 and (
            "player_telegram_ids" in resp.text or "winner_telegram_user_id" in resp.text
        ):
            resp = requests.get(
                HISTORY_REST,
                headers=HEADERS,
                params={"select": "game_name,players,winner,played_at"},
                timeout=30,
            )

        resp.raise_for_status()

        for row in resp.json():
            ids = row.get("player_telegram_ids") or []
            if not isinstance(ids, list):
                ids = []
            normalized_ids = set()
            for value in ids:
                try:
                    normalized_ids.add(int(value))
                except Exception:
                    pass

            winner_id = None
            try:
                if row.get("winner_telegram_user_id"):
                    winner_id = int(row.get("winner_telegram_user_id"))
            except Exception:
                winner_id = None

            # Нова схема: ID має пріоритет.
            is_player = bool(telegram_user_id and telegram_user_id in normalized_ids)
            is_winner = bool(telegram_user_id and winner_id == telegram_user_id)

            # Legacy fallback для старих записів без Telegram ID.
            if not normalized_ids:
                raw_players = str(row.get("players") or "")
                players = [
                    _profile_norm(p)
                    for p in re.split(r"[,;\n|]+", raw_players)
                    if _profile_norm(p)
                ]
                winner = _profile_norm(row.get("winner"))
                is_player = bool(aliases.intersection(players))
                if not is_player and winner and winner in aliases:
                    is_player = True
                if not winner_id:
                    is_winner = bool(winner and winner in aliases)

            if not is_player:
                continue

            games_played += 1
            game = str(row.get("game_name") or "Без назви").strip() or "Без назви"
            game_counts[game] = game_counts.get(game, 0) + 1
            if is_winner:
                wins += 1

    except Exception:
        logging.exception("Не вдалося порахувати статистику профілю з історії")

    # Відвідування зараховується тільки після завершення події.
    events_attended = 0
    try:
        completed = requests.get(
            EVENTS_REST,
            headers=HEADERS,
            params={"status": "eq.completed", "select": "id"},
            timeout=20,
        )
        completed.raise_for_status()
        completed_ids = {row.get("id") for row in completed.json()}

        attended_event_ids = set()

        if completed_ids and telegram_user_id:
            rr = requests.get(
                RSVPS_REST,
                headers=HEADERS,
                params={
                    "telegram_user_id": f"eq.{telegram_user_id}",
                    "status": "eq.going",
                    "select": "id,event_id",
                },
                timeout=20,
            )
            if rr.ok:
                attended_event_ids.update(
                    row.get("event_id")
                    for row in rr.json()
                    if row.get("event_id") in completed_ids
                )

        # Legacy RSVP fallback by username.
        if completed_ids and username:
            rr_old = requests.get(
                RSVPS_REST,
                headers=HEADERS,
                params={
                    "username": f"eq.{username}",
                    "status": "eq.going",
                    "select": "id,event_id,telegram_user_id",
                },
                timeout=20,
            )
            if rr_old.ok:
                for row in rr_old.json():
                    if row.get("event_id") not in completed_ids:
                        continue
                    # Якщо рядок уже належить іншому відомому Telegram ID — не беремо його.
                    row_tid = row.get("telegram_user_id")
                    if row_tid and telegram_user_id and str(row_tid) != str(telegram_user_id):
                        continue
                    attended_event_ids.add(row.get("event_id"))

        events_attended = len(attended_event_ids)
    except Exception:
        logging.exception("Не вдалося порахувати завершені події профілю")

    favorite_game = "—"
    if game_counts:
        favorite_game = sorted(
            game_counts.items(),
            key=lambda item: (-item[1], item[0].lower())
        )[0][0]

    return {
        "games_played": games_played,
        "wins": wins,
        "events_attended": events_attended,
        "favorite_game": favorite_game,
        "unique_games": len(game_counts),
    }



def _profile_tool_stats(telegram_user_id):
    result = {
        "first_player_wins": 0,
        "dice_rolls": 0,
        "dice_good_rolls": 0,
        "dice_max_rolls": 0,
        "dice_nat20s": 0,
        "dice_best_good_streak": 0,
    }
    try:
        resp = requests.get(
            PROFILE_TOOL_STATS_REST,
            headers=HEADERS,
            params={
                "telegram_user_id": f"eq.{int(telegram_user_id)}",
                "select": "first_player_wins,dice_rolls,dice_good_rolls,dice_max_rolls,dice_nat20s,dice_best_good_streak",
                "limit": 1,
            },
            timeout=20,
        )
        if resp.ok and resp.json():
            row = resp.json()[0]
            for key in result:
                result[key] = int(row.get(key) or 0)
    except Exception:
        logging.exception("Не вдалося прочитати статистику інструментів профілю")
    return result


def _merge_profile_stats(user, telegram_user_id):
    stats = _profile_history_stats(user, telegram_user_id)
    stats.update(_profile_tool_stats(telegram_user_id))
    return stats


def _profile_achievements(stats):
    games = int(stats.get("games_played") or 0)
    wins = int(stats.get("wins") or 0)
    events = int(stats.get("events_attended") or 0)
    unique_games = int(stats.get("unique_games") or 0)
    picker_wins = int(stats.get("first_player_wins") or 0)
    dice_rolls = int(stats.get("dice_rolls") or 0)
    dice_good = int(stats.get("dice_good_rolls") or 0)
    dice_max = int(stats.get("dice_max_rolls") or 0)
    nat20 = int(stats.get("dice_nat20s") or 0)
    good_streak = int(stats.get("dice_best_good_streak") or 0)

    def ach(aid, icon, name, description, progress, target, category, bonus_xp):
        return {
            "id": aid,
            "icon": icon,
            "name": name,
            "description": description,
            "unlocked": int(progress) >= int(target),
            "progress": int(progress),
            "target": int(target),
            "category": category,
            "bonus_xp": int(bonus_xp),
        }

    return [
        # Вступні.
        ach("first_game", "🎲", "Перша партія", "Зіграно першу записану партію", games, 1, "games", 25),
        ach("first_win", "🏆", "Перша перемога", "Здобуто першу перемогу", wins, 1, "wins", 50),

        # Партії.
        ach("regular", "🔥", "Завсідник", "Зіграно 25 партій", games, 25, "games", 100),
        ach("game_night_75", "🌙", "Ігрові ночі", "Зіграно 75 партій", games, 75, "games", 250),
        ach("game_night_150", "🎮", "Серйозний гравець", "Зіграно 150 партій", games, 150, "games", 500),
        ach("game_night_300", "💯", "Легенда столу", "Зіграно 300 партій", games, 300, "games", 1000),

        # Перемоги.
        ach("winner_15", "🥉", "Смак перемоги", "Здобуто 15 перемог", wins, 15, "wins", 150),
        ach("champion_40", "👑", "Чемпіон", "Здобуто 40 перемог", wins, 40, "wins", 300),
        ach("winner_100", "🥈", "Мисливець за перемогами", "Здобуто 100 перемог", wins, 100, "wins", 750),
        ach("winner_200", "🥇", "Домінатор столу", "Здобуто 200 перемог", wins, 200, "wins", 1500),

        # Різні ігри.
        ach("explorer_10", "🧭", "Дослідник", "Зіграно у 10 різних настільних ігор", unique_games, 10, "collection", 150),
        ach("explorer_25", "🗺", "Колекціонер досвіду", "Зіграно у 25 різних ігор", unique_games, 25, "collection", 400),
        ach("explorer_50", "🌍", "Настільний мандрівник", "Зіграно у 50 різних ігор", unique_games, 50, "collection", 1000),

        # Події.
        ach("event_guest_10", "👥", "У компанії", "Відвідано 10 завершених подій", events, 10, "events", 150),
        ach("event_regular_30", "🎉", "Свій у клубі", "Відвідано 30 завершених подій", events, 30, "events", 400),
        ach("event_regular_75", "🏛", "Серце клубу", "Відвідано 75 завершених подій", events, 75, "events", 1000),

        # «Хто перший».
        ach("picker_10", "☝️", "Перший серед рівних", "10 разів перемогти у «Хто перший»", picker_wins, 10, "picker", 100),
        ach("picker_30", "⚡", "Швидкий старт", "30 разів перемогти у «Хто перший»", picker_wins, 30, "picker", 250),
        ach("picker_75", "🧲", "Магніт першого ходу", "75 разів перемогти у «Хто перший»", picker_wins, 75, "picker", 600),
        ach("picker_150", "🚀", "Завжди перший", "150 разів перемогти у «Хто перший»", picker_wins, 150, "picker", 1200),

        # Кубики.
        ach("dice_50", "🎲", "Кидай ще", "Зроблено 50 кидків кубика", dice_rolls, 50, "dice", 50),
        ach("dice_250", "🌀", "Володар кубиків", "Зроблено 250 кидків кубика", dice_rolls, 250, "dice", 200),
        ach("dice_1000", "🔮", "Тисяча кидків", "Зроблено 1000 кидків кубика", dice_rolls, 1000, "dice", 750),

        # Вдалий кидок = 80%+ від максимуму.
        ach("dice_good_25", "🍀", "Щаслива рука", "25 разів випало не менше 80% від максимуму кубика", dice_good, 25, "dice", 100),
        ach("dice_good_100", "✨", "Улюбленець фортуни", "100 вдалих кидків (80%+ від максимуму)", dice_good, 100, "dice", 300),
        ach("dice_good_300", "🌟", "Фортуна на твоєму боці", "300 вдалих кидків (80%+ від максимуму)", dice_good, 300, "dice", 800),

        # Максимальні значення.
        ach("dice_max_3", "💥", "Максимум!", "3 рази викинути максимальне значення", dice_max, 3, "dice", 75),
        ach("dice_max_20", "🔥", "Максималіст", "20 разів викинути максимальне значення", dice_max, 20, "dice", 250),
        ach("dice_max_75", "☄️", "Неможлива удача", "75 разів викинути максимальне значення", dice_max, 75, "dice", 750),

        # Серії.
        ach("dice_streak_5", "🎯", "Гаряча серія", "5 вдалих кидків поспіль", good_streak, 5, "dice", 150),
        ach("dice_streak_8", "⚡", "Серія фортуни", "8 вдалих кидків поспіль", good_streak, 8, "dice", 500),

        # d20.
        ach("nat20_3", "🐉", "Критичний успіх", "Викинути натуральну 20 на d20 тричі", nat20, 3, "dice", 150),
        ach("nat20_15", "⚔️", "Критична легенда", "Викинути натуральну 20 на d20 15 разів", nat20, 15, "dice", 500),
        ach("nat20_50", "👁️", "Обранець долі", "Викинути натуральну 20 на d20 50 разів", nat20, 50, "dice", 1500),
    ]


# Старі вже отримані досягнення теж не втрачають свою цінність.
# Ця таблиця використовується лише для ID, яких більше немає в актуальному каталозі.
_LEGACY_ACHIEVEMENT_BONUS_XP = {
    "game_night_25": 100,
    "game_night_50": 150,
    "game_night_100": 250,
    "winner_5": 75,
    "champion": 125,
    "winner_25": 200,
    "winner_50": 300,
    "explorer": 75,
    "explorer_20": 250,
    "event_guest": 75,
    "event_regular_10": 100,
    "event_regular_25": 200,
    "picker_3": 50,
    "picker_25": 150,
    "picker_50": 250,
    "dice_10": 25,
    "dice_good_10": 50,
    "dice_good_50": 100,
    "dice_good_100": 200,
    "dice_max_1": 25,
    "dice_max_10": 100,
    "dice_streak_3": 50,
    "nat20_1": 50,
    "nat20_5": 150,
}


def _profile_xp_breakdown(stats, earned_ids, achievements=None):
    achievements = achievements or _profile_achievements(stats)
    earned_ids = {str(x) for x in (earned_ids or set()) if x}

    base_xp = (
        int(stats.get("games_played") or 0) * 20
        + int(stats.get("wins") or 0) * 10
        + int(stats.get("events_attended") or 0) * 25
        + int(stats.get("unique_games") or 0) * 5
    )

    catalog_bonus = {a["id"]: int(a.get("bonus_xp") or 0) for a in achievements}
    achievement_bonus_xp = 0
    for aid in earned_ids:
        if aid in catalog_bonus:
            achievement_bonus_xp += catalog_bonus[aid]
        else:
            achievement_bonus_xp += int(_LEGACY_ACHIEVEMENT_BONUS_XP.get(aid, 0))

    return {
        "base_xp": base_xp,
        "achievement_bonus_xp": achievement_bonus_xp,
        "total_xp": base_xp + achievement_bonus_xp,
    }


# ==================== НАГОРОДИ ЗА XP ====================
def _xp_reward_catalog(level):
    level = max(1, int(level or 1))
    rewards = [
        {
            "id": "title_club_player",
            "type": "title",
            "level_required": 3,
            "icon": "🎲",
            "name": "Клубний гравець",
            "value": "Клубний гравець",
            "description": "Перший титул за розвиток профілю.",
        },
        {
            "id": "title_tactician",
            "type": "title",
            "level_required": 5,
            "icon": "♟️",
            "name": "Тактик",
            "value": "Тактик",
            "description": "Титул за досягнення 5 рівня.",
        },
        {
            "id": "frame_steel",
            "type": "frame",
            "level_required": 10,
            "icon": "🩶",
            "name": "Сталева рамка",
            "value": "frame_steel",
            "description": "Сріблясто-синя рамка для аватарки.",
        },
        {
            "id": "title_strategist",
            "type": "title",
            "level_required": 15,
            "icon": "🧠",
            "name": "Стратег",
            "value": "Стратег",
            "description": "Рідкісний титул за 15 рівень.",
        },
        {
            "id": "badge_veteran",
            "type": "badge",
            "level_required": 20,
            "icon": "⭐",
            "name": "Ветеран клубу",
            "value": "⭐ Ветеран клубу",
            "description": "Значок, який видно у профілі та списку учасників.",
        },
        {
            "id": "frame_gold",
            "type": "frame",
            "level_required": 25,
            "icon": "🏅",
            "name": "Золота рамка",
            "value": "frame_gold",
            "description": "Золота рамка аватарки за 25 рівень.",
        },
        {
            "id": "theme_ocean",
            "type": "theme",
            "level_required": 30,
            "icon": "🌊",
            "name": "Deep Ocean",
            "value": "theme_ocean",
            "description": "Особлива темно-синя тема профілю.",
        },
        {
            "id": "title_master",
            "type": "title",
            "level_required": 35,
            "icon": "🎓",
            "name": "Майстер столу",
            "value": "Майстер столу",
            "description": "Титул для досвідчених учасників клубу.",
        },
        {
            "id": "badge_elite",
            "type": "badge",
            "level_required": 40,
            "icon": "👑",
            "name": "Еліта клубу",
            "value": "👑 Еліта клубу",
            "description": "Рідкісний клубний значок.",
        },
        {
            "id": "theme_midnight",
            "type": "theme",
            "level_required": 45,
            "icon": "🌙",
            "name": "Midnight",
            "value": "theme_midnight",
            "description": "Темна преміальна тема профілю.",
        },
        {
            "id": "frame_legendary",
            "type": "frame",
            "level_required": 50,
            "icon": "💠",
            "name": "Легендарна рамка",
            "value": "frame_legendary",
            "description": "Найрідкісніша рамка аватарки у поточному каталозі.",
        },
        {
            "id": "title_legend",
            "type": "title",
            "level_required": 50,
            "icon": "🏆",
            "name": "Легенда Styloteka",
            "value": "Легенда Styloteka",
            "description": "Легендарний титул за 50 рівень.",
        },
    ]
    for reward in rewards:
        reward["unlocked"] = level >= int(reward["level_required"])
    return rewards



def _profile_shop_catalog():
    """Косметика, яку можна придбати за золото з мінігри."""
    return [
        {
            "id": "shop_frame_bronze",
            "type": "frame",
            "icon": "🟤",
            "name": "Бронзова рамка",
            "value": "shop_frame_bronze",
            "price": 120,
            "description": "Тепла бронзова рамка у стилі пригодницької гільдії.",
        },
        {
            "id": "shop_frame_frost",
            "type": "frame",
            "icon": "❄️",
            "name": "Крижана рамка",
            "value": "shop_frame_frost",
            "price": 220,
            "description": "Холодне блакитне сяйво навколо аватарки.",
        },
        {
            "id": "shop_frame_arcane",
            "type": "frame",
            "icon": "🔮",
            "name": "Арканна рамка",
            "value": "shop_frame_arcane",
            "price": 380,
            "description": "Фіолетово-золота рамка для справжнього героя.",
        },
        {
            "id": "shop_frame_dragonfire",
            "type": "frame",
            "icon": "🐉",
            "name": "Вогонь дракона",
            "value": "shop_frame_dragonfire",
            "price": 560,
            "description": "Вогняна рамка з теплим червоно-золотим сяйвом.",
        },
        {
            "id": "shop_theme_forest",
            "type": "theme",
            "icon": "🌿",
            "name": "Смарагдовий ліс",
            "value": "shop_theme_forest",
            "price": 300,
            "description": "Темно-зелена тема профілю з м'якими золотими акцентами.",
        },
        {
            "id": "shop_theme_crimson",
            "type": "theme",
            "icon": "🔥",
            "name": "Багряний рейд",
            "value": "shop_theme_crimson",
            "price": 450,
            "description": "Темна червоно-графітова тема у стилі рейдового інтерфейсу.",
        },
        {
            "id": "shop_theme_royal",
            "type": "theme",
            "icon": "👑",
            "name": "Королівська ніч",
            "value": "shop_theme_royal",
            "price": 700,
            "description": "Преміальна фіолетово-золота тема профілю.",
        },
        {
            "id": "shop_theme_starlight",
            "type": "theme",
            "icon": "✨",
            "name": "Зоряне сяйво",
            "value": "shop_theme_starlight",
            "price": 950,
            "description": "Глибока синьо-фіолетова тема із холодним зоряним акцентом.",
        },
        {'id': 'shop_companion_owl', 'type': 'companion', 'icon': '🦉', 'name': 'Сова-хранитель', 'value': 'shop_companion_owl', 'price': 210, 'rarity': 'common', 'description': 'Мудра сова біля твоєї аватарки.'},
        {'id': 'shop_companion_ghost', 'type': 'companion', 'icon': '👻', 'name': 'Примарний супутник', 'value': 'shop_companion_ghost', 'price': 520, 'rarity': 'epic', 'description': 'Маленький магічний дух супроводжує тебе у профілі.'},
        {'id': 'shop_background_tavern', 'type': 'background', 'icon': '🏮', 'name': 'Таверна пригод', 'value': 'shop_background_tavern', 'price': 260, 'rarity': 'rare', 'description': 'Теплий фон із золотим світлом таверни.'},
        {'id': 'shop_background_cosmos', 'type': 'background', 'icon': '🌌', 'name': 'Зоряна безодня', 'value': 'shop_background_cosmos', 'price': 520, 'rarity': 'epic', 'description': 'Синьо-фіолетовий фон зі зірками.'},
        {'id': 'shop_coin_skin_crystal', 'type': 'coin_skin', 'icon': '💠', 'name': 'Кришталеві стілси', 'value': 'shop_coin_skin_crystal', 'price': 260, 'rarity': 'rare', 'description': 'Блакитне кришталеве оформлення Stils Rush.'},
        {'id': 'shop_treasure_skin_neon', 'type': 'treasure_skin', 'icon': '💜', 'name': 'Неонова скарбниця', 'value': 'shop_treasure_skin_neon', 'price': 340, 'rarity': 'rare', 'description': 'Фіолетове сяйво на картках Скарбниці.'},
        {'id': 'shop_dungeon_skin_arcane', 'type': 'dungeon_skin', 'icon': '🔮', 'name': 'Арканний данж', 'value': 'shop_dungeon_skin_arcane', 'price': 390, 'rarity': 'epic', 'description': 'Фіолетові магічні акценти в Данжі.'},
        {'id': 'shop_bundle_guild', 'type': 'bundle', 'icon': '🎁', 'name': 'Набір гільдії', 'price': 650, 'rarity': 'rare', 'description': '4 предмети: бронзова рамка, лісова тема, таверна та сова. Економія 240 стілсів.', 'grants': ['shop_frame_bronze', 'shop_theme_forest', 'shop_background_tavern', 'shop_companion_owl']},
        {'id': 'shop_bundle_arcane', 'type': 'bundle', 'icon': '💎', 'name': 'Скарби арканіста', 'price': 1680, 'rarity': 'legendary', 'description': '4 преміальні предмети: арканна рамка, зоряна тема, космос і примара. Економія 690 стілсів.', 'grants': ['shop_frame_arcane', 'shop_theme_starlight', 'shop_background_cosmos', 'shop_companion_ghost']},
        {'id': 'shop_box_lucky', 'type': 'mystery', 'icon': '🎲', 'name': 'Lucky Box', 'price': 240, 'rarity': 'rare', 'repeatable': True, 'description': 'Один випадковий ще не зібраний предмет зі звичайної колекції. Без дублікатів.', 'pool': ['shop_frame_bronze', 'shop_frame_frost', 'shop_theme_forest', 'shop_companion_owl', 'shop_background_tavern', 'shop_coin_skin_crystal']},
        {'id': 'shop_box_arcane', 'type': 'mystery', 'icon': '🌠', 'name': 'Arcane Box', 'price': 590, 'rarity': 'epic', 'repeatable': True, 'description': 'Один випадковий ще не зібраний рідкісний або епічний предмет. Без дублікатів.', 'pool': ['shop_frame_arcane', 'shop_frame_dragonfire', 'shop_theme_crimson', 'shop_theme_royal', 'shop_background_cosmos', 'shop_companion_ghost', 'shop_treasure_skin_neon', 'shop_dungeon_skin_arcane']},
        {'id': 'shop_frame_lightning', 'type': 'frame', 'icon': '⚡', 'name': 'Блискавична рамка', 'price': 720, 'rarity': 'epic', 'description': 'Електричні золотаві контури навколо аватарки.'},
        {'id': 'shop_frame_void', 'type': 'frame', 'icon': '🌑', 'name': 'Рамка Безодні', 'price': 950, 'rarity': 'legendary', 'description': 'Темна містична рамка з холодним сяйвом.'},
        {'id': 'shop_background_dungeon', 'type': 'background', 'icon': '🏰', 'name': 'Підземна зала', 'price': 330, 'rarity': 'rare', 'description': 'Атмосферний фон підземелля для профілю.'},
        {'id': 'shop_background_dragonlair', 'type': 'background', 'icon': '🐲', 'name': 'Лігво дракона', 'price': 760, 'rarity': 'epic', 'description': 'Вогняний фон профілю з драконячим настроєм.'},
        {'id': 'shop_companion_mimic', 'type': 'companion', 'icon': '🧰', 'name': 'Мімік', 'price': 390, 'rarity': 'rare', 'description': 'Хитрий скринько-монстрик біля твого профілю.'},
        {'id': 'shop_companion_dragon', 'type': 'companion', 'icon': '🐉', 'name': 'Маленький дракон', 'price': 880, 'rarity': 'legendary', 'description': 'Рідкісний дракончик-компаньйон.'},
        {'id': 'shop_coin_skin_rune', 'type': 'coin_skin', 'icon': 'ᚱ', 'name': 'Рунічні стілси', 'price': 420, 'rarity': 'rare', 'description': 'Магічні руни у грі Stils Rush.'},
        {'id': 'shop_coin_skin_styloteka', 'type': 'coin_skin', 'icon': '⭐', 'name': 'Золотий знак Styloteka', 'price': 640, 'rarity': 'epic', 'description': 'Фірмове золотаве оформлення стілсів.'},
        {'id': 'shop_treasure_skin_pirate', 'type': 'treasure_skin', 'icon': '🏴\u200d☠️', 'name': 'Піратська скарбниця', 'price': 340, 'rarity': 'rare', 'description': 'Піратське оформлення карток Скарбниці.'},
        {'id': 'shop_treasure_skin_dragon', 'type': 'treasure_skin', 'icon': '🐲', 'name': 'Скарби дракона', 'price': 760, 'rarity': 'epic', 'description': 'Драконячі скарби та вогняні акценти.'},
        {'id': 'shop_dungeon_skin_crypt', 'type': 'dungeon_skin', 'icon': '🪦', 'name': 'Стародавня крипта', 'price': 310, 'rarity': 'rare', 'description': 'Містичний образ кімнат Данжу.'},
        {'id': 'shop_dungeon_skin_inferno', 'type': 'dungeon_skin', 'icon': '🔥', 'name': 'Палаючий Данж', 'price': 720, 'rarity': 'epic', 'description': 'Полум’яні акценти під час пригод у Данжі.'},
        {'id': 'shop_bundle_shadow', 'type': 'bundle', 'icon': '🌘', 'name': 'Набір Тіней', 'price': 1650, 'rarity': 'legendary', 'description': '4 предмети: рамка Безодні, підземна зала, мімік і крипта. Економія 330 стілсів.', 'grants': ['shop_frame_void', 'shop_background_dungeon', 'shop_companion_mimic', 'shop_dungeon_skin_crypt']},
        {'id': 'shop_bundle_dragon', 'type': 'bundle', 'icon': '🐉', 'name': 'Набір Дракона', 'price': 3090, 'rarity': 'legendary', 'description': '5 предметів: блискавична рамка, дракончик, лігво, скарбниця та палаючий Данж. Економія 750 стілсів.', 'grants': ['shop_frame_lightning', 'shop_companion_dragon', 'shop_background_dragonlair', 'shop_treasure_skin_dragon', 'shop_dungeon_skin_inferno']},
        {'id': 'shop_box_relic', 'type': 'mystery', 'icon': '🗝️', 'name': 'Скриня реліквій', 'price': 480, 'rarity': 'epic', 'repeatable': True, 'description': 'Один випадковий ще не придбаний предмет із дев’яти реліквій. Без дублікатів.', 'pool': ['shop_frame_lightning', 'shop_background_dungeon', 'shop_companion_mimic', 'shop_coin_skin_rune', 'shop_treasure_skin_pirate', 'shop_dungeon_skin_crypt', 'shop_treasure_skin_dragon', 'shop_dungeon_skin_inferno', 'shop_background_dragonlair']},
    ]


def _owned_shop_items(profile=None):
    profile = profile or {}
    raw = profile.get("owned_shop_items") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        raw = []
    return {str(x) for x in raw if x}


def _profile_shop_state(profile, cosmetics=None):
    profile = profile or {}
    cosmetics = cosmetics or {}
    owned = _owned_shop_items(profile)
    selected_by_type = {
        "frame": str(cosmetics.get("frame_id") or ""),
        "theme": str(cosmetics.get("theme_id") or ""),
        **{kind: str(cosmetics.get(f"{kind}_id") or "") for kind in
           ("background", "companion", "coin_skin", "treasure_skin", "dungeon_skin")},
    }
    result = []
    for item in _profile_shop_catalog():
        row = dict(item)
        row["rarity"] = row.get("rarity") or "common"
        row["owned"] = row["id"] in owned
        row["active"] = selected_by_type.get(row["type"]) == row["id"]
        result.append(row)
    return result


def _resolve_profile_cosmetics(level, profile=None):
    profile = profile or {}
    rewards = _xp_reward_catalog(level)
    unlocked = {r["id"]: r for r in rewards if r.get("unlocked")}

    owned_shop = _owned_shop_items(profile)
    shop_available = {
        item["id"]: item
        for item in _profile_shop_catalog()
        if item["id"] in owned_shop
    }
    available = {**unlocked, **shop_available}

    selected = {
        "title": profile.get("selected_title_reward") or "",
        "frame": profile.get("selected_frame_reward") or "",
        "badge": profile.get("selected_badge_reward") or "",
        "theme": profile.get("selected_theme_reward") or "",
    }

    # XP-нагороди доступні після рівня, магазинні — після покупки.
    for kind in tuple(selected):
        rid = selected[kind]
        reward = available.get(rid)
        if not reward or reward.get("type") != kind:
            selected[kind] = ""

    extra_loadout = profile.get("selected_shop_loadout") or {}
    if not isinstance(extra_loadout, dict):
        extra_loadout = {}
    extras = {}
    for kind in ("background", "companion", "coin_skin", "treasure_skin", "dungeon_skin"):
        rid = str(extra_loadout.get(kind) or "")
        reward = available.get(rid)
        extras[f"{kind}_id"] = rid if reward and reward.get("type") == kind else ""

    title_reward = available.get(selected["title"])
    badge_reward = available.get(selected["badge"])

    cosmetics = {
        "title_id": selected["title"],
        "frame_id": selected["frame"],
        "badge_id": selected["badge"],
        "theme_id": selected["theme"],
        "title_label": title_reward.get("value") if title_reward else "",
        "badge_label": badge_reward.get("value") if badge_reward else "",
        **extras,
    }

    for reward in rewards:
        reward["active"] = selected.get(reward.get("type")) == reward.get("id")

    next_reward = next((r for r in rewards if not r.get("unlocked")), None)
    if next_reward:
        xp_for_level = (int(next_reward["level_required"]) - 1) * 250
        next_reward = dict(next_reward)
        next_reward["xp_needed"] = max(0, xp_for_level - int(profile.get("xp") or 0))

    return rewards, cosmetics, next_reward


def _profile_title(level):
    if level >= 15:
        return "Легенда клубу"
    if level >= 10:
        return "Ветеран"
    if level >= 5:
        return "Досвідчений"
    if level >= 3:
        return "Гравець"
    return "Новачок"


@app.route("/api/profile/me", methods=["GET"])
def get_my_profile():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    user_id = int(user.get("id"))
    username = (user.get("username") or "").lstrip("@").lower()
    display_name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x).strip() or username or "Гравець"
    photo_url = _profile_avatar_url(user_id)

    # Читаємо вже зароблені досягнення ДО оновлення профілю.
    existing_profile = None
    try:
        old = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={
                "telegram_user_id": f"eq.{user_id}",
                "select": "telegram_user_id,photo_url,earned_achievements,achievements_initialized,achievement_catalog_version,selected_title_reward,selected_frame_reward,selected_badge_reward,selected_theme_reward,xp,gold,owned_shop_items,selected_shop_loadout",
                "limit": 1,
            },
            timeout=20,
        )
        if old.ok and old.json():
            existing_profile = old.json()[0]
    except Exception:
        logging.exception("Не вдалося прочитати поточні досягнення профілю")

    # Якщо користувач завантажив власну аватарку, не перезаписуємо її Telegram-фото при кожному GET.
    if existing_profile:
        saved_photo_url = str(existing_profile.get("photo_url") or "").strip()
        if saved_photo_url:
            photo_url = saved_photo_url

    previous_earned = set()
    achievements_initialized = False
    achievement_catalog_version = 0
    if existing_profile:
        raw_earned = existing_profile.get("earned_achievements") or []
        if isinstance(raw_earned, list):
            previous_earned = {str(x) for x in raw_earned if x}
        achievements_initialized = bool(existing_profile.get("achievements_initialized"))
        achievement_catalog_version = int(existing_profile.get("achievement_catalog_version") or 0)

    stats = _merge_profile_stats(user, user_id)
    achievements = _profile_achievements(stats)
    currently_unlocked = {a["id"] for a in achievements if a.get("unlocked")}

    # Після переходу на каталог v4 вже виконані умови фіксуємо тихо.
    if achievements_initialized and achievement_catalog_version >= 4:
        newly_earned_ids = currently_unlocked - previous_earned
    else:
        newly_earned_ids = set()

    earned_ids = previous_earned | currently_unlocked

    xp_breakdown = _profile_xp_breakdown(stats, earned_ids, achievements)
    xp = xp_breakdown["total_xp"]
    xp_per_level = 250
    level = max(1, xp // xp_per_level + 1)
    level_xp = xp % xp_per_level
    progress = round((level_xp / xp_per_level) * 100) if xp_per_level else 0

    cosmetic_profile = dict(existing_profile or {})
    cosmetic_profile["xp"] = xp
    xp_rewards, cosmetics, next_xp_reward = _resolve_profile_cosmetics(level, cosmetic_profile)
    active_title = cosmetics.get("title_label") or _profile_title(level)

    # Досягнення назавжди лишається відкритим після першого отримання.
    for achievement in achievements:
        achievement["unlocked"] = achievement["id"] in earned_ids

    profile_payload = {
        "telegram_user_id": user_id,
        "username": username,
        "display_name": display_name,
        "photo_url": photo_url,
        "xp": xp,
        "earned_achievements": sorted(earned_ids),
        "achievements_initialized": True,
        "achievement_catalog_version": 4,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        up = requests.post(
            PROFILES_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "telegram_user_id"},
            json=profile_payload,
            timeout=30,
        )
        if not up.ok:
            logging.warning("Профіль не збережено в Supabase: %s", up.text[:500])
    except Exception:
        logging.exception("Не вдалося зберегти профіль")

    # Спочатку фіксуємо нагороду в Supabase, потім публікуємо анонс.
    # Так повторне відкриття профілю не створить повторне повідомлення.
    if achievements_initialized and newly_earned_ids:
        by_id = {a["id"]: a for a in achievements}
        for achievement_id in sorted(newly_earned_ids):
            achievement = by_id.get(achievement_id)
            if achievement:
                log_activity(
                    "achievement_unlocked",
                    f"{display_name} отримав(ла) досягнення «{achievement.get('name') or 'Досягнення'}»",
                    achievement.get("description") or "",
                    actor_name=display_name,
                    image_url=photo_url or None,
                    metadata={"achievement_id": achievement_id, "level": level, "xp": xp},
                )
                pass  # Вимкнено: досягнення, XP і нагороди не публікуються у Telegram-групі.

    return jsonify({
        "telegram_user_id": user_id,
        "username": username,
        "display_name": display_name,
        "photo_url": photo_url,
        "xp": xp,
        "base_xp": xp_breakdown["base_xp"],
        "achievement_bonus_xp": xp_breakdown["achievement_bonus_xp"],
        "level": level,
        "level_xp": level_xp,
        "xp_per_level": xp_per_level,
        "level_progress_percent": progress,
        "title": active_title,
        "stats": stats,
        "achievements": achievements,
        "xp_rewards": xp_rewards,
        "cosmetics": cosmetics,
        "next_xp_reward": next_xp_reward,
        "gold": int((existing_profile or {}).get("gold") or 0),
        "shop_items": _profile_shop_state(existing_profile or {}, cosmetics),
    })


@app.route("/api/profile/summary", methods=["GET"])
def get_profile_summary():
    """Легкий профіль для головного екрана без важкого перерахунку статистики/досягнень."""
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    user_id = int(user.get("id"))
    username = (user.get("username") or "").lstrip("@").lower()
    display_name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x).strip() or username or "Гравець"
    profile = {}
    try:
        resp = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={
                "telegram_user_id": f"eq.{user_id}",
                "select": "telegram_user_id,username,display_name,photo_url,xp,gold,owned_shop_items,selected_title_reward,selected_frame_reward,selected_badge_reward,selected_theme_reward,selected_shop_loadout",
                "limit": 1,
            },
            timeout=10,
        )
        if resp.ok and resp.json():
            profile = resp.json()[0]
    except Exception:
        logging.exception("Не вдалося швидко завантажити профіль для головного екрана")

    xp = int(profile.get("xp") or 0)
    level = max(1, xp // 250 + 1)
    _, cosmetics, _ = _resolve_profile_cosmetics(level, profile)
    photo_url = str(profile.get("photo_url") or "").strip() or _profile_avatar_url(user_id)
    return jsonify({
        "telegram_user_id": user_id,
        "username": profile.get("username") or username,
        "display_name": profile.get("display_name") or display_name,
        "photo_url": photo_url,
        "xp": xp,
        "level": level,
        "level_xp": xp % 250,
        "xp_per_level": 250,
        "gold": int(profile.get("gold") or 0),
        "title": cosmetics.get("title_label") or _profile_title(level),
        "cosmetics": cosmetics,
        "shop_items": _profile_shop_state(profile, cosmetics),
    })


@app.route("/api/profile/avatar", methods=["POST"])
def upload_profile_avatar():
    """Завантаження власної аватарки користувача з телефону."""
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    if request.content_length and request.content_length > 6 * 1024 * 1024:
        return jsonify({"error": "avatar_too_large"}), 413

    avatar_file = request.files.get("avatar")
    if not avatar_file or not avatar_file.filename:
        return jsonify({"error": "avatar_required"}), 400

    mimetype = str(avatar_file.mimetype or "").lower()
    if not mimetype.startswith("image/"):
        return jsonify({"error": "avatar_must_be_image"}), 400

    user_id = int(user.get("id"))
    username = (user.get("username") or "").lstrip("@").lower()
    display_name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x).strip() or username or "Гравець"

    old_url = ""
    try:
        current = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={"telegram_user_id": f"eq.{user_id}", "select": "photo_url", "limit": 1},
            timeout=15,
        )
        if current.ok and current.json():
            old_url = str(current.json()[0].get("photo_url") or "").strip()
    except Exception:
        logging.exception("Не вдалося прочитати попередню аватарку")

    try:
        new_url = upload_file(avatar_file, "avatars")
    except Exception:
        logging.exception("Не вдалося завантажити аватарку у Storage")
        return jsonify({"error": "avatar_upload_failed"}), 500

    payload = {
        "telegram_user_id": user_id,
        "username": username,
        "display_name": display_name,
        "photo_url": new_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        up = requests.post(
            PROFILES_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "telegram_user_id"},
            json=payload,
            timeout=20,
        )
        if not up.ok:
            delete_file(new_url)
            return jsonify({"error": "profile_update_failed"}), 500
    except Exception:
        delete_file(new_url)
        logging.exception("Не вдалося зберегти нову аватарку в профілі")
        return jsonify({"error": "profile_update_failed"}), 500

    if old_url and old_url != new_url:
        delete_file(old_url)

    return jsonify({"status": "ok", "photo_url": new_url})


@app.route("/api/profile/avatar", methods=["DELETE"])
def reset_profile_avatar():
    """Повертає Telegram-аватар і видаляє попередню власну аватарку зі Storage."""
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    user_id = int(user.get("id"))
    old_url = ""
    try:
        current = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={"telegram_user_id": f"eq.{user_id}", "select": "photo_url", "limit": 1},
            timeout=15,
        )
        if current.ok and current.json():
            old_url = str(current.json()[0].get("photo_url") or "").strip()
    except Exception:
        pass

    telegram_photo = _profile_avatar_url(user_id)
    try:
        up = requests.patch(
            PROFILES_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            params={"telegram_user_id": f"eq.{user_id}"},
            json={"photo_url": telegram_photo, "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=20,
        )
        if not up.ok:
            return jsonify({"error": "profile_update_failed"}), 500
    except Exception:
        return jsonify({"error": "profile_update_failed"}), 500

    if old_url and old_url != telegram_photo:
        delete_file(old_url)
    return jsonify({"status": "ok", "photo_url": telegram_photo})


@app.route("/api/profile/cosmetics", methods=["POST"])
def set_profile_cosmetics():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    user_id = int(user.get("id"))
    data = request.get_json(silent=True) or {}

    try:
        resp = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={
                "telegram_user_id": f"eq.{user_id}",
                "select": "telegram_user_id,xp,owned_shop_items,selected_title_reward,selected_frame_reward,selected_badge_reward,selected_theme_reward,selected_shop_loadout",
                "limit": 1,
            },
            timeout=20,
        )
        if not resp.ok or not resp.json():
            return jsonify({"error": "profile_not_found"}), 404
        profile = resp.json()[0]
    except Exception:
        logging.exception("Не вдалося прочитати профіль для зміни оформлення")
        return jsonify({"error": "profile_lookup_failed"}), 503

    xp = int(profile.get("xp") or 0)
    level = max(1, xp // 250 + 1)
    rewards = _xp_reward_catalog(level)
    unlocked = {r["id"]: r for r in rewards if r.get("unlocked")}
    owned_shop = _owned_shop_items(profile)
    shop_available = {
        item["id"]: item
        for item in _profile_shop_catalog()
        if item["id"] in owned_shop
    }
    available = {**unlocked, **shop_available}

    field_map = {
        "title": "selected_title_reward",
        "frame": "selected_frame_reward",
        "badge": "selected_badge_reward",
        "theme": "selected_theme_reward",
    }

    patch = {}
    shop_loadout = profile.get("selected_shop_loadout") or {}
    if not isinstance(shop_loadout, dict):
        shop_loadout = {}
    changed_extras = False
    for kind in ("background", "companion", "coin_skin", "treasure_skin", "dungeon_skin"):
        if kind not in data:
            continue
        reward_id = str(data.get(kind) or "").strip()
        if reward_id:
            reward = available.get(reward_id)
            if not reward or reward.get("type") != kind:
                return jsonify({"error": "reward_not_unlocked", "reward_id": reward_id}), 403
        shop_loadout[kind] = reward_id
        changed_extras = True
    if changed_extras:
        patch["selected_shop_loadout"] = shop_loadout

    for kind, field in field_map.items():
        if kind not in data:
            continue
        reward_id = str(data.get(kind) or "").strip()
        if not reward_id:
            patch[field] = None
            continue
        reward = available.get(reward_id)
        if not reward or reward.get("type") != kind:
            return jsonify({"error": "reward_not_unlocked", "reward_id": reward_id}), 403
        patch[field] = reward_id

    if not patch:
        return jsonify({"status": "ok"})

    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        up = requests.patch(
            PROFILES_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            params={"telegram_user_id": f"eq.{user_id}"},
            json=patch,
            timeout=20,
        )
        if not up.ok:
            logging.warning("Не вдалося зберегти оформлення профілю: %s", up.text[:500])
            return jsonify({"error": "profile_update_failed"}), 500
    except Exception:
        logging.exception("Не вдалося зберегти оформлення профілю")
        return jsonify({"error": "profile_update_failed"}), 500

    return jsonify({"status": "ok"})



def _run_shop_transaction(user_id, item_id, recipient_id=None, request_id=None):
    """Call server-only, transactional Supabase function; client cannot pass prices."""
    try:
        tx_id = str(uuid.UUID(str(request_id))) if request_id else str(uuid.uuid4())
    except (ValueError, AttributeError, TypeError):
        return {"error": "invalid_request_id"}, 400
    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/styloteka_shop_transaction",
            headers={**HEADERS, "Content-Type": "application/json"},
            json={"p_user_id": int(user_id), "p_item_id": str(item_id),
                  "p_recipient_id": recipient_id, "p_request_id": tx_id},
            timeout=25,
        )
        if not response.ok:
            logging.warning("Shop RPC failed: %s %s", response.status_code, response.text[:500])
            return {"error": "shop_transaction_failed"}, 503
        payload = response.json()
        if not isinstance(payload, dict):
            return {"error": "shop_transaction_failed"}, 503
        status = 200
        if payload.get("status") in ("not_enough_gold", "already_owned", "box_complete", "bundle_complete"):
            status = 409
        if payload.get("status") in ("invalid_item", "recipient_not_found", "gift_not_allowed", "self_gift", "profile_not_found", "request_conflict"):
            status = 400
        return payload, status
    except Exception:
        logging.exception("Shop RPC request failed")
        return {"error": "shop_transaction_failed"}, 503


@app.route("/api/profile/shop/buy", methods=["POST"])
def buy_profile_shop_item():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401
    data = request.get_json(silent=True) or {}
    item_id = str(data.get("item_id") or "").strip()
    if item_id not in {x["id"] for x in _profile_shop_catalog()}:
        return jsonify({"error": "shop_item_not_found"}), 404
    payload, status = _run_shop_transaction(user["id"], item_id, request_id=data.get("request_id"))
    return jsonify(payload), status


@app.route("/api/profile/shop/gift", methods=["POST"])
def send_profile_shop_gift():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401
    data = request.get_json(silent=True) or {}
    item_id = str(data.get("item_id") or "").strip()
    catalog = {x["id"]: x for x in _profile_shop_catalog()}
    item = catalog.get(item_id)
    if not item or item["type"] in ("bundle", "mystery", "coupon"):
        return jsonify({"error": "gift_not_allowed"}), 400
    try:
        recipient_id = int(data.get("recipient_id"))
        if recipient_id <= 0 or recipient_id == int(user["id"]):
            return jsonify({"error": "invalid_recipient"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "invalid_recipient"}), 400
    payload, status = _run_shop_transaction(user["id"], item_id, recipient_id, data.get("request_id"))
    return jsonify(payload), status


@app.route("/api/profile/shop/recipients", methods=["GET"])
def list_profile_shop_recipients():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401
    search = re.sub(r"[^\w\s@А-Яа-яІіЇїЄєҐґ-]", "", str(request.args.get("q") or ""))[:40].strip().lstrip("@")
    params = {"select": "telegram_user_id,username,display_name", "telegram_user_id": f"neq.{int(user['id'])}", "order": "display_name.asc", "limit": 30}
    if search:
        safe_term = search.replace(",", "").replace("%", "").replace("*", "")
        params["or"] = f"(display_name.ilike.*{safe_term}*,username.ilike.*{safe_term}*)"
    try:
        res = requests.get(PROFILES_REST, headers=HEADERS, params=params, timeout=15)
        if not res.ok:
            logging.warning("Shop recipients HTTP %s: %s", res.status_code, res.text[:300])
            return jsonify({"error": "recipients_load_failed"}), 503
        return jsonify([{"id": int(r["telegram_user_id"]), "name": str(r.get("display_name") or r.get("username") or "Гравець")[:70], "username": str(r.get("username") or "")[:80]} for r in res.json()])
    except Exception:
        logging.exception("Shop recipients loading failed")
        return jsonify({"error": "recipients_load_failed"}), 503


@app.route("/api/profile/shop/gifts", methods=["GET"])
def list_profile_shop_gifts():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401
    try:
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/styloteka_shop_gifts",
            headers=HEADERS,
            params={"recipient_id": f"eq.{int(user['id'])}", "select": "id,item_id,sender_name,created_at", "order": "created_at.desc", "limit": 25},
            timeout=15,
        )
        if not res.ok:
            return jsonify({"error": "gift_inbox_failed"}), 503
        return jsonify(res.json())
    except Exception:
        logging.exception("Shop gifts loading failed")
        return jsonify({"error": "gift_inbox_failed"}), 503


@app.route("/api/minigames/coin-rush/claim-v2", methods=["POST"])
def claim_coin_rush_reward_v2():
    """Зарахування нагороди Coin Rush v2 одним атомарним RPC.

    Старт гри повністю локальний. Сервер викликається лише після завершення
    раунду, а run_id робить повторні запити безпечними від подвійної виплати.
    """
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or "").strip()
    try:
        uuid.UUID(run_id)
    except Exception:
        return jsonify({"error": "invalid_run_id"}), 400

    try:
        score = max(0, min(200, int(data.get("score") or 0)))
        caught = max(0, min(120, int(data.get("caught") or 0)))
        duration_ms = max(0, min(60000, int(data.get("duration_ms") or 0)))
    except Exception:
        return jsonify({"error": "invalid_result"}), 400

    if duration_ms < 18000:
        return jsonify({"error": "round_too_short"}), 400

    user_id = int(user.get("id"))
    try:
        resp = requests.post(
            COIN_RUSH_CLAIM_RPC,
            headers={**HEADERS, "Content-Type": "application/json"},
            json={
                "p_run_id": run_id,
                "p_user_id": user_id,
                "p_score": score,
                "p_caught": caught,
                "p_duration_ms": duration_ms,
            },
            timeout=20,
        )
    except Exception:
        logging.exception("Coin Rush: помилка з'єднання з Supabase")
        return jsonify({"error": "reward_service_unavailable"}), 503

    if not resp.ok:
        logging.error(
            "Coin Rush RPC failed: status=%s body=%s",
            resp.status_code,
            (resp.text or "")[:800],
        )
        return jsonify({
            "error": "reward_rpc_failed",
            "upstream_status": resp.status_code,
        }), 502

    try:
        result = resp.json()
    except Exception:
        logging.error("Coin Rush RPC returned invalid JSON: %s", (resp.text or "")[:800])
        return jsonify({"error": "reward_rpc_invalid_response"}), 502

    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        result = {"status": "unknown"}

    status = str(result.get("status") or "")
    if status in ("settled", "already_settled"):
        return jsonify(result), 200
    if status == "profile_not_found":
        return jsonify({"error": "profile_not_found"}), 404
    if status == "too_early":
        return jsonify({"error": "round_too_short"}), 400
    if status == "run_conflict":
        return jsonify({"error": "run_conflict"}), 409

    logging.error("Coin Rush RPC unexpected result: %r", result)
    return jsonify({"error": "reward_unknown_result", "status": status}), 502

@app.route("/api/minigames/treasure-memory/claim", methods=["POST"])
def claim_treasure_memory_reward():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or "").strip()
    try:
        uuid.UUID(run_id)
    except Exception:
        return jsonify({"error": "invalid_run_id"}), 400

    try:
        pairs = max(0, min(8, int(data.get("pairs") or 0)))
        moves = max(0, min(200, int(data.get("moves") or 0)))
        duration_ms = max(0, min(60000, int(data.get("duration_ms") or 0)))
        completed = bool(data.get("completed"))
    except Exception:
        return jsonify({"error": "invalid_result"}), 400

    if duration_ms < 5000:
        return jsonify({"error": "round_too_short"}), 400

    try:
        resp = requests.post(
            TREASURE_MEMORY_CLAIM_RPC,
            headers={**HEADERS, "Content-Type": "application/json"},
            json={
                "p_run_id": run_id,
                "p_user_id": int(user.get("id")),
                "p_pairs": pairs,
                "p_moves": moves,
                "p_duration_ms": duration_ms,
                "p_completed": completed,
            },
            timeout=20,
        )
    except Exception:
        logging.exception("Treasure Memory: помилка з'єднання з Supabase")
        return jsonify({"error": "reward_service_unavailable"}), 503

    if not resp.ok:
        logging.error("Treasure Memory RPC failed: status=%s body=%s", resp.status_code, (resp.text or "")[:800])
        return jsonify({"error": "reward_rpc_failed", "upstream_status": resp.status_code}), 502

    try:
        result = resp.json()
    except Exception:
        logging.error("Treasure Memory RPC returned invalid JSON: %s", (resp.text or "")[:800])
        return jsonify({"error": "reward_rpc_invalid_response"}), 502

    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        result = {"status": "unknown"}

    status = str(result.get("status") or "")
    if status in ("settled", "already_settled"):
        return jsonify(result), 200
    if status == "profile_not_found":
        return jsonify({"error": "profile_not_found"}), 404
    if status == "too_early":
        return jsonify({"error": "round_too_short"}), 400
    if status == "run_conflict":
        return jsonify({"error": "run_conflict"}), 409

    logging.error("Treasure Memory RPC unexpected result: %r", result)
    return jsonify({"error": "reward_unknown_result", "status": status}), 502

@app.route("/api/dungeon/start", methods=["POST"])
def start_dungeon_run():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    user_id = int(user.get("id"))
    try:
        resp = requests.post(
            DUNGEON_RUNS_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
            json={"telegram_user_id": user_id},
            timeout=20,
        )
        if not resp.ok:
            logging.warning("Не вдалося створити похід: %s", resp.text[:500])
            return jsonify({"error": "dungeon_start_failed"}), 500
        rows = resp.json()
        if not rows:
            return jsonify({"error": "dungeon_start_failed"}), 500
        return jsonify({"status": "ok", "run_id": rows[0].get("id")})
    except Exception:
        logging.exception("Не вдалося створити похід")
        return jsonify({"error": "dungeon_start_failed"}), 500


@app.route("/api/dungeon/complete", methods=["POST"])
def complete_dungeon_run():
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or "").strip()
    if not re.match(r"^[0-9a-fA-F-]{36}$", run_id):
        return jsonify({"error": "invalid_run_id"}), 400

    try:
        reward_gold = int(data.get("gold") or 0)
    except Exception:
        reward_gold = 0
    reward_gold = max(0, min(120, reward_gold))
    won = bool(data.get("won"))
    user_id = int(user.get("id"))

    try:
        resp = requests.post(
            DUNGEON_SETTLE_RPC,
            headers={**HEADERS, "Content-Type": "application/json"},
            json={
                "p_run_id": run_id,
                "p_user_id": user_id,
                "p_gold": reward_gold,
                "p_won": won,
            },
            timeout=20,
        )
        if not resp.ok:
            logging.warning("Не вдалося зарахувати золото: %s", resp.text[:500])
            return jsonify({"error": "dungeon_settlement_failed"}), 500
        result = resp.json()
        if isinstance(result, list) and result:
            result = result[0]
        return jsonify(result if isinstance(result, dict) else {"status": "ok"})
    except Exception:
        logging.exception("Не вдалося зарахувати золото з підземелля")
        return jsonify({"error": "dungeon_settlement_failed"}), 500




def _refresh_profile_achievements_by_id(telegram_user_id, announce=True):
    """Перераховує досягнення конкретного профілю за Telegram ID."""
    try:
        pid = int(telegram_user_id)
    except Exception:
        return []

    try:
        resp = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={
                "telegram_user_id": f"eq.{pid}",
                "select": "telegram_user_id,username,display_name,photo_url,xp,earned_achievements,achievements_initialized,achievement_catalog_version",
                "limit": 1,
            },
            timeout=20,
        )
        if not resp.ok or not resp.json():
            return []
        profile = resp.json()[0]
    except Exception:
        logging.exception("Не вдалося прочитати профіль для перерахунку досягнень")
        return []

    pseudo_user = {
        "id": pid,
        "username": profile.get("username") or "",
        "first_name": profile.get("display_name") or profile.get("username") or "Гравець",
        "last_name": "",
    }
    stats = _merge_profile_stats(pseudo_user, pid)
    achievements = _profile_achievements(stats)
    current_ids = {a["id"] for a in achievements if a.get("unlocked")}
    old_ids = {str(x) for x in (profile.get("earned_achievements") or []) if x}
    initialized = bool(profile.get("achievements_initialized"))
    catalog_version = int(profile.get("achievement_catalog_version") or 0)

    if announce and initialized and catalog_version >= 4:
        new_ids = current_ids - old_ids
    else:
        new_ids = set()

    earned_ids = old_ids | current_ids

    xp_breakdown = _profile_xp_breakdown(stats, earned_ids, achievements)
    history_xp = xp_breakdown["total_xp"]
    level = max(1, history_xp // 250 + 1)

    try:
        requests.patch(
            PROFILES_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            params={"telegram_user_id": f"eq.{pid}"},
            json={
                "xp": history_xp,
                "earned_achievements": sorted(earned_ids),
                "achievements_initialized": True,
                "achievement_catalog_version": 4,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=20,
        )
    except Exception:
        logging.exception("Не вдалося зберегти перераховані досягнення")

    if new_ids:
        by_id = {a["id"]: a for a in achievements}
        display_name = profile.get("display_name") or profile.get("username") or "Гравець"
        for aid in sorted(new_ids):
            achievement = by_id.get(aid)
            if not achievement:
                continue
            try:
                log_activity(
                    "achievement_unlocked",
                    f"{display_name} отримав(ла) досягнення «{achievement.get('name') or 'Досягнення'}»",
                    achievement.get("description") or "",
                    actor_name=display_name,
                    image_url=_profile_avatar_url(profile.get("telegram_user_id")) or None,
                    metadata={"achievement_id": aid, "level": level, "xp": history_xp},
                )
                pass  # Вимкнено: досягнення, XP і нагороди не публікуються у Telegram-групі.
            except Exception:
                logging.exception("Не вдалося опублікувати нове досягнення")

    return [a for a in achievements if a.get("id") in new_ids]


@app.route("/api/profile/tool-event", methods=["POST"])
def record_profile_tool_event():
    recorder = _request_telegram_user()
    if not recorder:
        return jsonify({"error": "telegram_auth_required"}), 401

    data = request.get_json(silent=True) or {}
    event_type = str(data.get("event_type") or "").strip()
    if event_type not in {"dice_roll", "first_player_win"}:
        return jsonify({"error": "unsupported_event_type"}), 400

    try:
        recorder_id = int(recorder.get("id"))
        target_id = int(data.get("target_telegram_user_id") or recorder_id)
    except Exception:
        return jsonify({"error": "invalid_telegram_user_id"}), 400

    # Цільовий ID мусить належати реальному профілю в нашому клубі.
    try:
        check = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={"telegram_user_id": f"eq.{target_id}", "select": "telegram_user_id", "limit": 1},
            timeout=15,
        )
        if not check.ok or not check.json():
            return jsonify({"error": "profile_not_found"}), 404
    except Exception:
        return jsonify({"error": "profile_lookup_failed"}), 503

    # Перед новою дією тихо переводимо старий профіль на каталог v2.
    # Завдяки цьому старі новододані нагороди не засипають групу повідомленнями,
    # але нагорода, отримана саме цією новою дією, буде оголошена.
    _refresh_profile_achievements_by_id(target_id, announce=False)

    value = data.get("value")
    sides = data.get("sides")
    if event_type == "dice_roll":
        try:
            value = int(value)
            sides = int(sides)
        except Exception:
            return jsonify({"error": "invalid_dice_roll"}), 400
        if sides not in {4, 6, 8, 10, 12, 20, 100} or value < 1 or value > sides:
            return jsonify({"error": "invalid_dice_roll"}), 400
    else:
        value = None
        sides = None

    metadata = {}
    if event_type == "first_player_win":
        try:
            metadata["participants"] = max(2, min(20, int(data.get("participants") or 2)))
        except Exception:
            metadata["participants"] = 2

    try:
        rpc = requests.post(
            PROFILE_TOOL_EVENT_RPC,
            headers={**HEADERS, "Content-Type": "application/json"},
            json={
                "p_telegram_user_id": target_id,
                "p_recorded_by_telegram_user_id": recorder_id,
                "p_event_type": event_type,
                "p_value": value,
                "p_sides": sides,
                "p_metadata": metadata,
            },
            timeout=20,
        )
        if not rpc.ok:
            logging.warning("record_profile_tool_event RPC failed: %s", rpc.text[:500])
            return jsonify({"error": "tool_event_store_failed"}), 500
    except Exception:
        logging.exception("Не вдалося записати подію інструмента")
        return jsonify({"error": "tool_event_store_failed"}), 500

    newly = _refresh_profile_achievements_by_id(target_id, announce=True)
    return jsonify({
        "status": "ok",
        "target_telegram_user_id": target_id,
        "new_achievements": newly,
    })


# ==================== АКТИВНІСТЬ КЛУБУ ====================
def log_activity(activity_type, title, subtitle="", actor_name="", image_url=None, metadata=None):
    """Пише одну подію в стрічку. Якщо таблиця ще не створена — основна дія не ламається."""
    try:
        payload = {
            "activity_type": str(activity_type or "activity")[:64],
            "actor_name": str(actor_name or "")[:200],
            "title": str(title or "Активність клубу")[:300],
            "subtitle": str(subtitle or "")[:1000],
            "image_url": image_url or None,
            "metadata": metadata or {},
        }
        resp = requests.post(
            ACTIVITY_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json=payload,
            timeout=15,
        )
        if not resp.ok:
            logging.warning("Activity feed write skipped: %s", resp.text[:300])
    except Exception:
        logging.exception("Не вдалося записати активність клубу")


@app.route("/api/activity", methods=["GET"])
def get_activity_feed():
    try:
        limit = max(1, min(100, int(request.args.get("limit") or 30)))
    except ValueError:
        limit = 30
    try:
        resp = requests.get(
            ACTIVITY_REST,
            headers=HEADERS,
            params={"select": "*", "order": "created_at.desc,id.desc", "limit": limit},
            timeout=20,
        )
        if not resp.ok:
            return jsonify([])
        return jsonify(resp.json())
    except Exception:
        logging.exception("Не вдалося завантажити стрічку активності")
        return jsonify([])


@app.route("/api/profiles/public", methods=["GET"])
def get_public_profiles():
    try:
        resp = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={
                "select": "telegram_user_id,username,display_name,photo_url,xp,earned_achievements,owned_shop_items,selected_title_reward,selected_frame_reward,selected_badge_reward,selected_theme_reward,selected_shop_loadout,updated_at",
                "order": "xp.desc,updated_at.desc",
                "limit": 100,
            },
            timeout=25,
        )
        if not resp.ok:
            return jsonify([])
        result = []
        for row in resp.json():
            xp = int(row.get("xp") or 0)
            level = max(1, xp // 250 + 1)
            earned = row.get("earned_achievements") or []
            _, cosmetics, _ = _resolve_profile_cosmetics(level, row)
            public_title = cosmetics.get("title_label") or _profile_title(level)
            result.append({
                "telegram_user_id": row.get("telegram_user_id"),
                "username": row.get("username") or "",
                "display_name": row.get("display_name") or row.get("username") or "Гравець",
                "photo_url": str(row.get("photo_url") or "").strip() or _profile_avatar_url(row.get("telegram_user_id")),
                "xp": xp,
                "level": level,
                "title": public_title,
                "cosmetics": cosmetics,
                "achievement_count": len(earned) if isinstance(earned, list) else 0,
                "updated_at": row.get("updated_at"),
            })
        return jsonify(result)
    except Exception:
        logging.exception("Не вдалося завантажити публічні профілі")
        return jsonify([])


# ==================== АДМІНИ ====================
@app.route("/api/admins/check", methods=["POST"])
def check_admin():
    user = _request_telegram_user()
    is_admin = bool(user and _telegram_user_is_group_admin(user.get("id")))
    return jsonify({"is_admin": is_admin})


@app.route("/api/profile/visit", methods=["POST"])
def record_profile_visit():
    """Фіксує відкриття Mini App для авторизованого Telegram-користувача."""
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    user_id = int(user.get("id"))
    username = (user.get("username") or "").lstrip("@").lower()
    display_name = " ".join(
        x for x in [user.get("first_name"), user.get("last_name")] if x
    ).strip() or username or "Гравець"
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        current = requests.get(
            PROFILES_REST,
            headers=HEADERS,
            params={
                "telegram_user_id": f"eq.{user_id}",
                "select": "telegram_user_id,visit_count,photo_url,is_blocked",
                "limit": 1,
            },
            timeout=15,
        )
        current.raise_for_status()
        rows = current.json() or []
        existing = rows[0] if rows else None

        visit_count = int((existing or {}).get("visit_count") or 0) + 1
        payload = {
            "telegram_user_id": user_id,
            "username": username,
            "display_name": display_name,
            "last_seen_at": now_iso,
            "visit_count": visit_count,
            "updated_at": now_iso,
        }
        if not existing:
            payload["photo_url"] = _profile_avatar_url(user_id)

        saved = requests.post(
            PROFILES_REST,
            headers={
                **HEADERS,
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            params={"on_conflict": "telegram_user_id"},
            json=payload,
            timeout=20,
        )
        saved.raise_for_status()
        return jsonify({
            "ok": True,
            "telegram_user_id": user_id,
            "last_seen_at": now_iso,
            "visit_count": visit_count,
            "is_blocked": bool((existing or {}).get("is_blocked")),
        })
    except Exception as exc:
        logging.exception("Не вдалося зафіксувати відвідування Mini App")
        return jsonify({"error": "visit_save_failed", "detail": str(exc)[:180]}), 500


def _supabase_fetch_all(url, select="*", order=None, extra_params=None, page_size=1000):
    """Зчитує всі рядки PostgREST сторінками, щоб адмінка не обрізала список."""
    result = []
    offset = 0
    extra_params = dict(extra_params or {})
    while True:
        params = {**extra_params, "select": select, "limit": page_size, "offset": offset}
        if order:
            params["order"] = order
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        page = resp.json() or []
        if not isinstance(page, list):
            raise ValueError("supabase_page_not_list")
        result.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return result


def _parse_iso_datetime(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _admin_profile_by_id(user_id):
    resp = requests.get(
        PROFILES_REST,
        headers=HEADERS,
        params={"telegram_user_id": f"eq.{int(user_id)}", "select": "*", "limit": 1},
        timeout=20,
    )
    resp.raise_for_status()
    rows = resp.json() or []
    return rows[0] if rows else None


def _admin_count_runs(url, user_id):
    try:
        rows = _supabase_fetch_all(
            url,
            select="id",
            extra_params={"telegram_user_id": f"eq.{int(user_id)}"},
            page_size=1000,
        )
        return len(rows)
    except Exception:
        logging.exception("Не вдалося порахувати запуски мінігри для %s", user_id)
        return 0


def _admin_action_log(admin_id, target_id, action_type, amount=None, item_id=None, reason=None, metadata=None):
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/admin_user_actions",
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={
                "admin_telegram_user_id": int(admin_id),
                "target_telegram_user_id": int(target_id),
                "action_type": str(action_type or ""),
                "amount": int(amount) if amount is not None else None,
                "item_id": item_id,
                "reason": reason,
                "metadata": metadata or {},
            },
            timeout=20,
        ).raise_for_status()
    except Exception:
        logging.exception("Не вдалося записати адмін-дію")


@app.route("/api/admin/users", methods=["GET"])
def admin_users_dashboard():
    """Повний список користувачів Mini App. Доступ лише адміну Telegram-групи."""
    denied = _admin_required_response()
    if denied:
        return denied

    try:
        profiles = _supabase_fetch_all(
            PROFILES_REST,
            select=(
                "telegram_user_id,username,display_name,photo_url,xp,gold,"
                "owned_shop_items,created_at,updated_at,last_seen_at,visit_count,is_blocked"
            ),
            order="last_seen_at.desc.nullslast,created_at.desc",
        )

        run_counts = {}
        for run_url in (
            DUNGEON_RUNS_REST,
            f"{SUPABASE_URL}/rest/v1/coin_catcher_runs",
            f"{SUPABASE_URL}/rest/v1/treasure_memory_runs",
        ):
            try:
                for row in _supabase_fetch_all(run_url, select="telegram_user_id"):
                    uid = row.get("telegram_user_id")
                    if uid is not None:
                        uid = int(uid)
                        run_counts[uid] = int(run_counts.get(uid, 0)) + 1
            except Exception:
                logging.exception("Не вдалося порахувати частину запусків мініігор")

        tz = ZoneInfo(EVENT_TIMEZONE)
        now_local = datetime.now(tz)
        today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_local.astimezone(timezone.utc)
        seven_days_ago_utc = (now_local - timedelta(days=7)).astimezone(timezone.utc)

        users = []
        active_today = 0
        active_7d = 0
        new_today = 0

        for row in profiles:
            try:
                uid = int(row.get("telegram_user_id"))
            except Exception:
                continue

            xp = int(row.get("xp") or 0)
            gold = int(row.get("gold") or 0)
            owned = row.get("owned_shop_items") or []
            if isinstance(owned, str):
                try:
                    owned = json.loads(owned)
                except Exception:
                    owned = []
            if not isinstance(owned, list):
                owned = []

            last_seen_raw = row.get("last_seen_at") or row.get("updated_at") or row.get("created_at")
            last_seen = _parse_iso_datetime(last_seen_raw)
            created = _parse_iso_datetime(row.get("created_at"))
            if last_seen and last_seen >= today_start_utc:
                active_today += 1
            if last_seen and last_seen >= seven_days_ago_utc:
                active_7d += 1
            if created and created >= today_start_utc:
                new_today += 1

            users.append({
                "telegram_user_id": uid,
                "username": row.get("username") or "",
                "display_name": row.get("display_name") or row.get("username") or "Гравець",
                "photo_url": str(row.get("photo_url") or "").strip() or _profile_avatar_url(uid),
                "xp": xp,
                "level": max(1, xp // 250 + 1),
                "gold": gold,
                "shop_items_count": len(owned),
                "visit_count": max(1, int(row.get("visit_count") or 0)),
                "game_runs": int(run_counts.get(uid, 0)),
                "created_at": row.get("created_at"),
                "last_seen_at": last_seen_raw,
                "is_blocked": bool(row.get("is_blocked")),
            })

        users.sort(
            key=lambda x: _parse_iso_datetime(x.get("last_seen_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        response = jsonify({
            "stats": {
                "total_users": len(users),
                "active_today": active_today,
                "active_7d": active_7d,
                "new_today": new_today,
            },
            "users": users,
        })
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as exc:
        logging.exception("Не вдалося завантажити список користувачів для адмінки")
        return jsonify({"error": "admin_users_load_failed", "detail": str(exc)[:220]}), 500


@app.route("/api/admin/users/<int:user_id>", methods=["GET"])
def admin_user_detail(user_id):
    denied = _admin_required_response()
    if denied:
        return denied

    try:
        profile = _admin_profile_by_id(user_id)
        if not profile:
            return jsonify({"error": "profile_not_found"}), 404

        xp = int(profile.get("xp") or 0)
        owned_ids = profile.get("owned_shop_items") or []
        if isinstance(owned_ids, str):
            try:
                owned_ids = json.loads(owned_ids)
            except Exception:
                owned_ids = []
        if not isinstance(owned_ids, list):
            owned_ids = []

        catalog = _profile_shop_catalog()
        by_id = {str(item.get("id")): item for item in catalog}
        owned_items = []
        for item_id in owned_ids:
            item = by_id.get(str(item_id))
            if item:
                owned_items.append({
                    "id": item.get("id"),
                    "name": item.get("name") or item.get("id"),
                    "icon": item.get("icon") or "🎁",
                    "type": item.get("type") or "item",
                })
            else:
                owned_items.append({"id": str(item_id), "name": str(item_id), "icon": "🎁", "type": "item"})

        profile_out = {
            "telegram_user_id": int(profile.get("telegram_user_id")),
            "username": profile.get("username") or "",
            "display_name": profile.get("display_name") or profile.get("username") or "Гравець",
            "photo_url": str(profile.get("photo_url") or "").strip() or _profile_avatar_url(user_id),
            "xp": xp,
            "level": max(1, xp // 250 + 1),
            "gold": int(profile.get("gold") or 0),
            "visit_count": max(1, int(profile.get("visit_count") or 0)),
            "created_at": profile.get("created_at"),
            "last_seen_at": profile.get("last_seen_at") or profile.get("updated_at") or profile.get("created_at"),
            "is_blocked": bool(profile.get("is_blocked")),
            "blocked_at": profile.get("blocked_at"),
            "blocked_reason": profile.get("blocked_reason") or "",
            "owned_shop_items": owned_items,
        }

        action_resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/admin_user_actions",
            headers=HEADERS,
            params={
                "target_telegram_user_id": f"eq.{int(user_id)}",
                "select": "id,admin_telegram_user_id,action_type,amount,item_id,reason,metadata,created_at",
                "order": "created_at.desc",
                "limit": 30,
            },
            timeout=20,
        )
        if action_resp.ok:
            admin_actions = action_resp.json() or []
        else:
            admin_actions = []

        return jsonify({
            "user": profile_out,
            "game_runs": {
                "dungeon": _admin_count_runs(DUNGEON_RUNS_REST, user_id),
                "coin_rush": _admin_count_runs(f"{SUPABASE_URL}/rest/v1/coin_catcher_runs", user_id),
                "treasure": _admin_count_runs(f"{SUPABASE_URL}/rest/v1/treasure_memory_runs", user_id),
            },
            "gift_catalog": [
                {"id": x.get("id"), "name": x.get("name") or x.get("id"), "icon": x.get("icon") or "🎁", "type": x.get("type") or "item"}
                for x in catalog
            ],
            "admin_actions": admin_actions,
        })
    except Exception as exc:
        logging.exception("Не вдалося завантажити картку користувача")
        return jsonify({"error": "admin_user_detail_failed", "detail": str(exc)[:220]}), 500


@app.route("/api/admin/users/<int:user_id>/action", methods=["POST"])
def admin_user_action(user_id):
    admin = _request_telegram_user()
    if not admin or not _telegram_user_is_group_admin(admin.get("id")):
        return jsonify({"error": "group_admin_required"}), 403

    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip()
    if action not in {"add_gold", "add_xp", "gift_item", "toggle_block"}:
        return jsonify({"error": "unsupported_admin_action"}), 400

    try:
        profile = _admin_profile_by_id(user_id)
        if not profile:
            return jsonify({"error": "profile_not_found"}), 404

        patch = {"updated_at": datetime.now(timezone.utc).isoformat()}
        log_amount = None
        log_item_id = None
        log_reason = None

        if action in {"add_gold", "add_xp"}:
            try:
                amount = int(body.get("amount"))
            except Exception:
                return jsonify({"error": "invalid_amount"}), 400
            if amount < 1 or amount > 100000:
                return jsonify({"error": "invalid_amount"}), 400
            field = "gold" if action == "add_gold" else "xp"
            patch[field] = max(0, int(profile.get(field) or 0) + amount)
            log_amount = amount

        elif action == "gift_item":
            item_id = str(body.get("item_id") or "").strip()
            catalog = {str(x.get("id")): x for x in _profile_shop_catalog()}
            if not item_id or item_id not in catalog:
                return jsonify({"error": "shop_item_not_found"}), 404
            owned = profile.get("owned_shop_items") or []
            if isinstance(owned, str):
                try:
                    owned = json.loads(owned)
                except Exception:
                    owned = []
            if not isinstance(owned, list):
                owned = []
            owned = [str(x) for x in owned if x]
            if item_id not in owned:
                owned.append(item_id)
            patch["owned_shop_items"] = owned
            log_item_id = item_id

        elif action == "toggle_block":
            currently_blocked = bool(profile.get("is_blocked"))
            new_blocked = not currently_blocked
            patch["is_blocked"] = new_blocked
            patch["blocked_at"] = datetime.now(timezone.utc).isoformat() if new_blocked else None
            reason = str(body.get("reason") or "").strip()[:500]
            patch["blocked_reason"] = reason if new_blocked else None
            log_reason = reason if new_blocked else "розблоковано"

        resp = requests.patch(
            PROFILES_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
            params={"telegram_user_id": f"eq.{int(user_id)}"},
            json=patch,
            timeout=20,
        )
        resp.raise_for_status()
        rows = resp.json() or []
        if not rows:
            return jsonify({"error": "profile_update_failed"}), 500

        _admin_action_log(
            admin.get("id"), user_id, action,
            amount=log_amount,
            item_id=log_item_id,
            reason=log_reason,
        )
        return jsonify({"ok": True, "user": rows[0]})
    except Exception as exc:
        logging.exception("Не вдалося виконати адмін-дію")
        return jsonify({"error": "admin_action_failed", "detail": str(exc)[:220]}), 500

def _is_admin_username(username):
    uname = (username or "").lstrip("@").lower()
    if not uname:
        return False
    resp = requests.get(
        ADMINS_REST,
        headers=HEADERS,
        params={"username": f"eq.{uname}", "select": "username", "limit": 1},
        timeout=20,
    )
    resp.raise_for_status()
    return bool(resp.json())


# ==================== АВТОМАТИЧНЕ ЗАВЕРШЕННЯ ПОДІЙ ====================
def _event_local_due_at(event):
    """Час, коли подія переходить в історію, у часовому поясі клубу."""
    raw_date = str(event.get("event_date") or "").strip()
    if not raw_date:
        return None
    try:
        day = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

    raw_time = str(event.get("event_time") or "").strip()
    if raw_time:
        try:
            parts = raw_time.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            second = int(float(parts[2])) if len(parts) > 2 and parts[2] else 0
        except (ValueError, IndexError):
            hour, minute, second = 23, 59, 59
    else:
        # Якщо час не вказаний, подія активна до кінця вказаного дня.
        hour, minute, second = 23, 59, 59

    try:
        tz = ZoneInfo(EVENT_TIMEZONE)
    except Exception:
        logging.exception("Невідомий EVENT_TIMEZONE=%s, використовую UTC", EVENT_TIMEZONE)
        tz = timezone.utc
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=tz)


def _refresh_existing_profile_after_event(rsvp):
    """Оновлює XP/досягнення вже створеного профілю після завершення події."""
    username = (rsvp.get("username") or "").lstrip("@").lower()
    telegram_user_id = rsvp.get("telegram_user_id")
    profile = None
    try:
        params = {"select": "*", "limit": 1}
        if telegram_user_id:
            params["telegram_user_id"] = f"eq.{int(telegram_user_id)}"
        elif username:
            params["username"] = f"eq.{username}"
        else:
            return
        resp = requests.get(PROFILES_REST, headers=HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return
        profile = rows[0]
    except Exception:
        logging.exception("Не вдалося знайти профіль учасника завершеної події")
        return

    pseudo_user = {
        "username": profile.get("username") or username,
        "first_name": profile.get("display_name") or rsvp.get("display_name") or username,
        "last_name": "",
    }
    stats = _merge_profile_stats(pseudo_user, profile.get('telegram_user_id'))
    achievements = _profile_achievements(stats)
    current_ids = {a["id"] for a in achievements if a.get("unlocked")}
    old_ids = {str(x) for x in (profile.get("earned_achievements") or []) if x}
    initialized = bool(profile.get("achievements_initialized"))
    catalog_version = int(profile.get("achievement_catalog_version") or 0)
    newly_earned = current_ids - old_ids if initialized and catalog_version >= 4 else set()
    earned_ids = old_ids | current_ids

    xp_breakdown = _profile_xp_breakdown(stats, earned_ids, achievements)
    xp = xp_breakdown["total_xp"]
    level = max(1, xp // 250 + 1)

    try:
        patch = requests.patch(
            PROFILES_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            params={"telegram_user_id": f"eq.{profile.get('telegram_user_id')}"},
            json={
                "xp": xp,
                "earned_achievements": sorted(earned_ids),
                "achievements_initialized": True,
                "achievement_catalog_version": 4,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=20,
        )
        patch.raise_for_status()
    except Exception:
        logging.exception("Не вдалося оновити XP профілю після завершення події")
        return

    if initialized and newly_earned:
        by_id = {a["id"]: a for a in achievements}
        display_name = profile.get("display_name") or rsvp.get("display_name") or username or "Гравець"
        for achievement_id in sorted(newly_earned):
            achievement = by_id.get(achievement_id)
            if achievement:
                pass  # Вимкнено: досягнення, XP і нагороди не публікуються у Telegram-групі.


def _complete_event(event):
    """Атомарно завершує одну подію. Повертає True тільки для процесу, який зробив перехід."""
    event_id = event.get("id")
    if not event_id:
        return False
    now_utc = datetime.now(timezone.utc).isoformat()
    resp = requests.patch(
        EVENTS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        params={"id": f"eq.{event_id}", "status": "eq.scheduled"},
        json={"status": "completed", "completed_at": now_utc},
        timeout=30,
    )
    resp.raise_for_status()
    changed = resp.json() if resp.content else []
    if not changed:
        return False

    try:
        rr = requests.get(
            RSVPS_REST,
            headers=HEADERS,
            params={"event_id": f"eq.{event_id}", "status": "eq.going", "select": "*"},
            timeout=30,
        )
        rr.raise_for_status()
        for rsvp in rr.json():
            _refresh_existing_profile_after_event(rsvp)
    except Exception:
        logging.exception("Не вдалося нарахувати XP учасникам завершеної події %s", event_id)

    log_activity(
        "event_completed",
        f"Подію завершено: {event.get('title') or 'Ігрова подія'}",
        "Учасникам зараховано відвідування та XP.",
        actor_name="Система",
        image_url=event.get("cover_url") or None,
        metadata={"event_id": event_id},
    )
    logging.info("Подію %s автоматично завершено", event_id)
    return True


def auto_complete_due_events():
    """Завершує всі заплановані події, час яких уже настав."""
    try:
        try:
            tz = ZoneInfo(EVENT_TIMEZONE)
        except Exception:
            tz = timezone.utc
        now_local = datetime.now(tz)
        resp = requests.get(
            EVENTS_REST,
            headers=HEADERS,
            params={
                "status": "eq.scheduled",
                "event_date": f"lte.{now_local.date().isoformat()}",
                "select": "*",
                "order": "event_date.asc,event_time.asc",
            },
            timeout=30,
        )
        resp.raise_for_status()
        for event in resp.json():
            due_at = _event_local_due_at(event)
            if due_at and now_local >= due_at:
                try:
                    _complete_event(event)
                except Exception:
                    logging.exception("Не вдалося завершити подію %s", event.get("id"))
    except Exception:
        logging.exception("Помилка автоматичної перевірки завершення подій")


def _event_reminder_due_at(event):
    """Час групового нагадування за добу до події у часовому поясі клубу."""
    raw_date = str(event.get("event_date") or "").strip()
    if not raw_date:
        return None
    try:
        day = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

    try:
        tz = ZoneInfo(EVENT_TIMEZONE)
    except Exception:
        logging.exception("Невідомий EVENT_TIMEZONE=%s, використовую UTC", EVENT_TIMEZONE)
        tz = timezone.utc

    raw_time = str(event.get("event_time") or "").strip()
    if raw_time:
        try:
            parts = raw_time.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            second = int(float(parts[2])) if len(parts) > 2 and parts[2] else 0
        except (ValueError, IndexError):
            hour, minute, second = 12, 0, 0
        event_at = datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=tz)
        return event_at - timedelta(days=1)

    # Якщо час події не вказаний, нагадуємо опівдні попереднього дня.
    previous_day = day - timedelta(days=1)
    return datetime(previous_day.year, previous_day.month, previous_day.day, 12, 0, 0, tzinfo=tz)


def format_group_event_reminder(event):
    """Текст нагадування за день до події."""
    base = format_group_event(event)
    return "⏰ Нагадування: подія вже завтра!\n\n" + base


def notify_event_reminder_groups_sync(event):
    """Надсилає нагадування про подію в усі активні Telegram-групи. Повертає True, якщо надіслано хоча б в одну."""
    event_id = event.get("id")
    if not event_id:
        return False

    try:
        fresh_event = get_event_snapshot(event_id) or event
        text = format_group_event_reminder(fresh_event)
        keyboard = telegram_keyboard_payload(event_id, _event_google_maps_url(fresh_event))
        group_ids = get_active_group_ids_sync()
        if not group_ids:
            logging.warning("Немає активних Telegram-груп для нагадування про подію %s", event_id)
            return False

        photo_url = (fresh_event.get("cover_url") or "").strip()
        sent_any = False
        for chat_id in group_ids:
            ok = _send_group_event_text_sync(
                chat_id, text, keyboard,
                photo_url=photo_url,
                summary="⏰ Нагадування: подія вже завтра!\n\n" + _format_group_event_header(fresh_event),
                event_id=event_id, reminder=True,
            )
            if ok:
                sent_any = True
                logging.info("Нагадування про подію %s надіслано в групу %s", event_id, chat_id)
            else:
                logging.error(
                    "Не вдалось надіслати нагадування про подію %s в групу %s",
                    event_id, chat_id
                )
        return sent_any
    except Exception:
        logging.exception("Не вдалось надіслати групове нагадування про подію %s", event_id)
        return False


def _claim_event_day_reminder(event_id):
    """Атомарно позначає нагадування як взяте в роботу, щоб не було дублювань між воркерами."""
    now_utc = datetime.now(timezone.utc).isoformat()
    resp = requests.patch(
        EVENTS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        params={
            "id": f"eq.{event_id}",
            "status": "eq.scheduled",
            "reminder_day_sent_at": "is.null",
        },
        json={"reminder_day_sent_at": now_utc},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json() if resp.content else []
    return rows[0] if rows else None


def _release_event_day_reminder_claim(event_id):
    """Повертає подію в чергу нагадувань, якщо Telegram тимчасово не зміг нічого надіслати."""
    try:
        resp = requests.patch(
            EVENTS_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            params={"id": f"eq.{event_id}", "status": "eq.scheduled"},
            json={"reminder_day_sent_at": None},
            timeout=20,
        )
        resp.raise_for_status()
    except Exception:
        logging.exception("Не вдалося повернути нагадування події %s у чергу", event_id)


def auto_send_event_day_reminders():
    """Надсилає рівно одне групове нагадування приблизно за 24 години до кожної майбутньої події."""
    try:
        try:
            tz = ZoneInfo(EVENT_TIMEZONE)
        except Exception:
            logging.exception("Невідомий EVENT_TIMEZONE=%s, використовую UTC", EVENT_TIMEZONE)
            tz = timezone.utc

        now_local = datetime.now(tz)
        # Достатньо переглянути сьогоднішні та завтрашні заплановані події.
        max_date = (now_local.date() + timedelta(days=1)).isoformat()
        resp = requests.get(
            EVENTS_REST,
            headers=HEADERS,
            params={
                "status": "eq.scheduled",
                "event_date": f"lte.{max_date}",
                "reminder_day_sent_at": "is.null",
                "select": "*",
                "order": "event_date.asc,event_time.asc",
            },
            timeout=30,
        )
        resp.raise_for_status()

        for event in resp.json():
            event_due = _event_local_due_at(event)
            reminder_due = _event_reminder_due_at(event)
            if not event_due or not reminder_due:
                continue
            # Якщо воркер/Render був недоступний у точний момент, нагадування все одно піде після відновлення,
            # доки сама подія ще не почалася/не завершилась.
            if reminder_due <= now_local < event_due:
                event_id = event.get("id")
                if not event_id:
                    continue
                try:
                    claimed = _claim_event_day_reminder(event_id)
                    if not claimed:
                        continue
                    if not notify_event_reminder_groups_sync(claimed):
                        _release_event_day_reminder_claim(event_id)
                except Exception:
                    logging.exception("Не вдалося обробити нагадування події %s", event_id)
                    _release_event_day_reminder_claim(event_id)
    except Exception:
        logging.exception("Помилка автоматичної перевірки нагадувань про події")


def event_completion_worker():
    # Невелика затримка після запуску, щоб застосунок встиг ініціалізуватися.
    time.sleep(5)
    step=0
    while True:
        try:
            auto_send_event_day_reminders()
            auto_complete_due_events()
            # Webhook залишається основним механізмом; рідкісна перевірка
            # допомагає відновити пропущені відповіді банку.
            if step % 5 == 0:
                _reconcile_recent_event_payments()
                _sync_linked_quests_event_counts()
        except Exception:
            logging.exception('Помилка робітника подій; наступна спроба за хвилину')
        step+=1
        time.sleep(60)


# ==================== ОПЛАТА ПОДІЙ / MONOBANK ACQUIRING ====================
def _event_price_kop(data):
    """Ціна у гривнях, зберігаємо тільки ціле число копійок."""
    mode = str(data.get("price_mode") or "").strip().lower()
    if mode == "free":
        return 0
    raw = str(data.get("price_uah") or "0").strip().replace(",", ".")
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError):
        raise ValueError("invalid_event_price")
    if not value.is_finite() or value < 0 or value > Decimal('1000000'):
        raise ValueError("invalid_event_price")
    kop = value * 100
    if kop != kop.to_integral_value():
        raise ValueError("invalid_event_price")
    if mode == "paid" and kop < 100:
        raise ValueError("minimum_price_1_uah")
    return int(kop)


def _mono_headers():
    return {"X-Token": MONOBANK_ACQUIRING_TOKEN, "Content-Type": "application/json"}


def _mono_invoice_status(invoice_id):
    """Лише відповідь API банку може підтвердити оплату; фронтенду не довіряємо."""
    if not MONOBANK_ACQUIRING_TOKEN:
        raise RuntimeError('monobank_not_configured')
    r = requests.get(MONOBANK_BASE_URL + "/status", headers=_mono_headers(),
                     params={"invoiceId": str(invoice_id)}, timeout=18)
    r.raise_for_status()
    return r.json()


def _payment_update(row_id, values):
    r=requests.patch(MONOBANK_EVENT_PAYMENTS_REST,
        headers={**HEADERS,"Content-Type":"application/json","Prefer":"return=representation"},
        params={"id":f"eq.{int(row_id)}","select":"id,status,invoice_id,page_url"}, json=values, timeout=20)
    r.raise_for_status()
    return r.json()


def _payment_identity(user):
    uid=int(user['id'])
    username=(user.get('username') or '').strip().lstrip('@').lower() or f'tg_{uid}'
    name=' '.join(v for v in [user.get('first_name'), user.get('last_name')] if v).strip()
    return uid,username,name or username


def _sync_paid_event_member_profile(user_id, username, display_name):
    """Створює/оновлює профіль за перевіреним Telegram ID, не змінюючи RSVP."""
    try:
        result = requests.post(
            PROFILES_REST,
            headers={**HEADERS, 'Content-Type': 'application/json',
                     'Prefer': 'resolution=merge-duplicates,return=minimal'},
            params={'on_conflict': 'telegram_user_id'},
            json={'telegram_user_id': int(user_id), 'username': str(username),
                  'display_name': str(display_name),
                  'updated_at': datetime.now(timezone.utc).isoformat()},
            timeout=12,
        )
        if not result.ok:
            logging.warning('Профіль учасника перед оплатою не оновлено (%s)', result.status_code)
    except requests.RequestException:
        logging.exception('Помилка синхронізації Telegram-профілю перед оплатою')


def _lookup_payment(event_id, user_id):
    r=requests.get(MONOBANK_EVENT_PAYMENTS_REST,headers=HEADERS,params={
        'event_id':f'eq.{int(event_id)}','telegram_user_id':f'eq.{int(user_id)}',
        'select':'*','order':'created_at.desc','limit':8},timeout=20)
    r.raise_for_status()
    return r.json()


def _sync_monobank_payment(row):
    """Викликається після webhook або перевірки самим користувачем.

    Ідемпотентне завершення — транзакційна SQL-функція, ні вебхук, ні клієнт
    не можуть безпосередньо створити paid/going.
    """
    if row.get('status') == 'paid':
        return {'status':'paid','rsvp_status':row.get('rsvp_status') or 'going'}
    if not row.get('invoice_id'):
        return {'status':'pending'}
    state = _mono_invoice_status(row['invoice_id'])
    if str(state.get('invoiceId') or '') != str(row['invoice_id']):
        raise ValueError('invoice_id_mismatch')
    if str(state.get('reference') or '').replace('-','').lower() != str(row['reference']).replace('-','').lower():
        raise ValueError('invoice_reference_mismatch')
    if str(state.get('status')) == 'success':
        # Не підтверджуємо випадковий invoice зі зміненою сумою.
        if int(state.get('amount') or 0)!=int(row['amount_kop']) or int(state.get('ccy') or 0)!=980:
            raise ValueError('invoice_amount_mismatch')
        r=requests.post(MONOBANK_CONFIRM_RPC,headers={**HEADERS,'Content-Type':'application/json'},json={
            'p_invoice_id':row['invoice_id'], 'p_reference':row['reference'],
            'p_amount_kop':int(row['amount_kop']),'p_ccy':980},timeout=20)
        r.raise_for_status()
        result=r.json() or {}
        if result.get('status')!='paid':
            raise ValueError('payment_confirmation_'+str(result.get('error') or 'failed'))
        # Оновлюємо лічильник на вже опублікованому пості після підтвердженої оплати.
        refresh_event_group_posts_async(row.get('event_id'))
        return result
    status=str(state.get('status') or 'created')
    if status in {'expired','failure','reversed'}:
        _payment_update(row['id'],{'status':'expired' if status=='expired' else 'failed',
                                   'modified_at':datetime.now(timezone.utc).isoformat()})
        return {'status':'expired' if status=='expired' else 'failed'}
    return {'status':'pending','payment_status':status}


def _reconcile_recent_event_payments():
    if not MONOBANK_ACQUIRING_TOKEN:
        return
    try:
        cutoff=(datetime.now(timezone.utc)-timedelta(minutes=35)).isoformat()
        r=requests.get(MONOBANK_EVENT_PAYMENTS_REST,headers=HEADERS,
            params={'status':'eq.pending','created_at':f'gte.{cutoff}',
                    'select':'*','order':'created_at.asc','limit':20},timeout=20)
        r.raise_for_status()
        for row in r.json():
            if row.get('invoice_id'):
                try:
                    _sync_monobank_payment(row)
                except Exception:
                    logging.exception('Не вдалось звірити рахунок події %s',row.get('event_id'))
    except Exception:
        logging.exception('Звірка платежів monobank недоступна')


def _lookup_invoice_by_id(invoice_id):
    r=requests.get(MONOBANK_EVENT_PAYMENTS_REST,headers=HEADERS,
        params={'invoice_id':f'eq.{invoice_id}','select':'*','limit':1},timeout=20)
    r.raise_for_status()
    return (r.json() or [None])[0]


@app.route('/api/events/<int:event_id>/payment/start',methods=['POST'])
def start_event_payment(event_id):
    user=_request_telegram_user()
    if not user: return jsonify({'error':'telegram_auth_required'}),401
    if not MONOBANK_ACQUIRING_TOKEN: return jsonify({'error':'monobank_not_configured'}),503
    uid,username,display_name=_payment_identity(user)
    _sync_paid_event_member_profile(uid,username,display_name)
    try:
        event=get_event_snapshot(event_id)
        if not event: return jsonify({'error':'event_not_found'}),404
        if str(event.get('status') or 'scheduled')!='scheduled':
            return jsonify({'error':'event_closed'}),409
        price=int(event.get('price_kop') or 0)
        if price<=0: return jsonify({'error':'free_event'}),409
        existing=get_event_rsvp_response(event_id,username,uid)
        if existing and existing.get('status') in ('going','waitlist'):
            return jsonify({'status':'already_registered','rsvp_status':existing.get('status')}),200
        previous=_lookup_payment(event_id,uid)
        for row in previous:
            if row.get('status')=='paid':
                return jsonify({'status':'paid','rsvp_status':row.get('rsvp_status')}),200
            if row.get('status') in ('creating','pending'):
                if row.get('invoice_id'):
                    checked=_sync_monobank_payment(row)
                    if checked['status']=='paid': return jsonify(checked),200
                    if checked['status']=='pending' and row.get('page_url'):
                        return jsonify({'status':'pending','page_url':row['page_url']}),200
                else:
                    # Забули/не встигли створити invoice: не створюємо дубль під час
                    # паралельного запиту, але відпускаємо завислий через 2 хвилини.
                    created=str(row.get('created_at') or '')
                    try:
                        dt=datetime.fromisoformat(created.replace('Z','+00:00'))
                        if (datetime.now(timezone.utc)-dt).total_seconds()<120:
                            return jsonify({'status':'creating'}),202
                    except Exception: pass
                    _payment_update(row['id'],{'status':'failed'})
        reference=uuid.uuid4().hex
        reserved=requests.post(MONOBANK_RESERVE_RPC,
            headers={**HEADERS,'Content-Type':'application/json'},json={
                'p_event_id':event_id,'p_user_id':uid,
                'p_username':username,'p_display_name':display_name,
                'p_amount_kop':price,'p_reference':reference},timeout=20)
        reserved.raise_for_status()
        booking=reserved.json() or {}
        if booking.get('status')!='reserved':
            status=booking.get('status') or 'reservation_failed'
            return jsonify({'error':status}),409
        record={'id':booking['id']}
        try:
            # Не довіряємо Host заголовку від клієнта; адреса береться з
            # перевірених змінних середовища або фіксованого Render домену.
            public_url = (os.environ.get('MONOBANK_PUBLIC_URL','').strip() or
                          os.environ.get('WEBAPP_URL','').strip() or
                          os.environ.get('RENDER_EXTERNAL_URL','').strip() or
                          'https://board-games-bot-hkfp.onrender.com')
            parsed=urlparse(public_url)
            if parsed.scheme!='https' or not parsed.hostname:
                raise RuntimeError('https_origin_required')
            origin=f'https://{parsed.netloc}'
            payload={
                'amount':price,'ccy':980,
                'merchantPaymInfo':{'reference':reference,'destination':f'Styloteka · {str(event.get("title") or "Подія")[:100]}',
                                    'comment':f'Участь у події №{event_id}'},
                'redirectUrl':_event_mini_app_link(event_id) or origin,
                'webHookUrl':origin+'/api/payments/monobank/webhook',
                'validity':1800,'paymentType':'debit'
            }
            resp=requests.post(MONOBANK_BASE_URL+'/create',headers=_mono_headers(),json=payload,timeout=22)
            resp.raise_for_status()
            inv=resp.json()
            invoice=str(inv.get('invoiceId') or '')
            page=str(inv.get('pageUrl') or '')
            payment_host=(urlparse(page).hostname or '').lower()
            if not invoice or not page.startswith('https://') or not (
                payment_host=='pay.mbnk.biz' or payment_host=='monobank.ua'
                or payment_host.endswith('.monobank.ua')
            ):
                raise ValueError('monobank_invalid_invoice_response')
            _payment_update(record['id'],{'invoice_id':invoice,'page_url':page,'status':'pending',
                                          'modified_at':datetime.now(timezone.utc).isoformat()})
            return jsonify({'status':'pending','page_url':page,'invoice_id':invoice}),200
        except Exception:
            logging.exception('Не вдалося сформувати рахунок Monobank для події %s',event_id)
            try:_payment_update(record['id'],{'status':'failed'})
            except Exception:logging.exception('Не вдалося скасувати створення рахунку')
            return jsonify({'error':'monobank_invoice_unavailable'}),502
    except requests.RequestException:
        logging.exception('Помилка платіжної операції події %s',event_id)
        return jsonify({'error':'payment_service_unavailable'}),503
    except Exception:
        logging.exception('Неочікувана помилка створення платежу події %s',event_id)
        return jsonify({'error':'payment_service_unavailable'}),503


@app.route('/api/events/<int:event_id>/payment/status',methods=['GET'])
def event_payment_status(event_id):
    user=_request_telegram_user()
    if not user:return jsonify({'error':'telegram_auth_required'}),401
    uid,username,_=_payment_identity(user)
    try:
        payments=_lookup_payment(event_id,uid)
        if payments:
            row=payments[0]
            result=_sync_monobank_payment(row)
            return jsonify(result)
        event=get_event_snapshot(event_id)
        if not event:return jsonify({'error':'event_not_found'}),404
        if int(event.get('price_kop') or 0)==0:return jsonify({'status':'free'})
        return jsonify({'status':'not_started'})
    except (ValueError,requests.RequestException):
        logging.exception('Помилка перевірки оплати події %s',event_id)
        return jsonify({'error':'payment_status_unavailable'}),503


@app.route('/api/payments/monobank/webhook',methods=['POST'])
def event_monobank_webhook():
    payload=request.get_json(silent=True) or {}
    invoice=str(payload.get('invoiceId') or '')
    if not invoice or len(invoice)>140:
        return jsonify({'error':'invalid_invoice'}),400
    try:
        row=_lookup_invoice_by_id(invoice)
        if not row:
            return jsonify({'status':'ignored'}),200
        if str(payload.get('status'))=='success':
            # Ніякого trust-on-first-use: перепитуємо банківський API незалежно від тіла вебхука.
            result=_sync_monobank_payment(row)
            return jsonify(result)
        return jsonify({'status':'received'}),200
    except Exception:
        logging.exception('Помилка вебхука монобанку')
        return jsonify({'error':'temporary_unavailable'}),503


# ==================== QUESTS INC LINKED EVENT ====================
def _validated_quests_event_url(raw):
    """Only accept an actual HTTPS Quests page, not an arbitrary redirect."""
    url = str(raw or "").strip()
    if not url:
        return ""
    if len(url) > 1200:
        raise ValueError("quests_url_too_long")
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        raise ValueError("invalid_quests_url")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port or host not in {
        "quests.inc", "www.quests.inc", "share.quests.inc", "questsapp.com", "www.questsapp.com"
    }:
        raise ValueError("invalid_quests_url")
    return url


def _quests_count_from_input(raw):
    try:
        value = int(str(raw or "0").strip())
    except (ValueError, TypeError):
        raise ValueError("invalid_quests_count")
    if value < 0 or value > 10000:
        raise ValueError("invalid_quests_count")
    return value


# Quests Inc exposes a read-only public event summary to its own share page.
# It contains the total number of attendees, but at most 15 public profiles.
# Never fetch an arbitrary URL supplied by an event editor (SSRF protection).
_QUESTS_CACHE = {}
_QUESTS_CACHE_LOCK = Lock()
_QUESTS_CACHE_SECONDS = 75


def _quests_public_id(event_url):
    try:
        parsed = urlparse(_validated_quests_event_url(event_url))
        if parsed.hostname not in ("share.quests.inc", "quests.inc", "www.quests.inc"):
            return None
        if parsed.path.rstrip("/") != "/event.html":
            return None
        values = parse_qs(parsed.query).get("id", [])
        if len(values) != 1:
            return None
        return str(uuid.UUID(values[0]))
    except (ValueError, AttributeError):
        return None


def _quests_public_snapshot(event_url, force=False):
    quest_id = _quests_public_id(event_url)
    if not quest_id:
        return {"status": "unsupported", "quest_id": None, "count": None, "participants": []}
    now = time.monotonic()
    with _QUESTS_CACHE_LOCK:
        old = _QUESTS_CACHE.get(quest_id)
        if old and not force and now - old[0] < _QUESTS_CACHE_SECONDS:
            return old[1]
    try:
        response = requests.get(
            f"https://share.quests.inc/api/quests/{quest_id}/public",
            timeout=8, allow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "Styloteka-Quests-Sync/1.0"},
        )
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("unexpected Quests redirect")
        payload = response.json()
        records = payload.get("data") or []
        if payload.get("is_success") is not True or not isinstance(records, list) or not records:
            raise ValueError("invalid Quests response")
        record = records[0]
        count = int(record.get("participators_count"))
        if not 0 <= count <= 10000:
            raise ValueError("invalid Quests participant count")
        people = []
        seen = set()
        for item in (record.get("participators_15") or [])[:15]:
            if not isinstance(item, dict):
                continue
            profile_url = str(item.get("share_url") or "").strip()
            username = str(item.get("username") or "").strip()
            identity = profile_url or username
            if not identity:
                continue
            key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            full_name = " ".join(str(item.get(k) or "").strip()
                                 for k in ("firstname", "last_name")).strip()
            people.append({"key": key, "name": full_name[:140] or username[:140] or "Гість Quests"})
        snapshot = {"status": "synced", "quest_id": quest_id,
                    "count": count, "participants": people,
                    "complete": len(people) >= count,
                    "updated_at": datetime.now(timezone.utc).isoformat()}
        with _QUESTS_CACHE_LOCK:
            _QUESTS_CACHE[quest_id] = (now, snapshot)
        return snapshot
    except (requests.RequestException, ValueError, TypeError, KeyError):
        logging.warning("Quests Inc temporarily unavailable for quest %s", quest_id, exc_info=True)
        if old:
            return {**old[1], "status": "stale"}
        return {"status": "unavailable", "quest_id": quest_id,
                "count": None, "participants": []}


def _quests_checked_keys(event_id, quest_id):
    response = requests.get(QUESTS_CHECKINS_REST, headers=HEADERS, params={
        "event_id": f"eq.{int(event_id)}", "quest_id": f"eq.{quest_id}",
        "select": "participant_key,checked_in_at", "limit": "10000"}, timeout=12)
    response.raise_for_status()
    return {row["participant_key"] for row in response.json() if row.get("checked_in_at")}


def _quests_set_count(event_id, old_count, new_count):
    if old_count == new_count:
        return
    try:
        response = requests.patch(EVENTS_REST, headers={**HEADERS, "Content-Type": "application/json"},
            params={"id": f"eq.{int(event_id)}"}, json={
                "quests_count": int(new_count),
                "quests_count_updated_at": datetime.now(timezone.utc).isoformat()}, timeout=12)
        response.raise_for_status()
        refresh_event_group_posts_async(event_id)
    except requests.RequestException:
        logging.exception("Cannot persist synced Quests count for event %s", event_id)


def _sync_linked_quests_event_counts():
    """Periodically refresh linked events even when no visitor opens the Mini App."""
    try:
        response = requests.get(EVENTS_REST, headers=HEADERS, params={
            "status": "eq.scheduled", "quests_event_url": "not.is.null",
            "select": "id,quests_event_url,quests_count", "limit": "100"}, timeout=15)
        response.raise_for_status()
        for event in response.json():
            if not event.get('quests_event_url'):
                continue
            sync = _quests_public_snapshot(event['quests_event_url'])
            if sync['status'] == 'synced':
                _quests_set_count(event['id'], int(event.get('quests_count') or 0), sync['count'])
    except requests.RequestException:
        logging.exception('Cannot update scheduled Quests event counts')


def _event_source_counts(event):
    going = sum(1 for row in (event.get("rsvps") or []) if (row.get("status") or "going") == "going")
    quests = max(0, int(event.get("quests_count") or 0)) if event.get("quests_event_url") else 0
    return going, quests


def _remember_event_group_post(event_id, chat_id, message_id, is_photo, reminder=False):
    try:
        response = requests.post(
            EVENT_GROUP_POSTS_REST,
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "chat_id,message_id"},
            json={"event_id": int(event_id), "chat_id": int(chat_id), "message_id": int(message_id),
                  "is_photo": bool(is_photo), "is_reminder": bool(reminder)},
            timeout=12,
        )
        response.raise_for_status()
    except Exception:
        logging.exception("Не вдалося зберегти id групового поста для події %s", event_id)


def refresh_event_group_posts_sync(event_id):
    """Edit existing linked group posts, no new post and no notification flood."""
    try:
        event = get_event_snapshot(event_id)
        if not event:
            return
        r = requests.get(EVENT_GROUP_POSTS_REST, headers=HEADERS,
            params={"event_id": f"eq.{int(event_id)}", "select": "chat_id,message_id,is_photo,is_reminder", "limit": "100"},
            timeout=18)
        r.raise_for_status()
        for post in r.json():
            reminder = bool(post.get("is_reminder"))
            full = format_group_event_reminder(event) if reminder else format_group_event(event)
            heading = ("⏰ Нагадування: подія вже завтра!\n\n" if reminder else "") + _format_group_event_header(event)
            lead, _followups = _prepare_group_event_post(full, heading, photo=bool(post.get("is_photo")))
            photo = bool(post.get("is_photo"))
            payload = {"chat_id": post["chat_id"], "message_id": post["message_id"],
                       "reply_markup": telegram_keyboard_payload(event_id, _event_google_maps_url(event))}
            payload["caption" if photo else "text"] = lead
            ok, response = telegram_api_post("editMessageCaption" if photo else "editMessageText", payload)
            if not ok and "message is not modified" not in str(response).lower():
                logging.warning("Не вдалося оновити груповий пост події %s: %s", event_id, response)
    except Exception:
        logging.exception("Помилка синхронізації групових постів події %s", event_id)


def refresh_event_group_posts_async(event_id):
    if not event_id:
        return
    Thread(target=refresh_event_group_posts_sync, args=(int(event_id),), daemon=True).start()


# ==================== ДОПОМІЖНА ЛОГІКА ПОДІЙ ====================
def get_event_snapshot(event_id):
    """Повертає подію разом зі списком RSVP або None."""
    eresp = requests.get(
        EVENTS_REST,
        headers=HEADERS,
        params={"id": f"eq.{event_id}", "select": "*", "limit": 1},
        timeout=20,
    )
    eresp.raise_for_status()
    rows = eresp.json()
    if not rows:
        return None
    event = rows[0]
    rr = requests.get(
        RSVPS_REST,
        headers=HEADERS,
        params={"event_id": f"eq.{event_id}", "select": "*", "order": "id.asc"},
        timeout=20,
    )
    rr.raise_for_status()
    event["rsvps"] = rr.json()
    try:
        resp = requests.get(EVENT_CHECKINS_REST,headers=HEADERS,params={
            'event_id':f'eq.{event_id}','checked_in_at':'not.is.null',
            'select':'event_id','limit':'10000'},timeout=12)
        resp.raise_for_status()
        event['checked_in_count'] = len(resp.json())
    except requests.RequestException:
        logging.exception('Не вдалося отримати QR-відмітки події %s',event_id)
        event['checked_in_count'] = 0
    return event



def get_event_rsvp_response(event_id, username, telegram_user_id=None):
    """Повертає вже збережену відповідь користувача на подію або None."""
    username = (username or "").lstrip("@").lower()

    # Надійна прив'язка — Telegram user_id.
    if telegram_user_id:
        try:
            resp = requests.get(
                RSVPS_REST,
                headers=HEADERS,
                params={
                    "event_id": f"eq.{int(event_id)}",
                    "telegram_user_id": f"eq.{int(telegram_user_id)}",
                    "select": "id,status,username,display_name,telegram_user_id",
                    "limit": 1,
                },
                timeout=20,
            )
            if resp.ok and resp.json():
                return resp.json()[0]
        except Exception:
            logging.exception("Не вдалося перевірити RSVP за Telegram user_id")

    # Сумісність зі старими RSVP, де telegram_user_id ще не був записаний.
    if username:
        try:
            resp = requests.get(
                RSVPS_REST,
                headers=HEADERS,
                params={
                    "event_id": f"eq.{int(event_id)}",
                    "username": f"eq.{username}",
                    "select": "id,status,username,display_name,telegram_user_id",
                    "limit": 1,
                },
                timeout=20,
            )
            if resp.ok and resp.json():
                return resp.json()[0]
        except Exception:
            logging.exception("Не вдалося перевірити RSVP за username")

    return None


def add_event_decline(event_id, username, display_name, telegram_user_id=None):
    """Зберігає відповідь «Не йду» замість видалення RSVP."""
    username = (username or "").lstrip("@").lower()
    if not username:
        raise ValueError("username is required")

    eresp = requests.get(
        EVENTS_REST,
        headers=HEADERS,
        params={"id": f"eq.{event_id}", "select": "id,status", "limit": 1},
        timeout=20,
    )
    eresp.raise_for_status()
    rows = eresp.json()
    if not rows:
        raise ValueError("event_not_found")
    if (rows[0].get("status") or "scheduled") != "scheduled":
        raise ValueError("event_closed")

    payload = {
        "event_id": int(event_id),
        "username": username,
        "display_name": display_name or username,
        "status": "declined",
        "telegram_user_id": int(telegram_user_id) if telegram_user_id else None,
    }

    resp = requests.post(
        RSVPS_REST,
        headers={
            **HEADERS,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        params={"on_conflict": "event_id,username"},
        json=payload,
        timeout=30,
    )

    # Сумісність, якщо поле telegram_user_id ще не додано в старій базі.
    if resp.status_code >= 400 and "telegram_user_id" in resp.text:
        payload.pop("telegram_user_id", None)
        resp = requests.post(
            RSVPS_REST,
            headers={
                **HEADERS,
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            params={"on_conflict": "event_id,username"},
            json=payload,
            timeout=30,
        )

    resp.raise_for_status()
    return "declined"


def rsvp_locked_message(status):
    status = (status or "going").strip().lower()
    if status == "declined":
        return "Ви вже проголосували: не йдете ❌"
    if status == "waitlist":
        return "Ви вже проголосували: ви в черзі ⏳"
    return "Ви вже проголосували: ви йдете ✅"

def add_event_rsvp(event_id, username, display_name, telegram_user_id=None):
    """Записує користувача на подію. Повертає going або waitlist."""
    username = (username or "").lstrip("@").lower()
    if not username:
        raise ValueError("username is required")

    status = "going"
    try:
        eresp = requests.get(
            EVENTS_REST,
            headers=HEADERS,
            params={"id": f"eq.{event_id}", "select": "id,max_participants,status", "limit": 1},
            timeout=20,
        )
        eresp.raise_for_status()
        rows = eresp.json()
        if not rows:
            raise ValueError("event_not_found")
        if (rows[0].get("status") or "scheduled") != "scheduled":
            raise ValueError("event_closed")
        cap = int(rows[0].get("max_participants") or 0)
        if cap > 0:
            cresp = requests.get(
                RSVPS_REST,
                headers=HEADERS,
                params={"event_id": f"eq.{event_id}", "status": "eq.going", "select": "id"},
                timeout=20,
            )
            cresp.raise_for_status()
            # Якщо користувач уже "going", не відправляємо його в чергу через власне місце.
            mine = requests.get(
                RSVPS_REST,
                headers=HEADERS,
                params={"event_id": f"eq.{event_id}", "username": f"eq.{username}", "select": "status", "limit": 1},
                timeout=20,
            )
            mine_rows = mine.json() if mine.ok else []
            already_going = bool(mine_rows and (mine_rows[0].get("status") or "going") == "going")
            if len(cresp.json()) >= cap and not already_going:
                status = "waitlist"
    except ValueError:
        raise
    except Exception:
        # Сумісність зі старою схемою, якщо max_participants ще не додано.
        status = "going"

    resp = requests.post(
        RSVPS_REST,
        headers={
            **HEADERS,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        params={"on_conflict": "event_id,username"},
        json={
            "event_id": event_id,
            "username": username,
            "display_name": display_name or username,
            "status": status,
            "telegram_user_id": int(telegram_user_id) if telegram_user_id else None,
        },
        timeout=30,
    )
    # Сумісність, якщо міграцію telegram_user_id ще не виконано.
    if resp.status_code >= 400 and "telegram_user_id" in resp.text:
        resp = requests.post(
            RSVPS_REST,
            headers={
                **HEADERS,
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            params={"on_conflict": "event_id,username"},
            json={
                "event_id": event_id,
                "username": username,
                "display_name": display_name or username,
                "status": status,
            },
            timeout=30,
        )
    resp.raise_for_status()
    return status


def remove_event_rsvp(event_id, username):
    """Скасовує RSVP лише для активної події й піднімає першого з waitlist."""
    try:
        eresp = requests.get(
            EVENTS_REST,
            headers=HEADERS,
            params={"id": f"eq.{event_id}", "select": "status", "limit": 1},
            timeout=20,
        )
        eresp.raise_for_status()
        erows = eresp.json()
        if not erows:
            raise ValueError("event_not_found")
        if (erows[0].get("status") or "scheduled") != "scheduled":
            raise ValueError("event_closed")
    except ValueError:
        raise

    uname = (username or "").lstrip("@").lower()
    if not uname:
        return False
    old_status = None
    try:
        before = requests.get(
            RSVPS_REST,
            headers=HEADERS,
            params={"event_id": f"eq.{event_id}", "username": f"eq.{uname}", "select": "status", "limit": 1},
            timeout=20,
        )
        if before.ok and before.json():
            old_status = before.json()[0].get("status")
    except Exception:
        pass

    resp = requests.delete(
        RSVPS_REST,
        headers=HEADERS,
        params={"event_id": f"eq.{event_id}", "username": f"eq.{uname}"},
        timeout=30,
    )
    resp.raise_for_status()

    if old_status in (None, "going"):
        try:
            wait = requests.get(
                RSVPS_REST,
                headers=HEADERS,
                params={"event_id": f"eq.{event_id}", "status": "eq.waitlist", "select": "id", "order": "id.asc", "limit": 1},
                timeout=20,
            )
            wait.raise_for_status()
            rows = wait.json()
            if rows:
                promote = requests.patch(
                    RSVPS_REST,
                    headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
                    params={"id": f"eq.{rows[0]['id']}"},
                    json={"status": "going"},
                    timeout=20,
                )
                promote.raise_for_status()
        except Exception:
            logging.exception("Не вдалось автоматично підняти учасника з черги")
    return old_status is not None


# ==================== ПОДІЇ ====================
@app.route("/api/events", methods=["GET"])
def get_events():
    # Працює і як страховка, якщо Render спав у момент запланованого часу.
    auto_complete_due_events()

    scope = (request.args.get("scope") or "upcoming").strip().lower()
    if scope == "history":
        params = {
            "select": "*",
            "status": "eq.completed",
            "order": "completed_at.desc,event_date.desc,event_time.desc",
        }
    else:
        params = {
            "select": "*",
            "status": "eq.scheduled",
            "order": "event_date.asc,event_time.asc",
        }

    resp = requests.get(EVENTS_REST, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    events = resp.json()

    ids = [str(e["id"]) for e in events]
    rsvps_by_event = {}
    if ids:
        rr = requests.get(
            RSVPS_REST,
            headers=HEADERS,
            params={"select": "*", "event_id": f"in.({','.join(ids)})"},
            timeout=30,
        )
        rr.raise_for_status()
        for row in rr.json():
            rsvps_by_event.setdefault(row["event_id"], []).append(row)

    picks_by_event = {}
    if ids:
        try:
            picks_resp = requests.get(
                EVENT_GAME_PICKS_REST,
                headers=HEADERS,
                params={"select": "id,event_id,game_id,game_name,telegram_user_id,display_name,created_at", "event_id": f"in.({','.join(ids)})", "order": "created_at.asc"},
                timeout=20,
            )
            picks_resp.raise_for_status()
            for pick in picks_resp.json():
                picks_by_event.setdefault(int(pick["event_id"]), []).append(pick)
        except Exception:
            logging.exception("Не вдалось завантажити пропозиції ігор для подій")
    checked_by_event = {}
    if ids:
        try:
            resp_checkins = requests.get(EVENT_CHECKINS_REST, headers=HEADERS, params={
                'select': 'event_id', 'event_id': f"in.({','.join(ids)})",
                'checked_in_at': 'not.is.null', 'limit': '10000',
            }, timeout=20)
            resp_checkins.raise_for_status()
            for item in resp_checkins.json():
                key = int(item['event_id'])
                checked_by_event[key] = checked_by_event.get(key,0)+1
        except requests.RequestException:
            logging.exception('Не вдалося отримати статистику QR-відміток')
    for e in events:
        e['checked_in_count'] = checked_by_event.get(int(e['id']),0)
        if e.get('quests_event_url'):
            sync = _quests_public_snapshot(e['quests_event_url'])
            e['quests_sync_status'] = sync['status']
            e['quests_profiles_visible'] = len(sync['participants'])
            e['quests_profiles_complete'] = bool(sync.get('complete'))
            if sync.get('count') is not None:
                old_count = max(0, int(e.get('quests_count') or 0))
                e['quests_count'] = sync['count']
                if sync['status'] == 'synced':
                    _quests_set_count(e['id'], old_count, sync['count'])
                try:
                    if sync.get('quest_id'):
                        e['quests_checked_in_count'] = len(_quests_checked_keys(e['id'], sync['quest_id']))
                except requests.RequestException:
                    logging.exception('Cannot load Quests check-ins for event %s', e['id'])
        e['quests_checked_in_count'] = (min(max(0,int(e.get('quests_checked_in_count') or 0)),
            max(0,int(e.get('quests_count') or 0))) if e.get('quests_event_url') else 0)
        e["rsvps"] = rsvps_by_event.get(e["id"], [])
        stylo_count, quests_count = _event_source_counts(e)
        e["styloteka_count"] = stylo_count
        e["quests_display_count"] = quests_count
        e["registration_count"] = stylo_count + quests_count
        e["member_game_picks"] = picks_by_event.get(int(e["id"]), [])

    return jsonify(events)


@app.route('/api/events/<int:event_id>/quests/participants', methods=['GET'])
def event_quests_participants(event_id):
    """Organizer-only names and personal attendance, never a public list endpoint."""
    if not _request_is_group_admin():
        return jsonify({'error': 'group_admin_required'}), 403
    try:
        event = get_event_snapshot(event_id)
        if not event or not event.get('quests_event_url'):
            return jsonify({'error': 'quests_event_not_linked'}), 404
        sync = _quests_public_snapshot(event['quests_event_url'])
        if sync.get('count') is None:
            return jsonify({'error': 'quests_sync_unavailable', 'status': sync['status']}), 503
        checked = _quests_checked_keys(event_id, sync['quest_id'])
        participants = [{**p, 'checked_in': p['key'] in checked}
                        for p in sync['participants']]
        return jsonify({'status': sync['status'], 'count': sync['count'],
                        'complete': bool(sync.get('complete')), 'visible': len(participants),
                        'checked_in_count': len(checked), 'participants': participants,
                        'updated_at': sync.get('updated_at')})
    except requests.RequestException:
        logging.exception('Quests participants unavailable for event %s', event_id)
        return jsonify({'error': 'quests_sync_unavailable'}), 503


@app.route('/api/events/<int:event_id>/quests/checkin', methods=['POST'])
def event_quests_checkin(event_id):
    if not _request_is_group_admin():
        return jsonify({'error': 'group_admin_required'}), 403
    user = _request_telegram_user()
    if not user:
        return jsonify({'error': 'telegram_auth_required'}), 401
    data = request.get_json(silent=True) or {}
    key = str(data.get('participant_key') or '')
    if not re.fullmatch(r'[a-f0-9]{64}', key) or type(data.get('checked_in')) is not bool:
        return jsonify({'error': 'invalid_checkin'}), 400
    try:
        event = get_event_snapshot(event_id)
        if not event or not event.get('quests_event_url'):
            return jsonify({'error': 'quests_event_not_linked'}), 404
        if event.get('status') != 'scheduled':
            return jsonify({'error': 'event_closed'}), 409
        sync = _quests_public_snapshot(event['quests_event_url'])
        if sync['status'] not in ('synced', 'stale'):
            return jsonify({'error': 'quests_sync_unavailable'}), 503
        if not any(person['key'] == key for person in sync['participants']):
            return jsonify({'error': 'participant_not_found'}), 404
        match = {'event_id': f'eq.{event_id}', 'quest_id': f'eq.{sync["quest_id"]}',
                 'participant_key': f'eq.{key}'}
        if data['checked_in']:
            response = requests.post(QUESTS_CHECKINS_REST,
                headers={**HEADERS, 'Content-Type': 'application/json',
                         'Prefer': 'resolution=merge-duplicates,return=minimal'},
                params={'on_conflict': 'event_id,quest_id,participant_key'},
                json={'event_id': event_id, 'quest_id': sync['quest_id'],
                      'participant_key': key, 'checked_in_at': datetime.now(timezone.utc).isoformat(),
                      'checked_in_by': int(user['id'])}, timeout=15)
        else:
            response = requests.delete(QUESTS_CHECKINS_REST, headers=HEADERS,
                                       params=match, timeout=15)
        response.raise_for_status()
        count = len(_quests_checked_keys(event_id, sync['quest_id']))
        update = requests.patch(EVENTS_REST, headers={**HEADERS, 'Content-Type': 'application/json'},
            params={'id': f'eq.{event_id}'}, json={'quests_checked_in_count': count}, timeout=12)
        update.raise_for_status()
        refresh_event_group_posts_async(event_id)
        return jsonify({'checked_in': data['checked_in'], 'checked_in_count': count})
    except requests.RequestException:
        logging.exception('Quests check-in unavailable for event %s', event_id)
        return jsonify({'error': 'quests_checkin_unavailable'}), 503




# ==================== STYLOTEKA v5.1: PERSONAL QR TICKETS ====================
def _event_ticket_code(event_id, ticket_id):
    """Opaque random UUID, no names, phone numbers, or Telegram ID in QR."""
    return f"STY51:{int(event_id)}:{ticket_id}"


def _event_ticket_svg(ticket_value):
    """Build a QR as SVG using vendored, BSD-licensed qrcode Python library."""
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(ticket_value)
    qr.make(fit=True)
    cells = qr.get_matrix()
    n = len(cells)
    paths = ''.join(f'<path d="M{x} {y}h1v1h-1z"/>'
                    for y,row in enumerate(cells) for x, filled in enumerate(row) if filled)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" '
            f'width="300" height="300" role="img" aria-label="QR квиток">'
            f'<rect width="100%" height="100%" fill="white"/>'
            f'<g fill="#0f1525">{paths}</g></svg>')


@app.route('/api/events/<int:event_id>/checkin/ticket',methods=['POST'])
def member_event_ticket(event_id):
    user = _request_telegram_user()
    if not user: return jsonify({'error':'telegram_auth_required'}),401
    uid = int(user['id'])
    try:
        event = _event_for_member_games(event_id)
        if not event: return jsonify({'error':'event_not_found'}),404
        if event.get('status') != 'scheduled': return jsonify({'error':'event_closed'}),409
        username = str(user.get('username') or '').lstrip('@').lower()
        rsvp = get_event_rsvp_response(event_id,username,uid)
        # Neither unpaid purchases nor the waiting list grant tickets.
        if not rsvp or rsvp.get('status')!='going' or int(rsvp.get('telegram_user_id') or 0)!=uid:
            return jsonify({'error':'going_rsvp_required'}),403
        lookup = {'event_id':f'eq.{event_id}','telegram_user_id':f'eq.{uid}',
                  'select':'ticket_id,checked_in_at','limit':'1'}
        r=requests.get(EVENT_CHECKINS_REST,headers=HEADERS,params=lookup,timeout=20)
        r.raise_for_status()
        rows=r.json()
        if not rows:
            r=requests.post(EVENT_CHECKINS_REST,headers={**HEADERS,
                'Content-Type':'application/json','Prefer':'resolution=ignore-duplicates,return=representation'},
                params={'on_conflict':'event_id,telegram_user_id'},
                json={'event_id':event_id,'telegram_user_id':uid},timeout=20)
            r.raise_for_status()
            rows=r.json()
            if not rows:
                r=requests.get(EVENT_CHECKINS_REST,headers=HEADERS,params=lookup,timeout=20)
                r.raise_for_status()
                rows=r.json()
        if not rows: return jsonify({'error':'ticket_unavailable'}),503
        row=rows[0]
        code=_event_ticket_code(event_id,row['ticket_id'])
        return jsonify({'ticket':code,'qr_svg':_event_ticket_svg(code),
            'checked_in':bool(row.get('checked_in_at'))})
    except requests.RequestException:
        logging.exception('Не вдалося видати квиток події %s',event_id)
        return jsonify({'error':'ticket_unavailable'}),503


@app.route('/api/events/<int:event_id>/checkin/scan',methods=['POST'])
def event_checkin_scan(event_id):
    denied=_admin_required_response()
    if denied: return denied
    user=_request_telegram_user()
    body=request.get_json(silent=True) or {}
    code=str(body.get('ticket') or '').strip()
    found=re.fullmatch(r'STY51:(\d{1,18}):([0-9a-fA-F-]{36})',code)
    if not found or int(found.group(1))!=event_id: return jsonify({'error':'invalid_ticket'}),400
    try: ticket_id=str(uuid.UUID(found.group(2)))
    except ValueError: return jsonify({'error':'invalid_ticket'}),400
    try:
        r=requests.post(EVENT_CHECKIN_RPC,headers={**HEADERS,'Content-Type':'application/json'},
            json={'p_event_id':event_id,'p_ticket_id':ticket_id,'p_admin_id':int(user['id'])},timeout=20)
        r.raise_for_status()
        out=r.json() or {}
        status=out.get('status')
        if status=='checked_in': refresh_event_group_posts_async(event_id)
        return jsonify(out),(200 if status in {'checked_in','already_checked_in'} else 409)
    except requests.RequestException:
        logging.exception('Не вдалося відмітити відвідування події %s',event_id)
        return jsonify({'error':'checkin_unavailable'}),503


# ==================== ІГРИ В ПОДІЯХ: ПРОПОЗИЦІЇ УЧАСНИКІВ ====================
def _event_for_member_games(event_id):
    resp = requests.get(
        EVENTS_REST,
        headers=HEADERS,
        params={"id": f"eq.{int(event_id)}", "select": "id,title,status", "limit": 1},
        timeout=20,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def _get_event_game_picks(event_id):
    response = requests.get(
        EVENT_GAME_PICKS_REST,
        headers=HEADERS,
        params={
            "event_id": f"eq.{int(event_id)}",
            "select": "id,event_id,game_id,game_name,telegram_user_id,display_name,created_at",
            "order": "created_at.asc",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def _event_games_membership(event_id, user):
    """Права звичайних учасників базуються на RSVP, а не на статусі адміна."""
    if not user:
        return False
    user_id = int(user["id"])
    username = (user.get("username") or "").strip().lstrip("@").lower()
    vote = get_event_rsvp_response(event_id, username, user_id)
    if not vote:
        return False
    return (vote.get("status") or "going") in {"going", "waitlist"}


@app.route("/api/events/<int:event_id>/member-games", methods=["GET"])
def member_event_games_get(event_id):
    try:
        event = _event_for_member_games(event_id)
        if not event:
            return jsonify({"error": "event_not_found"}), 404
        picks = _get_event_game_picks(event_id)
        user = _request_telegram_user()
        return jsonify({
            "event": event,
            "picks": picks,
            "can_pick": event.get("status") == "scheduled" and _event_games_membership(event_id, user),
            "my_game_ids": [int(p["game_id"]) for p in picks if user and int(p["telegram_user_id"]) == int(user["id"])],
        })
    except requests.RequestException:
        logging.exception("Помилка отримання ігор учасників події %s", event_id)
        return jsonify({"error": "event_games_unavailable"}), 503


@app.route("/api/events/<int:event_id>/member-games", methods=["POST"])
def member_event_games_add(event_id):
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401
    data = request.get_json(silent=True) or {}
    try:
        game_id = int(data.get("game_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_game"}), 400
    if game_id <= 0:
        return jsonify({"error": "invalid_game"}), 400
    try:
        event = _event_for_member_games(event_id)
        if not event:
            return jsonify({"error": "event_not_found"}), 404
        if event.get("status") != "scheduled":
            return jsonify({"error": "event_closed"}), 409
        if not _event_games_membership(event_id, user):
            return jsonify({"error": "rsvp_required"}), 403
        user_id = int(user["id"])
        # Жодного ліміту на кількість ігор від одного учасника. Унікальність
        # (event_id,game_id,telegram_user_id) забезпечує сама база даних.
        game_response = requests.get(
            REST,
            headers=HEADERS,
            params={"id": f"eq.{game_id}", "select": "id,name", "limit": 1},
            timeout=20,
        )
        game_response.raise_for_status()
        games = game_response.json()
        if not games:
            return jsonify({"error": "game_not_found"}), 404
        name = " ".join(str(v) for v in (user.get("first_name"), user.get("last_name")) if v).strip()
        name = name or str(user.get("username") or "Гравець")
        created = requests.post(
            EVENT_GAME_PICKS_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "event_id,game_id,telegram_user_id"},
            json={"event_id": event_id, "game_id": game_id, "game_name": games[0]["name"], "telegram_user_id": user_id, "display_name": name[:100]},
            timeout=20,
        )
        created.raise_for_status()
        return jsonify({"status": "selected", "picks": _get_event_game_picks(event_id)})
    except requests.RequestException:
        logging.exception("Помилка додавання гри учасником до події %s", event_id)
        return jsonify({"error": "event_games_unavailable"}), 503


@app.route("/api/events/<int:event_id>/member-games/<int:game_id>", methods=["DELETE"])
def member_event_games_remove(event_id, game_id):
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401
    try:
        event = _event_for_member_games(event_id)
        if not event:
            return jsonify({"error": "event_not_found"}), 404
        if event.get("status") != "scheduled":
            return jsonify({"error": "event_closed"}), 409
        # Видалити можна тільки власну пропозицію. RSVP уже може бути скасований адміном.
        response = requests.delete(
            EVENT_GAME_PICKS_REST,
            headers=HEADERS,
            params={"event_id": f"eq.{event_id}", "game_id": f"eq.{game_id}", "telegram_user_id": f"eq.{int(user['id'])}"},
            timeout=20,
        )
        response.raise_for_status()
        return jsonify({"status": "removed", "picks": _get_event_game_picks(event_id)})
    except requests.RequestException:
        logging.exception("Помилка видалення власної гри в події %s", event_id)
        return jsonify({"error": "event_games_unavailable"}), 503


@app.route("/api/events", methods=["POST"])
def add_event():
    denied = _admin_required_response()
    if denied:
        return denied

    content_type = (request.content_type or "").lower()
    if "multipart/form-data" in content_type:
        data = request.form.to_dict(flat=True)
        cover_file = request.files.get("cover")
    else:
        data = request.get_json(silent=True, force=True) or {}
        cover_file = None

    try:
        raw_games = data.get("game_names_json")
        game_names = json.loads(raw_games) if isinstance(raw_games, str) and raw_games.strip() else data.get("game_names", [])
    except Exception:
        game_names = []

    if not isinstance(game_names, list):
        game_names = []
    game_names = [str(name).strip() for name in game_names if str(name).strip()]
    game_names = list(dict.fromkeys(game_names))

    legacy_game_name = (data.get("game_name") or "").strip()
    if not game_names and legacy_game_name:
        game_names = [legacy_game_name]
    primary_game = game_names[0] if game_names else legacy_game_name

    uploaded_cover_url = (
        upload_file(cover_file, "event-covers")
        if cover_file and getattr(cover_file, "filename", "")
        else None
    )

    location = _normalize_event_location(data.get("location_text"))

    try:
        price_kop = _event_price_kop(data)
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400

    try:
        quests_url = _validated_quests_event_url(data.get("quests_event_url"))
        quests_count = _quests_count_from_input(data.get("quests_count")) if quests_url else 0
        quests_checked = _quests_count_from_input(data.get('quests_checked_in_count')) if quests_url else 0
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if quests_url:
        sync = _quests_public_snapshot(quests_url)
        if sync['status'] == 'synced':
            quests_count, quests_checked = sync['count'], 0
    if quests_checked > quests_count:
        return jsonify({'error':'quests_checkins_exceed_registration'}),400

    payload = {
        "quests_event_url": quests_url,
        "quests_count": quests_count,
        "quests_checked_in_count": quests_checked,
        "quests_count_updated_at": datetime.now(timezone.utc).isoformat() if quests_url else None,
        "price_kop":price_kop,
        "title": data.get("title", ""),
        "event_date": data.get("event_date"),
        "event_time": data.get("event_time", ""),
        "location_text": location["location_text"],
        "location_map_url": location["location_map_url"],
        "location_lat": location["location_lat"],
        "location_lng": location["location_lng"],
        "game_name": primary_game,
        "game_names": game_names,
        "description": data.get("description", ""),
        "created_by": data.get("created_by", "невідомо"),
        "max_participants": max(0, int(data.get("max_participants") or 0)),
        "cover_url": uploaded_cover_url,
        "status": "scheduled",
        "completed_at": None,
    }

    resp = requests.post(
        EVENTS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        json=payload,
        timeout=30,
    )

    if resp.status_code >= 400 and any(field in resp.text for field in ("max_participants", "cover_url", "game_names", "location_text", "location_map_url", "location_lat", "location_lng")):
        fallback = dict(payload)
        if "max_participants" in resp.text:
            fallback.pop("max_participants", None)
        if "cover_url" in resp.text:
            fallback.pop("cover_url", None)
        if "game_names" in resp.text:
            fallback.pop("game_names", None)
        if "location_text" in resp.text:
            fallback.pop("location_text", None)
        if "location_map_url" in resp.text:
            fallback.pop("location_map_url", None)
        if "location_lat" in resp.text:
            fallback.pop("location_lat", None)
        if "location_lng" in resp.text:
            fallback.pop("location_lng", None)
        resp = requests.post(
            EVENTS_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
            json=fallback,
            timeout=30,
        )

    resp.raise_for_status()
    created_rows = resp.json() if resp.content else []
    event_id = created_rows[0].get("id") if created_rows else None

    event_photo = uploaded_cover_url
    if not event_photo and primary_game:
        try:
            gresp = requests.get(
                REST,
                headers=HEADERS,
                params={"name": f"eq.{primary_game}", "select": "cover_url", "limit": 1},
                timeout=15,
            )
            if gresp.ok and gresp.json():
                event_photo = gresp.json()[0].get("cover_url")
        except Exception:
            logging.exception("Не вдалось знайти обкладинку гри для сповіщення")

    notify_lines = [f"📅 Нова подія: {data.get('title', '')}"]
    date_time = data.get("event_date", "")
    if data.get("event_time"):
        date_time += f" о {data.get('event_time')}"
    if date_time:
        notify_lines.append(date_time)
    notify_lines.append(f"💳 Вартість: {price_kop/100:.2f} грн" if price_kop else "🆓 Безкоштовно")
    if game_names:
        notify_lines.append(f"🎲 Ігри: {', '.join(game_names)}")
    location_text = location.get("location_text") or ""
    if location_text:
        notify_lines.append(f"📍 {location_text}")

    max_participants = max(0, int(data.get("max_participants") or 0))
    if max_participants:
        notify_lines.append(f"👥 Місць: {max_participants}")
    if data.get("description"):
        notify_lines.append(data.get("description"))

    notify_subscribers_async("\n".join(notify_lines), photo_url=event_photo)

    if event_id:
        notify_groups_async(event_id, photo_url=event_photo)

    game_label = ", ".join(game_names) if game_names else ""
    log_activity(
        "event_created",
        f"Створено нову подію: {data.get('title', '')}",
        (
            f"Ігри: {game_label}" + (f" · 📍 {location.get('location_text')}" if location.get("location_text") else "")
            if game_label
            else (location.get("location_text") or data.get("description", ""))
        ),
        actor_name=data.get("created_by", ""),
        image_url=event_photo,
        metadata={"event_id": event_id, "games": game_names},
    )

    return jsonify({"status": "ok", "event_id": event_id})


@app.route("/api/events/<int:event_id>", methods=["PATCH"])
def edit_event(event_id):
    denied = _admin_required_response()
    if denied:
        return denied

    current_resp = requests.get(
        EVENTS_REST,
        headers=HEADERS,
        params={"id": f"eq.{event_id}", "select": "*", "limit": 1},
        timeout=30,
    )
    current_resp.raise_for_status()
    current_rows = current_resp.json()
    if not current_rows:
        return jsonify({"error": "event_not_found"}), 404

    current_event = current_rows[0]
    if str(current_event.get("status") or "scheduled") != "scheduled":
        return jsonify({"error": "completed_event_cannot_be_edited"}), 409

    content_type = (request.content_type or "").lower()
    if "multipart/form-data" in content_type:
        data = request.form.to_dict(flat=True)
        cover_file = request.files.get("cover")
    else:
        data = request.get_json(silent=True, force=True) or {}
        cover_file = None

    title = str(data.get("title") or "").strip()
    event_date = str(data.get("event_date") or "").strip()
    if not title or not event_date:
        return jsonify({"error": "title_and_date_required"}), 400

    try:
        raw_games = data.get("game_names_json")
        game_names = json.loads(raw_games) if isinstance(raw_games, str) and raw_games.strip() else data.get("game_names", [])
    except Exception:
        game_names = []

    if not isinstance(game_names, list):
        game_names = []
    game_names = [str(name).strip() for name in game_names if str(name).strip()]
    game_names = list(dict.fromkeys(game_names))

    legacy_game_name = str(data.get("game_name") or "").strip()
    if not game_names and legacy_game_name:
        game_names = [legacy_game_name]
    primary_game = game_names[0] if game_names else legacy_game_name

    try:
        max_participants = max(0, int(data.get("max_participants") or 0))
    except Exception:
        max_participants = 0

    location = _normalize_event_location(data.get("location_text"))
    old_cover_url = current_event.get("cover_url")
    remove_cover = str(data.get("remove_cover") or "").strip().lower() in {"1", "true", "yes", "on"}
    new_cover_url = None

    if cover_file and getattr(cover_file, "filename", ""):
        new_cover_url = upload_file(cover_file, "event-covers")
        cover_url = new_cover_url
    elif remove_cover:
        cover_url = None
    else:
        cover_url = old_cover_url

    new_event_time = str(data.get("event_time") or "").strip()
    if "price_mode" not in data and "price_uah" not in data:
        # Старі клієнти та закешовані форми не повинні скидати платну подію в безкоштовну.
        price_kop = int(current_event.get("price_kop") or 0)
    else:
        try:
            price_kop = _event_price_kop(data)
        except ValueError as exc:
            return jsonify({"error":str(exc)}),400
    old_capacity=int(current_event.get('max_participants') or 0)
    if price_kop>0 and max_participants>0 and (old_capacity<=0 or max_participants<old_capacity):
        try:
            reserved_checks=requests.get(MONOBANK_EVENT_PAYMENTS_REST,headers=HEADERS,params={
                "event_id":f"eq.{event_id}","status":"in.(creating,pending,paid)",
                "select":"id","limit":1},timeout=20)
            reserved_checks.raise_for_status()
            if reserved_checks.json():
                return jsonify({"error":"event_capacity_locked_after_invoice"}),409
        except requests.RequestException:
            return jsonify({"error":"payment_check_unavailable"}),503
    if price_kop != int(current_event.get("price_kop") or 0):
        try:
            rsvp_checks=requests.get(RSVPS_REST,headers=HEADERS,params={
                "event_id":f"eq.{event_id}","status":"in.(going,waitlist)",
                "select":"id","limit":1},timeout=20)
            rsvp_checks.raise_for_status()
            if rsvp_checks.json():return jsonify({"error":"event_price_locked_after_registration"}),409
            paid_checks=requests.get(MONOBANK_EVENT_PAYMENTS_REST,headers=HEADERS,params={
                "event_id":f"eq.{event_id}","select":"id","limit":1},timeout=20)
            paid_checks.raise_for_status()
            if paid_checks.json():return jsonify({"error":"event_price_locked_after_invoice"}),409
        except requests.RequestException:
            return jsonify({"error":"payment_check_unavailable"}),503
    try:
        quests_url = _validated_quests_event_url(data.get("quests_event_url", current_event.get("quests_event_url")))
        quests_count = _quests_count_from_input(data.get("quests_count", current_event.get("quests_count"))) if quests_url else 0
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400

    try:
        quests_checked = _quests_count_from_input(data.get('quests_checked_in_count',current_event.get('quests_checked_in_count') or 0)) if quests_url else 0
    except ValueError:
        return jsonify({'error':'invalid_quests_checkins'}),400
    if quests_url:
        sync = _quests_public_snapshot(quests_url)
        if sync['status'] == 'synced':
            quests_count = sync['count']
            if sync.get('quest_id'):
                try:
                    quests_checked = len(_quests_checked_keys(event_id, sync['quest_id']))
                except requests.RequestException:
                    return jsonify({'error':'quests_checkins_unavailable'}),503
    if quests_checked > quests_count:
        return jsonify({'error':'quests_checkins_exceed_registration'}),400

    payload = {
        "quests_event_url": quests_url,
        "quests_count": quests_count,
        "quests_checked_in_count": quests_checked,
        "quests_count_updated_at": datetime.now(timezone.utc).isoformat() if (quests_url != (current_event.get("quests_event_url") or "") or quests_count != int(current_event.get("quests_count") or 0)) else current_event.get("quests_count_updated_at"),
        "price_kop":price_kop,
        "title": title,
        "event_date": event_date,
        "event_time": new_event_time,
        "location_text": location["location_text"],
        "location_map_url": location["location_map_url"],
        "location_lat": location["location_lat"],
        "location_lng": location["location_lng"],
        "game_name": primary_game,
        "game_names": game_names,
        "description": str(data.get("description") or "").strip(),
        "max_participants": max_participants,
        "cover_url": cover_url,
    }

    # Якщо адміністратор переносить дату або час уже анонсованої події,
    # дозволяємо системі надіслати нове нагадування за день до нового часу.
    old_event_date = str(current_event.get("event_date") or "").strip()[:10]
    old_event_time = str(current_event.get("event_time") or "").strip()[:5]
    normalized_new_time = new_event_time[:5]
    if old_event_date != event_date[:10] or old_event_time != normalized_new_time:
        payload["reminder_day_sent_at"] = None

    resp = requests.patch(
        EVENTS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        params={"id": f"eq.{event_id}"},
        json=payload,
        timeout=30,
    )

    if resp.status_code >= 400 and any(field in resp.text for field in (
        "max_participants", "cover_url", "game_names", "location_text",
        "location_map_url", "location_lat", "location_lng"
    )):
        fallback = dict(payload)
        for field in (
            "max_participants", "cover_url", "game_names", "location_text",
            "location_map_url", "location_lat", "location_lng"
        ):
            if field in resp.text:
                fallback.pop(field, None)
        resp = requests.patch(
            EVENTS_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
            params={"id": f"eq.{event_id}"},
            json=fallback,
            timeout=30,
        )

    if resp.status_code >= 400:
        if new_cover_url:
            try:
                delete_file(new_cover_url)
            except Exception:
                logging.exception("Не вдалось прибрати нову обкладинку після помилки редагування події")
        resp.raise_for_status()

    if (new_cover_url or remove_cover) and old_cover_url and old_cover_url != cover_url:
        try:
            delete_file(old_cover_url)
        except Exception:
            logging.exception("Не вдалось видалити попередню обкладинку події")

    updated_rows = resp.json() if resp.content else []
    updated_event = updated_rows[0] if updated_rows else {**current_event, **payload}

    log_activity(
        "event_updated",
        f"Оновлено подію: {title}",
        (
            f"Ігри: {', '.join(game_names)}" +
            (f" · 📍 {location.get('location_text')}" if location.get("location_text") else "")
            if game_names
            else (location.get("location_text") or payload.get("description", ""))
        ),
        actor_name=str(data.get("updated_by") or data.get("created_by") or ""),
        image_url=cover_url,
        metadata={"event_id": event_id, "games": game_names},
    )

    refresh_event_group_posts_async(event_id)
    return jsonify({"status": "updated", "event": updated_event})


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    denied = _admin_required_response()
    if denied:
        return denied
    # Оплачувану подію з рахунками не видаляємо — інакше банк може
    # підтвердити платіж після каскадного видалення замовлення.
    try:
        linked = requests.get(MONOBANK_EVENT_PAYMENTS_REST,headers=HEADERS,
            params={'event_id':f'eq.{event_id}','select':'id','limit':1},timeout=20)
        linked.raise_for_status()
        if linked.json():
            return jsonify({'error':'event_has_payment_history'}),409
    except requests.RequestException:
        return jsonify({'error':'payment_check_unavailable'}),503
    try:
        current = requests.get(
            EVENTS_REST,
            headers=HEADERS,
            params={"id": f"eq.{event_id}", "select": "cover_url"},
            timeout=20,
        )
        if current.ok and current.json():
            delete_file(current.json()[0].get("cover_url"))
    except Exception:
        logging.exception("Не вдалось видалити обкладинку події")

    resp = requests.delete(EVENTS_REST, headers=HEADERS, params={"id": f"eq.{event_id}"}, timeout=30)
    resp.raise_for_status()
    return jsonify({"status": "deleted"})


@app.route("/api/events/<int:event_id>/rsvp", methods=["POST"])
def rsvp_event(event_id):
    user = _request_telegram_user()
    if not user:
        return jsonify({"error": "telegram_auth_required"}), 401

    user_id = int(user.get("id"))
    try:
        event=get_event_snapshot(event_id)
        if not event:return jsonify({"error":"event_not_found"}),404
        if int(event.get("price_kop") or 0)>0:
            return jsonify({"status":"payment_required","error":"payment_required"}),402
    except requests.RequestException:
        return jsonify({"error":"event_lookup_failed"}),503
    username = (user.get("username") or "").lstrip("@").lower() or f"tg_{user_id}"
    display_name = " ".join(
        x for x in [user.get("first_name"), user.get("last_name")] if x
    ).strip() or user.get("username") or "Гравець"

    existing = get_event_rsvp_response(event_id, username, user_id)
    if existing:
        return jsonify({
            "status": "already_voted",
            "vote_status": existing.get("status") or "going",
        })

    try:
        status = add_event_rsvp(
            event_id,
            username,
            display_name,
            user_id,
        )
    except ValueError as exc:
        if str(exc) == "event_closed":
            return jsonify({"status": "completed"}), 409
        return jsonify({"status": "not_found"}), 404

    refresh_event_group_posts_async(event_id)
    return jsonify({"status": status})


@app.route("/api/events/<int:event_id>/rsvp/<username>", methods=["DELETE"])
def cancel_rsvp(event_id, username):
    # Звичайний користувач після голосування не може міняти відповідь.
    # Видалення RSVP лишається тільки інструментом адміністратора.
    denied = _admin_required_response()
    if denied:
        return denied

    try:
        remove_event_rsvp(event_id, username)
        refresh_event_group_posts_async(event_id)
    except ValueError as exc:
        if str(exc) == "event_closed":
            return jsonify({"status": "completed"}), 409
        return jsonify({"status": "not_found"}), 404
    return jsonify({"status": "deleted"})


# ==================== МАЙСТЕР-КЛАС: ЗАПИСИ ====================
@app.route("/api/masterclass/slots", methods=["GET"])
def masterclass_slots():
    booking_date = request.args.get("date", "").strip()
    if not booking_date:
        return jsonify({"unavailable": []})
    try:
        resp = requests.get(
            MASTERCLASS_REST,
            headers=HEADERS,
            params={
                "booking_date": f"eq.{booking_date}",
                "status": "in.(pending,confirmed)",
                "select": "booking_time",
            },
            timeout=20,
        )
        resp.raise_for_status()
        unavailable = sorted({str(r.get("booking_time") or "")[:5] for r in resp.json() if r.get("booking_time")})
        return jsonify({"unavailable": unavailable})
    except requests.HTTPError as e:
        return jsonify({"unavailable": [], "error": "masterclass_table_missing"}), 503


@app.route("/api/masterclass/bookings", methods=["GET"])
def get_masterclass_bookings():
    username = (request.args.get("username") or "").lstrip("@").lower()
    params = {"select": "*", "order": "booking_date.asc,booking_time.asc,id.desc"}
    try:
        if not _request_is_group_admin():
            if not username:
                return jsonify([])
            params["telegram_username"] = f"eq.{username}"
        resp = requests.get(MASTERCLASS_REST, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        return jsonify(resp.json())
    except requests.HTTPError:
        return jsonify({"error": "masterclass_table_missing"}), 503


@app.route("/api/masterclass/bookings", methods=["POST"])
def add_masterclass_booking():
    data = request.get_json(silent=True, force=True) or {}
    if not data.get("booking_date") or not data.get("booking_time"):
        return jsonify({"error": "date_and_time_required"}), 400
    booking_time = str(data.get("booking_time"))[:5]
    # Один активний запис на слот.
    existing = requests.get(
        MASTERCLASS_REST,
        headers=HEADERS,
        params={
            "booking_date": f"eq.{data.get('booking_date')}",
            "booking_time": f"eq.{booking_time}",
            "status": "in.(pending,confirmed)",
            "select": "id",
            "limit": 1,
        },
        timeout=20,
    )
    if existing.ok and existing.json():
        return jsonify({"error": "slot_taken"}), 409

    payload = {
        "telegram_username": (data.get("telegram_username") or "").lstrip("@").lower(),
        "display_name": data.get("display_name", ""),
        "figure": data.get("figure", ""),
        "style": data.get("style", ""),
        "level": data.get("level", ""),
        "booking_date": data.get("booking_date"),
        "booking_time": booking_time,
        "status": "pending",
    }
    resp = requests.post(
        MASTERCLASS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    row = rows[0] if rows else {}
    return jsonify({"status": "pending", "id": row.get("id")})


@app.route("/api/masterclass/bookings/<int:booking_id>/status", methods=["PATCH"])
def update_masterclass_booking_status(booking_id):
    denied = _admin_required_response()
    if denied:
        return denied
    data = request.get_json(silent=True, force=True) or {}
    status = data.get("status")
    if status not in {"pending", "confirmed", "completed", "cancelled"}:
        return jsonify({"error": "bad_status"}), 400
    resp = requests.patch(
        MASTERCLASS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        params={"id": f"eq.{booking_id}"},
        json={"status": status},
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify({"status": "ok"})


# ==================== TELEGRAM BOT ====================
SUBSCRIBERS_REST = f"{SUPABASE_URL}/rest/v1/subscribers"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

bot_loop = None  # заповнюється при старті фонового потоку бота


@dp.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Відкрити бібліотеку ігор", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )
    await message.answer(
        "Привіт! Тисни кнопку нижче, щоб відкрити бібліотеку настільних ігор.",
        reply_markup=kb,
    )

    try:
        requests.post(
            SUBSCRIBERS_REST,
            headers={
                **HEADERS,
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            params={"on_conflict": "chat_id"},
            json={
                "chat_id": message.chat.id,
                "username": message.from_user.username or "",
                "first_name": message.from_user.first_name or "",
            },
            timeout=15,
        )
    except Exception:
        logging.exception("Не вдалось зберегти підписника")



TELEGRAM_API_BASE = f"https://api.telegram.org/bot{API_TOKEN}"


def telegram_api_post(method, payload):
    """Надійний синхронний виклик Telegram Bot API, незалежний від aiogram polling loop."""
    if not API_TOKEN:
        logging.error("BOT_TOKEN порожній — Telegram-повідомлення неможливо надіслати")
        return False, "BOT_TOKEN is empty"

    try:
        resp = requests.post(
            f"{TELEGRAM_API_BASE}/{method}",
            json=payload,
            timeout=25,
        )
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        if not resp.ok or not data.get("ok", False):
            logging.error(
                "Telegram API %s error: status=%s response=%s",
                method, resp.status_code, resp.text[:1000]
            )
            return False, data.get("description") or resp.text

        return True, data.get("result")
    except Exception as exc:
        logging.exception("Telegram API %s request failed", method)
        return False, str(exc)


def _event_mini_app_link(event_id, payment=False):
    """Відкриває подію всередині Telegram, щоб initData містив Telegram ID.

    Для named Mini App задається TELEGRAM_APP_SHORT_NAME; інакше потрібна
    налаштована Main Mini App в BotFather.
    """
    bot_name = TELEGRAM_BOT_USERNAME
    if not bot_name:
        return ""
    base = f"https://t.me/{bot_name}"
    if TELEGRAM_APP_SHORT_NAME:
        base += f"/{TELEGRAM_APP_SHORT_NAME}"
    start=f"pay_{int(event_id)}" if payment else f"event_{int(event_id)}"
    return f"{base}?startapp={start}"


# Event game votes are shared with the Mini App's event_game_picks table.
# Every game in the Styloteka library is an option in a group announcement,
# regardless of the games an organizer mentioned in the event description.
# Cache the catalog to avoid querying it for every group post edit.
_EVENT_GAME_OPTIONS_CACHE = {}
_EVENT_GAME_OPTIONS_LOCK = Lock()


def _event_game_poll_options(event):
    key = "styloteka_library"
    now = time.monotonic()
    with _EVENT_GAME_OPTIONS_LOCK:
        saved = _EVENT_GAME_OPTIONS_CACHE.get(key)
        if saved and now - saved[0] < 300:
            return saved[1]
    try:
        response = requests.get(REST, headers=HEADERS,
            params={"select": "id,name", "limit": "10000", "order": "name.asc"}, timeout=20)
        response.raise_for_status()
        options = []
        seen_ids = set()
        for row in sorted(response.json(), key=lambda item: str(item.get("name") or "").casefold()):
            name = str(row.get("name") or "").strip()
            game_id = int(row.get("id") or 0)
            if name and game_id > 0 and game_id not in seen_ids:
                options.append((game_id, name))
                seen_ids.add(game_id)
        with _EVENT_GAME_OPTIONS_LOCK:
            _EVENT_GAME_OPTIONS_CACHE[key] = (now, options)
        return options
    except (requests.RequestException, ValueError, KeyError, TypeError):
        logging.exception("Cannot load Styloteka game library for event %s", event.get('id'))
        return saved[1] if saved else []


def _event_game_poll_rows(event):
    """Two library game choices per row, with votes shared with the Mini App."""
    options = _event_game_poll_options(event)
    if not options:
        return []
    try:
        response = requests.get(EVENT_GAME_PICKS_REST, headers=HEADERS,
            params={"event_id": f"eq.{int(event['id'])}",
                    "select": "game_id", "limit": "10000"}, timeout=15)
        response.raise_for_status()
        counts = Counter(int(p['game_id']) for p in response.json())
    except (requests.RequestException, ValueError, KeyError):
        logging.exception("Cannot load game vote counts for event %s", event.get('id'))
        counts = Counter()
    buttons = []
    for game_id, name in options:
        short = name[:19] + ('…' if len(name) > 19 else '')
        buttons.append({"text": f"🎲 {short} · {counts[game_id]}",
                        "callback_data": f"ev_game:{int(event['id'])}:{game_id}"})
    return [buttons[i:i+2] for i in range(0, len(buttons), 2)]


def telegram_keyboard_payload(event_id, map_url=""):
    event = get_event_snapshot(event_id)
    price = int((event or {}).get('price_kop') or 0)
    if price > 0:
        pay_link=_event_mini_app_link(event_id, payment=True)
        rows=[[{'text':f'💳 Я йду · {price/100:.2f} грн', 'url':pay_link}]] if pay_link else []
        rows.append([{'text':'❌ Не йду', 'callback_data':f'ev_no:{event_id}'}])
    else:
        rows = [[
            {"text": "✅ Я йду", "callback_data": f"ev_go:{event_id}"},
            {"text": "❌ Не йду", "callback_data": f"ev_no:{event_id}"},
        ]]
    if event and event.get('status') == 'scheduled':
        rows.extend(_event_game_poll_rows(event))
    map_url = str(map_url or "").strip()
    if map_url:
        rows.append([{
            "text": "📍 Маршрут",
            "url": map_url,
        }])
    quests_url = str((event or {}).get("quests_event_url") or "").strip()
    if quests_url:
        rows.append([{"text": "🔗 Учасники Quests Inc", "url": quests_url}])
    app_link = _event_mini_app_link(event_id)
    if app_link:
        rows.append([{"text": "📅 Відкрити подію в Styloteka", "url": app_link}])
    return {"inline_keyboard": rows}


def get_active_group_ids_sync():
    try:
        resp = requests.get(
            TELEGRAM_GROUPS_REST,
            headers=HEADERS,
            params={"select": "chat_id", "active": "eq.true"},
            timeout=20,
        )
        resp.raise_for_status()
        ids = [int(row["chat_id"]) for row in resp.json() if row.get("chat_id")]
        logging.info("Активні Telegram-групи для анонсів: %s", ids)
        return ids
    except Exception:
        logging.exception("Не вдалось отримати Telegram-групи. Чи виконано SQL для telegram_groups?")
        return []


def notify_subscribers_sync(text, photo_url=None):
    """Особиста розсилка напряму через Bot API — не залежить від polling."""
    try:
        resp = requests.get(
            SUBSCRIBERS_REST,
            headers=HEADERS,
            params={"select": "chat_id"},
            timeout=30,
        )
        resp.raise_for_status()
        subscribers = resp.json()
    except Exception:
        logging.exception("Не вдалось отримати список підписників")
        return

    for row in subscribers:
        chat_id = row.get("chat_id")
        if not chat_id:
            continue

        if photo_url:
            ok, err = telegram_api_post("sendPhoto", {
                "chat_id": chat_id,
                "photo": photo_url,
                "caption": text[:1024],
            })
        else:
            ok, err = telegram_api_post("sendMessage", {
                "chat_id": chat_id,
                "text": text[:4096],
            })

        if not ok:
            logging.warning("Не вдалось надіслати особисте сповіщення %s: %s", chat_id, err)


def game_group_keyboard_payload(game_id=None):
    """Посилання відкриває саме нову гру всередині Telegram Mini App."""
    rows = []
    try:
        game_id = int(game_id)
    except (TypeError, ValueError):
        game_id = None
    if game_id and game_id > 0 and TELEGRAM_BOT_USERNAME:
        bot_url = f"https://t.me/{TELEGRAM_BOT_USERNAME}"
        if TELEGRAM_APP_SHORT_NAME:
            bot_url += f"/{TELEGRAM_APP_SHORT_NAME}"
        rows.append([{"text": "🎲 Відкрити гру", "url": f"{bot_url}?startapp=game_{game_id}"}])
    elif WEBAPP_URL:
        rows.append([{"text": "🎲 Відкрити бібліотеку", "url": WEBAPP_URL}])
    return {"inline_keyboard": rows} if rows else None


def _game_group_announcement(name, description):
    """Тримай основний пост у межах Telegram photo caption — 1024 символи."""
    title = str(name or "Без назви").strip()
    desc = str(description or "").strip()
    main = f"🎲 Нова настільна гра в Styloteka!\n\n🆕 {title}"
    # Опис може бути необмеженим у базі, але анонс — коротким.
    # Не розділяємо сурогатні пари та зберігаємо охайний підпис.
    max_length = 1010
    if len(main) > max_length:
        main = main[:max_length - 1] + "…"
    available = max(0, max_length - len(main) - 2)
    if desc and available >= 16:
        summary = desc[:available]
        if len(desc) > available:
            cut = summary.rfind(" ")
            if cut > len(summary) * 0.6:
                summary = summary[:cut]
            summary = summary.rstrip(".,;:! ") + "…"
        main += "\n\n" + summary
    return main


def notify_game_groups_sync(name, description="", cover_url=None, game_id=None):
    """Публікує разовий анонс тільки після фактичного створення гри."""
    try:
        group_ids = get_active_group_ids_sync()
        if not group_ids:
            logging.info("Немає активних груп для анонсу нової гри")
            return
        announcement = _game_group_announcement(name, description)
        keyboard = game_group_keyboard_payload(game_id)
        for chat_id in set(group_ids):
            common = {"chat_id": chat_id, "disable_notification": False}
            if keyboard:
                common["reply_markup"] = keyboard
            if cover_url:
                ok, err = telegram_api_post("sendPhoto", {
                    **common, "photo": cover_url, "caption": announcement,
                })
                if not ok:
                    logging.warning("Фото нової гри для групи %s не надіслано (%s). Пробуємо текст.", chat_id, err)
                    ok, err = telegram_api_post("sendMessage", {
                        **common, "text": announcement,
                    })
            else:
                ok, err = telegram_api_post("sendMessage", {
                    **common, "text": announcement,
                })
            if ok:
                logging.info("Анонс нової гри %r (ID %s) надіслано в %s", name, game_id, chat_id)
            else:
                logging.error("Помилка анонсу нової гри %r для %s: %s", name, chat_id, err)
    except Exception:
        logging.exception("Не вдалося опублікувати анонс нової гри")


def notify_game_groups_async(name, description="", cover_url=None, game_id=None):
    """Не блокує запит додавання гри й не дублює анонси при редагуванні."""
    Thread(
        target=notify_game_groups_sync,
        kwargs={"name": name, "description": description,
                "cover_url": cover_url, "game_id": game_id},
        daemon=True,
    ).start()


def profile_group_keyboard_payload():
    rows = []
    if WEBAPP_URL:
        sep = "&" if "?" in WEBAPP_URL else "?"
        rows.append([{
            "text": "👤 Відкрити профіль",
            "url": f"{WEBAPP_URL}{sep}view=profile",
        }])
    return {"inline_keyboard": rows} if rows else None


def notify_achievement_groups_sync(display_name, achievement, level, xp):
    """Публікує нове досягнення учасника у всіх активних Telegram-групах."""
    try:
        group_ids = get_active_group_ids_sync()
        if not group_ids:
            logging.warning("Немає активних Telegram-груп для анонсу досягнення")
            return

        icon = achievement.get("icon") or "🏆"
        name = achievement.get("name") or "Досягнення"
        description = (achievement.get("description") or "").strip()
        lines = [
            "🏆 Нове досягнення!",
            "",
            f"👤 {display_name}",
            f"{icon} {name}",
        ]
        if description:
            lines.append(description)
        bonus_xp = int(achievement.get("bonus_xp") or 0)
        if bonus_xp:
            lines.append(f"🎁 Нагорода: +{bonus_xp} XP")
        lines.extend(["", f"⭐ Рівень {level} · {xp} XP"])
        text = "\n".join(lines)
        keyboard = profile_group_keyboard_payload()

        for chat_id in group_ids:
            payload = {"chat_id": chat_id, "text": text[:4096]}
            if keyboard:
                payload["reply_markup"] = keyboard
            ok, err = telegram_api_post("sendMessage", payload)
            if ok:
                logging.info(
                    "Досягнення '%s' користувача '%s' опубліковано в групі %s",
                    name, display_name, chat_id
                )
            else:
                logging.error(
                    "Не вдалось опублікувати досягнення '%s' в групі %s: %s",
                    name, chat_id, err
                )
    except Exception:
        logging.exception("Не вдалось опублікувати групове сповіщення про досягнення")


def notify_achievement_groups_async(display_name, achievement, level, xp):
    """Вимкнено: в Telegram-групу надсилаються лише події й нагадування."""
    return None


def _split_telegram_event_text(text, max_chars=3600):
    """Надсилає увесь опис серією постів, зважаючи на UTF-16 ліміт Telegram.

    Emoji можуть займати дві одиниці UTF-16, тому рахуємо їх окремо.
    """
    text = str(text or "")
    if not text:
        return [""]
    chunks = []
    while text:
        units = 0
        end = 0
        for character in text:
            weight = 2 if ord(character) > 0xFFFF else 1
            if units + weight > max_chars:
                break
            units += weight
            end += 1
        if end == len(text):
            chunks.append(text)
            break
        # Краще розбивати на межі абзацу/слова, але не губити роздільник.
        break_at = text.rfind("\n", 0, end)
        if break_at < end // 2:
            break_at = text.rfind(" ", 0, end)
        if break_at >= end // 2:
            end = break_at + 1
        chunks.append(text[:end])
        text = text[end:]
    return chunks


def _telegram_utf16_length(text):
    """Telegram counts UTF-16 code units for photo captions and message limits."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in str(text or ""))


def _prepare_group_event_post(text, summary, photo=False):
    """Keep an entire short event under its photo, and move only overflow below.

    Returns main caption/text and list of follow-up texts. Never truncates the
    event description. The first message always owns the registration buttons.
    """
    text = str(text or "")
    summary = str(summary or "")
    first_limit = 990 if photo else 3850  # Telegram limits: 1024 / 4096 UTF-16 units
    if _telegram_utf16_length(text) <= first_limit:
        return text, []

    hint = "\n\n📝 Повний опис події — у повідомленні нижче ⬇️"
    if text.startswith(summary) and _telegram_utf16_length(summary + hint) <= first_limit:
        lead = summary + hint
        remaining = text[len(summary):].lstrip("\n")
    else:
        # Even a very long title or game list must not cause any lost text.
        fallback_title = "⏰ Нагадування: подія вже завтра!" if text.startswith("⏰ Нагадування") else "📅 Подія Styloteka"
        lead = fallback_title + "\n📝 Усі деталі — у повідомленні нижче ⬇️"
        remaining = text
    return lead, _split_telegram_event_text(remaining, max_chars=3600)


def _send_group_event_text_sync(chat_id, text, keyboard, photo_url=None, summary="", event_id=None, reminder=False):
    """Photo + caption + buttons in ONE post; overflow follows without clipping."""
    photo_url = str(photo_url or "").strip()
    lead, followups = _prepare_group_event_post(text, summary, photo=bool(photo_url))
    first_payload = {"chat_id": chat_id, "reply_markup": keyboard}
    sent_as_photo = bool(photo_url)
    if photo_url:
        ok, result = telegram_api_post("sendPhoto", {
            **first_payload, "photo": photo_url, "caption": lead,
        })
        if not ok:
            logging.warning("Фото події в групу %s не надіслано: %s; повторюю як текст", chat_id, result)
            # If a picture is invalid, publish the complete content as normal text.
            sent_as_photo = False
            lead, followups = _prepare_group_event_post(text, summary, photo=False)
            ok, result = telegram_api_post("sendMessage", {**first_payload, "text": lead})
    else:
        ok, result = telegram_api_post("sendMessage", {**first_payload, "text": lead})

    if not ok:
        logging.error("Не вдалося опублікувати основний пост події в групу %s: %s", chat_id, result)
        return False

    first_message_id = result.get("message_id") if isinstance(result, dict) else None
    if event_id and first_message_id:
        _remember_event_group_post(event_id, chat_id, first_message_id, sent_as_photo, reminder)
    all_sent = True
    for index, part in enumerate(followups, start=1):
        payload = {"chat_id": chat_id, "text": part}
        if first_message_id:
            payload["reply_parameters"] = {
                "message_id": first_message_id,
                "allow_sending_without_reply": True,
            }
        sent, err = telegram_api_post("sendMessage", payload)
        if not sent and "reply_parameters" in payload:
            # Not all group settings allow a reply: send the continuation anyway.
            payload.pop("reply_parameters", None)
            sent, err = telegram_api_post("sendMessage", payload)
        if not sent:
            all_sent = False
            logging.error("Не надіслано продовження %s/%s події в групу %s: %s",
                          index, len(followups), chat_id, err)
    return all_sent


def notify_groups_sync(event_id, photo_url=None):
    """Публікує подію в усіх активних групах напряму через Telegram Bot API."""
    try:
        event = get_event_snapshot(event_id)
        if not event:
            logging.error("Не знайдено подію id=%s для групового анонсу", event_id)
            return

        text = format_group_event(event)
        keyboard = telegram_keyboard_payload(event_id, _event_google_maps_url(event))
        group_ids = get_active_group_ids_sync()

        if not group_ids:
            logging.warning(
                "Немає активних Telegram-груп. Надішли /setgroup у потрібній групі."
            )
            return

        for chat_id in group_ids:
            ok = _send_group_event_text_sync(
                chat_id, text, keyboard,
                photo_url=photo_url or event.get("cover_url"),
                summary=_format_group_event_header(event),
                event_id=event_id,
            )
            if ok:
                logging.info("Подію %s успішно опубліковано в групі %s", event_id, chat_id)
            else:
                logging.error(
                    "Не вдалось опублікувати подію %s в групі %s",
                    event_id, chat_id
                )
    except Exception:
        logging.exception("Не вдалось опублікувати групове сповіщення про подію")


async def notify_subscribers(text, photo_url=None):
    """Надсилає повідомлення всім, хто колись натискав /start."""
    try:
        resp = requests.get(
            SUBSCRIBERS_REST,
            headers=HEADERS,
            params={"select": "chat_id"},
            timeout=30,
        )
        resp.raise_for_status()
        subscribers = resp.json()
    except Exception:
        logging.exception("Не вдалось отримати список підписників")
        return

    for row in subscribers:
        chat_id = row.get("chat_id")
        if not chat_id:
            continue
        try:
            if photo_url:
                await bot.send_photo(chat_id, photo=photo_url, caption=text)
            else:
                await bot.send_message(chat_id, text)
        except Exception:
            # Людина могла заблокувати бота чи видалити чат - просто пропускаємо
            logging.info(f"Не вдалось надіслати сповіщення {chat_id}")


def notify_subscribers_async(text, photo_url=None):
    """Фонова розсилка, незалежна від aiogram polling loop."""
    Thread(
        target=notify_subscribers_sync,
        args=(text, photo_url),
        daemon=True,
    ).start()


def event_group_keyboard(event_id, map_url=""):
    event = get_event_snapshot(event_id)
    price = int((event or {}).get('price_kop') or 0)
    if price > 0:
        url=_event_mini_app_link(event_id,payment=True)
        rows=[[InlineKeyboardButton(text=f'💳 Я йду · {price/100:.2f} грн',url=url)]] if url else []
        rows.append([InlineKeyboardButton(text='❌ Не йду',callback_data=f'ev_no:{event_id}')])
    else:
        rows = [[
            InlineKeyboardButton(text="✅ Я йду", callback_data=f"ev_go:{event_id}"),
            InlineKeyboardButton(text="❌ Не йду", callback_data=f"ev_no:{event_id}"),
        ]]
    if event and event.get('status') == 'scheduled':
        rows.extend([[InlineKeyboardButton(**button) for button in buttons]
                     for buttons in _event_game_poll_rows(event)])
    map_url = str(map_url or "").strip()
    if map_url:
        rows.append([
            InlineKeyboardButton(text="📍 Маршрут", url=map_url)
        ])
    quests_url = str((event or {}).get("quests_event_url") or "").strip()
    if quests_url:
        rows.append([InlineKeyboardButton(text="🔗 Учасники Quests Inc", url=quests_url)])
    app_link = _event_mini_app_link(event_id)
    if app_link:
        rows.append([InlineKeyboardButton(text="📅 Відкрити подію в Styloteka", url=app_link)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _event_location_in_group(event):
    """Show a readable location; put Google Maps URLs into the button only."""
    location = str(event.get("location_text") or "").strip()
    if not location:
        return ""
    if _is_google_maps_url(location):
        label = _google_maps_label_from_url(location)
        if not label:
            try:
                query = parse_qs(urlparse(location).query)
                label = (query.get("q") or query.get("query") or [""])[0].strip()
            except (ValueError, TypeError):
                pass
        if label and not label.startswith(("http://", "https://")) and len(label) <= 160:
            return label
        return "Місце на мапі (натисни «📍 Маршрут»)"
    if re.match(r"^https?://", location, re.IGNORECASE):
        return "Місце за посиланням (натисни «📍 Маршрут»)"
    return location


def _format_group_event_header(event):
    rsvps = event.get("rsvps") or []
    going = [r for r in rsvps if (r.get("status") or "going") == "going"]
    waiting = [r for r in rsvps if r.get("status") == "waitlist"]
    title = event.get("title") or "Ігрова подія"
    lines = [f"📅 {title}"]
    dt = event.get("event_date") or ""
    if event.get("event_time"):
        dt += f" · {str(event.get('event_time'))[:5]}"
    if dt:
        lines.append(f"🗓 {dt}")
    price = int(event.get("price_kop") or 0)
    lines.append(f"💳 Вартість: {price / 100:.2f} грн" if price else "🆓 Вартість: безкоштовно")

    names = event.get("game_names") or []
    names = [str(name).strip() for name in names if str(name).strip()] if isinstance(names, list) else []
    if not names and event.get("game_name"):
        names = [str(event.get("game_name")).strip()]
    if names:
        lines.append(f"🎲 Ігри: {', '.join(names)}")

    location = _event_location_in_group(event)
    if location:
        lines.append(f"📍 {location}")

    cap = int(event.get("max_participants") or 0)
    places = f"👥 {len(going)}/{cap} місць" if cap else f"👥 Учасників: {len(going)}"
    if waiting:
        places += f" · черга: {len(waiting)}"
    places += f" · відповіли: {len(rsvps)}"
    lines.append(places)
    if event.get("quests_event_url"):
        local, external = _event_source_counts(event)
        lines.append(f"🎲 Styloteka: {local} · 🔗 Quests Inc: {external}")
        lines.append(f"👥 Разом записів: {local + external} (без перевірки повторів)")
    local_present = max(0,int(event.get('checked_in_count') or 0))
    quests_present = min(max(0,int(event.get('quests_checked_in_count') or 0)),
        max(0,int(event.get('quests_count') or 0))) if event.get('quests_event_url') else 0
    if local_present or quests_present:
        lines.append(f"✅ Прийшли: Styloteka {local_present} · Quests Inc {quests_present} (вручну)")
    return "\n".join(lines)


def format_group_event(event):
    lines = [_format_group_event_header(event)]
    desc = str(event.get("description") or "").strip()
    if desc:
        lines.extend(["", desc])
    if event.get('status') == 'scheduled':
        lines.extend(["", "🎲 Обери настільні ігри кнопками нижче після запису на подію. "
                      "Варіанти — з усієї бібліотеки Styloteka. "
                      "Можна голосувати за кілька ігор; повторне натискання прибирає голос."])
    lines.extend(["", "Натисни кнопку нижче, щоб записатися."])
    return "\n".join(lines)


async def get_active_group_ids():
    try:
        resp = requests.get(
            TELEGRAM_GROUPS_REST,
            headers=HEADERS,
            params={"select": "chat_id", "active": "eq.true"},
            timeout=20,
        )
        resp.raise_for_status()
        return [int(row["chat_id"]) for row in resp.json() if row.get("chat_id")]
    except Exception:
        logging.exception("Не вдалось отримати Telegram-групи. Чи виконано SQL для telegram_groups?")
        return []


async def notify_groups(event_id, photo_url=None):
    """Use the same tested caption-first layout for async and sync callers."""
    await asyncio.to_thread(notify_groups_sync, event_id, photo_url)


def notify_groups_async(event_id, photo_url=None):
    """Фоновий груповий анонс, незалежний від aiogram polling loop."""
    Thread(
        target=notify_groups_sync,
        args=(event_id, photo_url),
        daemon=True,
    ).start()


async def refresh_group_event_message(callback: CallbackQuery, event_id: int):
    """Refresh RSVP counts on the main photo/caption without losing overflow."""
    try:
        event = get_event_snapshot(event_id)
        if not event or not callback.message:
            return
        photo = bool(callback.message.photo)
        old_text = str(getattr(callback.message, "caption" if photo else "text", "") or "")
        reminder = old_text.startswith("⏰ Нагадування")
        full = format_group_event_reminder(event) if reminder else format_group_event(event)
        summary = ("⏰ Нагадування: подія вже завтра!\n\n" if reminder else "") + _format_group_event_header(event)
        first, _ = _prepare_group_event_post(full, summary, photo=photo)
        keyboard = event_group_keyboard(event_id, _event_google_maps_url(event))
        if photo:
            await callback.message.edit_caption(caption=first, reply_markup=keyboard)
        elif old_text.startswith(("📅 ", "⏰ Нагадування")):
            await callback.message.edit_text(first, reply_markup=keyboard)
        else:
            # Older multi-part posts put their buttons on the last text chunk.
            # Don't replace the original continuation with a duplicate heading.
            await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception as exc:
        logging.info("Не вдалося оновити повідомлення події: %s", exc)


def _ensure_event_attendee_profile(telegram_user):
    """Зберігає ідентичність через незмінний Telegram ID після групового RSVP."""
    if not telegram_user:
        return
    uid = int(telegram_user.id)
    name = " ".join(x for x in [telegram_user.first_name, telegram_user.last_name] if x).strip()
    username = str(telegram_user.username or "").strip().lstrip("@").lower()
    payload = {
        "telegram_user_id": uid,
        "username": username,
        "display_name": name or username or "Гравець",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(
            PROFILES_REST,
            headers={**HEADERS, "Content-Type":"application/json", "Prefer":"resolution=merge-duplicates,return=minimal"},
            params={"on_conflict":"telegram_user_id"},
            json=payload,
            timeout=20,
        )
        if not resp.ok:
            logging.warning("Не вдалося синхронізувати профіль RSVP Telegram ID %s: %s", uid, resp.text[:200])
    except requests.RequestException:
        logging.exception("Помилка синхронізації профілю після RSVP")


def telegram_rsvp_identity(user):
    username = (user.username or "").lstrip("@").lower()
    if not username:
        username = f"tg_{user.id}"
    display_name = (user.full_name or user.username or "Гравець").strip()
    return username, display_name


@dp.message(Command("setgroup"))
async def cmd_setgroup(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Цю команду потрібно надіслати в групі, де бот має публікувати події.")
        return
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            await message.answer("Зареєструвати групу може лише адміністратор групи.")
            return
    except Exception:
        await message.answer("Не вдалося перевірити права адміністратора.")
        return

    try:
        resp = requests.post(
            TELEGRAM_GROUPS_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "chat_id"},
            json={"chat_id": message.chat.id, "title": message.chat.title or "", "active": True},
            timeout=20,
        )
        resp.raise_for_status()
        await message.answer("✅ Готово. Групу підключено. Для перевірки надішли /testgroup.")
    except Exception:
        logging.exception("Не вдалось зареєструвати групу")
        await message.answer("Не вдалося зберегти групу. Перевір, чи виконано SQL-оновлення в Supabase.")


@dp.message(Command("unsetgroup"))
async def cmd_unsetgroup(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            await message.answer("Вимкнути сповіщення може лише адміністратор групи.")
            return
        resp = requests.patch(
            TELEGRAM_GROUPS_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            params={"chat_id": f"eq.{message.chat.id}"},
            json={"active": False},
            timeout=20,
        )
        resp.raise_for_status()
        await message.answer("🔕 Автоматичні анонси подій у цій групі вимкнено.")
    except Exception:
        logging.exception("Не вдалось вимкнути групу")
        await message.answer("Не вдалося змінити налаштування групи.")



@dp.message(Command("testgroup"))
async def cmd_testgroup(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Цю команду потрібно надіслати в групі.")
        return

    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            await message.answer("Тест може запускати лише адміністратор групи.")
            return
    except Exception:
        await message.answer("Не вдалося перевірити права адміністратора.")
        return

    active_ids = get_active_group_ids_sync()
    if message.chat.id not in active_ids:
        await message.answer("⚠️ Ця група ще не підключена. Спочатку надішли /setgroup.")
        return

    ok, result = telegram_api_post("sendMessage", {
        "chat_id": message.chat.id,
        "text": "🧪 Тестове повідомлення. Автоматичні анонси подій працюють ✅",
    })
    if not ok:
        await message.answer(f"❌ Telegram не прийняв повідомлення: {result}")


@dp.message(Command("chatid"))
async def cmd_chatid(message: Message):
    await message.answer(f"ID цього чату: {message.chat.id}")


@dp.message(Command("bindevent"))
async def bind_existing_group_event_post(message: Message):
    """Admin recovery for a pre-update group post: reply /bindevent <id>."""
    if message.chat.type not in ("group", "supergroup"):
        return
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            await message.answer("Команда доступна тільки адміністраторам.")
            return
        args = (message.text or "").split()
        if len(args) != 2 or not args[1].isdigit() or not message.reply_to_message:
            await message.answer("Відповідай на головний пост бота командою /bindevent ID_події")
            return
        event_id = int(args[1])
        target = message.reply_to_message
        me = await bot.get_me()
        if not target.from_user or target.from_user.id != me.id:
            await message.answer("Потрібно відповісти саме на пост Styloteka.")
            return
        event = await asyncio.to_thread(get_event_snapshot, event_id)
        if not event:
            await message.answer("Подію з таким ID не знайдено.")
            return
        old_text = str(target.caption or target.text or "")
        reminder = old_text.startswith("⏰ Нагадування")
        await asyncio.to_thread(_remember_event_group_post, event_id, message.chat.id,
                                target.message_id, bool(target.photo), reminder)
        refresh_event_group_posts_async(event_id)
        await message.answer("✅ Пост прив’язано. Лічильники в ньому тепер оновлюватимуться.")
    except Exception:
        logging.exception("Не вдалося прив’язати старий пост події")
        await message.answer("Не вдалося прив’язати пост. Перевір ID події та спробуй ще раз.")


@dp.message(Command("app"))
async def cmd_app(message: Message):
    """Надсилає в групу постійну кнопку запуску Styloteka Mini App."""
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            "У групі команда /app створить кнопку запуску Styloteka."
        )
        return

    # Щоб учасники не засмічували групу однаковими кнопками,
    # публікувати її можуть лише адміністратор або власник групи.
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            await message.answer("Кнопку запуску може опублікувати лише адміністратор групи.")
            return
    except Exception:
        await message.answer("Не вдалося перевірити права адміністратора.")
        return

    try:
        me = await bot.get_me()
        bot_username = (me.username or "").lstrip("@")
        if not bot_username:
            await message.answer("Не вдалося визначити username бота.")
            return

        if TELEGRAM_APP_SHORT_NAME:
            launch_url = f"https://t.me/{bot_username}/{TELEGRAM_APP_SHORT_NAME}?startapp=home"
        else:
            # Працює для Main Mini App, налаштованого в BotFather.
            launch_url = f"https://t.me/{bot_username}?startapp=home"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎲 Відкрити Styloteka",
                        url=launch_url,
                    )
                ]
            ]
        )

        await message.answer(
            "🎲 <b>Styloteka</b>\n"
            "Бібліотека настільних ігор, події, профілі, досягнення та ігрові інструменти.",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception:
        logging.exception("Не вдалося створити кнопку запуску Mini App")
        await message.answer("Не вдалося створити кнопку запуску Styloteka.")


def _toggle_group_event_game_vote(event_id, game_id, telegram_user):
    """Toggle the same vote used by Mini App, using Telegram's verified user ID."""
    event = get_event_snapshot(event_id)
    if not event:
        raise ValueError('event_not_found')
    if event.get('status') != 'scheduled':
        raise ValueError('event_closed')
    options = dict(_event_game_poll_options(event))
    if game_id not in options:
        raise ValueError('game_not_in_poll')
    user = {"id": int(telegram_user.id), "username": telegram_user.username or "",
            "first_name": telegram_user.first_name or "",
            "last_name": telegram_user.last_name or ""}
    if not _event_games_membership(event_id, user):
        raise ValueError('rsvp_required')
    lookup = {"event_id": f"eq.{event_id}", "game_id": f"eq.{game_id}",
              "telegram_user_id": f"eq.{user['id']}"}
    existing = requests.get(EVENT_GAME_PICKS_REST, headers=HEADERS,
                            params={**lookup, "select": "id", "limit": "1"}, timeout=15)
    existing.raise_for_status()
    if existing.json():
        response = requests.delete(EVENT_GAME_PICKS_REST, headers=HEADERS,
                                   params=lookup, timeout=15)
        response.raise_for_status()
        return False, options[game_id]
    display = " ".join(filter(None, (user['first_name'], user['last_name']))).strip()
    display = display or user['username'] or 'Гравець'
    response = requests.post(EVENT_GAME_PICKS_REST,
        headers={**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=ignore-duplicates,return=minimal"},
        params={"on_conflict": "event_id,game_id,telegram_user_id"},
        json={"event_id": event_id, "game_id": game_id,
              "game_name": options[game_id], "telegram_user_id": user['id'],
              "display_name": display[:100]}, timeout=15)
    response.raise_for_status()
    return True, options[game_id]


@dp.callback_query(F.data.startswith("ev_game:"))
async def callback_event_game_vote(callback: CallbackQuery):
    try:
        parts = callback.data.split(':')
        if len(parts) != 3:
            raise ValueError('invalid_game')
        event_id, game_id = int(parts[1]), int(parts[2])
        if event_id <= 0 or game_id <= 0:
            raise ValueError('invalid_game')
        selected, game_name = await asyncio.to_thread(
            _toggle_group_event_game_vote, event_id, game_id, callback.from_user)
        message = ("✅ Голос зараховано: " if selected else "↩️ Голос прибрано: ") + game_name[:75]
        await callback.answer(message, show_alert=False)
        await refresh_group_event_message(callback, event_id)
        refresh_event_group_posts_async(event_id)
    except ValueError as exc:
        messages = {
            'event_not_found': 'Подію не знайдено.',
            'event_closed': 'Подія вже завершена, голосування закрито.',
            'game_not_in_poll': 'Цю гру прибрано з опитування.',
            'rsvp_required': 'Спочатку натисни «✅ Я йду» та запишись на подію.',
        }
        await callback.answer(messages.get(str(exc), 'Невірний варіант голосування.'),
                              show_alert=True)
    except requests.RequestException:
        logging.exception('Не вдалося зберегти голос із групового анонсу')
        await callback.answer('Помилка збереження голосу. Спробуй пізніше.', show_alert=True)
    except Exception:
        logging.exception('Помилка групового голосування за настільні ігри')
        await callback.answer('Не вдалося проголосувати.', show_alert=True)


@dp.callback_query(F.data.startswith("ev_go:"))
async def callback_event_go(callback: CallbackQuery):
    try:
        event_id = int(callback.data.split(":", 1)[1])
        event = await asyncio.to_thread(get_event_snapshot, event_id)
        if not event:
            await callback.answer("Подію не знайдено.", show_alert=True)
            return
        if int(event.get('price_kop') or 0)>0:
            await callback.answer("Ця подія платна. Натисни «💳 Я йду — оплатити» у новому пості та оплати через monobank.",show_alert=True)
            return
        username, display_name = telegram_rsvp_identity(callback.from_user)

        existing = get_event_rsvp_response(
            event_id,
            username,
            callback.from_user.id,
        )
        if existing:
            if (existing.get("status") or "going") in {"going", "waitlist"}:
                await asyncio.to_thread(_ensure_event_attendee_profile, callback.from_user)
            await callback.answer(
                rsvp_locked_message(existing.get("status")),
                show_alert=True,
            )
            return

        status = await asyncio.to_thread(
            add_event_rsvp,
            event_id, username, display_name, callback.from_user.id,
        )
        await asyncio.to_thread(_ensure_event_attendee_profile, callback.from_user)

        if status == "waitlist":
            await callback.answer(
                "Відповідь збережена. Місць немає — ви в черзі ⏳",
                show_alert=True,
            )
        else:
            await callback.answer(
                "Відповідь збережена: ви йдете ✅",
                show_alert=True,
            )

        await refresh_group_event_message(callback, event_id)
        refresh_event_group_posts_async(event_id)

    except ValueError as exc:
        if str(exc) == "event_closed":
            await callback.answer("Ця подія вже завершена ✅", show_alert=True)
        else:
            await callback.answer("Подію вже не знайдено.", show_alert=True)
    except Exception:
        logging.exception("Помилка RSVP із Telegram-групи")
        await callback.answer(
            "Не вдалося записати відповідь. Спробуйте ще раз.",
            show_alert=True,
        )

@dp.callback_query(F.data.startswith("ev_no:"))
async def callback_event_no(callback: CallbackQuery):
    try:
        event_id = int(callback.data.split(":", 1)[1])
        username, display_name = telegram_rsvp_identity(callback.from_user)

        existing = get_event_rsvp_response(
            event_id,
            username,
            callback.from_user.id,
        )
        if existing:
            await callback.answer(
                rsvp_locked_message(existing.get("status")),
                show_alert=True,
            )
            return

        add_event_decline(
            event_id,
            username,
            display_name,
            callback.from_user.id,
        )

        await callback.answer(
            "Відповідь збережена: ви не йдете ❌",
            show_alert=True,
        )
        await refresh_group_event_message(callback, event_id)
        refresh_event_group_posts_async(event_id)

    except ValueError as exc:
        if str(exc) == "event_closed":
            await callback.answer(
                "Ця подія вже завершена — відповіді зафіксовано ✅",
                show_alert=True,
            )
        else:
            await callback.answer("Подію вже не знайдено.", show_alert=True)
    except Exception:
        logging.exception("Помилка відповіді «Не йду» із Telegram-групи")
        await callback.answer(
            "Не вдалося записати відповідь.",
            show_alert=True,
        )

def run_bot_polling():
    global bot_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_loop = loop
    loop.run_until_complete(dp.start_polling(bot, handle_signals=False))


# Запускаємо бота та автоматичне завершення подій у фонових потоках, а Flask - в основному
Thread(target=run_bot_polling, daemon=True).start()
Thread(target=event_completion_worker, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
