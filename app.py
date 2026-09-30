import os
import uuid
import asyncio
import logging
from threading import Thread
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, send_from_directory
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

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


# ==================== TELEGRAM BOT ====================
bot = Bot(token=API_TOKEN)
dp = Dispatcher()


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


def run_bot_polling():
    asyncio.run(dp.start_polling(bot, handle_signals=False))


# Запускаємо бота у фоновому потоці, а Flask - в основному
Thread(target=run_bot_polling, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
