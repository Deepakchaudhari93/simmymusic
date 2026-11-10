import asyncio
import html
import importlib
import logging
import random
import re
import string
import time
from urllib.parse import quote_plus

import aiohttp
from yarl import URL

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message
from pytgcalls.exceptions import NoActiveGroupCall

try:  # kurigram: rich messages (same as start.py)
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

try:  # kurigram: coloured buttons
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

try:  # classic copy button (only used by the classic fallback keyboard)
    from pyrogram.types import CopyTextButton
except Exception:  # pragma: no cover
    CopyTextButton = None

import config
from SHUKLAMUSIC import Apple, Resso, SoundCloud, Spotify, Telegram, YouTube, app
from SHUKLAMUSIC.core.call import SHUKLA
from SHUKLAMUSIC.core.mongo import mongodb
from SHUKLAMUSIC.utils import seconds_to_min, time_to_seconds
from SHUKLAMUSIC.utils.channelplay import get_channeplayCB
from SHUKLAMUSIC.utils.decorators.language import languageCB
from SHUKLAMUSIC.utils.decorators.play import PlayWrapper
from SHUKLAMUSIC.utils.formatters import formats
from SHUKLAMUSIC.utils.inline import botplaylist_markup  # (no-query /play panel: still classic)
from SHUKLAMUSIC.utils.logger import play_logs
from SHUKLAMUSIC.utils.stream.stream import stream
from config import BANNED_USERS, lyrical

log = logging.getLogger("play")

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
ADMIN_ID = 7659846392
MUSIC_API_BASE = getattr(config, "MUSIC_API_BASE", "https://itzstalker.xyz/music")
MUSIC_API_KEY = getattr(config, "MUSIC_API_KEY", "stalker9c6b81df51dadc9aa19d00f7")
MUSIC_API_LIMIT = 10          # slider walks results 0..9
MUSIC_LOCAL_TTL = 3600        # seconds a search stays in the bot's own memory
DEFAULT_THUMB = config.PLAYLIST_IMG_URL

# MongoDB collection for admin-added songs
addsongdb = mongodb.added_songs

# ─── RITESH YT API ────────────────────────────────────────────────────────────
# Fast path: search → get vidid → stream URL by vidid. No yt-dlp, no download.
RITESH_API_BASE = "https://api.riteshyt.in"
RITESH_API_KEY = "riteshfreedaa236a0f9e521b0dd0ff7df"
RITESH_SEARCH_TTL = 3600  # local in-memory cache TTL (seconds)

# Separate MongoDB client + collection for the song stream-url cache
# Uses a dedicated URI so it doesn't collide with the main bot DB.
_ritesh_mongo_client = None
_ritesh_song_col = None

def _get_ritesh_col():
    """Lazy-init the separate MongoDB collection for Ritesh song cache."""
    global _ritesh_mongo_client, _ritesh_song_col
    if _ritesh_song_col is not None:
        return _ritesh_song_col
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        _ritesh_mongo_client = AsyncIOMotorClient(
            "mongodb+srv://shivam:Shivam9103@cluster0.e0hj6z5.mongodb.net/",
            serverSelectionTimeoutMS=5000,
        )
        _ritesh_song_col = _ritesh_mongo_client["ritesh_cache"]["songs"]
    except Exception as e:
        log.warning("Ritesh mongo init failed: %r", e)
        _ritesh_song_col = None
    return _ritesh_song_col

_RITESH_SEARCH_CACHE = {}  # query_lower -> (ts, {vidid, title, duration, thumb})


async def _ritesh_search(query: str):
    """Search Ritesh API, return first result dict or None. In-memory cached."""
    key = query.strip().lower()
    hit = _RITESH_SEARCH_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < RITESH_SEARCH_TTL:
        return hit[1]
    url = f"{RITESH_API_BASE}/search?q={quote_plus(query)}&limit=1&api_key={RITESH_API_KEY}"
    try:
        async with _session().get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
        results = data.get("result") or []
        if not results:
            return None
        item = results[0]
        meta = {
            "vidid": item.get("id", ""),
            "title": item.get("title", query),
            "duration": item.get("duration", "00:00"),
            "thumb": (item.get("thumbnails") or [{}])[0].get("url", DEFAULT_THUMB),
            "link": item.get("link", ""),
        }
        if len(_RITESH_SEARCH_CACHE) > 500:
            _RITESH_SEARCH_CACHE.clear()
        _RITESH_SEARCH_CACHE[key] = (time.monotonic(), meta)
        return meta
    except Exception as e:
        log.warning("Ritesh search error: %r", e)
        return None


async def _ritesh_stream_url(vidid: str, video: bool = False) -> str | None:
    """Get direct stream URL for a video ID from Ritesh API."""
    dl_type = "video" if video else "audio"
    url = f"{RITESH_API_BASE}/download?query={vidid}&dl_type={dl_type}&api_key={RITESH_API_KEY}"
    try:
        async with _session().get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            # The API returns the stream URL directly (or as a redirect)
            # Check if it's JSON with a url field, or a plain redirect
            ct = r.headers.get("Content-Type", "")
            if "json" in ct:
                data = await r.json(content_type=None)
                return data.get("url") or data.get("stream") or str(r.url)
            # It's the actual stream URL (after redirects)
            return str(r.url)
    except Exception as e:
        log.warning("Ritesh stream url error for %s: %r", vidid, e)
        return None


async def _db_get_song(exact_query: str):
    """Exact-match lookup in Ritesh song cache DB. Returns doc or None."""
    col = _get_ritesh_col()
    if col is None:
        return None
    try:
        return await col.find_one({"query": exact_query})
    except Exception as e:
        log.warning("Ritesh DB get error: %r", e)
        return None


async def _db_save_song(exact_query: str, title: str, duration: str, stream_url: str):
    """Save song to Ritesh cache DB."""
    col = _get_ritesh_col()
    if col is None:
        return
    try:
        await col.update_one(
            {"query": exact_query},
            {"$set": {"title": title, "duration": duration, "stream_url": stream_url}},
            upsert=True,
        )
    except Exception as e:
        log.warning("Ritesh DB save error: %r", e)


async def resolve_ritesh_fast(query: str, video: bool = False):
    """
    PRIMARY fast path for both audio (/play) and video (/vplay):
    1. Exact-match DB lookup (instant if previously played).
    2. Otherwise: search API for vidid, then get stream url — both fast HTTP calls.
    3. Save to DB for next time.
    DB key includes type so "tum se" audio and "tum se" video are stored separately.
    Returns details dict for stream(streamtype="ritesh") or None.
    """
    exact_key = f"{'v' if video else 'a'}:{query.strip().lower()}"

    # 1) DB cache hit — instant
    cached = await _db_get_song(exact_key)
    if cached and cached.get("stream_url"):
        return {
            "title": cached["title"],
            "duration_min": cached["duration"],
            "thumb": DEFAULT_THUMB,
            "vidid": exact_key,
            "filepath": cached["stream_url"],
            "link": "",
        }

    # 2) Search to get vidid, then fetch stream url
    meta = await _ritesh_search(query)
    if not meta or not meta.get("vidid"):
        return None

    stream_url = await _ritesh_stream_url(meta["vidid"], video=video)
    if not stream_url:
        return None

    # 3) Save for next time (fire and forget)
    asyncio.create_task(_db_save_song(exact_key, meta["title"], meta["duration"], stream_url))

    return {
        "title": meta["title"],
        "duration_min": meta["duration"],
        "thumb": meta.get("thumb", DEFAULT_THUMB),
        "vidid": meta["vidid"],
        "filepath": stream_url,
        "link": meta.get("link", ""),
    }

# id -> DRX song dict, filled by every DRX search so the Audio/Video buttons
# play exactly the song that is shown on the card
_DRX_CACHE = {}


# ─── MUSIC API HELPERS (itzstalker.xyz) ───────────────────────────────────────
# Only title, duration (already "M:SS") and the stream url are used from the API.
# /play -> result=audio, /vplay -> result=video (one search call each).

_API_CACHE = {}   # (lowercase query, "audio"|"video") -> (monotonic ts, [songs])
_http = None


def _session():
    global _http
    if _http is None or _http.closed:
        _http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=12, connect=5),
            connector=aiohttp.TCPConnector(limit=50, ttl_dns_cache=300),
        )
    return _http


def _parse_song(item, mode):
    """Reduce one API item to {id, title, duration, audio, video} or None."""
    if not isinstance(item, dict):
        return None
    vid = item.get("id")
    st = item.get("stream")
    if isinstance(st, dict):  # safety: if the API ever returns both
        audio, video = st.get("audio"), st.get("video")
    elif mode == "video":
        audio, video = None, st
    else:
        audio, video = st, None
    if not vid or not (audio or video):
        return None
    return {
        "id": str(vid),
        "title": str(item.get("title") or "Unknown"),
        "duration": str(item.get("duration") or "00:00"),
        "audio": audio or None,
        "video": video or None,
    }


async def drx_search_songs(query: str, video=False):
    """Search via itzstalker API.
    video=False -> result=audio (for /play), video=True -> result=video (for /vplay).
    Returns list of songs or None."""
    q = (query or "").strip()
    if not q:
        return None
    mode = "video" if video else "audio"
    ck = (q.lower(), mode)
    hit = _API_CACHE.get(ck)
    if hit and time.monotonic() - hit[0] < MUSIC_LOCAL_TTL:
        return hit[1]

    url = (
        f"{MUSIC_API_BASE}/search?q={quote_plus(q)}"
        f"&result={mode}&limit={MUSIC_API_LIMIT}&key={MUSIC_API_KEY}"
    )
    for _attempt in (1, 2):
        try:
            async with _session().get(URL(url, encoded=True)) as resp:
                if resp.status != 200:
                    if resp.status in (401, 400):
                        return None
                    continue
                data = await resp.json(content_type=None)
        except Exception:
            continue
        songs = [x for x in (_parse_song(i, mode) for i in (data.get("result") or [])) if x]
        if not songs:
            return None
        if len(_API_CACHE) > 500:
            _API_CACHE.clear()
        _API_CACHE[ck] = (time.monotonic(), songs)
        if len(_DRX_CACHE) > 2000:
            _DRX_CACHE.clear()
        for sg in songs:
            old = _DRX_CACHE.get(sg["id"])
            if old:  # keep the other stream type if we already have it
                sg["audio"] = sg["audio"] or old.get("audio")
                sg["video"] = sg["video"] or old.get("video")
            _DRX_CACHE[sg["id"]] = sg
        return songs
    return None


def pick_stream(song, video):
    """Video stream url for /vplay, audio stream url for /play."""
    if not song:
        return None
    return song.get("video") if video else song.get("audio")


# ─── yt-dlp METADATA (title + duration) ───────────────────────────────────────
# The API is only used for the stream url. Title / duration / id come from yt-dlp
# (flat search, nothing is downloaded) and run in parallel with the API call.

try:
    import yt_dlp
except Exception:  # pragma: no cover
    yt_dlp = None

YTDLP_TIMEOUT = 12
_META_CACHE = {}     # video id -> {"id", "title", "duration"}
_META_Q_CACHE = {}   # lowercase query -> (monotonic ts, meta)
_BG_TASKS = set()


def _sec_to_min(sec):
    """125 -> '2:05', 3725 -> '1:02:05', unknown / live -> None"""
    try:
        sec = int(float(sec))
    except Exception:
        return None
    if sec <= 0:
        return None
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _ytdlp_search_sync(query):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": True,
        "socket_timeout": 8,
        "retries": 1,
        "extractor_retries": 1,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
    for entry in (info or {}).get("entries") or []:
        if entry:
            return entry
    return None


async def ytdlp_search_meta(query: str):
    """Fast yt-dlp lookup -> {"id", "title", "duration"} or None."""
    q = (query or "").strip()
    if not q or yt_dlp is None:
        return None
    key = q.lower()
    hit = _META_Q_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < MUSIC_LOCAL_TTL:
        return hit[1]
    loop = asyncio.get_running_loop()
    try:
        entry = await asyncio.wait_for(
            loop.run_in_executor(None, _ytdlp_search_sync, q), YTDLP_TIMEOUT
        )
    except Exception as e:
        log.warning("yt-dlp search failed for %r: %r", q, e)
        return None
    if not entry or not entry.get("id") or not entry.get("title"):
        return None
    meta = {
        "id": str(entry["id"]),
        "title": str(entry["title"]),
        "duration": _sec_to_min(entry.get("duration")),  # None for live / unknown
    }
    if len(_META_Q_CACHE) > 500:
        _META_Q_CACHE.clear()
    if len(_META_CACHE) > 2000:
        _META_CACHE.clear()
    _META_Q_CACHE[key] = (time.monotonic(), meta)
    _META_CACHE[meta["id"]] = meta
    return meta


async def _safe(coro):
    """Await a coroutine, never raise (returns None on error)."""
    try:
        return await coro
    except Exception as e:
        log.warning("background lookup failed: %r", e)
        return None


def _spawn(coro):
    t = asyncio.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return t


def build_drx_details(song, meta, stream_url):
    """title/duration from yt-dlp when we have it, API values otherwise."""
    meta = meta or {}
    return {
        "title": meta.get("title") or song["title"],
        "duration_min": meta.get("duration") or song["duration"],
        "thumb": DEFAULT_THUMB,
        "vidid": song["id"],
        "filepath": stream_url,
    }


async def resolve_drx_fast(query: str, video):
    """yt-dlp (title, duration) and the API (stream url) run at the same time.
    Returns the `details` dict for stream(streamtype="drx") or None."""
    meta, songs = await asyncio.gather(
        _safe(ytdlp_search_meta(query)),
        _safe(drx_search_songs(query, bool(video))),
    )
    if not songs:
        return None
    song = None
    if meta:
        song = next((s for s in songs if s["id"] == meta["id"]), None)
        if song is None:  # API ranked a different song first: ask it for yt-dlp's pick
            more = await _safe(drx_search_songs(meta["id"], bool(video)))
            if more:
                song = next((s for s in more if s["id"] == meta["id"]), None)
        if song is None:
            meta = None  # never mix yt-dlp's title with another song's audio
    if song is None:
        song = songs[0]
    stream_url = pick_stream(song, video)
    if not stream_url:
        return None
    return build_drx_details(song, meta, stream_url)


# ─── ADDSONG DB HELPERS ───────────────────────────────────────────────────────

async def get_added_song(query: str):
    """Get an admin-added song by query (case-insensitive)."""
    data = await addsongdb.find_one({"query": query.lower().strip()})
    return data


async def save_added_song(query: str, url: str, title: str = None, duration: str = "00:00"):
    """Save an admin-added song to DB."""
    await addsongdb.update_one(
        {"query": query.lower().strip()},
        {"$set": {"url": url, "title": title or query, "duration": duration}},
        upsert=True,
    )


async def delete_added_song(query: str):
    """Delete an admin-added song from DB."""
    await addsongdb.delete_one({"query": query.lower().strip()})


async def list_added_songs():
    """List all admin-added songs."""
    cursor = addsongdb.find({})
    return await cursor.to_list(length=1000)


# ─── ADDSONG COMMAND ────────────────────────────────────────────────────────────

@app.on_message(filters.command("addsong") & filters.user(ADMIN_ID))
async def addsong_command(client, message: Message):
    """
    /addsong <query>
    Admin adds a custom song URL for a query.
    Bot will ask for the song URL in the next step.
    """
    if len(message.command) < 2:
        return await message.reply_text(
            "**Usage:**\n"
            "`/addsong <query>`\n\n"
            "Example: `/addsong tum hi ho`\n\n"
            "Then reply with the song URL to save it."
        )
    query = message.text.split(None, 1)[1].strip()
    
    # Check if already exists
    existing = await get_added_song(query)
    if existing:
        return await message.reply_text(
            f"⚠️ A song already exists for **{query}**\n"
            f"URL: `{existing['url']}`\n\n"
            "Reply with a new URL to update, or use `/delsong {query}` to remove first."
        )
    
    # Store pending state
    pending_key = f"addsong_pending:{message.from_user.id}"
    await mongodb.pending_states.update_one(
        {"key": pending_key},
        {"$set": {"key": pending_key, "query": query, "chat_id": message.chat.id}},
        upsert=True,
    )
    
    await message.reply_text(
        f"🎵 **Add Song Mode**\n"
        f"Query: `{query}`\n\n"
        "Now reply to this message with the **song URL** you want to save.\n"
        "Or reply with `cancel` to abort."
    )


@app.on_message(filters.reply & filters.user(ADMIN_ID))
async def addsong_url_handler(client, message: Message):
    """Handle URL reply for /addsong command."""
    if not message.reply_to_message:
        return
    
    pending_key = f"addsong_pending:{message.from_user.id}"
    pending = await mongodb.pending_states.find_one({"key": pending_key})
    if not pending:
        return
    
    # Clean up pending state
    await mongodb.pending_states.delete_one({"key": pending_key})
    
    if message.text.lower().strip() == "cancel":
        return await message.reply_text("❌ Add song cancelled.")
    
    url = message.text.strip()
    query = pending["query"]
    
    await save_added_song(query, url, title=query)
    await message.reply_text(
        f"✅ **Song Added!**\n"
        f"Query: `{query}`\n"
        f"URL: `{url}`\n\n"
        "Whenever someone searches for this query, this URL will be played directly."
    )


@app.on_message(filters.command("delsong") & filters.user(ADMIN_ID))
async def delsong_command(client, message: Message):
    """Delete an admin-added song."""
    if len(message.command) < 2:
        return await message.reply_text("**Usage:** `/delsong <query>`")
    query = message.text.split(None, 1)[1].strip()
    existing = await get_added_song(query)
    if not existing:
        return await message.reply_text(f"❌ No song found for query: `{query}`")
    await delete_added_song(query)
    await message.reply_text(f"✅ Deleted song for query: `{query}`")


@app.on_message(filters.command("listsongs") & filters.user(ADMIN_ID))
async def listsongs_command(client, message: Message):
    """List all admin-added songs."""
    songs = await list_added_songs()
    if not songs:
        return await message.reply_text("📭 No admin-added songs found.")
    
    text = "📋 **Admin Added Songs:**\n\n"
    for i, song in enumerate(songs, 1):
        text += f"{i}. `{song['query']}` → `{song.get('url', 'N/A')[:50]}...`\n"
    await message.reply_text(text)


# ═══════════════════════════════════════════════════════════════════════════════
#   RICH UI  —  rich messages + rich buttons (same engine as start.py)
#   Everything for play / playforce / vplay / cplay / stream / sliders lives here.
# ═══════════════════════════════════════════════════════════════════════════════

TUNES_URL = "https://t.me/nyxxai/room?startapp"

# Media shown on EVERY now-playing card (the song's own api image is NOT used).
# Direct http(s) link: jpg/png/webp image OR mp4/gif video.
STREAM_MEDIA_URL = "https://files.catbox.moe/7nn61x.mp4"
STREAM_MEDIA_SPOILER = True  # hide the media behind a spoiler until tapped

EMOJI_ANALYSING = 5573473356579078196  # the "analysis" emoji from start.py

# duration bar shown on the copy button (text before the duration)
DUR_BAR = "» •၊၊||၊|။||||။‌‌‌‌‌၊|။‌‌၊|၊|။||||။‌‌‌‌‌၊|။‌‌၊•"

LBL_RESUME = "𝖱𝖾𝗌𝗎𝗆𝖾"
LBL_PAUSE = "𝖯𝖺𝗎𝗌𝖾"
LBL_STOP = "𝖲𝗍𝗈𝗉"
LBL_VC = "𝖦𝗈 ᐯᑕ-ᒪᗴՏՏ (ʀᴏᴏϻ's)"
LBL_CLOSE = "ᑕᒪOՏᗴ"
EMO_RESUME = 5807414083388971488
EMO_PAUSE = 5807945302124012062
EMO_STOP = 6102688455014228420
EMO_CLOSE = 5841541824803509441

RICH_EMOJI_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected


# ─── low level helpers ────────────────────────────────────────────────────────

def _remoji(emoji_id: int, style: int, fallback: str = "⭐") -> str:
    if style == 0:
        return f"<emoji id={emoji_id}>{fallback}</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def _mention_html(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name or "User", quote=False)}</a>'


def _classic_to_rich(text: str, style: int) -> str:
    """Make a classic Telegram-HTML caption (language strings) safe inside a rich message."""
    text = re.sub(
        r'<emoji id="?(\d+)"?>(.*?)</emoji>',
        lambda m: _remoji(int(m.group(1)), style, m.group(2) or "⭐"),
        text,
        flags=re.S,
    )
    text = re.sub(
        r"</?spoiler>",
        lambda m: "</tg-spoiler>" if m.group(0)[1] == "/" else "<tg-spoiler>",
        text,
    )
    text = re.sub(r"</?blockquote[^>]*>", "", text)  # no quote inside the quote
    return text.replace("\r", "").replace("\n", "<br>")


def _fmt_duration(d) -> str:
    """2:39 -> '2:39 ᴍιη'.  Live / unknown values are shown as they are."""
    d = str(d or "").strip()
    if re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", d) and d not in ("0:00", "00:00"):
        return f"{d} ᴍιη"
    return d or "00:00"


def _title_html(title, link=None) -> str:
    t = html.escape(str(title or "Unknown"), quote=False)
    if link and str(link).startswith(("http://", "https://", "tg://")):
        return f'<a href="{html.escape(str(link))}">{t}</a>'
    return t


def _media_figure(url: str, caption_html: str = "", spoiler: bool = False) -> str:
    """Image OR video (decided by the link, exactly like START_MEDIA_URL in start.py)."""
    cap = f"<figcaption>{caption_html}</figcaption>" if caption_html else ""
    u = html.escape(url)
    sp = " tg-spoiler" if spoiler else ""
    if url.lower().split("?")[0].endswith((".mp4", ".mov", ".webm", ".gif")):
        return f'<figure><video src="{u}"{sp}></video>{cap}</figure>'
    return f'<figure><img src="{u}"{sp}/>{cap}</figure>'


def _info_aside(style: int, duration, mention: str = None) -> str:
    e = _remoji(EMOJI_ANALYSING, style, "📊")
    out = (
        f"{e} <b><u><i>ᴅᴜʀᴧᴛιση</i></u></b>: "
        f"<i>{html.escape(_fmt_duration(duration), quote=False)}</i>"
    )
    if mention:
        out += f"<br>{e} <b><u><i>ʀєǫᴜєsᴛєᴅ</i></u></b>: <i>{mention}</i>"
    return f"<aside>{out}</aside>"


def _classic_caption(title, duration, mention: str = None) -> str:
    cap = (
        f"<b>{html.escape(str(title or 'Unknown'), quote=False)}</b>\n\n"
        f"<b>ᴅᴜʀᴧᴛιση</b>: <i>{html.escape(_fmt_duration(duration), quote=False)}</i>"
    )
    if mention:
        cap += f"\n<b>ʀєǫᴜєsᴛєᴅ</b>: {mention}"
    return cap


def _fit_cb(prefix: str, query: str, suffix: str) -> str:
    """callback_data must be <= 64 BYTES; trim the query (max 20 chars) to fit."""
    q = (query or "")[:20]
    room = 64 - len((prefix + suffix).encode("utf-8"))
    while q and len(q.encode("utf-8")) > room:
        q = q[:-1]
    return f"{prefix}{q}{suffix}"


# ─── buttons ──────────────────────────────────────────────────────────────────
# row item = (text, kind, value, style)
#   kind  : "url" | "callback" | "copy"
#   style : "primary" | "success" | "danger" | None

def _btn_label(text, emoji=None) -> str:
    """Rich button label: custom emoji (<tg-emoji>) and/or text. Emoji-only is allowed."""
    parts = []
    if emoji:
        parts.append(_remoji(int(emoji), RICH_EMOJI_STYLE, "⭐"))
    if text:
        parts.append(html.escape(text, quote=False))
    return " ".join(parts)


def rich_buttons(rows) -> str:
    """Buttons live inside the rich HTML itself (<tg-button-row> + <tg-button>)."""
    out = ""
    for row in rows:
        out += "<tg-button-row>"
        for item in row:
            text, kind, value, style = item[:4]
            emoji = item[4] if len(item) > 4 else None
            st = f' style="{style}"' if style else ""
            label = _btn_label(text, emoji)
            if kind == "url":
                out += f'<tg-button type="url" url="{html.escape(value)}"{st}>{label}</tg-button>'
            elif kind == "copy":
                out += f'<tg-button type="copy_text" text="{html.escape(value)}"{st}>{label}</tg-button>'
            else:
                out += f'<tg-button type="callback_data" data="{html.escape(value)}"{st}>{label}</tg-button>'
        out += "</tg-button-row>"
    return out


def classic_keyboard(rows) -> InlineKeyboardMarkup:
    """Normal inline keyboard, used only if a rich message is rejected."""
    kb = []
    for row in rows:
        line = []
        for item in row:
            text, kind, value, style = item[:4]
            text = text or "\u2060"  # classic buttons need a label
            if kind == "url":
                kwargs = {"url": value}
            elif kind == "copy":
                if CopyTextButton is None:
                    continue
                try:
                    kwargs = {"copy_text": CopyTextButton(text=value)}
                except Exception:
                    continue
            else:
                kwargs = {"callback_data": value}
            btn = None
            if style and ButtonStyle is not None:
                try:
                    btn = InlineKeyboardButton(
                        text, style=getattr(ButtonStyle, style.upper()), **kwargs
                    )
                except Exception:
                    btn = None
            if btn is None:
                try:
                    btn = InlineKeyboardButton(text, **kwargs)
                except Exception:
                    continue
            line.append(btn)
        if line:
            kb.append(line)
    return InlineKeyboardMarkup(kb)


# search / selection rows (these replace the old inline play(10) markups) ------

def track_rows(_, videoid, user_id, channel, fplay):
    return [
        [
            (_["P_B_1"], "callback", f"MusicStream {videoid}|{user_id}|a|{channel}|{fplay}", "primary"),
            (_["P_B_2"], "callback", f"MusicStream {videoid}|{user_id}|v|{channel}|{fplay}", "primary"),
        ],
        [(_["CLOSE_BUTTON"], "callback", f"forceclose {videoid}|{user_id}", "danger")],
    ]


def playlist_rows(_, videoid, user_id, ptype, channel, fplay):
    return [
        [
            (_["P_B_1"], "callback", f"SHUKLAPlaylists {videoid}|{user_id}|{ptype}|a|{channel}|{fplay}", "primary"),
            (_["P_B_2"], "callback", f"SHUKLAPlaylists {videoid}|{user_id}|{ptype}|v|{channel}|{fplay}", "primary"),
        ],
        [(_["CLOSE_BUTTON"], "callback", f"forceclose {videoid}|{user_id}", "danger")],
    ]


def livestream_rows(_, videoid, user_id, mode, channel, fplay):
    return [
        [(_["P_B_3"], "callback", f"LiveStream {videoid}|{user_id}|{mode}|{channel}|{fplay}", "primary")],
        [(_["CLOSE_BUTTON"], "callback", f"forceclose {videoid}|{user_id}", "danger")],
    ]


def slider_rows(_, videoid, user_id, query, query_type, channel, fplay):
    tail = f"|{user_id}|{channel}|{fplay}"
    return [
        [
            (_["P_B_1"], "callback", f"MusicStream {videoid}|{user_id}|a|{channel}|{fplay}", "primary"),
            (_["P_B_2"], "callback", f"MusicStream {videoid}|{user_id}|v|{channel}|{fplay}", "primary"),
        ],
        [
            ("◁", "callback", _fit_cb(f"slider B|{query_type}|", query, tail), None),
            (_["CLOSE_BUTTON"], "callback", _fit_cb("forceclose ", query, f"|{user_id}"), "danger"),
            ("▷", "callback", _fit_cb(f"slider F|{query_type}|", query, tail), None),
        ],
    ]


# now-playing rows ---------------------------------------------------------------

def stream_rows(chat_id, duration):
    dur = str(duration or "00:00")
    return [
        # no style, copies the duration (e.g. 2:39)
        [(f"{DUR_BAR} {dur}", "copy", dur, None)],
        # same callbacks as the old stream_markup
        [
            ("", "callback", f"ADMIN Resume|{chat_id}", "primary", EMO_RESUME),
            ("", "callback", f"ADMIN Pause|{chat_id}", "primary", EMO_PAUSE),
            ("", "callback", f"ADMIN Stop|{chat_id}", "primary", EMO_STOP),
        ],
        [(LBL_VC, "url", TUNES_URL, "success")],
        # same callback as the old close button
        [(LBL_CLOSE, "callback", "close", "danger", EMO_CLOSE)],
    ]


# ─── sending ──────────────────────────────────────────────────────────────────

async def send_rich(client, chat_id: int, builders, reply_to: int = None):
    """
    builders = [(html_func(style) -> str, media_or_None), ...] tried in order.
    For each builder: current emoji tag style first, then the other one.
    Returns the sent Message, or None if every rich attempt failed.
    """
    global RICH_EMOJI_STYLE
    if InputRichMessage is None or not hasattr(client, "send_rich_message"):
        return None

    variants = []
    if reply_to:
        if ReplyParameters is not None:
            variants.append(dict(reply_parameters=ReplyParameters(message_id=reply_to)))
        variants.append(dict(reply_to_message_id=reply_to))
    variants.append({})  # last resort: no reply (e.g. the command message was deleted)

    last_err = None
    for html_func, media in builders:
        for style in (RICH_EMOJI_STYLE, 1 - RICH_EMOJI_STYLE):
            try:
                if media:
                    rich = InputRichMessage(html=html_func(style), media=media)
                else:
                    rich = InputRichMessage(html=html_func(style))
            except Exception as e:
                last_err = e
                break
            for extra in variants:
                try:
                    msg = await client.send_rich_message(chat_id, rich, **extra)
                    RICH_EMOJI_STYLE = style
                    return msg
                except Exception as e:
                    last_err = e
                    log.warning(
                        "send_rich_message failed (style=%s extra=%s): %r", style, list(extra), e
                    )
                    continue
    log.warning("rich message failed, using classic fallback: %r", last_err)
    return None


def now_playing_html(style, media_url, title, link, duration, mention, chat_id) -> str:
    cap = _title_html(title, link)
    fig = _media_figure(media_url, cap, STREAM_MEDIA_SPOILER) if media_url else cap
    return (
        f"<blockquote>{fig}</blockquote>"          # image + caption (song title) in a quote
        f"{_info_aside(style, duration, mention)}"  # pullquote: duration + requested by
        f"{rich_buttons(stream_rows(chat_id, duration))}"
    )


async def send_now_playing(
    _, original_chat_id, chat_id, link, title, duration, user_name, user_id, thumb=None
):
    """
    The rich 'now playing' card. stream.py calls this (it is injected at the bottom of
    this section). Returns the sent Message, or None -> stream.py sends the classic one.
    """
    media_url = STREAM_MEDIA_URL or getattr(config, "PLAYLIST_IMG_URL", None)
    mention = _mention_html(user_id, user_name)
    builders = [
        (lambda s: now_playing_html(s, media_url, title, link, duration, mention, chat_id), None),
        (lambda s: now_playing_html(s, None, title, link, duration, mention, chat_id), None),
    ]
    msg = await send_rich(app, original_chat_id, builders)
    if msg is not None:
        _freeze_markup(msg)
    return msg


async def _noop(*args, **kwargs):
    return None


def _freeze_markup(msg):
    """The live timer-bar updater keeps calling edit_reply_markup on db[chat]['mystic'],
    which adds a classic keyboard under the rich buttons. Make those calls do nothing
    on this message (rich buttons live inside the message itself)."""
    for name in ("edit_reply_markup", "edit_message_reply_markup", "edit_caption"):
        try:
            setattr(msg, name, _noop)
        except Exception:
            pass


def card_html(style, img, caption, rows, duration=None, spoiler=True) -> str:
    cap = caption(style) if callable(caption) else caption
    fig = _media_figure(img, cap, spoiler) if img else cap
    aside = _info_aside(style, duration) if duration else ""
    return f"<blockquote>{fig}</blockquote>{aside}{rich_buttons(rows)}"


async def send_card(client, chat_id, img, caption, rows, duration=None, spoiler=True, reply_to=None):
    """Image + caption in a quote, optional duration pullquote, rich buttons."""
    builders = [
        (lambda s: card_html(s, img, caption, rows, duration, spoiler), None),
        (lambda s: card_html(s, None, caption, rows, duration, spoiler), None),
    ]
    return await send_rich(client, chat_id, builders, reply_to=reply_to)


async def send_text_card(client, chat_id, text_classic, rows, reply_to=None):
    builders = [
        (
            lambda s: f"<blockquote><aside>{_classic_to_rich(text_classic, s)}</aside></blockquote>"
            f"{rich_buttons(rows)}",
            None,
        )
    ]
    return await send_rich(client, chat_id, builders, reply_to=reply_to)


# song card (search result / slider) -------------------------------------------

async def reply_card(message, img, title, duration, rows, spoiler=True):
    sent = await send_card(
        app, message.chat.id, img, _title_html(title), rows, duration, spoiler, reply_to=message.id
    )
    if sent is not None:
        return sent
    return await message.reply_photo(  # classic fallback
        photo=img,
        has_spoiler=spoiler,
        caption=_classic_caption(title, duration),
        reply_markup=classic_keyboard(rows),
    )


# playlist card (caption comes from the language file) ---------------------------

async def reply_card_html(message, img, cap_classic, rows):
    sent = await send_card(
        app,
        message.chat.id,
        img,
        lambda s: _classic_to_rich(cap_classic, s),
        rows,
        None,
        False,
        reply_to=message.id,
    )
    if sent is not None:
        return sent
    return await message.reply_photo(
        photo=img, caption=cap_classic, reply_markup=classic_keyboard(rows)
    )


async def show_livestream_prompt(mystic, _, track_id, user_id, mode, channel, fplay):
    rows = livestream_rows(_, track_id, user_id, mode, channel, fplay)
    sent = await send_text_card(app, mystic.chat.id, _["play_13"], rows)
    if sent is not None:
        try:
            await mystic.delete()
        except Exception:
            pass
        return sent
    return await mystic.edit_text(_["play_13"], reply_markup=classic_keyboard(rows))


async def replace_with_card(CallbackQuery, img, title, duration, rows, spoiler=True):
    """Slider ◁ ▷: a rich message can't be edited into another rich message with media,
    so send the new card and delete the old one (same approach as start.py)."""
    msg = CallbackQuery.message
    sent = await send_card(app, msg.chat.id, img, _title_html(title), rows, duration, spoiler)
    if sent is not None:
        try:
            await msg.delete()
        except Exception:
            pass
        return sent
    med = InputMediaPhoto(
        media=img, has_spoiler=spoiler, caption=_classic_caption(title, duration)
    )
    return await CallbackQuery.edit_message_media(
        media=med, reply_markup=classic_keyboard(rows)
    )


# hand the now-playing card to stream.py -----------------------------------------
try:
    _stream_mod = importlib.import_module("SHUKLAMUSIC.utils.stream.stream")
    _stream_mod.send_now_playing = send_now_playing
except Exception as _e:  # pragma: no cover
    log.warning("couldn't hook rich now-playing into stream.py: %r", _e)


# ─── MAIN PLAY COMMAND ────────────────────────────────────────────────────────

@app.on_message(
    filters.command(
        ["play", "vplay", "cplay", "cvplay", "playforce", "vplayforce", "cplayforce", "cvplayforce"],
        prefixes=["/", "!", "%", ",", "", ".", "@", "#"],
    )
    & filters.group
    & ~BANNED_USERS
)
@PlayWrapper
async def play_commnd(
    client,
    message: Message,
    _,
    chat_id,
    video,
    channel,
    playmode,
    url,
    fplay,
):
    mystic = await message.reply_text(
        _["play_2"].format(channel) if channel else _["play_1"]
    )
    plist_id = None
    slider = None
    plist_type = None
    spotify = None
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    audio_telegram = (
        (message.reply_to_message.audio or message.reply_to_message.voice)
        if message.reply_to_message
        else None
    )

    video_telegram = (
        (message.reply_to_message.video or message.reply_to_message.document)
        if message.reply_to_message
        else None
    )
    
    # ─── TELEGRAM AUDIO REPLY ───────────────────────────────────────────────
    if audio_telegram:
        if audio_telegram.file_size > 104857600:
            return await mystic.edit_text(_["play_5"])
        duration_min = seconds_to_min(audio_telegram.duration)
        if (audio_telegram.duration) > config.DURATION_LIMIT:
            return await mystic.edit_text(
                _["play_6"].format(config.DURATION_LIMIT_MIN, app.mention)
            )
        file_path = await Telegram.get_filepath(audio=audio_telegram)
        if await Telegram.download(_, message, mystic, file_path):
            message_link = await Telegram.get_link(message)
            file_name = await Telegram.get_filename(audio_telegram, audio=True)
            dur = await Telegram.get_duration(audio_telegram, file_path)
            details = {
                "title": file_name,
                "link": message_link,
                "path": file_path,
                "dur": dur,
            }

            try:
                await stream(
                    _,
                    mystic,
                    user_id,
                    details,
                    chat_id,
                    user_name,
                    message.chat.id,
                    streamtype="telegram",
                    forceplay=fplay,
                )
            except Exception as e:
                ex_type = type(e).__name__
                err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                return await mystic.edit_text(err)
            return await mystic.delete()
        return
    
    # ─── TELEGRAM VIDEO REPLY ───────────────────────────────────────────────
    elif video_telegram:
        if message.reply_to_message.document:
            try:
                ext = video_telegram.file_name.split(".")[-1]
                if ext.lower() not in formats:
                    return await mystic.edit_text(
                        _["play_7"].format(f"{' | '.join(formats)}")
                    )
            except:
                return await mystic.edit_text(
                    _["play_7"].format(f"{' | '.join(formats)}")
                )
        if video_telegram.file_size > config.TG_VIDEO_FILESIZE_LIMIT:
            return await mystic.edit_text(_["play_8"])
        file_path = await Telegram.get_filepath(video=video_telegram)
        if await Telegram.download(_, message, mystic, file_path):
            message_link = await Telegram.get_link(message)
            file_name = await Telegram.get_filename(video_telegram)
            dur = await Telegram.get_duration(video_telegram, file_path)
            details = {
                "title": file_name,
                "link": message_link,
                "path": file_path,
                "dur": dur,
            }
            try:
                await stream(
                    _,
                    mystic,
                    user_id,
                    details,
                    chat_id,
                    user_name,
                    message.chat.id,
                    video=True,
                    streamtype="telegram",
                    forceplay=fplay,
                )
            except Exception as e:
                ex_type = type(e).__name__
                err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                return await mystic.edit_text(err)
            return await mystic.delete()
        return
    
    # ─── URL PROVIDED ─────────────────────────────────────────────────────────
    elif url:
        # Check for YouTube URL
        if await YouTube.exists(url):
            if "playlist" in url:
                try:
                    details = await YouTube.playlist(
                        url,
                        config.PLAYLIST_FETCH_LIMIT,
                        message.from_user.id,
                    )
                except Exception as e:
                    print(e)
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "yt"
                if "&" in url:
                    plist_id = (url.split("=")[1]).split("&")[0]
                else:
                    plist_id = url.split("=")[1]
                img = config.PLAYLIST_IMG_URL
                has_spoiler = True
                cap = _["play_10"]
            elif "https://youtu.be" in url:
                videoid = url.split("/")[-1].split("?")[0]
                details, track_id = await YouTube.track(f"https://www.youtube.com/watch?v={videoid}")
                streamtype = "youtube"
                img = details["thumb"]
                cap = _["play_11"].format(
                    details["title"],
                    details["duration_min"],
                )
            else:
                try:
                    details, track_id = await YouTube.track(url)
                except Exception as e:
                    print(e)
                    return await mystic.edit_text(_["play_3"])
                streamtype = "youtube"
                img = details["thumb"]
                has_spoiler = True
                cap = _["play_11"].format(
                    details["title"],
                    details["duration_min"],
                )
        elif await Spotify.valid(url):
            spotify = True
            if not config.SPOTIFY_CLIENT_ID and not config.SPOTIFY_CLIENT_SECRET:
                return await mystic.edit_text(
                    "» sᴘᴏᴛɪғʏ ɪs ɴᴏᴛ sᴜᴘᴘᴏʀᴛᴇᴅ ʏᴇᴛ.\n\nᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ."
                )
            if "track" in url:
                try:
                    details, track_id = await Spotify.track(url)
                except:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "youtube"
                img = details["thumb"]
                has_spoiler = True
                cap = _["play_10"].format(details["title"], details["duration_min"])
            elif "playlist" in url:
                try:
                    details, plist_id = await Spotify.playlist(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "spplay"
                img = config.SPOTIFY_PLAYLIST_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)
            elif "album" in url:
                try:
                    details, plist_id = await Spotify.album(url)
                except:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "spalbum"
                img = config.SPOTIFY_ALBUM_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)
            elif "artist" in url:
                try:
                    details, plist_id = await Spotify.artist(url)
                except:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "spartist"
                img = config.SPOTIFY_ARTIST_IMG_URL
                cap = _["play_11"].format(message.from_user.first_name)
            else:
                return await mystic.edit_text(_["play_15"])
        elif await Apple.valid(url):
            if "album" in url:
                try:
                    details, track_id = await Apple.track(url)
                except:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "youtube"
                img = details["thumb"]
                has_spoiler = True
                cap = _["play_10"].format(details["title"], details["duration_min"])
            elif "playlist" in url:
                spotify = True
                try:
                    details, plist_id = await Apple.playlist(url)
                except:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "apple"
                cap = _["play_12"].format(app.mention, message.from_user.mention)
                img = url
            else:
                return await mystic.edit_text(_["play_3"])
        elif await Resso.valid(url):
            try:
                details, track_id = await Resso.track(url)
            except:
                return await mystic.edit_text(_["play_3"])
            streamtype = "youtube"
            img = details["thumb"]
            has_spoiler = True
            cap = _["play_10"].format(details["title"], details["duration_min"])
        elif await SoundCloud.valid(url):
            try:
                details, track_path = await SoundCloud.download(url)
            except:
                return await mystic.edit_text(_["play_3"])
            duration_sec = details["duration_sec"]
            if duration_sec > config.DURATION_LIMIT:
                return await mystic.edit_text(
                    _["play_6"].format(
                        config.DURATION_LIMIT_MIN,
                        app.mention,
                    )
                )
            try:
                await stream(
                    _,
                    mystic,
                    user_id,
                    details,
                    chat_id,
                    user_name,
                    message.chat.id,
                    streamtype="soundcloud",
                    forceplay=fplay,
                )
            except Exception as e:
                ex_type = type(e).__name__
                err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                return await mystic.edit_text(err)
            return await mystic.delete()
        else:
            # Generic URL / M3U8 / Index link
            try:
                await SHUKLA.stream_call(url)
            except NoActiveGroupCall:
                await mystic.edit_text(_["black_9"])
                return await app.send_message(
                    chat_id=config.LOGGER_ID,
                    text=_["play_17"],
                )
            except Exception as e:
                return await mystic.edit_text(_["general_2"].format(type(e).__name__))
            await mystic.edit_text(_["str_2"])
            try:
                await stream(
                    _,
                    mystic,
                    message.from_user.id,
                    url,
                    chat_id,
                    message.from_user.first_name,
                    message.chat.id,
                    video=video,
                    streamtype="index",
                    forceplay=fplay,
                )
            except Exception as e:
                ex_type = type(e).__name__
                err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                return await mystic.edit_text(err)
            return await play_logs(message, streamtype="M3u8 or Index Link")
    
    # ─── QUERY SEARCH (DRX API) ─────────────────────────────────────────────
    else:
        if len(message.command) < 2:
            buttons = botplaylist_markup(_)
            return await mystic.edit_text(
                _["play_18"],
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        
        query = message.text.split(None, 1)[1]
        if "-v" in query:
            query = query.replace("-v", "")
        
        # ─── CHECK ADMIN-ADDED SONGS FIRST ──────────────────────────────────
        added_song = await get_added_song(query)
        if added_song:
            # Play the admin-added URL directly
            try:
                await mystic.edit_text(f"🎵 **Playing admin-added song...**\n`{added_song['query']}`")
                details = {
                    "title": added_song.get("title", added_song["query"]),
                    "link": added_song["url"],
                    "path": added_song["url"],
                    "dur": 0,
                }
                await stream(
                    _,
                    mystic,
                    user_id,
                    details,
                    chat_id,
                    user_name,
                    message.chat.id,
                    video=video,
                    streamtype="index",
                    forceplay=fplay,
                )
            except Exception as e:
                ex_type = type(e).__name__
                err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                return await mystic.edit_text(err)
            await mystic.delete()
            return await play_logs(message, streamtype="Admin Added Song")
        
        # ─── MUSIC API SEARCH (fast path) ───────────────────────────────────
        slider = True
        direct_mode = str(playmode) == "Direct"
        cb_chat = "c" if channel else "g"
        cb_force = "f" if fplay else "d"

        if direct_mode:
            # 1) assistant joins the VC right now (background)
            # 2) Ritesh API: DB cache hit = instant, else search+stream in parallel
            # 3) if Ritesh fails, try DRX, then YouTube
            await SHUKLA.start_prejoin(chat_id)

            # ── Ritesh fast path (audio + video) ──
            details = await resolve_ritesh_fast(query, video=bool(video))
            if details:
                try:
                    await stream(
                        _,
                        mystic,
                        user_id,
                        details,
                        chat_id,
                        user_name,
                        message.chat.id,
                        video=video,
                        streamtype="ritesh",
                        forceplay=fplay,
                    )
                except Exception as e:
                    await SHUKLA.cancel_prejoin(chat_id)
                    ex_type = type(e).__name__
                    err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                    return await mystic.edit_text(err)
                await SHUKLA.finish_prejoin(chat_id)
                await mystic.delete()
                return await play_logs(message, streamtype="Ritesh API")

            # ── DRX fallback ──
            details = await resolve_drx_fast(query, video)
            if details:
                try:
                    await stream(
                        _,
                        mystic,
                        user_id,
                        details,
                        chat_id,
                        user_name,
                        message.chat.id,
                        video=video,
                        streamtype="drx",
                        forceplay=fplay,
                    )
                except Exception as e:
                    await SHUKLA.cancel_prejoin(chat_id)
                    ex_type = type(e).__name__
                    err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                    return await mystic.edit_text(err)
                await SHUKLA.finish_prejoin(chat_id)
                await mystic.delete()
                return await play_logs(message, streamtype="DRX API")
            # API had nothing -> YouTube fallback below (assistant stays joined)
        else:
            # selection card: warm the API cache in the background so the Audio/Video
            # button starts instantly, title/duration come from yt-dlp
            warm = _spawn(_safe(drx_search_songs(query, bool(video))))
            meta = await _safe(ytdlp_search_meta(query))
            if meta:
                duration_min = meta["duration"]
                if not duration_min:  # live / unknown -> try the API's value
                    api_songs = await warm
                    api_song = next((s for s in (api_songs or []) if s["id"] == meta["id"]), None)
                    duration_min = api_song["duration"] if api_song else "00:00"
                buttons = slider_rows(_, meta["id"], user_id, query, 0, cb_chat, cb_force)
                await mystic.delete()
                await reply_card(
                    message,
                    DEFAULT_THUMB,
                    meta["title"].title(),
                    duration_min,
                    buttons,
                    spoiler=True,
                )
                return await play_logs(message, streamtype="Searched on DRX")
            # yt-dlp unavailable -> old behaviour: card straight from the API result
            drx_results = await warm
            song = drx_results[0] if drx_results else None
            if song and pick_stream(song, video):
                buttons = slider_rows(_, song["id"], user_id, query, 0, cb_chat, cb_force)
                await mystic.delete()
                await reply_card(
                    message,
                    DEFAULT_THUMB,
                    song["title"].title(),
                    song["duration"],
                    buttons,
                    spoiler=True,
                )
                return await play_logs(message, streamtype="Searched on DRX")

        # Fallback to YouTube if the API has nothing / no stream url
        try:
            details, track_id = await YouTube.track(query)
        except:
            await SHUKLA.cancel_prejoin(chat_id)
            return await mystic.edit_text(_["play_3"])
        streamtype = "youtube"

    # ─── DIRECT PLAY MODE ────────────────────────────────────────────────────
    if str(playmode) == "Direct":
        if not plist_type:
            if details.get("duration_min"):
                duration_sec = time_to_seconds(details["duration_min"])
                if duration_sec > config.DURATION_LIMIT:
                    await SHUKLA.cancel_prejoin(chat_id)
                    return await mystic.edit_text(
                        _["play_6"].format(config.DURATION_LIMIT_MIN, app.mention)
                    )
            else:
                await SHUKLA.cancel_prejoin(chat_id)
                return await show_livestream_prompt(
                    mystic,
                    _,
                    track_id if 'track_id' in locals() else "",
                    user_id,
                    "v" if video else "a",
                    "c" if channel else "g",
                    "f" if fplay else "d",
                )
        try:
            await stream(
                _,
                mystic,
                user_id,
                details,
                chat_id,
                user_name,
                message.chat.id,
                video=video,
                streamtype=streamtype,
                spotify=spotify,
                forceplay=fplay,
            )
        except Exception as e:
            await SHUKLA.cancel_prejoin(chat_id)
            ex_type = type(e).__name__
            err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
            return await mystic.edit_text(err)
        await SHUKLA.finish_prejoin(chat_id)
        await mystic.delete()
        return await play_logs(message, streamtype=streamtype)
    else:
        if plist_type:
            ran_hash = "".join(
                random.choices(string.ascii_uppercase + string.digits, k=10)
            )
            lyrical[ran_hash] = plist_id
            buttons = playlist_rows(
                _,
                ran_hash,
                message.from_user.id,
                plist_type,
                "c" if channel else "g",
                "f" if fplay else "d",
            )
            await mystic.delete()
            await reply_card_html(message, img, cap, buttons)
            return await play_logs(message, streamtype=f"Playlist : {plist_type}")
        else:
            if slider:
                buttons = slider_rows(
                    _,
                    track_id if 'track_id' in locals() else "",
                    message.from_user.id,
                    query,
                    0,
                    "c" if channel else "g",
                    "f" if fplay else "d",
                )
                await mystic.delete()
                await reply_card(
                    message,
                    details.get("thumb", details.get("image", config.PLAYLIST_IMG_URL)),
                    details.get("title", "Unknown").title(),
                    details.get("duration_min", "00:00"),
                    buttons,
                    spoiler=True,
                )
                return await play_logs(message, streamtype=f"Searched on {'DRX' if streamtype == 'drx' else 'Youtube'}")
            else:
                buttons = track_rows(
                    _,
                    track_id if 'track_id' in locals() else "",
                    message.from_user.id,
                    "c" if channel else "g",
                    "f" if fplay else "d",
                )
                await mystic.delete()
                await reply_card(
                    message,
                    img,
                    details.get("title", "Unknown"),
                    details.get("duration_min", "00:00"),
                    buttons,
                    spoiler=False,
                )
                return await play_logs(message, streamtype=f"URL Searched Inline")


# ─── CALLBACK: MusicStream (for slider/track selection) ───────────────────────

@app.on_callback_query(filters.regex("MusicStream") & ~BANNED_USERS)
@languageCB
async def play_music(client, CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    vidid, user_id, mode, cplay, fplay = callback_request.split("|")
    if CallbackQuery.from_user.id != int(user_id):
        try:
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)
        except:
            return
    try:
        chat_id, channel = await get_channeplayCB(_, cplay, CallbackQuery)
    except:
        return
    user_name = CallbackQuery.from_user.first_name
    try:
        await CallbackQuery.message.delete()
        await CallbackQuery.answer()
    except:
        pass
    mystic = await CallbackQuery.message.reply_text(
        _["play_2"].format(channel) if channel else _["play_1"]
    )
    
    video = True if mode == "v" else None
    ffplay = True if fplay == "f" else None

    # assistant joins the VC immediately while the stream url is being fetched
    await SHUKLA.start_prejoin(chat_id)

    # Music API for the stream url (song cached from the card that was clicked,
    # else search by id). Title/duration: yt-dlp values saved by /play if we have them.
    song = _DRX_CACHE.get(vidid)
    stream_url = pick_stream(song, video)
    if not stream_url:
        # not cached for this type (audio/video): ask the API with the right result type
        drx_results = await drx_search_songs(vidid, bool(video))
        if drx_results:
            song = next((x for x in drx_results if x["id"] == vidid), None)
            stream_url = pick_stream(song, video)
    if stream_url:
        details = build_drx_details(song, _META_CACHE.get(song["id"]), stream_url)
        try:
            await stream(
                _,
                mystic,
                CallbackQuery.from_user.id,
                details,
                chat_id,
                user_name,
                CallbackQuery.message.chat.id,
                video,
                streamtype="drx",
                forceplay=ffplay,
            )
        except Exception as e:
            await SHUKLA.cancel_prejoin(chat_id)
            ex_type = type(e).__name__
            err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
            return await mystic.edit_text(err)
        await SHUKLA.finish_prejoin(chat_id)
        return await mystic.delete()

    # Fallback to YouTube
    try:
        details, track_id = await YouTube.track(vidid, True)
    except:
        await SHUKLA.cancel_prejoin(chat_id)
        return await mystic.edit_text(_["play_3"])
    if details["duration_min"]:
        duration_sec = time_to_seconds(details["duration_min"])
        if duration_sec > config.DURATION_LIMIT:
            await SHUKLA.cancel_prejoin(chat_id)
            return await mystic.edit_text(
                _["play_6"].format(config.DURATION_LIMIT_MIN, app.mention)
            )
    else:
        await SHUKLA.cancel_prejoin(chat_id)
        return await show_livestream_prompt(
            mystic,
            _,
            track_id,
            CallbackQuery.from_user.id,
            mode,
            "c" if cplay == "c" else "g",
            "f" if fplay == "f" else "d",
        )
    video = True if mode == "v" else None
    ffplay = True if fplay == "f" else None
    try:
        await stream(
            _,
            mystic,
            CallbackQuery.from_user.id,
            details,
            chat_id,
            user_name,
            CallbackQuery.message.chat.id,
            video,
            streamtype="youtube",
            forceplay=ffplay,
        )
    except Exception as e:
        await SHUKLA.cancel_prejoin(chat_id)
        ex_type = type(e).__name__
        err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
        return await mystic.edit_text(err)
    await SHUKLA.finish_prejoin(chat_id)
    return await mystic.delete()


@app.on_callback_query(filters.regex("SHUKLAmousAdmin") & ~BANNED_USERS)
async def SHUKLAmous_check(client, CallbackQuery):
    try:
        await CallbackQuery.answer(
            "» ʀᴇᴠᴇʀᴛ ʙᴀᴄᴋ ᴛᴏ ᴜsᴇʀ ᴀᴄᴄᴏᴜɴᴛ :\n\nᴏᴘᴇɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ sᴇᴛᴛɪɴɢs.\n-> ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs\n-> ᴄʟɪᴄᴋ ᴏɴ ʏᴏᴜʀ ɴᴀᴍᴇ\n-> ᴜɴᴄʜᴇᴄᴋ ᴀɴᴏɴʏᴍᴏᴜs ᴀᴅᴍɪɴ ᴘᴇʀᴍɪssɪᴏɴs.",
            show_alert=True,
        )
    except:
        pass


@app.on_callback_query(filters.regex("SHUKLAPlaylists") & ~BANNED_USERS)
@languageCB
async def play_playlists_command(client, CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    (
        videoid,
        user_id,
        ptype,
        mode,
        cplay,
        fplay,
    ) = callback_request.split("|")
    if CallbackQuery.from_user.id != int(user_id):
        try:
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)
        except:
            return
    try:
        chat_id, channel = await get_channeplayCB(_, cplay, CallbackQuery)
    except:
        return
    user_name = CallbackQuery.from_user.first_name
    await CallbackQuery.message.delete()
    try:
        await CallbackQuery.answer()
    except:
        pass
    mystic = await CallbackQuery.message.reply_text(
        _["play_2"].format(channel) if channel else _["play_1"]
    )
    videoid = lyrical.get(videoid)
    video = True if mode == "v" else None
    ffplay = True if fplay == "f" else None
    spotify = True
    if ptype == "yt":
        spotify = False
        try:
            result = await YouTube.playlist(
                videoid,
                config.PLAYLIST_FETCH_LIMIT,
                CallbackQuery.from_user.id,
                True,
            )
        except:
            return await mystic.edit_text(_["play_3"])
    if ptype == "spplay":
        try:
            result, spotify_id = await Spotify.playlist(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
    if ptype == "spalbum":
        try:
            result, spotify_id = await Spotify.album(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
    if ptype == "spartist":
        try:
            result, spotify_id = await Spotify.artist(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
    if ptype == "apple":
        try:
            result, apple_id = await Apple.playlist(videoid, True)
        except:
            return await mystic.edit_text(_["play_3"])
    try:
        await stream(
            _,
            mystic,
            user_id,
            result,
            chat_id,
            user_name,
            CallbackQuery.message.chat.id,
            video,
            streamtype="playlist",
            spotify=spotify,
            forceplay=ffplay,
        )
    except Exception as e:
        ex_type = type(e).__name__
        err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
        return await mystic.edit_text(err)
    return await mystic.delete()


@app.on_callback_query(filters.regex("slider") & ~BANNED_USERS)
@languageCB
async def slider_queries(client, CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    (
        what,
        rtype,
        query,
        user_id,
        cplay,
        fplay,
    ) = callback_request.split("|")
    if CallbackQuery.from_user.id != int(user_id):
        try:
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)
        except:
            return
    what = str(what)
    rtype = int(rtype)

    if what == "F":
        query_type = 0 if rtype == 9 else int(rtype + 1)
    elif what == "B":
        query_type = 9 if rtype == 0 else int(rtype - 1)
    else:
        return
    try:
        await CallbackQuery.answer(_["playcb_2"])
    except:
        pass

    drx_results = await drx_search_songs(query)
    if drx_results and query_type < len(drx_results):
        song = drx_results[query_type]
        title = song["title"].title()
        duration_min = song["duration"]
        thumbnail = DEFAULT_THUMB
        vid = song["id"]
    else:
        # Fallback to YouTube slider
        title, duration_min, thumbnail, vid = await YouTube.slider(query, query_type)
        title = title.title()

    buttons = slider_rows(_, vid, user_id, query, query_type, cplay, fplay)
    return await replace_with_card(CallbackQuery, thumbnail, title, duration_min, buttons)
