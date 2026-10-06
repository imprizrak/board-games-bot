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
from threading import Thread
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
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
BUCKET = "files"
EVENT_TIMEZONE = os.environ.get("EVENT_TIMEZONE", "Europe/Berlin")

HEADERS = {"apikey": SUPABASE_KEY}
# Старі ключі (JWT) потребують ще й заголовка Authorization
if SUPABASE_KEY.startswith("eyJ"):
    HEADERS["Authorization"] = f"Bearer {SUPABASE_KEY}"

REST = f"{SUPABASE_URL}/rest/v1/games"
HISTORY_REST = f"{SUPABASE_URL}/rest/v1/game_history"
WISHLIST_REST = f"{SUPABASE_URL}/rest/v1/wishlist"
EVENTS_REST = f"{SUPABASE_URL}/rest/v1/events"
RSVPS_REST = f"{SUPABASE_URL}/rest/v1/event_rsvps"
ADMINS_REST = f"{SUPABASE_URL}/rest/v1/admins"
MASTERCLASS_REST = f"{SUPABASE_URL}/rest/v1/masterclass_bookings"
TELEGRAM_GROUPS_REST = f"{SUPABASE_URL}/rest/v1/telegram_groups"
PROFILES_REST = f"{SUPABASE_URL}/rest/v1/profiles"
ACTIVITY_REST = f"{SUPABASE_URL}/rest/v1/activity_feed"
PROFILE_TOOL_STATS_REST = f"{SUPABASE_URL}/rest/v1/profile_tool_stats"
PROFILE_TOOL_EVENT_RPC = f"{SUPABASE_URL}/rest/v1/rpc/record_profile_tool_event"
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
    return send_from_directory(".", "index.html")


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
    denied = _admin_required_response()
    if denied:
        return denied
    name = request.form.get("name")
    description = request.form.get("description", "")
    added_by = request.form.get("added_by", "невідомо")
    tags = request.form.get("tags", "")
    rules_link = normalize_rules_url(request.form.get("rules_url", ""))
    if rules_link is None:
        return jsonify({"error": "Некоректне посилання на правила. Використай адресу сайту http:// або https://"}), 400

    cover_file = request.files.get("cover")
    rules_file = request.files.get("rules")

    cover_url = upload_file(cover_file, "covers") if cover_file else None
    # Якщо одночасно додані URL і PDF, файл має пріоритет.
    rules_url = upload_file(rules_file, "rules") if rules_file else (rules_link or None)

    resp = requests.post(
        REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
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
    notify_game_groups_async(name, description=description, cover_url=cover_url)
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


def _resolve_profile_cosmetics(level, profile=None):
    profile = profile or {}
    rewards = _xp_reward_catalog(level)
    unlocked = {r["id"]: r for r in rewards if r.get("unlocked")}

    selected = {
        "title": profile.get("selected_title_reward") or "",
        "frame": profile.get("selected_frame_reward") or "",
        "badge": profile.get("selected_badge_reward") or "",
        "theme": profile.get("selected_theme_reward") or "",
    }

    # Якщо нагорода ще не відкрита або ID більше не існує — ігноруємо вибір.
    for kind in tuple(selected):
        rid = selected[kind]
        reward = unlocked.get(rid)
        if not reward or reward.get("type") != kind:
            selected[kind] = ""

    title_reward = unlocked.get(selected["title"])
    badge_reward = unlocked.get(selected["badge"])

    cosmetics = {
        "title_id": selected["title"],
        "frame_id": selected["frame"],
        "badge_id": selected["badge"],
        "theme_id": selected["theme"],
        "title_label": title_reward.get("value") if title_reward else "",
        "badge_label": badge_reward.get("value") if badge_reward else "",
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
                "select": "telegram_user_id,photo_url,earned_achievements,achievements_initialized,achievement_catalog_version,selected_title_reward,selected_frame_reward,selected_badge_reward,selected_theme_reward,xp",
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
                notify_achievement_groups_async(display_name, achievement, level, xp)

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
                "select": "telegram_user_id,username,display_name,photo_url,xp,selected_title_reward,selected_frame_reward,selected_badge_reward,selected_theme_reward",
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
        "title": cosmetics.get("title_label") or _profile_title(level),
        "cosmetics": cosmetics,
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
                "select": "telegram_user_id,xp,selected_title_reward,selected_frame_reward,selected_badge_reward,selected_theme_reward",
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

    field_map = {
        "title": "selected_title_reward",
        "frame": "selected_frame_reward",
        "badge": "selected_badge_reward",
        "theme": "selected_theme_reward",
    }

    patch = {}
    for kind, field in field_map.items():
        if kind not in data:
            continue
        reward_id = str(data.get(kind) or "").strip()
        if not reward_id:
            patch[field] = None
            continue
        reward = unlocked.get(reward_id)
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
                notify_achievement_groups_async(display_name, achievement, level, history_xp)
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
                "select": "telegram_user_id,username,display_name,photo_url,xp,earned_achievements,selected_title_reward,selected_frame_reward,selected_badge_reward,selected_theme_reward,updated_at",
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
                notify_achievement_groups_async(display_name, achievement, level, xp)


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
            if photo_url:
                ok, err = telegram_api_post("sendPhoto", {
                    "chat_id": chat_id,
                    "photo": photo_url,
                    "caption": text[:1024],
                    "reply_markup": keyboard,
                })
            else:
                ok, err = telegram_api_post("sendMessage", {
                    "chat_id": chat_id,
                    "text": text[:4096],
                    "reply_markup": keyboard,
                })

            if ok:
                sent_any = True
                logging.info("Нагадування про подію %s надіслано в групу %s", event_id, chat_id)
            else:
                logging.error(
                    "Не вдалось надіслати нагадування про подію %s в групу %s: %s",
                    event_id, chat_id, err
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
    while True:
        auto_send_event_day_reminders()
        auto_complete_due_events()
        time.sleep(60)


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

    for e in events:
        e["rsvps"] = rsvps_by_event.get(e["id"], [])

    return jsonify(events)


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

    payload = {
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
    payload = {
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

    return jsonify({"status": "updated", "event": updated_event})


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    denied = _admin_required_response()
    if denied:
        return denied
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


def telegram_keyboard_payload(event_id, map_url=""):
    rows = [[
        {"text": "✅ Я йду", "callback_data": f"ev_go:{event_id}"},
        {"text": "❌ Не йду", "callback_data": f"ev_no:{event_id}"},
    ]]
    map_url = str(map_url or "").strip()
    if map_url:
        rows.append([{
            "text": "📍 Google Maps",
            "url": map_url,
        }])
    if WEBAPP_URL:
        sep = "&" if "?" in WEBAPP_URL else "?"
        rows.append([{
            "text": "📅 Відкрити подію",
            "url": f"{WEBAPP_URL}{sep}view=events&event={event_id}",
        }])
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


def game_group_keyboard_payload():
    rows = []
    if WEBAPP_URL:
        rows.append([{
            "text": "🎲 Відкрити бібліотеку",
            "url": WEBAPP_URL,
        }])
    return {"inline_keyboard": rows} if rows else None


def notify_game_groups_sync(name, description="", cover_url=None):
    """Публікує нову гру в усіх активних Telegram-групах."""
    try:
        group_ids = get_active_group_ids_sync()
        if not group_ids:
            logging.warning(
                "Немає активних Telegram-груп для анонсу нової гри. Надішли /setgroup у потрібній групі."
            )
            return

        lines = ["🎲 Нова гра в бібліотеці", "", str(name or "Без назви")]
        desc = (description or "").strip()
        if desc:
            lines.extend(["", desc[:700]])
        lines.extend(["", "Відкрий бібліотеку, щоб переглянути гру."])
        text = "\n".join(lines)
        keyboard = game_group_keyboard_payload()

        for chat_id in group_ids:
            payload = {"chat_id": chat_id}
            if keyboard:
                payload["reply_markup"] = keyboard

            if cover_url:
                payload.update({
                    "photo": cover_url,
                    "caption": text[:1024],
                })
                ok, err = telegram_api_post("sendPhoto", payload)
            else:
                payload["text"] = text[:4096]
                ok, err = telegram_api_post("sendMessage", payload)

            if ok:
                logging.info("Нову гру '%s' опубліковано в групі %s", name, chat_id)
            else:
                logging.error(
                    "Не вдалось опублікувати нову гру '%s' в групі %s: %s",
                    name, chat_id, err
                )
    except Exception:
        logging.exception("Не вдалось опублікувати групове сповіщення про нову гру")


def notify_game_groups_async(name, description="", cover_url=None):
    """Запускає груповий анонс нової гри у фоні."""
    Thread(
        target=notify_game_groups_sync,
        args=(name, description, cover_url),
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
    Thread(
        target=notify_achievement_groups_sync,
        args=(display_name, achievement, level, xp),
        daemon=True,
    ).start()


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
            if photo_url:
                ok, err = telegram_api_post("sendPhoto", {
                    "chat_id": chat_id,
                    "photo": photo_url,
                    "caption": text[:1024],
                    "reply_markup": keyboard,
                })
            else:
                ok, err = telegram_api_post("sendMessage", {
                    "chat_id": chat_id,
                    "text": text[:4096],
                    "reply_markup": keyboard,
                })

            if ok:
                logging.info("Подію %s успішно опубліковано в групі %s", event_id, chat_id)
            else:
                logging.error(
                    "Не вдалось опублікувати подію %s в групі %s: %s",
                    event_id, chat_id, err
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
    rows = [[
        InlineKeyboardButton(text="✅ Я йду", callback_data=f"ev_go:{event_id}"),
        InlineKeyboardButton(text="❌ Не йду", callback_data=f"ev_no:{event_id}"),
    ]]
    map_url = str(map_url or "").strip()
    if map_url:
        rows.append([
            InlineKeyboardButton(text="📍 Google Maps", url=map_url)
        ])
    if WEBAPP_URL:
        sep = "&" if "?" in WEBAPP_URL else "?"
        rows.append([
            InlineKeyboardButton(text="📅 Відкрити подію", url=f"{WEBAPP_URL}{sep}view=events&event={event_id}")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_group_event(event):
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

    game_names = event.get("game_names") or []
    if not isinstance(game_names, list):
        game_names = []
    game_names = [str(name).strip() for name in game_names if str(name).strip()]
    if not game_names and event.get("game_name"):
        game_names = [str(event.get("game_name")).strip()]
    if game_names:
        lines.append(f"🎲 Ігри: {', '.join(game_names)}")

    location_text = (event.get("location_text") or "").strip()
    if location_text:
        lines.append(f"📍 {location_text}")

    cap = int(event.get("max_participants") or 0)
    if cap:
        places = f"👥 {len(going)}/{cap} місць"
    else:
        places = f"👥 Учасників: {len(going)}"
    if waiting:
        places += f" · черга: {len(waiting)}"
    places += f" · відповіли: {len(rsvps)}"
    lines.append(places)
    desc = (event.get("description") or "").strip()
    if desc:
        lines.extend(["", desc[:500]])
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
    """Публікує нову подію у всіх активних Telegram-групах."""
    try:
        event = get_event_snapshot(event_id)
        if not event:
            return
        text = format_group_event(event)
        keyboard = event_group_keyboard(event_id, _event_google_maps_url(event))
        for chat_id in await get_active_group_ids():
            try:
                if photo_url:
                    await bot.send_photo(chat_id, photo=photo_url, caption=text[:1024], reply_markup=keyboard)
                else:
                    await bot.send_message(chat_id, text[:4096], reply_markup=keyboard)
            except Exception:
                logging.exception("Не вдалось опублікувати подію в групі %s", chat_id)
    except Exception:
        logging.exception("Не вдалось опублікувати групове сповіщення про подію")


def notify_groups_async(event_id, photo_url=None):
    """Фоновий груповий анонс, незалежний від aiogram polling loop."""
    Thread(
        target=notify_groups_sync,
        args=(event_id, photo_url),
        daemon=True,
    ).start()


async def refresh_group_event_message(callback: CallbackQuery, event_id: int):
    """Оновлює лічильник місць прямо в повідомленні групи після RSVP."""
    try:
        event = get_event_snapshot(event_id)
        if not event or not callback.message:
            return
        text = format_group_event(event)
        keyboard = event_group_keyboard(event_id, _event_google_maps_url(event))
        if callback.message.photo:
            await callback.message.edit_caption(caption=text[:1024], reply_markup=keyboard)
        else:
            await callback.message.edit_text(text[:4096], reply_markup=keyboard)
    except Exception as exc:
        # "message is not modified" та старі повідомлення не повинні ламати callback.
        logging.info("Не вдалося оновити повідомлення події: %s", exc)


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


@dp.callback_query(F.data.startswith("ev_go:"))
async def callback_event_go(callback: CallbackQuery):
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

        status = add_event_rsvp(
            event_id,
            username,
            display_name,
            callback.from_user.id,
        )

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
