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
from datetime import datetime, timezone
from urllib.parse import quote, parse_qsl

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
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
BUCKET = "files"

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
STORAGE = f"{SUPABASE_URL}/storage/v1/object"


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

    cover_file = request.files.get("cover")
    rules_file = request.files.get("rules")

    cover_url = upload_file(cover_file, "covers") if cover_file else None
    rules_url = upload_file(rules_file, "rules") if rules_file else None

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
    data = request.get_json(silent=True, force=True) or {}
    payload = {
        "game_name": data.get("game_name", ""),
        "played_at": data.get("played_at"),
        "winner": data.get("winner", ""),
        "note": data.get("note", ""),
        "added_by": data.get("added_by", "невідомо"),
        "players": data.get("players", ""),
        "duration_minutes": int(data.get("duration_minutes") or 0),
    }
    resp = requests.post(
        HISTORY_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        json=payload,
        timeout=30,
    )
    # До виконання SQL-міграції старі таблиці можуть не мати нових колонок.
    # У такому випадку не ламаємо старий функціонал, а зберігаємо базові поля.
    if resp.status_code >= 400 and ("players" in resp.text or "duration_minutes" in resp.text):
        payload.pop("players", None)
        payload.pop("duration_minutes", None)
        resp = requests.post(
            HISTORY_REST,
            headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json=payload,
            timeout=30,
        )
    resp.raise_for_status()
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




# ==================== ПРОФІЛЬ УЧАСНИКА ====================
def _profile_norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lstrip("@").lower())


def _profile_history_stats(user):
    username = _profile_norm(user.get("username"))
    full_name = _profile_norm(" ".join(x for x in [user.get("first_name"), user.get("last_name")] if x))
    first_name = _profile_norm(user.get("first_name"))
    aliases = {x for x in [username, full_name, first_name] if x}

    games_played = 0
    wins = 0
    game_counts = {}
    try:
        resp = requests.get(
            HISTORY_REST,
            headers=HEADERS,
            params={"select": "game_name,players,winner,played_at"},
            timeout=30,
        )
        resp.raise_for_status()
        for row in resp.json():
            raw_players = str(row.get("players") or "")
            players = [_profile_norm(p) for p in re.split(r"[,;\n|]+", raw_players) if _profile_norm(p)]
            winner = _profile_norm(row.get("winner"))
            is_player = bool(aliases.intersection(players))
            # Старі записи іноді містять лише переможця без списку гравців.
            if not is_player and winner and winner in aliases:
                is_player = True
            if not is_player:
                continue
            games_played += 1
            game = str(row.get("game_name") or "Без назви").strip() or "Без назви"
            game_counts[game] = game_counts.get(game, 0) + 1
            if winner and winner in aliases:
                wins += 1
    except Exception:
        logging.exception("Не вдалося порахувати статистику профілю з історії")

    events_attended = 0
    if username:
        try:
            rr = requests.get(
                RSVPS_REST,
                headers=HEADERS,
                params={"username": f"eq.{username}", "status": "eq.going", "select": "id"},
                timeout=20,
            )
            if rr.ok:
                events_attended = len(rr.json())
        except Exception:
            logging.exception("Не вдалося порахувати події профілю")

    favorite_game = "—"
    if game_counts:
        favorite_game = sorted(game_counts.items(), key=lambda item: (-item[1], item[0].lower()))[0][0]

    return {
        "games_played": games_played,
        "wins": wins,
        "events_attended": events_attended,
        "favorite_game": favorite_game,
        "unique_games": len(game_counts),
    }


def _profile_achievements(stats):
    games = int(stats.get("games_played") or 0)
    wins = int(stats.get("wins") or 0)
    events = int(stats.get("events_attended") or 0)
    unique_games = int(stats.get("unique_games") or 0)
    return [
        {"id": "first_game", "icon": "🎲", "name": "Перша партія", "description": "Зіграно першу записану партію", "unlocked": games >= 1},
        {"id": "first_win", "icon": "🏆", "name": "Перша перемога", "description": "Здобуто першу перемогу", "unlocked": wins >= 1},
        {"id": "regular", "icon": "🔥", "name": "Завсідник", "description": "Зіграно 10 партій", "unlocked": games >= 10},
        {"id": "explorer", "icon": "🧭", "name": "Дослідник", "description": "Зіграно у 5 різних ігор", "unlocked": unique_games >= 5},
        {"id": "event_guest", "icon": "👥", "name": "У компанії", "description": "Відвідано 5 подій", "unlocked": events >= 5},
        {"id": "champion", "icon": "👑", "name": "Чемпіон", "description": "Здобуто 10 перемог", "unlocked": wins >= 10},
    ]


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
    photo_url = user.get("photo_url") or ""

    # Зберігаємо базовий профіль за незмінним Telegram user_id.
    profile_payload = {
        "telegram_user_id": user_id,
        "username": username,
        "display_name": display_name,
        "photo_url": photo_url,
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
        # Якщо SQL ще не виконано, профіль все одно показуємо з Telegram-даних.
        if not up.ok:
            logging.warning("Профіль не збережено в Supabase: %s", up.text[:500])
    except Exception:
        logging.exception("Не вдалося зберегти профіль")

    stats = _profile_history_stats(user)
    # XP нараховується за фактичну активність, а не лише за перемоги.
    xp = stats["games_played"] * 20 + stats["wins"] * 10 + stats["events_attended"] * 25 + stats["unique_games"] * 5
    xp_per_level = 250
    level = max(1, xp // xp_per_level + 1)
    level_xp = xp % xp_per_level
    progress = round((level_xp / xp_per_level) * 100) if xp_per_level else 0

    return jsonify({
        "telegram_user_id": user_id,
        "username": username,
        "display_name": display_name,
        "photo_url": photo_url,
        "xp": xp,
        "level": level,
        "level_xp": level_xp,
        "xp_per_level": xp_per_level,
        "level_progress_percent": progress,
        "title": _profile_title(level),
        "stats": stats,
        "achievements": _profile_achievements(stats),
    })


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


def add_event_rsvp(event_id, username, display_name):
    """Записує користувача на подію. Повертає going або waitlist."""
    username = (username or "").lstrip("@").lower()
    if not username:
        raise ValueError("username is required")

    status = "going"
    try:
        eresp = requests.get(
            EVENTS_REST,
            headers=HEADERS,
            params={"id": f"eq.{event_id}", "select": "id,max_participants", "limit": 1},
            timeout=20,
        )
        eresp.raise_for_status()
        rows = eresp.json()
        if not rows:
            raise ValueError("event not found")
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
        },
        timeout=30,
    )
    resp.raise_for_status()
    return status


def remove_event_rsvp(event_id, username):
    """Скасовує RSVP і піднімає першого з waitlist, якщо звільнилось місце."""
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
    from datetime import date, timezone
    resp = requests.get(
        EVENTS_REST,
        headers=HEADERS,
        params={"select": "*", "event_date": f"gte.{date.today().isoformat()}", "order": "event_date.asc"},
        timeout=30,
    )
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

    uploaded_cover_url = upload_file(cover_file, "event-covers") if cover_file and getattr(cover_file, "filename", "") else None

    payload = {
        "title": data.get("title", ""),
        "event_date": data.get("event_date"),
        "event_time": data.get("event_time", ""),
        "game_name": data.get("game_name", ""),
        "description": data.get("description", ""),
        "created_by": data.get("created_by", "невідомо"),
        "max_participants": max(0, int(data.get("max_participants") or 0)),
        "cover_url": uploaded_cover_url,
    }
    resp = requests.post(
        EVENTS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=representation"},
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 400 and ("max_participants" in resp.text or "cover_url" in resp.text):
        fallback = dict(payload)
        if "max_participants" in resp.text:
            fallback.pop("max_participants", None)
        if "cover_url" in resp.text:
            fallback.pop("cover_url", None)
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
    game_name = data.get("game_name", "")
    if not event_photo and game_name:
        try:
            gresp = requests.get(
                REST,
                headers=HEADERS,
                params={"name": f"eq.{game_name}", "select": "cover_url", "limit": 1},
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
    if game_name:
        notify_lines.append(f"🎲 Гра: {game_name}")
    max_participants = max(0, int(data.get("max_participants") or 0))
    if max_participants:
        notify_lines.append(f"👥 Місць: {max_participants}")
    if data.get("description"):
        notify_lines.append(data.get("description"))
    notify_subscribers_async("\n".join(notify_lines), photo_url=event_photo)

    if event_id:
        notify_groups_async(event_id, photo_url=event_photo)

    return jsonify({"status": "ok", "event_id": event_id})


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
    data = request.get_json(silent=True, force=True) or {}
    username = (data.get("username") or "").lstrip("@").lower()
    if not username:
        return jsonify({"status": "no_username"}), 400
    try:
        status = add_event_rsvp(event_id, username, data.get("display_name", username))
    except ValueError:
        return jsonify({"status": "not_found"}), 404
    return jsonify({"status": status})


@app.route("/api/events/<int:event_id>/rsvp/<username>", methods=["DELETE"])
def cancel_rsvp(event_id, username):
    remove_event_rsvp(event_id, username)
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


def telegram_keyboard_payload(event_id):
    rows = [[
        {"text": "✅ Я йду", "callback_data": f"ev_go:{event_id}"},
        {"text": "❌ Не йду", "callback_data": f"ev_no:{event_id}"},
    ]]
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


def notify_groups_sync(event_id, photo_url=None):
    """Публікує подію в усіх активних групах напряму через Telegram Bot API."""
    try:
        event = get_event_snapshot(event_id)
        if not event:
            logging.error("Не знайдено подію id=%s для групового анонсу", event_id)
            return

        text = format_group_event(event)
        keyboard = telegram_keyboard_payload(event_id)
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


def event_group_keyboard(event_id):
    rows = [[
        InlineKeyboardButton(text="✅ Я йду", callback_data=f"ev_go:{event_id}"),
        InlineKeyboardButton(text="❌ Не йду", callback_data=f"ev_no:{event_id}"),
    ]]
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
    if event.get("game_name"):
        lines.append(f"🎲 {event.get('game_name')}")
    cap = int(event.get("max_participants") or 0)
    if cap:
        places = f"👥 {len(going)}/{cap} місць"
    else:
        places = f"👥 Учасників: {len(going)}"
    if waiting:
        places += f" · черга: {len(waiting)}"
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
        keyboard = event_group_keyboard(event_id)
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
        keyboard = event_group_keyboard(event_id)
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


@dp.callback_query(F.data.startswith("ev_go:"))
async def callback_event_go(callback: CallbackQuery):
    try:
        event_id = int(callback.data.split(":", 1)[1])
        username, display_name = telegram_rsvp_identity(callback.from_user)
        status = add_event_rsvp(event_id, username, display_name)
        if status == "waitlist":
            await callback.answer("Місць немає — тебе додано в чергу ⏳", show_alert=True)
        else:
            await callback.answer("Ти записаний ✅")
        await refresh_group_event_message(callback, event_id)
    except ValueError:
        await callback.answer("Подію вже не знайдено.", show_alert=True)
    except Exception:
        logging.exception("Помилка RSVP із Telegram-групи")
        await callback.answer("Не вдалося записатися. Спробуй ще раз.", show_alert=True)


@dp.callback_query(F.data.startswith("ev_no:"))
async def callback_event_no(callback: CallbackQuery):
    try:
        event_id = int(callback.data.split(":", 1)[1])
        username, _ = telegram_rsvp_identity(callback.from_user)
        existed = remove_event_rsvp(event_id, username)
        await callback.answer("Запис скасовано ❌" if existed else "Ти не був записаний на цю подію.")
        await refresh_group_event_message(callback, event_id)
    except Exception:
        logging.exception("Помилка скасування RSVP із Telegram-групи")
        await callback.answer("Не вдалося скасувати запис.", show_alert=True)


def run_bot_polling():
    global bot_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_loop = loop
    loop.run_until_complete(dp.start_polling(bot, handle_signals=False))


# Запускаємо бота у фоновому потоці, а Flask - в основному
Thread(target=run_bot_polling, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
