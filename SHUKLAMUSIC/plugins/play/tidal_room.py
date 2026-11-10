"""DM listening rooms for the Tidal mini app (itzstalker.xyz/tidal).
Drop this file into SHUKLAMUSIC/plugins/play/ . Only private chats are handled here;
the group commands in play.py stay untouched.

bot.php returns structured data (see its play / rank / leaderboard acts):
  play        -> view = "playing" | "queued", title, duration, thumb, position, room_id, join, share
  rank        -> view = "rank", name, level, rank, plays, hours
  leaderboard -> view = "leaderboard", top = [{uid, name, level, hours, songs}, ...]
  anything else -> "text" is shown in a plain blockquote
"""
import asyncio
import html
import logging
import random

import aiohttp
from pyrogram import filters, enums
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle,
                            InlineQueryResultPhoto, InputTextMessageContent)

try:  # kurigram: rich messages
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

try:  # kurigram: coloured buttons (classic keyboard only)
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

from SHUKLAMUSIC import app
from config import BANNED_USERS

log = logging.getLogger("tidal_room")

TIDAL_API = "https://itzstalker.xyz/tidal/bot.php"
TIDAL_SECRET = "a1ee66c027ce85672c85923b7804808c"  # must equal BOT_SECRET in config.php
BOT_URL = "https://t.me/TidalXMusicBot"
ZW = "\u2060"  # invisible label for buttons that only have an emoji

# ================================
#        CUSTOM EMOJI IDS
# ================================
E = {
    "fetch1": 5573473356579078196,
    "fetch2": 5573333417954639880,
    "delete": 5841541824803509441,
    "close": 5775981206319402773,
    "end": 5891206318353551398,
    "play": 5890997763331591703,
    "mute": 6005986106703613755,
    "sound": 5891249688933305846,
    "video": 5994502837327892086,
    "music": 5967548335542767952,
    "levelup": 5884290437459480896,
    "queue": 5794182096603847292,
    "camera": 5794303034292968945,
    "skip": 6102860691792732091,
    "pause": 6102535751746987490,
    # no dedicated ID left in the list for these two, so they reuse one; swap if you have IDs
    "room_door": 5794426162415409242,
    "link": 5793905801357695657,
}

# level emojis 1..10 (keycap list)
LEVEL_IDS = [
    5794031944547178894, 5793901252987330401, 5794066823976592976, 5794235255414069703,
    5794030595927448202, 5794426162415409242, 5793905801357695657, 5794310013614824017,
    6102600519853810919, 6100268816468550688,
]

RICH_STYLE = 1  # 1 = <tg-emoji>, 0 = <emoji>; auto-detected on first successful send
PLACEHOLDER = "⭐"  # visible text inside an emoji tag (only shown where custom emoji are not allowed)


def _level_id(level) -> int:
    try:
        n = int(level or 1)
    except (TypeError, ValueError):
        n = 1
    return LEVEL_IDS[min(max(n, 1), 10) - 1]


def _safe_url(u) -> str:
    """Telegram rejects the whole message (BUTTON_URL_INVALID) on one bad url, so check it first."""
    u = (u or "").strip()
    if u.startswith(("https://", "http://", "tg://")) and " " not in u and len(u) <= 2048:
        return u
    log.warning("invalid button url %r, using bot link", u)
    return BOT_URL


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def _rich_emo(emoji_id: int, style: int) -> str:
    if style == 0:
        return f"<emoji id={emoji_id}>{PLACEHOLDER}</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">{PLACEHOLDER}</tg-emoji>'


def _classic_emo(emoji_id: int) -> str:
    return f'<emoji id="{emoji_id}">{PLACEHOLDER}</emoji>'


def _nl(text: str) -> str:
    return text.replace("\n", "<br>")


# ================================
#        BUTTON HELPERS
# ================================
# row item = (text, kind, value, style, emoji_id)
#   kind     : "url" | "callback_data"
#   style    : "primary" | "success" | "danger" | None
#   emoji_id : optional custom emoji, placed INSIDE the rich button label
def _btn_url(text, url, style=None, emoji=None):
    return (text, "url", url, style, emoji)


def _btn_cb(text, data, style=None, emoji=None):
    return (text, "callback_data", data, style, emoji)


def _btn_label(text, emoji=None) -> str:
    """Rich button label = optional custom emoji + optional text. Emoji-only is allowed."""
    parts = []
    if emoji:
        parts.append(_rich_emo(int(emoji), RICH_STYLE))
    if text:
        parts.append(_e(text))
    return " ".join(parts)


def rich_buttons(rows) -> str:
    """Buttons live inside the rich HTML: <tg-button-row> + <tg-button>."""
    out = ""
    for row in rows:
        out += "<tg-button-row>"
        for text, kind, value, style, emoji in row:
            attrs = f' style="{style}"' if style else ""
            label = _btn_label(text, emoji)
            if kind == "url":
                out += f'<tg-button type="url" url="{html.escape(value)}"{attrs}>{label}</tg-button>'
            else:
                out += f'<tg-button type="callback_data" data="{html.escape(value)}"{attrs}>{label}</tg-button>'
        out += "</tg-button-row>"
    return out


def classic_keyboard(rows):
    """Normal inline keyboard, used only when the rich message is rejected.
    Classic buttons cannot show custom emoji in the label, so emoji-only buttons get an invisible label."""
    kb = []
    for row in rows:
        line = []
        for text, kind, value, style, _emoji in row:
            kwargs = {"url": value} if kind == "url" else {"callback_data": value}
            label = text or ZW
            btn = None
            if style and ButtonStyle is not None:
                try:
                    btn = InlineKeyboardButton(label, style=getattr(ButtonStyle, style.upper()), **kwargs)
                except Exception:
                    btn = None
            if btn is None:
                btn = InlineKeyboardButton(label, **kwargs)
            line.append(btn)
        if line:
            kb.append(line)
    return InlineKeyboardMarkup(kb)


# ================================
#        RICH SENDING
# ================================
def _rich_fn(body_fn, rows):
    def build(style: int) -> str:
        return _nl(body_fn(lambda i: _rich_emo(i, style))) + rich_buttons(rows)
    return build


async def send_rich(client, chat_id, html_fns, reply_to=None):
    """Try each html builder with both emoji tag styles. Returns the Message or None."""
    global RICH_STYLE
    if InputRichMessage is None or not hasattr(client, "send_rich_message"):
        return None
    if reply_to:
        extras = []
        if ReplyParameters is not None:
            extras.append(dict(reply_parameters=ReplyParameters(message_id=reply_to)))
        extras.append(dict(reply_to_message_id=reply_to))
    else:
        extras = [{}]

    for fn in html_fns:
        for style in (RICH_STYLE, 1 - RICH_STYLE):
            try:
                rich = InputRichMessage(html=fn(style))
            except Exception as e:
                log.warning("rich build failed: %r", e)
                break
            for extra in extras:
                try:
                    msg = await client.send_rich_message(chat_id, rich, **extra)
                    RICH_STYLE = style
                    return msg
                except Exception as e:
                    log.warning("send_rich_message failed (style=%s): %r", style, e)
    return None


async def send_ui(client, chat_id, body_fn, rows, reply_to=None):
    """Rich message first; classic message with a normal keyboard as the last resort."""
    sent = await send_rich(client, chat_id, [_rich_fn(body_fn, rows)], reply_to=reply_to)
    if sent is not None:
        return sent
    try:
        return await client.send_message(
            chat_id,
            body_fn(_classic_emo),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=classic_keyboard(rows) if rows else None,
            reply_to_message_id=reply_to,
        )
    except Exception as e:
        log.warning("classic fallback failed: %r", e)
        return None


# ================================
#        RICH THINKING DRAFT
# ================================
class Thinking:
    """'Searching...' rich draft (sendRichMessageDraft). Drafts live ~30s, so a keep-alive re-sends it."""

    def __init__(self, client, chat_id: int):
        self.client = client
        self.chat_id = chat_id
        try:
            self.draft_id = client.rnd_id()
        except Exception:
            self.draft_id = random.randint(1, 2**31 - 1)
        self.ok = InputRichMessage is not None and hasattr(client, "send_rich_message_draft")
        self.current = None
        self._task = None

    async def _send(self, emoji_id: int, text: str):
        icon = _rich_emo(emoji_id, RICH_STYLE)
        await self.client.send_rich_message_draft(
            chat_id=self.chat_id,
            draft_id=self.draft_id,
            rich_message=InputRichMessage(html=f"<tg-thinking>{icon} {text}</tg-thinking>"),
        )

    async def show(self, emoji_id: int, text: str):
        if not self.ok:
            return
        self.current = (emoji_id, text)
        try:
            await self._send(emoji_id, text)
        except Exception as e:
            log.debug("draft failed: %r", e)
            self.ok = False
            return
        if self._task is None:
            self._task = asyncio.create_task(self._keepalive())

    async def _keepalive(self):
        while True:
            await asyncio.sleep(20)
            if self.current:
                try:
                    await self._send(*self.current)
                except Exception:
                    pass

    async def close(self):
        if self._task:
            self._task.cancel()


# ================================
#        MESSAGE BODIES
# ================================
def _text_body(text: str):
    def body(em):
        return f"<blockquote>{text}</blockquote>"
    return body


def _playing_body(d):
    title = _e(d.get("title") or "Unknown")
    dur = _e(d.get("duration") or "--:--")
    rid = _e(d.get("room_id") or "-")
    thumb = d.get("thumb") or ""

    def body(em):
        # first line: title link carrying the thumbnail, so Telegram shows the preview above the text
        link = f'<a href="{html.escape(thumb)}">{title}</a>' if thumb else f"<b>{title}</b>"
        return (
            f"{link}\n"
            f'<blockquote expandable>{em(E["music"])} ᴛιᴛʟє: {title}\n'
            f'{em(E["play"])} ᴅᴜʀᴧᴛιση: {dur} ᴍιη\n'
            f'{em(E["room_door"])} Room ID: <code>{rid}</code></blockquote>'
        )
    return body


def _queued_body(d):
    pos = _e(d.get("position") or 1)
    dur = _e(d.get("duration") or "--:--")

    def body(em):
        return (
            f'{em(E["queue"])} Aᴅᴅᴇᴅ Tᴏ Qᴜᴇᴜᴇ Aᴛ #{pos}\n'
            f'{em(E["sound"])} 10 Second Timer\n'
            f'{em(E["fetch2"])} ᴅᴜʀᴀᴛɪᴏɴ: {dur} ᴍιηᴜᴛєs'
        )
    return body


def _leaderboard_body(d):
    top = (d.get("top") or [])[:10]

    def body(em):
        lines = []
        for i, row in enumerate(top, 1):
            name = _e(row.get("name") or "User")
            uid = row.get("uid")
            lvl = row.get("level") or 1
            mention = f'<a href="tg://user?id={uid}">{name}</a>' if uid else name
            lines.append(f"{em(_level_id(lvl))} {i}. {mention} <i>Lv {_e(lvl)}</i>")
        listed = "\n".join(lines) or "<i>No listeners yet. Be the first with /play!</i>"
        return (
            "<blockquote><b><u>Top 10 listeners</u></b></blockquote>"
            f"<blockquote>{listed}</blockquote>"
        )
    return body


def _rank_body(d):
    name = _e(d.get("name") or "")
    lvl = _e(d.get("level") or 1)
    rank = _e(d.get("rank") or "-")
    plays = _e(d.get("plays") or 0)
    hours = _e(d.get("hours") or 0)

    def body(em):
        return (
            f'<blockquote><b>{name}</b>\n'
            f'{em(E["levelup"])} <i>Level</i> <b>{lvl}</b>\n'
            f'{em(E["queue"])} <i>Global rank</i> <b>#{rank}</b>\n'
            f'{em(E["music"])} <i>Songs played</i> <b>{plays}</b>\n'
            f'{em(E["fetch2"])} <i>Listened</i> <b>{hours}h</b></blockquote>'
        )
    return body


# ================================
#        BUTTON LAYOUTS
# ================================
def _playing_rows(rid, join, share):
    return [
        [_btn_url("Join room", join, "success", E["room_door"])],
        [
            _btn_cb("", f"tl:skip:{rid}", None, E["skip"]),
            _btn_cb("", f"tl:end:{rid}", None, E["end"]),
            _btn_cb("", f"tl:pause:{rid}", None, E["pause"]),
        ],
        [_btn_url("Share Room link", share, "danger", E["link"])],
    ]


def _queued_rows(rid, share):
    return [
        [_btn_cb("Play now", f"tl:playnow:{rid}", "success", E["play"])],
        [
            _btn_cb("View queue", f"tl:queue:{rid}", None, E["queue"]),
            _btn_url("Share link", share, None, E["link"]),
        ],
    ]


def _leaderboard_rows():
    return [
        [_btn_cb("Today", "tl:lb:today", "success", E["camera"]),
         _btn_cb("Week", "tl:lb:week", "success", E["video"])],
        [_btn_cb("Lifetime", "tl:lb:life", None, E["music"])],
        [_btn_cb("Close", "tl:close", "danger", E["delete"])],
    ]


async def show_result(client, chat_id, res: dict, reply_to=None):
    view = res.get("view") or "text"
    rid = res.get("room_id") or ""
    join = _safe_url(res.get("join"))
    share = _safe_url(res.get("share") or join)

    if view == "playing":
        body, rows = _playing_body(res), _playing_rows(rid, join, share)
    elif view == "queued":
        body, rows = _queued_body(res), _queued_rows(rid, share)
    elif view == "leaderboard":
        body, rows = _leaderboard_body(res), _leaderboard_rows()
    elif view == "rank":
        body, rows = _rank_body(res), []
    else:
        body, rows = _text_body(res.get("text") or "Done."), []
    return await send_ui(client, chat_id, body, rows, reply_to=reply_to)


# ================================
#        API
# ================================
async def _call(payload: dict) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.post(TIDAL_API, json=payload, headers={"X-Secret": TIDAL_SECRET}) as r:
                return await r.json(content_type=None)
    except Exception as e:
        log.warning("tidal api error: %r", e)
        return {"ok": False, "text": "Room server is not reachable right now."}


def _user(m):
    u = m.from_user
    return {"uid": u.id, "name": " ".join(filter(None, [u.first_name, u.last_name])), "username": u.username or ""}


# ================================
#        DM COMMANDS
# ================================
@app.on_message(
    filters.command(["play", "vplay", "playforce", "vplayforce"], prefixes=["/", "!", "."])
    & filters.private & ~BANNED_USERS,
    group=-5,
)
async def tidal_dm_play(client, message):
    cmd = message.command[0].lower()
    q = " ".join(message.command[1:]).strip()
    if not q and message.reply_to_message:
        q = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    if not q:
        return await message.reply_text(f"Usage: /{cmd} song name")

    thinking = Thinking(client, message.chat.id)
    try:
        await thinking.show(E["fetch1"], f"Searching {_e(q)}")
        res = await _call({
            "act": "play", "q": q, "video": cmd.startswith("v"),
            "force": cmd.endswith("force"), **_user(message),
        })
    finally:
        await thinking.close()

    if not res.get("ok"):
        return await message.reply_text(res.get("text") or "Something went wrong.")
    await show_result(client, message.chat.id, res, reply_to=message.id)


@app.on_message(filters.command(["rank"], prefixes=["/", "!", "."]) & ~BANNED_USERS, group=-5)
async def tidal_rank(client, message):
    res = await _call({"act": "rank", **_user(message)})
    if not res.get("ok") and not res.get("view"):
        return await message.reply_text(res.get("text") or "Could not fetch your rank right now.")
    await show_result(client, message.chat.id, res, reply_to=message.id)


@app.on_message(filters.command(["leaderboard", "lb"], prefixes=["/", "!", "."]) & ~BANNED_USERS, group=-5)
async def tidal_leaderboard(client, message):
    res = await _call({"act": "leaderboard", "period": "life", **_user(message)})
    if not res.get("ok") and not res.get("view"):
        return await message.reply_text(res.get("text") or "Could not fetch the leaderboard right now.")
    await show_result(client, message.chat.id, res, reply_to=message.id)


@app.on_message(filters.command(["skip", "end"], prefixes=["/", "!", "."]) & filters.private & ~BANNED_USERS, group=-5)
async def tidal_dm_ctl(_, message):
    res = await _call({"act": message.command[0].lower(), **_user(message)})
    await message.reply_text(res.get("text") or "Done.")


# ================================
#        CALLBACKS
# ================================
@app.on_callback_query(filters.regex(r"^tl:(skip|end|pause|playnow|queue):") & ~BANNED_USERS, group=-5)
async def tidal_cb(client, cq):
    _, act, room = cq.data.split(":", 2)
    res = await _call({"act": act, "room": room, **_user(cq)})
    if res.get("ok") and res.get("view") and cq.message:
        await cq.answer()
        return await show_result(client, cq.message.chat.id, res)
    await cq.answer((res.get("text") or "Done")[:180], show_alert=not res.get("ok"))
    if res.get("ended") and cq.message:
        try:
            await cq.edit_message_reply_markup(None)
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^tl:lb:(today|week|life)$") & ~BANNED_USERS, group=-5)
async def tidal_lb_cb(client, cq):
    period = cq.data.split(":")[2]
    res = await _call({"act": "leaderboard", "period": period, **_user(cq)})
    await cq.answer()
    if cq.message:
        await show_result(client, cq.message.chat.id, res)


@app.on_callback_query(filters.regex(r"^tl:close$") & ~BANNED_USERS, group=-5)
async def tidal_close(_, cq):
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.answer()


@app.on_callback_query(filters.regex(r"^tl:noop$"), group=-5)
async def tidal_noop(_, cq):
    await cq.answer("Working on it...")


# ───── inline mode:  @bot song name   |   @bot /vplay song   |   @bot /playforce song   |   @bot /skip ─────
_latest = {}


def _wait_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Please wait...", callback_data="tl:noop")]])


def _info(rid, title, desc, text):
    return InlineQueryResultArticle(id=rid, title=title, description=desc,
                                    input_message_content=InputTextMessageContent(text))


async def _inline_answer(iq, q):
    uid = iq.from_user.id
    _latest[uid] = iq.id
    await asyncio.sleep(0.5)  # debounce per-keystroke queries
    if _latest.get(uid) != iq.id:
        return
    res = await _call({"act": "isearch", "q": q, **_user(iq)})
    if _latest.get(uid) != iq.id:
        return
    room = res.get("room")
    out = []
    if res.get("mode") == "skip":
        if not room:
            out = [_info("tl|x|n", "No active room", "Play a song or join a room first",
                         "No active room. Search a song to start one.")]
        elif not room.get("can_ctl"):
            out = [_info("tl|x|c", "Only the host can skip", room.get("title") or "",
                         "Only the host can skip in this room.")]
        else:
            out = [InlineQueryResultArticle(
                id="tl|s", title="Skip current song",
                description=("Now playing: " + room["title"]) if room.get("title") else "Nothing is playing",
                input_message_content=InputTextMessageContent("Skipping..."), reply_markup=_wait_kb())]
    elif room and not room.get("can_add"):
        out = [_info("tl|x|a", "Adding songs is off", "The host turned off adding songs in this room",
                     "The host turned off adding songs in this room.")]
    elif not res.get("results"):
        out = [_info("tl|x|h", "Search a song", "Type a song name after the bot username",
                     "Type a song name after the bot username.")]
    else:
        force, video = res.get("force"), res.get("video")
        if not room:
            verb = "Start a room and play"
        elif force or not room.get("playing"):
            verb = "Play now"
        else:
            verb = "Add to queue"
        for r in res["results"]:
            out.append(InlineQueryResultPhoto(
                id=f"tl|{'v' if video else 'a'}|{'f' if force else 'n'}|{r['id']}",
                photo_url=r["thumb"], thumb_url=r["thumb"],
                title=r["title"], description=f"{verb} - {r['durtxt']}" + (" - video" if video else ""),
                caption=f"Adding {r['title']}...", reply_markup=_wait_kb()))
    await iq.answer(out, cache_time=0, is_personal=True)



# ─── inline helpers: plain (classic) buttons, edits are done here ─────────────
def _ikb(text, url=None, cb=None, style=None, emoji=None):
    """Classic inline button. Tries the richest variant first, then drops what the build rejects."""
    base = {"url": url} if url else {"callback_data": cb}
    st = None
    if style and ButtonStyle is not None:
        st = getattr(ButtonStyle, style.upper(), None)
    variants = []
    if st is not None and emoji:
        variants.append(dict(base, style=st, icon_custom_emoji_id=str(emoji)))
    if st is not None:
        variants.append(dict(base, style=st))
    variants.append(dict(base))
    for kw in variants:
        try:
            return InlineKeyboardButton(text or ZW, **kw)
        except Exception:
            continue
    return InlineKeyboardButton(text or ZW, **base)


def _plain_kb(kb):
    """Same buttons without custom emoji or styles (used when Telegram rejects the fancy markup)."""
    rows = []
    for row in kb.inline_keyboard:
        line = []
        for b in row:
            if b.url:
                line.append(InlineKeyboardButton(b.text or ZW, url=b.url))
            else:
                line.append(InlineKeyboardButton(b.text or ZW, callback_data=b.callback_data))
        rows.append(line)
    return InlineKeyboardMarkup(rows)


async def _inline_edit(client, imid, text, kb=None, photo=True):
    """Edit the inline message (photo result -> caption, article result -> text).
    If Telegram rejects the markup (e.g. custom emoji on buttons), retry with plain buttons."""
    async def _go(markup):
        if photo:
            await client.edit_inline_caption(imid, text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        else:
            await client.edit_inline_text(imid, text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    try:
        await _go(kb)
        return
    except Exception as e:
        log.warning("inline edit failed with buttons, retrying plain: %r", e)
    try:
        await _go(_plain_kb(kb) if kb else None)
    except Exception as e:
        log.warning("inline edit failed: %r", e)


def _added_caption(res, uid, name):
    if res.get("played"):
        head = "<b>Now playing</b>"
    else:
        head = f"<b>Added to queue</b> (#{_e(res.get('index') or 0)})"
    mention = f'<a href="tg://user?id={uid}">{_e(name or "User")}</a>'
    return (
        f"{head}\n\n<b>{_e(res.get('title') or 'Unknown')}</b>\n"
        f"Duration: {_e(res.get('duration') or '--:--')}\n"
        f"By {mention}"
    )


@app.on_inline_query(group=-20)
async def tidal_inline(_, iq):
    q = (iq.query or "").strip()
    if not q or q.lower().startswith("gen"):  # empty query / UPI "gen" keep their old handlers
        return
    try:
        await _inline_answer(iq, q)
    except Exception as e:
        log.warning("tidal inline error: %r", e)
    iq.stop_propagation()


@app.on_chosen_inline_result(group=-20)
async def tidal_chosen(client, cr):
    log.info("chosen inline result: id=%r imid=%r", cr.result_id, cr.inline_message_id)
    p = (cr.result_id or "").split("|")
    if p[0] != "tl" or len(p) < 2 or p[1] == "x":
        return
    imid = cr.inline_message_id or ""
    if not imid:
        log.warning("chosen inline result has no inline_message_id: enable Inline Feedback in BotFather")
        return
    user = _user(cr)
    base = {**user, "imid": imid}

    if p[1] == "s":  # @bot /skip
        res = await _call({"act": "iskip", **base})
        return await _inline_edit(client, imid, res.get("text") or "Skipped.", None, photo=False)

    if len(p) != 4:
        return
    # 1) show "fetching" right away
    await _inline_edit(client, imid, "Fetching…", InlineKeyboardMarkup([[
        _ikb("Fetching…", cb="tl:noop", emoji=E["fetch1"])]]))
    # 2) fetch the stream and add/play it in the room
    res = await _call({
        "act": "iplay", "id": p[3], "video": p[1] == "v", "force": p[2] == "f", **base,
    })
    if not res.get("ok"):
        return await _inline_edit(client, imid, res.get("text") or "Could not add this song.", None)
    # 3) edit to the song card + Join room (success style, room emoji)
    join = _safe_url(res.get("join"))
    await _inline_edit(
        client, imid,
        _added_caption(res, user["uid"], user["name"]),
        InlineKeyboardMarkup([[_ikb("Join room", url=join, style="success", emoji=E["room_door"])]]),
    )
