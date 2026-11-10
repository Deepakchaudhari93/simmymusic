import asyncio
import html
import logging
import os
import random
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pyrogram import enums, filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:  # kurigram: rich messages (same as start.py)
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:  # media inside rich messages
    from pyrogram.types import InputRichMessageMedia, InputMediaVideo
except Exception:  # pragma: no cover
    InputRichMessageMedia = None
    InputMediaVideo = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

try:  # kurigram: coloured buttons
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

import config
import SHUKLAMUSIC
from SHUKLAMUSIC import app

# --- DATABASE (same import fallback as start.py) ---
try:
    from SHUKLAMUSIC.core.mongo import mongodb as db
except ImportError:
    try:
        from SHUKLAMUSIC.utils.database import mongodb as db
    except ImportError:
        from SHUKLAMUSIC.core.mongo import mongodb
        db = mongodb

log = logging.getLogger("couples")

# ================================
#            CONFIG
# ================================
CAPTION = "@nyxxai"
OWNER_ID = 7659846392  # only this user can use /clearcoupledb
COOLDOWN = 120         # seconds between /couples per user (2 minutes)

_PKG = os.path.dirname(os.path.abspath(SHUKLAMUSIC.__file__))
VIDEO_PATH = os.path.join(_PKG, "assets", "Video.Guru_20261022-195124425.mp4")
FONT_PATH = os.path.join(_PKG, "plugins", "tools", "Poppins-Bold.ttf")
UPIC_PATH = os.path.join(_PKG, "assets", "upic.png")

# Output video (resolution isn't important -> 480p)
OUT_W, OUT_H = 854, 480

# Circles in the 1264x720 source video: (center_x, center_y, diameter)
# (measured from the video; the circles never move)
CIRCLE_1 = (311, 239, 266)
CIRCLE_2 = (983, 235, 295)
NAME_GAP = 12       # px between circle bottom and the name
NAME_MAX_W = 300    # max name width in px
NAME_SIZE = 38      # starting font size (shrinks to fit)

# Premium emoji ids
EMOJI_TITLE = 5411516363254551554
EMOJI_HEART = 5260413856093598223
EMOJI_ANALYSING = 5573473356579078196
EMOJI_ALERT = 5881702736843511327

IST = timezone(timedelta(hours=5, minutes=30))

# ================================
#            DATABASE
# ================================
# Only a tiny pointer is stored per group: the message id of its latest couple
# (used for the "View Previous couple" button). No video / file is ever saved.
couples_col = db.couples_last
legacy_col = db.couples_24h  # old collection (held videos) -> only touched by /clearcoupledb


async def save_last_couple(chat_id: int, message_id: int):
    try:
        await couples_col.replace_one(
            {"_id": chat_id},
            {"_id": chat_id, "message_id": message_id, "created_at": datetime.utcnow()},
            upsert=True,
        )
    except Exception as e:
        log.warning("couples save_last_couple failed: %r", e)


async def get_last_couple(chat_id: int):
    try:
        return await couples_col.find_one({"_id": chat_id})
    except Exception as e:
        log.warning("couples get_last_couple failed: %r", e)
        return None


async def clear_all_couples() -> int:
    res = await couples_col.delete_many({})
    old = await legacy_col.delete_many({})
    return res.deleted_count + old.deleted_count


# ================================
#         DATE / TEXT HELPERS
# ================================
_MONO_DIGITS = str.maketrans("0123456789", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿")


def tomorrow_india() -> str:
    return (datetime.now(IST) + timedelta(days=1)).strftime("%d/%m/%Y")


def _mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{html.escape(name or "User")}</a>'


def _emoji(emoji_id: int, fallback: str = "⭐") -> str:
    """Classic HTML parse-mode premium emoji (same form as start.py)."""
    return f"<emoji id={emoji_id}>{fallback}</emoji>"


RICH_EMOJI_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected


def _remoji(emoji_id: int, style: int, fallback: str = "⭐") -> str:
    if style == 0:
        return f"<emoji id={emoji_id}>{fallback}</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def _msg_link(chat, message_id: int):
    """Link to a message of the group (public username or private supergroup)."""
    if not message_id:
        return None
    if getattr(chat, "username", None):
        return f"https://t.me/{chat.username}/{message_id}"
    cid = str(chat.id)
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{message_id}"
    return None


# ================================
#       RICH BUTTONS / SENDER
# ================================
PIN_ROWS = [[(" Pin this message", "callback", "pin_couple", "primary")]]


def rich_buttons(rows) -> str:
    out = ""
    for row in rows:
        out += "<tg-button-row>"
        for text, kind, value, style in row:
            st = f' style="{style}"' if style else ""
            label = html.escape(text, quote=False)
            if kind == "url":
                out += f'<tg-button type="url" url="{html.escape(value)}"{st}>{label}</tg-button>'
            else:
                out += f'<tg-button type="callback_data" data="{html.escape(value)}"{st}>{label}</tg-button>'
        out += "</tg-button-row>"
    return out


def classic_keyboard(rows) -> InlineKeyboardMarkup:
    kb = []
    for row in rows:
        line = []
        for text, kind, value, style in row:
            kwargs = {"url": value} if kind == "url" else {"callback_data": value}
            btn = None
            if style and ButtonStyle is not None:
                try:
                    btn = InlineKeyboardButton(text, style=getattr(ButtonStyle, style.upper()), **kwargs)
                except Exception:
                    btn = None
            if btn is None:
                btn = InlineKeyboardButton(text, **kwargs)
            line.append(btn)
        kb.append(line)
    return InlineKeyboardMarkup(kb)


async def send_rich(client, chat_id: int, builders, reply_to: int = None):
    """builders = [(html_func(style) -> str, media_list_or_None), ...] tried in order."""
    global RICH_EMOJI_STYLE
    if InputRichMessage is None or not hasattr(client, "send_rich_message"):
        return None

    base = []
    if reply_to:
        if ReplyParameters is not None:
            base.append(dict(reply_parameters=ReplyParameters(message_id=reply_to)))
        base.append(dict(reply_to_message_id=reply_to))
    else:
        base.append({})

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
            for extra in base:
                try:
                    msg = await client.send_rich_message(chat_id, rich, **extra)
                    RICH_EMOJI_STYLE = style
                    return msg
                except Exception as e:
                    last_err = e
                    log.warning("couple send_rich_message failed (media=%s style=%s): %r", bool(media), style, e)
    log.warning("rich couple failed, using classic fallback: %r", last_err)
    return None


def couple_html(doc, style: int, with_video: bool) -> str:
    e = lambda i: _remoji(i, style)
    next_txt = f"𝙽𝙴𝚇𝚃 𝙲𝙾𝚄𝙿𝙻𝙴𝚂 𝚆𝙸𝙻𝙻 𝙱𝙴 𝚂𝙴𝙻𝙴𝙲𝚃𝙴𝙳 𝙾𝙽 {tomorrow_india().translate(_MONO_DIGITS)}"

    video = ""
    if with_video:
        video = (
            '<figure><video src="tg://video?id=couple" tg-spoiler></video>'
            f"<figcaption>{html.escape(CAPTION)}</figcaption></figure>"
        )
    return (
        f"<h1><i><u>{e(EMOJI_TITLE)} Couple of The Day</u></i></h1>"
        f"<blockquote>{video}</blockquote>"
        f"<aside>{_mention(doc['c1_id'], doc['c1_name'])}<br>{e(EMOJI_HEART)}"
        f"<cite>{_mention(doc['c2_id'], doc['c2_name'])}</cite></aside>"
        f"<blockquote><b>{next_txt}</b> {e(EMOJI_ANALYSING)}</blockquote>"
        f"{rich_buttons(PIN_ROWS)}"
    )


def classic_caption(doc) -> str:
    next_txt = f"𝙽𝙴𝚇𝚃 𝙲𝙾𝚄𝙿𝙻𝙴𝚂 𝚆𝙸𝙻𝙻 𝙱𝙴 𝚂𝙴𝙻𝙴𝙲𝚃𝙴𝙳 𝙾𝙽 {tomorrow_india().translate(_MONO_DIGITS)}"
    return (
        f"{_emoji(EMOJI_TITLE)} <b>Couple of The Day</b>\n\n"
        f"<blockquote>{_mention(doc['c1_id'], doc['c1_name'])} {_emoji(EMOJI_HEART)} "
        f"{_mention(doc['c2_id'], doc['c2_name'])}</blockquote>\n"
        f"<blockquote><b>{next_txt}</b> {_emoji(EMOJI_ANALYSING)}</blockquote>"
    )


async def deliver_couple(client, message, doc):
    """rich (file_id) -> classic video (file_id) -> text only."""
    sources = [doc.get("file_id")] if doc.get("file_id") else []

    for src in sources:
        if InputRichMessageMedia is not None and InputMediaVideo is not None:
            try:
                media = [InputRichMessageMedia(id="couple", media=InputMediaVideo(media=src))]
                sent = await send_rich(
                    client, message.chat.id, [(lambda s: couple_html(doc, s, True), media)],
                    reply_to=message.id,
                )
                if sent is not None:
                    return sent
            except Exception as e:
                log.warning("couple rich media build failed: %r", e)
        try:
            return await message.reply_video(
                src,
                caption=classic_caption(doc),
                has_spoiler=True,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=classic_keyboard(PIN_ROWS),
            )
        except Exception as e:
            log.warning("couple classic video failed: %r", e)

    # last resort: no video
    sent = await send_rich(
        client, message.chat.id, [(lambda s: couple_html(doc, s, False), None)], reply_to=message.id
    )
    if sent is not None:
        return sent
    return await message.reply_text(
        classic_caption(doc), parse_mode=enums.ParseMode.HTML, reply_markup=classic_keyboard(PIN_ROWS)
    )


# ================================
#      VIDEO / OVERLAY RENDER
# ================================
def _clean_name(name: str, font) -> str:
    """Poppins has Latin + Devanagari; drop emoji / unsupported glyphs so no tofu boxes."""
    out = "".join(
        ch for ch in (name or "")
        if 0x20 <= ord(ch) <= 0x24F or 0x900 <= ord(ch) <= 0x97F or 0x2000 <= ord(ch) <= 0x206F
    )
    out = " ".join(out.split())  # collapse gaps left by removed emoji
    return out or "User"


def _circle_pfp(path: str, d: int) -> Image.Image:
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        img = Image.open(UPIC_PATH).convert("RGB")
    img = ImageOps.fit(img, (d, d), Image.LANCZOS)  # center-crop to a square
    ss = 4  # supersample the mask -> smooth edge
    mask = Image.new("L", (d * ss, d * ss), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d * ss - 1, d * ss - 1), fill=255)
    mask = mask.resize((d, d), Image.LANCZOS)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def _fit_font(draw, text: str):
    size = NAME_SIZE
    while size >= 26:
        font = ImageFont.truetype(FONT_PATH, size)
        if draw.textlength(text, font=font) <= NAME_MAX_W:
            return font, text
        size -= 2
    font = ImageFont.truetype(FONT_PATH, 26)
    while len(text) > 1 and draw.textlength(text + "…", font=font) > NAME_MAX_W:
        text = text[:-1]
    return font, text + "…"


def build_overlay(pfp1: str, pfp2: str, name1: str, name2: str, out_png: str, size=(1264, 720)):
    """Transparent 1264x720 layer: 2 round pfps on the video circles + names below (Poppins-Bold)."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for (cx, cy, d), pfp, name in ((CIRCLE_1, pfp1, name1), (CIRCLE_2, pfp2, name2)):
        circle = _circle_pfp(pfp, d)
        layer.alpha_composite(circle, (cx - d // 2, cy - d // 2))

        probe = ImageFont.truetype(FONT_PATH, NAME_SIZE)
        font, text = _fit_font(draw, _clean_name(name, probe))
        draw.text(
            (cx, cy + d // 2 + NAME_GAP),
            text,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=3,
            stroke_fill=(0, 0, 0, 255),
            anchor="ma",  # middle / top
        )
    layer.save(out_png)


async def render_couple_video(pfp1, pfp2, name1, name2, tmp_dir) -> str:
    overlay = os.path.join(tmp_dir, "overlay.png")
    out = os.path.join(tmp_dir, "couple.mp4")
    await asyncio.to_thread(build_overlay, pfp1, pfp2, name1, name2, overlay)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-v", "error",
        "-i", VIDEO_PATH, "-i", overlay,
        "-filter_complex", f"[0:v][1:v]overlay=0:0,scale={OUT_W}:{OUT_H}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "29", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
        out,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='ignore')[-400:]}")
    return out


# ================================
#        PICK + BUILD COUPLE
# ================================
async def _pick_two(chat_id: int):
    users = []
    async for m in app.get_chat_members(chat_id, limit=200):
        u = m.user
        if u and not u.is_bot and not getattr(u, "is_deleted", False):
            users.append(u)
    if len(users) < 2:
        return None
    return random.sample(users, 2)


async def _download_pfp(user_id: int, path: str) -> str:
    try:
        photo = (await app.get_chat(user_id)).photo
        got = await app.download_media(photo.big_file_id, file_name=path)
        return got or UPIC_PATH
    except Exception:
        return UPIC_PATH


async def make_new_couple(chat_id: int):
    """Picks a fresh couple, renders the video, uploads it once (file_id) and wipes
    every local file right after. Nothing is saved on the server or in the database."""
    pair = await _pick_two(chat_id)
    if not pair:
        return None
    u1, u2 = pair
    n1, n2 = u1.first_name or "User", u2.first_name or "User"

    tmp_dir = tempfile.mkdtemp(prefix="couple_")
    try:
        p1 = await _download_pfp(u1.id, os.path.join(tmp_dir, "p1.jpg"))
        p2 = await _download_pfp(u2.id, os.path.join(tmp_dir, "p2.jpg"))
        video = await render_couple_video(p1, p2, n1, n2, tmp_dir)

        # upload once to the log chat -> reuse its file_id inside the rich message
        sent = await app.send_video(
            config.LOGGER_ID, video, width=OUT_W, height=OUT_H, supports_streaming=True,
            caption=f"couple • {chat_id}",
        )
        file_id = sent.video.file_id

        return {
            "c1_id": u1.id,
            "c1_name": n1,
            "c2_id": u2.id,
            "c2_name": n2,
            "file_id": file_id,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)  # video deleted from the server


# ================================
#       SPAM PROTECTION
# ================================
_last_used = {}   # user_id -> time of last accepted /couples
_warned = {}      # user_id -> time until which we stay quiet (one alert per cooldown)
_chat_locks = {}  # chat_id -> asyncio.Lock (no double generation in the same group)


def _cooldown_left(user_id: int) -> int:
    now = time.time()
    if len(_last_used) > 2000:  # keep memory small
        for k in [k for k, v in _last_used.items() if now - v > COOLDOWN]:
            _last_used.pop(k, None)
            _warned.pop(k, None)
    left = COOLDOWN - (now - _last_used.get(user_id, 0))
    return int(left) + 1 if left > 0 else 0


async def _send_cooldown_alert(message):
    """'wait 2 minutes' alert + primary 'View Previous couple' button (link to the group's last couple)."""
    rows = None
    last = await get_last_couple(message.chat.id)
    link = _msg_link(message.chat, last["message_id"]) if last else None
    if link:
        rows = [[("View Previous couple", "url", link, "primary")]]

    def rich_html(style: int) -> str:
        txt = (
            f"{_remoji(EMOJI_ALERT, style, '⚠️')} <b>You've just used couples command, "
            f"wait for 2 minutes before using it again.</b>"
        )
        return txt + (rich_buttons(rows) if rows else "")

    sent = await send_rich(app, message.chat.id, [(rich_html, None)], reply_to=message.id)
    if sent is not None:
        return sent
    return await message.reply_text(
        f"{_emoji(EMOJI_ALERT, '⚠️')} <b>You've just used couples command, "
        f"wait for 2 minutes before using it again.</b>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=classic_keyboard(rows) if rows else None,
    )


# ================================
#          /couples
# ================================
@app.on_message(filters.command("couples"))
async def couples_cmd(_, message):
    if message.chat.type == ChatType.PRIVATE:
        return await message.reply_text("ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ᴏɴʟʏ ᴡᴏʀᴋs ɪɴ ɢʀᴏᴜᴘs.")
    if not message.from_user:
        return

    uid = message.from_user.id
    left = _cooldown_left(uid)
    if left:
        if _warned.get(uid, 0) < time.time():  # alert once, then ignore the spam silently
            _warned[uid] = time.time() + left
            try:
                await _send_cooldown_alert(message)
            except Exception as e:
                log.warning("couples cooldown alert failed: %r", e)
        return
    _last_used[uid] = time.time()

    try:
        await message.react(emoji="🍓", big=True)
    except Exception:
        pass

    cid = message.chat.id
    lock = _chat_locks.setdefault(cid, asyncio.Lock())
    status = None
    try:
        async with lock:
            # unlimited couples: every /couples picks a brand-new couple
            status = await message.reply_text(
                f"{_emoji(EMOJI_ANALYSING)} <b>Love is in the air, let's see who are the couple of the day...</b>",
                parse_mode=enums.ParseMode.HTML,
            )
            doc = await make_new_couple(cid)
            if not doc:
                return await status.edit_text("Not enough members to generate a couple.")
        sent = await deliver_couple(app, message, doc)
        # remember only the message id (no video) for the "View Previous couple" button
        sent_id = getattr(sent, "id", None)
        if sent_id:
            await save_last_couple(cid, sent_id)
    except Exception as e:
        log.exception("couples failed: %r", e)
        try:
            await message.reply_text("❌ Couldn't generate the couple right now, try again later.")
        except Exception:
            pass
    finally:
        if status is not None:
            try:
                await status.delete()
            except Exception:
                pass


# ================================
#       /clearcoupledb (owner)
# ================================
@app.on_message(filters.command("clearcoupledb") & filters.user(OWNER_ID))
async def clear_couple_db(_, message):
    try:
        count = await clear_all_couples()
        _chat_locks.clear()
        await message.reply_text(
            f"✅ <b>Couple database cleared.</b>\n"
            f"<b>Deleted:</b> <code>{count}</code> couple video(s).\n\n"
            "Every group can now pick a new couple right away.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ Failed: <code>{html.escape(str(e))}</code>", parse_mode=enums.ParseMode.HTML)


# ================================
#           PIN BUTTON
# ================================
@app.on_callback_query(filters.regex("^pin_couple$"))
async def pin_couple_callback(_, query):
    user_id = query.from_user.id
    chat_id = query.message.chat.id

    try:
        member = await app.get_chat_member(chat_id, user_id)
        is_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        is_admin = False
    if not is_admin and user_id != OWNER_ID:
        return await query.answer("⚠️ Only group admins can pin the couple of the day!", show_alert=True)

    try:
        await query.message.pin()
        await query.answer("📌 Message pinned successfully!", show_alert=False)
    except Exception:
        await query.answer("Failed to pin: make sure the bot has pin permission.", show_alert=True)


__mod__ = "COUPLES"
__help__ = """
**» /couples** - Get a new couple of the group (unlimited, 2 minutes gap per user)
**» /clearcoupledb** - (owner only) reset every group's couple data
"""
