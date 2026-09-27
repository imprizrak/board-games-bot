
import os
import asyncio
import sqlite3
import logging
from threading import Thread

from flask import Flask, request, jsonify, send_from_directory
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)

# ==== НАЛАШТУВАННЯ (беруться зі змінних середовища на Render) ====
API_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН_ВІД_BOTFATHER")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://ВАША-АДРЕСА.onrender.com")

DB_NAME = "games.db"
COVERS_DIR = os.path.join("static", "covers")
RULES_DIR = os.path.join("static", "rules")
os.makedirs(COVERS_DIR, exist_ok=True)
os.makedirs(RULES_DIR, exist_ok=True)


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            cover_url TEXT,
            rules_url TEXT,
            added_by TEXT
        )
        """
    )
    conn.commit()
    conn.close()


init_db()

# ==================== FLASK (веб-сторінка + API) ====================
app = Flask(__name__, static_folder="static")


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


@app.route("/api/games", methods=["GET"])
def get_games():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, cover_url, rules_url, added_by FROM games ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    games = [
        {"id": r[0], "name": r[1], "description": r[2], "cover_url": r[3], "rules_url": r[4], "added_by": r[5]}
        for r in rows
    ]
    return jsonify(games)


@app.route("/api/games", methods=["POST"])
def add_game():
    name = request.form.get("name")
    description = request.form.get("description", "")
    added_by = request.form.get("added_by", "невідомо")

    cover_file = request.files.get("cover")
    rules_file = request.files.get("rules")

    cover_url = None
    rules_url = None

    if cover_file:
        cover_path = os.path.join(COVERS_DIR, cover_file.filename)
        cover_file.save(cover_path)
        cover_url = f"/static/covers/{cover_file.filename}"

    if rules_file:
        rules_path = os.path.join(RULES_DIR, rules_file.filename)
        rules_file.save(rules_path)
        rules_url = f"/static/rules/{rules_file.filename}"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO games (name, description, cover_url, rules_url, added_by) VALUES (?, ?, ?, ?, ?)",
        (name, description, cover_url, rules_url, added_by),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/games/<int:game_id>", methods=["PUT"])
def update_game(game_id):
    name = request.form.get("name")
    description = request.form.get("description", "")

    cover_file = request.files.get("cover")
    rules_file = request.files.get("rules")

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT cover_url, rules_url FROM games WHERE id = ?", (game_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"status": "not_found"}), 404

    cover_url, rules_url = row

    if cover_file:
        cover_path = os.path.join(COVERS_DIR, cover_file.filename)
        cover_file.save(cover_path)
        cover_url = f"/static/covers/{cover_file.filename}"

    if rules_file:
        rules_path = os.path.join(RULES_DIR, rules_file.filename)
        rules_file.save(rules_path)
        rules_url = f"/static/rules/{rules_file.filename}"

    cur.execute(
        "UPDATE games SET name = ?, description = ?, cover_url = ?, rules_url = ? WHERE id = ?",
        (name, description, cover_url, rules_url, game_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "updated"})


@app.route("/api/games/<int:game_id>", methods=["DELETE"])
def delete_game(game_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM games WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
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
    asyncio.run(dp.start_polling(bot))


# Запускаємо бота у фоновому потоці, а Flask - в основному
Thread(target=run_bot_polling, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
