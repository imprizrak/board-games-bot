
import os
import sqlite3
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")

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


# ---- Головна сторінка (веб-застосунок) ----
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


# ---- API: отримати список усіх ігор ----
@app.route("/api/games", methods=["GET"])
def get_games():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, cover_url, rules_url, added_by FROM games ORDER BY name")
    rows = cur.fetchall()
    conn.close()

    games = []
    for row in rows:
        games.append({
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "cover_url": row[3],
            "rules_url": row[4],
            "added_by": row[5],
        })
    return jsonify(games)


# ---- API: додати нову гру (з файлами) ----
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


# ---- API: оновити гру (редагування) ----
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


# ---- API: видалити гру ----
@app.route("/api/games/<int:game_id>", methods=["DELETE"])
def delete_game(game_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM games WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
