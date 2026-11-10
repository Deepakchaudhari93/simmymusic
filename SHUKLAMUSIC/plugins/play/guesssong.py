import random
import asyncio
from urllib.parse import urlparse, parse_qs
from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)

from SHUKLAMUSIC import app, YouTube
from SHUKLAMUSIC.core.call import SHUKLA
from SHUKLAMUSIC.utils.stream.stream import stream
from SHUKLAMUSIC.utils.decorators.language import LanguageStart

from pymongo import MongoClient
from rapidfuzz import fuzz

# ------------------ MONGO ------------------
MONGO_DB = "mongodb+srv://arrush:Arrush123@arrush0.w4uwjly.mongodb.net/?retryWrites=true&w=majority&appName=arrush0"
mongo = MongoClient(MONGO_DB)

db = mongo["GuessSong"]
global_users = db["GlobalUsers"]      # lifetime global points
chat_users = db["ChatUsers"]          # lifetime chat points

# ------------------ GAME STORAGE ------------------
game_sessions = {}   # chat_id: {rounds, current, session_scores}
active_round = {}    # chat_id: {"answer": title, "guessed": False}


# ------------------ SONGS (YOUTUBE) ------------------
GUESS_SONGS = [
    {"title": "tu", "url": "https://youtu.be/4dkss90fdPc"},
    {"title": "pal pal", "url": "https://youtu.be/HdZK0uJyfqM?si=LrIb-Cr-AXf5JSbd"},
    {"title": "295", "url": "https://youtu.be/n_FCrCQ6-bA"},
    {"title": "zaroorat", "url": "https://youtu.be/VMEXKJbsUmE"},
    {"title": "pasoori", "url": "https://youtu.be/IV_JCpPe3SM"},
    {"title": "soulmate", "url": "https://youtu.be/9aOgTYO5UKs"}
    
]

# ------------------ YOUTUBE ID EXTRACTOR ------------------
def extract_video_id(url):
    query = urlparse(url)
    if query.hostname == "youtu.be":
        return query.path[1:]
    if query.hostname in ("www.youtube.com", "youtube.com"):
        if query.path == "/watch":
            return parse_qs(query.query).get("v", [None])[0]
        if query.path.startswith("/embed/") or query.path.startswith("/v/"):
            return query.path.split("/")[2]
    return None


# ---------------------------------------------------------
# START GAME
# ---------------------------------------------------------
@app.on_message(filters.command("guesssong") & filters.group)
async def start_game(client, message: Message):

    chat_id = message.chat.id

    if chat_id in game_sessions:
        return await message.reply_text("⚠️ A game is already running here!")

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("3 Songs", callback_data="gs_rounds_3"),
                InlineKeyboardButton("5 Songs", callback_data="gs_rounds_5"),
            ]
        ]
    )

    await message.reply_text("🎮 **Choose number of rounds:**", reply_markup=keyboard)


# ---------------------------------------------------------
# HANDLE ROUNDS CHOICE
# ---------------------------------------------------------
@app.on_callback_query(filters.regex("gs_rounds_"))
async def round_choice(client, query: CallbackQuery):

    chat_id = query.message.chat.id
    rounds = int(query.data.split("_")[2])

    game_sessions[chat_id] = {
        "rounds": rounds,
        "current": 0,
        "session_scores": {}
    }

    await query.message.edit_text(f"🎧 Game Started!\nRounds: {rounds}")

    await start_round(chat_id)


# ---------------------------------------------------------
# START ONE ROUND
# ---------------------------------------------------------
async def start_round(chat_id):

    session = game_sessions.get(chat_id)
    if not session:
        return

    if session["current"] >= session["rounds"]:
        return await end_game(chat_id)

    session["current"] += 1

    song = random.choice(GUESS_SONGS)
    title = song["title"].strip().lower()

    active_round[chat_id] = {"answer": title, "guessed": False}

    # Announce round
    await app.send_message(
        chat_id,
        f"🎵 <b>Round {session['current']} of {session['rounds']}</b>\n"
        f"<b>Guess the song!</b>\n"
        f"You have 60 seconds.\n"
        f"Use: /guess <code>answer</code>"
    )

    # START STREAM IN VC
    try:
        video_id = extract_video_id(song["url"])
        yt = await YouTube.track(video_id)
        
        if yt is None or isinstance(yt, str):
            await app.send_message(chat_id, "❌ Failed to load YouTube audio. Skipping this round...")
            del active_round[chat_id]
            return await start_round(chat_id)

        # SAFE unpack
        details, _id = yt

        await stream(
            _id,
            None,
            None,
            details,
            chat_id,
            "GuessGame",
            chat_id,
            streamtype="youtube",
        )

    except Exception as e:
        await app.send_message(chat_id, f"❌ Error streaming song.\n{e}")
        del active_round[chat_id]
        return await start_round(chat_id)

    # Timer for 60 seconds
    await asyncio.sleep(60)

    # If not guessed
    if chat_id in active_round and not active_round[chat_id]["guessed"]:
        try:
            await SHUKLA.stop_stream(chat_id)
            
        except:
            pass
            try:
                await SHUKLA.leave_group_call(chat_id)
                
            except:
                
                pass

        await app.send_message(chat_id, "⏱ No guesses! Next round...")
        del active_round[chat_id]

    await start_round(chat_id)


# ---------------------------------------------------------
# GUESS SYSTEM
# ---------------------------------------------------------
@app.on_message(filters.command("guess") & filters.group)
async def guess_handler(client, message: Message):

    chat_id = message.chat.id

    if chat_id not in active_round:
        return await message.reply_text("❌ No active round right now!")

    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        return await message.reply_text("❗Usage: `/guess your answer`")

    guess = parts[1].strip().lower()
    answer = active_round[chat_id]["answer"]

    if fuzz.ratio(guess, answer) >= 70:

        if active_round[chat_id]["guessed"]:
            return

        active_round[chat_id]["guessed"] = True
        user_id = message.from_user.id

        # per-game session score
        game_sessions[chat_id]["session_scores"][user_id] = (
            game_sessions[chat_id]["session_scores"].get(user_id, 0) + 1
        )

        # GLOBAL +20
        global_users.update_one(
            {"user_id": user_id},
            {"$inc": {"points": 20}},
            upsert=True
        )

        # CHAT-based +20
        chat_users.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {"$inc": {"points": 20}},
            upsert=True
        )

        total_points = global_users.find_one({"user_id": user_id})["points"]

        # STOP VC
        try:
            await SHUKLA.leave_group_call(chat_id)
        except:
            pass

        await message.reply_text(
            f"🎉 *Correct!*\n"
            f"Song was: **{answer.title()}**\n\n"
            f"🎁 +20 Points Earned\n"
            f"🏆 Total Score: **{total_points}**"
        )

        del active_round[chat_id]
        return await start_round(chat_id)

    else:
        return await message.reply_text("❌ Wrong guess! Try again.")


# ---------------------------------------------------------
# END GAME
# ---------------------------------------------------------
async def end_game(chat_id):

    session = game_sessions.get(chat_id)
    if not session:
        return

    results = sorted(
        session["session_scores"].items(),
        key=lambda x: x[1],
        reverse=True
    )

    text = "🏁 **GAME OVER**\n\n**Top Players:**\n\n"

    rank = 1
    for user_id, score in results:
        u = await app.get_users(user_id)
        text += f"{rank}. {u.mention} — {score} correct 🎯\n"
        rank += 1

    await app.send_message(chat_id, text)

    del game_sessions[chat_id]
    if chat_id in active_round:
        del active_round[chat_id]


# ---------------------------------------------------------
# STOP GAME
# ---------------------------------------------------------
@app.on_message(filters.command("stopgame") & filters.group)
async def stop_game(client, message: Message):

    chat_id = message.chat.id

    if chat_id not in game_sessions:
        return await message.reply_text("❌ No game running.")

    try:
        await SHUKLA.leave_group_call(chat_id)
    except:
        pass

    del game_sessions[chat_id]
    if chat_id in active_round:
        del active_round[chat_id]

    await message.reply_text("🛑 Game stopped.")


# ---------------------------------------------------------
# RANKING SYSTEM
# ---------------------------------------------------------
@app.on_message(filters.command("guessranking") & filters.group)
async def ranking(client, message: Message):

    chat_id = message.chat.id

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌍 Global Ranking", callback_data=f"gs_rank_global_{chat_id}")],
            [InlineKeyboardButton("👥 Chat Ranking", callback_data=f"gs_rank_chat_{chat_id}")],
            [InlineKeyboardButton("❌ Close", callback_data="gs_rank_close")]
        ]
    )

    await message.reply_text(
        "📊 Choose ranking:",
        reply_markup=keyboard
    )


@app.on_callback_query(filters.regex("gs_rank_"))
async def ranking_show(client, query: CallbackQuery):

    data = query.data

    # Close button
    if data == "gs_rank_close":
        return await query.message.delete()

    # Extract chat_id  
    parts = data.split("_")
    mode = parts[2]       # global or chat
    chat_id = int(parts[3])

    # ------------------------------------------
    # GLOBAL RANK
    # ------------------------------------------
    if mode == "global":

        top = list(global_users.find().sort("points", -1).limit(10))

        text = "🌍 <b>Global Ranking — Top 10</b>\n\n"
        rank = 1
        for user in top:
            try:
                u = await app.get_users(user["user_id"])
                text += f"{rank}. {u.mention} — {user['points']} pts 🏆\n"
                rank += 1
            except:
                pass

        # Toggle UI
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("👥 Chat Ranking", callback_data=f"gs_rank_chat_{chat_id}")],
                [InlineKeyboardButton("❌ Close", callback_data="gs_rank_close")]
            ]
        )

        return await query.message.edit_text(text, reply_markup=keyboard)


    # ------------------------------------------
    # CHAT RANK
    # ------------------------------------------
    if mode == "chat":

        top_chat = list(
            chat_users.find({"chat_id": chat_id}).sort("points", -1).limit(10)
        )

        if not top_chat:
            return await query.message.edit_text("❌ No ranking data in this chat!")

        text = "👥 <b>Lifetime Chat Ranking</b>\n\n"
        rank = 1

        for doc in top_chat:
            u = await app.get_users(doc["user_id"])
            text += f"{rank}. {u.mention} — {doc['points']} pts 🎯\n"
            rank += 1

        # Toggle UI
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🌍 Global Ranking", callback_data=f"gs_rank_global_{chat_id}")],
                [InlineKeyboardButton("❌ Close", callback_data="gs_rank_close")]
            ]
        )

        return await query.message.edit_text(text, reply_markup=keyboard)
