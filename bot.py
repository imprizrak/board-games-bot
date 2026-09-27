
import asyncio
import logging
import sqlite3

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

API_TOKEN = "8978955303:AAG9wrkh3wsBBI4yPO16Gk4EYCwMkxUU9Mg"

# АДРЕСА, ДЕ РОЗМІЩЕНА ВАША ВЕБ-СТОРІНКА (обов'язково https)
WEBAPP_URL = "https://recognize-gecko-easiness.ngrok-free.dev"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

DB_NAME = "games.db"


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


@dp.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="Відкрити бібліотеку ігор",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )]
        ]
    )
    await message.answer(
        "Привіт! Тисни кнопку нижче, щоб відкрити бібліотеку настільних ігор.",
        reply_markup=kb,
    )


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
