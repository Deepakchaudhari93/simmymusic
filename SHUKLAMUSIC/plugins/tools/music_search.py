import asyncio
import html
import logging
import os
import random
import re
import shutil
import tempfile
import time
from urllib.parse import quote_plus

import aiohttp
from pyrogram import filters
from pyrogram.enums import ChatAction, ParseMode
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputMediaAudio,
    InputTextMessageContent,
    Message,
)

from SHUKLAMUSIC import app

try:
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

try:
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

log = logging.getLogger("music_search")

# ───────────────────────────── CONFIG ─────────────────────────────
API_KEY = os.getenv("STALKER_API_KEY", "stalker697577fdd58a5477ca0dbc15")
API_URL = "https://itzstalker.xyz/music/search={q}&result=audio&key={key}"

BOT_TAG = "@nyxxai"
RESULTS = 5
DELETE_AFTER = 600            # options message auto-delete (10 min)
COOLDOWN_SECONDS = 5
MAX_QUERY_LEN = 100
USE_RICH_BUTTONS = True       # try rich <tg-button> message first, fall back to normal keyboard
# Inline mode needs a chat where the bot can upload the audio once to get a file_id.
# Set MUSIC_STORAGE_CHAT (or LOGGER_ID in config) to a group/channel id the bot is in.
# If neither is set it tries the user's own DM with the bot (user must have started it).
try:
    from config import LOGGER_ID as _LOGGER_ID
except Exception:  # pragma: no cover
    _LOGGER_ID = 0
STORAGE_CHAT = int(os.getenv("MUSIC_STORAGE_CHAT") or _LOGGER_ID or 0)

EMOJI_ANALYSING = 5573473356579078196
EMOJI_DOWNLOADED = 5537203062138994712
EMOJI_NOT_FOUND = 5535088426400874513

# ───────────────────────────── HELPERS ─────────────────────────────
_bg_tasks: set = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def _emoji(emoji_id: int, fallback: str = "⭐") -> str:
    return f"<emoji id={emoji_id}>{fallback}</emoji>"


_NOISE = [
    "full song with lyrics", "full video song", "official music video", "official lyric video",
    "official video", "official audio", "lyrical video", "lyric video", "full video", "full song",
    "full audio", "song video", "video song", "with lyrics", "lyrics", "lyrical", "audio",
]
_NOISE_RE = re.compile(
    r"[\(\[]?\s*\b(?:" + "|".join(re.escape(n) for n in _NOISE) + r")\b\s*[\)\]]?",
    re.IGNORECASE,
)


def clean_title(raw: str, limit: int = 45) -> str:
    """'"Tum Hi Ho" Aashiqui 2 Full Song With Lyrics | Aditya Roy Kapur' -> '"Tum Hi Ho" Aashiqui 2'"""
    t = re.split(r"\s*[|•]\s*", raw or "", maxsplit=1)[0]
    t = _NOISE_RE.sub(" ", t)
    t = re.sub(r"[\(\[]\s*[\)\]]", " ", t)          # empty brackets
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—:,.")
    t = t or (raw or "Unknown").strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _secs(d: str) -> int:
    try:
        n = 0
        for p in (d or "0").split(":"):
            n = n * 60 + int(p)
        return n
    except Exception:
        return 0


def _caption(title: str) -> str:
    return f"🎵 <b>{html.escape(title)}</b>\n\n<i>by {BOT_TAG}</i>"


_search_cache: dict = {}  # query -> (ts, results)


async def search_songs(query: str, timeout_s: int = 25) -> list:
    key = query.lower()
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    res = await _search_songs(query, timeout_s)
    if res:
        if len(_search_cache) > 500:
            _search_cache.clear()
        _search_cache[key] = (time.time(), res)
    return res


async def _search_songs(query: str, timeout_s: int) -> list:
    url = API_URL.format(q=quote_plus(query), key=API_KEY)
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(url) as r:
            data = await r.json(content_type=None)
    items = [x for x in (data.get("result") or []) if x.get("stream")]
    out = []
    for x in items[:RESULTS]:
        thumbs = x.get("thumbnails") or [{}]
        out.append(
            {
                "id": x.get("id"),
                "title": clean_title(x.get("title", "")),
                "stream": x["stream"],
                "duration": _secs(x.get("duration")),
                "duration_text": x.get("duration", ""),
                "channel": (x.get("channel") or {}).get("name", ""),
                "thumb": thumbs[0].get("url"),
            }
        )
    return out


async def _download(url: str, path: str) -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return False
                with open(path, "wb") as f:
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        f.write(chunk)
        return os.path.getsize(path) > 0
    except Exception as e:
        log.warning("download failed: %r", e)
        return False


async def _fetch_audio(item: dict, work_dir: str):
    audio = os.path.join(work_dir, f"{item['id']}.mp3")
    if not await _download(item["stream"], audio):
        raise RuntimeError("audio download failed")
    thumb = None
    if item.get("thumb"):
        tp = os.path.join(work_dir, "thumb.jpg")
        if await _download(item["thumb"], tp):
            thumb = tp
    return audio, thumb


async def _delete_later(client, chat_id: int, msg_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


# ───────────────────────────── THINKING DRAFT (DM) ─────────────────────────────
RICH_EMOJI_STYLE = 1


def _thinking_html(emoji_id: int, text: str, style: int) -> str:
    icon = (
        f"<emoji id={emoji_id}>⭐</emoji>"
        if style == 0
        else f'<tg-emoji emoji-id="{emoji_id}">⭐</tg-emoji>'
    )
    return f"<tg-thinking>{icon} {html.escape(text)}</tg-thinking>"


class Thinking:
    """Same rich 'thinking' draft as download.py, with edit-in-place fallback."""

    def __init__(self, client, chat_id: int):
        self.client, self.chat_id = client, chat_id
        try:
            self.draft_id = client.rnd_id()
        except Exception:
            self.draft_id = random.randint(1, 2**31 - 1)
        self.style = RICH_EMOJI_STYLE
        self.rich_ok = InputRichMessage is not None and hasattr(client, "send_rich_message_draft")
        self.current = None
        self._flipped = False
        self.fallback_msg = None
        self._keepalive = None

    async def _send_draft(self, emoji_id, text):
        await self.client.send_rich_message_draft(
            chat_id=self.chat_id,
            draft_id=self.draft_id,
            rich_message=InputRichMessage(html=_thinking_html(emoji_id, text, self.style)),
        )

    async def show(self, emoji_id: int, text: str):
        global RICH_EMOJI_STYLE
        self.current = (emoji_id, text)
        if self.rich_ok:
            try:
                await self._send_draft(emoji_id, text)
                if self._keepalive is None:
                    self._keepalive = asyncio.create_task(self._keepalive_loop())
                return
            except Exception as e:
                if not self._flipped:
                    self._flipped = True
                    self.style = 1 - self.style
                    try:
                        await self._send_draft(emoji_id, text)
                        RICH_EMOJI_STYLE = self.style
                        return
                    except Exception as e2:
                        e = e2
                log.warning("rich draft failed, using edit fallback: %r", e)
                self.rich_ok = False
        body = f"{_emoji(emoji_id)} {html.escape(text)}"
        try:
            if self.fallback_msg is None:
                self.fallback_msg = await self.client.send_message(self.chat_id, body, parse_mode=ParseMode.HTML)
            else:
                await self.fallback_msg.edit_text(body, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.debug("status update failed: %r", e)

    async def _keepalive_loop(self):
        try:
            while True:
                await asyncio.sleep(20)
                if self.rich_ok and self.current:
                    try:
                        await self._send_draft(*self.current)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass

    async def close(self):
        if self._keepalive:
            self._keepalive.cancel()
        if self.fallback_msg is not None:
            try:
                await self.fallback_msg.delete()
            except Exception:
                pass


# ───────────────────────────── OPTIONS MESSAGE ─────────────────────────────
_sessions: dict = {}      # token -> {"user": id, "results": [...], "ts": time}
_inline_cache: dict = {}  # result_id -> item
_last_request: dict = {}
_active_users: set = set()

NOTE = f"This message will be deleted in {DELETE_AFTER // 60} minutes"


def _prune():
    now = time.time()
    for tok, s in list(_sessions.items()):
        if now - s["ts"] > DELETE_AFTER + 60:
            _sessions.pop(tok, None)
    if len(_inline_cache) > 2000:
        for k in list(_inline_cache)[:1000]:
            _inline_cache.pop(k, None)


def _options_rich_html(token: str, results: list) -> str:
    rows = "".join(
        f'<tg-button-row><tg-button type="callback" data="mdl|{token}|{i}" style="primary">'
        f"{html.escape(r['title'])}</tg-button></tg-button-row>"
        for i, r in enumerate(results)
    )
    close = (
        f'<tg-button-row><tg-button type="callback" data="mclose|{token}" style="danger">'
        "Close</tg-button></tg-button-row>"
    )
    return f"<p><b>Found!! Pick a song</b></p>{rows}{close}<blockquote>{NOTE}</blockquote>"


def _options_keyboard(token: str, results: list) -> InlineKeyboardMarkup:
    def btn(text, data, style_name):
        if ButtonStyle is not None:
            try:
                return InlineKeyboardButton(text, callback_data=data, style=getattr(ButtonStyle, style_name))
            except Exception:
                pass
        return InlineKeyboardButton(text, callback_data=data)

    rows = [[btn(r["title"], f"mdl|{token}|{i}", "PRIMARY")] for i, r in enumerate(results)]
    rows.append([btn("Close", f"mclose|{token}", "DANGER")])
    return InlineKeyboardMarkup(rows)


async def _send_options(client, chat_id: int, token: str, results: list):
    if USE_RICH_BUTTONS and InputRichMessage is not None and hasattr(client, "send_rich_message"):
        for _ in range(2):  # 2nd pass flips the emoji/tag style if needed
            try:
                return await client.send_rich_message(
                    chat_id, InputRichMessage(html=_options_rich_html(token, results))
                )
            except Exception as e:
                log.warning("rich options failed, using keyboard fallback: %r", e)
                break
    return await client.send_message(
        chat_id,
        f"<b>Found!! Pick a song</b>\n\n<blockquote>{NOTE}</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=_options_keyboard(token, results),
    )


# ───────────────────────────── DM: PLAIN TEXT HANDLER ─────────────────────────────
_LINK_RE = re.compile(
    r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|be|me|org|net|io|in|ly|co|app|tv|gg)\b(?:/\S*)?",
    re.IGNORECASE,
)


def _plain_text(_, __, m: Message) -> bool:
    t = (m.text or "").strip()
    if not t or t.startswith("/") or len(t) > MAX_QUERY_LEN:
        return False
    if _LINK_RE.search(t):
        return False
    for ent in m.entities or []:
        if getattr(ent, "url", None) or "URL" in str(ent.type) or "BOT_COMMAND" in str(ent.type):
            return False
    return True


plain_text = filters.create(_plain_text)


async def _handle_query(client, message: Message, query: str):
    user_id = message.from_user.id
    chat_id = message.chat.id
    thinking = Thinking(client, chat_id)
    try:
        await thinking.show(EMOJI_ANALYSING, "Analysing....")
        await asyncio.sleep(0.8)
        await thinking.show(EMOJI_ANALYSING, f"Oh, you want to download {query}...")
        results = await search_songs(query)
        if not results:
            await thinking.close()
            await message.reply_text(
                f"<b>{_emoji(EMOJI_NOT_FOUND)} Didn't find anything for</b> <code>{html.escape(query)}</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        await thinking.show(EMOJI_DOWNLOADED, "Found!! sending options....")
        await asyncio.sleep(0.6)

        token = "%08x" % random.getrandbits(32)
        _prune()
        _sessions[token] = {"user": user_id, "results": results, "ts": time.time()}
        sent = await _send_options(client, chat_id, token, results)
        await thinking.close()
        _spawn(_delete_later(client, chat_id, sent.id, DELETE_AFTER))
    except Exception:
        log.exception("music search failed")
        await thinking.close()
        try:
            await message.reply_text(
                f"<b>{_emoji(EMOJI_NOT_FOUND)} Something went wrong, please try again later.</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    finally:
        _active_users.discard(user_id)


# own handler group + background task + no stop_propagation -> never blocks other commands/handlers
@app.on_message(
    filters.private & filters.text & plain_text & ~filters.bot & ~filters.via_bot,
    group=-11,
)
async def music_plain_text(client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    if uid in _active_users:
        return
    now = time.monotonic()
    if now - _last_request.get(uid, 0) < COOLDOWN_SECONDS:
        return
    _last_request[uid] = now
    _active_users.add(uid)
    _spawn(_handle_query(client, message, message.text.strip()))


# ───────────────────────────── DM: BUTTON CALLBACKS ─────────────────────────────
@app.on_callback_query(filters.regex(r"^mclose\|"))
async def music_close(client, q):
    tok = q.data.split("|")[1]
    sess = _sessions.get(tok)
    if sess and q.from_user.id != sess["user"]:
        return await q.answer("This list isn't for you.", show_alert=True)
    _sessions.pop(tok, None)
    await q.answer()
    try:
        await q.message.delete()
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^mdl\|"))
async def music_pick(client, q):
    _, tok, idx = q.data.split("|")
    sess = _sessions.get(tok)
    if not sess:
        return await q.answer("This list expired, send the song name again.", show_alert=True)
    if q.from_user.id != sess["user"]:
        return await q.answer("This list isn't for you.", show_alert=True)
    item = sess["results"][int(idx)]
    _sessions.pop(tok, None)
    await q.answer()

    chat_id = q.message.chat.id
    try:
        await q.message.delete()  # remove buttons + note
    except Exception:
        pass

    thinking = Thinking(client, chat_id)
    work_dir = tempfile.mkdtemp(prefix="ms_")
    try:
        await thinking.show(EMOJI_ANALYSING, f"Sending {item['title']}....")
        audio, thumb = await _fetch_audio(item, work_dir)
        try:
            await client.send_chat_action(chat_id, ChatAction.UPLOAD_AUDIO)
        except Exception:
            pass
        kwargs = dict(
            audio=audio,
            caption=_caption(item["title"]),
            parse_mode=ParseMode.HTML,
            title=item["title"],
            performer=BOT_TAG.lstrip("@"),
            duration=item["duration"],
        )
        if thumb:
            kwargs["thumb"] = thumb
        await client.send_audio(chat_id, **kwargs)  # not auto-deleted
    except Exception:
        log.exception("send song failed")
        try:
            await client.send_message(
                chat_id,
                f"<b>{_emoji(EMOJI_NOT_FOUND)} Couldn't send that song, try another one.</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    finally:
        await thinking.close()
        shutil.rmtree(work_dir, ignore_errors=True)
