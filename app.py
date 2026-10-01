import os
import io
import uuid
import asyncio
import logging
from threading import Thread
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, send_from_directory, send_file
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

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


@app.route("/api/games", methods=["POST"])
def add_game():
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

    return jsonify({"status": "ok"})


@app.route("/api/games/<int:game_id>", methods=["PUT"])
def update_game(game_id):
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


@app.route("/api/games/<int:game_id>", methods=["DELETE"])
def delete_game(game_id):
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
    resp = requests.post(
        HISTORY_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        json={
            "game_name": data.get("game_name", ""),
            "played_at": data.get("played_at"),
            "winner": data.get("winner", ""),
            "note": data.get("note", ""),
            "added_by": data.get("added_by", "невідомо"),
        },
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


# ==================== АДМІНИ ====================
@app.route("/api/admins/check", methods=["POST"])
def check_admin():
    data = request.get_json(silent=True, force=True) or {}
    uname = (data.get("username") or "").lstrip("@").lower()
    if not uname:
        return jsonify({"is_admin": False})
    resp = requests.get(
        ADMINS_REST,
        headers=HEADERS,
        params={"username": f"eq.{uname}", "select": "username"},
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify({"is_admin": len(resp.json()) > 0})


# ==================== ПОДІЇ ====================
@app.route("/api/events", methods=["GET"])
def get_events():
    from datetime import date
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
    data = request.get_json(silent=True, force=True) or {}
    resp = requests.post(
        EVENTS_REST,
        headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
        json={
            "title": data.get("title", ""),
            "event_date": data.get("event_date"),
            "event_time": data.get("event_time", ""),
            "game_name": data.get("game_name", ""),
            "description": data.get("description", ""),
            "created_by": data.get("created_by", "невідомо"),
        },
        timeout=30,
    )
    resp.raise_for_status()

    event_photo = None
    game_name = data.get("game_name", "")
    if game_name:
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
        notify_lines.append(f"Гра: {game_name}")
    if data.get("description"):
        notify_lines.append(data.get("description"))
    notify_subscribers_async("\n".join(notify_lines), photo_url=event_photo)

    return jsonify({"status": "ok"})


@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    resp = requests.delete(EVENTS_REST, headers=HEADERS, params={"id": f"eq.{event_id}"}, timeout=30)
    resp.raise_for_status()
    return jsonify({"status": "deleted"})


@app.route("/api/events/<int:event_id>/rsvp", methods=["POST"])
def rsvp_event(event_id):
    data = request.get_json(silent=True, force=True) or {}
    username = (data.get("username") or "").lstrip("@").lower()
    if not username:
        return jsonify({"status": "no_username"}), 400

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
            "display_name": data.get("display_name", username),
            "status": "going",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify({"status": "ok"})


@app.route("/api/events/<int:event_id>/rsvp/<username>", methods=["DELETE"])
def cancel_rsvp(event_id, username):
    resp = requests.delete(
        RSVPS_REST,
        headers=HEADERS,
        params={"event_id": f"eq.{event_id}", "username": f"eq.{username.lstrip('@').lower()}"},
        timeout=30,
    )
    resp.raise_for_status()
    return jsonify({"status": "deleted"})


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
    """Викликається з синхронних Flask-роутів, щоб не чекати на розсилку."""
    if bot_loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(notify_subscribers(text, photo_url), bot_loop)
    except Exception:
        logging.exception("Не вдалось запланувати розсилку")


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
