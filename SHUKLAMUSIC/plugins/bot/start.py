import asyncio
import html
import logging
import os
import random
import re
import time

from pyrogram import filters, enums
from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message
from youtubesearchpython.__future__ import VideosSearch

try:  # kurigram: rich messages (same as download.py)
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:  # media inside rich messages (Bot API 10.2+)
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
from SHUKLAMUSIC.misc import _boot_
from SHUKLAMUSIC.plugins.sudo.sudoers import sudoers_list
from SHUKLAMUSIC.utils.database import (
    add_served_chat,
    add_served_user,
    blacklisted_chats,
    get_lang,
    is_banned_user,
    is_on_off,
)

# --- DATABASE FIX (Ping Jaisa) ---
try:
    from SHUKLAMUSIC.core.mongo import mongodb as db
except ImportError:
    try:
        from SHUKLAMUSIC.utils.database import mongodb as db
    except ImportError:
        from SHUKLAMUSIC.core.mongo import mongodb
        db = mongodb

from SHUKLAMUSIC.utils.decorators.language import LanguageStart
from SHUKLAMUSIC.utils.formatters import get_readable_time
from SHUKLAMUSIC.utils.inline import help_pannel, start_panel
from config import BANNED_USERS
from strings import get_string

log = logging.getLogger("start")

# ================================
#        DATABASE SETUP
# ================================
welcome_db = db.welcome_config

YUMI_PICS = [
    "https://files.catbox.moe/wifnjm.jpg",
    "https://files.catbox.moe/zp7b9k.jpg",
    "https://files.catbox.moe/qd4pv8.jpg",
]

# ================================
#        START CONFIG (DM)
# ================================
BOT_URL = "https://t.me/Ishanimusice_bot"
START_EFFECT_ID = 5046509860389126442

# video file sits inside the SHUKLAMUSIC package folder
START_VIDEO = os.path.join(
    os.path.dirname(os.path.abspath(SHUKLAMUSIC.__file__)),
    "VID_20261022_114101_321.mp4",
)
START_VIDEO_CAPTION = "𝙸𝚜𝚑𝚊𝚗𝚒 𝙼𝚞𝚜𝚒𝚌 ⓒ𝟸𝟶𝟸𝟼"

# Optional: put a direct http(s) link here to use an IMAGE (jpg/png/webp) or a VIDEO (mp4)
# from a URL instead of the local video file. No upload needed. "" = use the local video.
START_MEDIA_URL = "https://files.catbox.moe/7nn61x.mp4"   # e.g. "https://files.catbox.moe/wifnjm.jpg"

QUOTE_EMOJIS = [
    5042192219960771668,
    5213412401180853808,
    5211032856154885824,
    5211094355791594395,
    5210820276748566172,
    5211009208064953810,
]
HEY_EMOJI_LEFT = 6235366428648678928
HEY_EMOJI_RIGHT = 5409029744693897259
EMOJI_ANALYSING = 5573473356579078196
EMOJI_SUBDICT = 6235366428648678928


DM_TEXT = "E𝗇𝗃𝗈𝗒 𝖯𝗋𝖾𝗆𝗂𝗎𝗆 𝖺𝗇𝖽 𝗎𝗇𝗅𝗂𝗆𝗂𝗍𝖾𝖽 𝖫𝗂𝗌𝗍𝖾𝗇𝗂𝗇𝗀 𝖾𝗑𝗉𝖾𝗋𝗂𝖾𝗇𝖼𝖾 𝗐𝗂𝗍𝗁 Ishani 𝖬𝗎𝗌𝗂𝖼 𝖡𝗈𝗍"
DM_TEXT_AUTHOR = "- 𝚂𝚝𝚊𝚕𝚔𝚎𝚛"
DM_TAUTHOR = "-𝙵𝚛𝚘𝚖 @Mee_Aarush"
SONG_PROMPT_TEXT = "Send the Song name directly in this chat, I'll download it for you, or use /play {song name}"
HELP_CALLBACK = "settings_back_helper"  # same callback your old private_panel help button used

# (text, kind, value, style)   kind: "url" | "callback"   style: primary/success/danger/None
BTN_ADD = ("𝖠𝖽𝖽 𝖬𝖾 𝗂𝗇 𝗒𝗈𝗎𝗋 𝖦𝗋𝗈𝗎𝗉", "url", f"{BOT_URL}?startgroup=true", "primary")
BTN_VCLESS = ("𝖦𝗈 𝖵𝖢𝗅𝖾𝗌𝗌 (𝖱𝗈𝗈𝗆'𝗌)", "url", "https://t.me/nyxxai/room?startapp", "success")
BTN_UPDATES = ("𝖴𝗉𝖽𝖺𝗍𝖾𝗌", "url", "https://t.me/+2TXf5vdnm1UxMWM1", "danger")
BTN_SUPPORT = ("𝖲𝗎𝗉𝗉𝗈𝗋𝗍", "url", "http://t.me/Mee_Aarush", "danger")
BTN_REPORT = ("𝖱𝖾𝗉𝗈𝗋𝗍 𝖡𝗎𝗀", "url", "http://t.me/Mee_Aarush", "danger")
BTN_SUGGEST = ("𝖲𝗎𝗀𝗀𝖾𝗌𝗍", "url", "https://t.me/+2TXf5vdnm1UxMWM1", "danger")
BTN_HELP = ("𝖧𝖾𝗅𝗉 & 𝖢𝗈𝗆𝗆𝖺𝗇𝖽𝗌", "callback", HELP_CALLBACK, None)

DM_BUTTONS = [
    [BTN_ADD],
    [BTN_VCLESS],
    [BTN_UPDATES, BTN_SUPPORT],
    [BTN_REPORT, BTN_SUGGEST],
    [BTN_HELP],
]
GROUP_BUTTONS = [
    [BTN_ADD],
    [BTN_UPDATES, BTN_SUPPORT],
]

# ================================
#        RICH HELPERS
# ================================
RICH_EMOJI_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected


def _emoji(emoji_id: int, fallback: str = "⭐") -> str:
    return f"<emoji id={emoji_id}>{fallback}</emoji>"


def _remoji(emoji_id: int, style: int, fallback: str = "⭐") -> str:
    if style == 0:
        return f"<emoji id={emoji_id}>{fallback}</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def rich_buttons(rows) -> str:
    """Buttons live inside the rich HTML itself (<tg-button-row> + <tg-button>)."""
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
    """Normal inline keyboard, used only if the rich message is rejected."""
    kb = []
    for row in rows:
        line = []
        for text, kind, value, style in row:
            kwargs = {"url": value} if kind == "url" else {"callback_data": value}
            btn = None
            if style and ButtonStyle is not None:
                try:
                    btn = InlineKeyboardButton(
                        text, style=getattr(ButtonStyle, style.upper()), **kwargs
                    )
                except Exception:
                    btn = None
            if btn is None:
                btn = InlineKeyboardButton(text, **kwargs)
            line.append(btn)
        kb.append(line)
    return InlineKeyboardMarkup(kb)


def _mention_html(user) -> str:
    return f'<a href="tg://user?id={user.id}">{html.escape(user.first_name or "User")}</a>'


async def send_rich(client, chat_id: int, builders, reply_to: int = None, effect_id: int = None):
    """
    builders = [(html_func(style) -> str, media_list_or_None), ...]  tried in order.
    For each builder: current emoji tag style first, then the other one.
    Returns the sent Message, or None if every rich attempt failed.
    """
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
    variants = ([dict(v, effect_id=effect_id) for v in base] + base) if effect_id else base

    last_err = None
    for html_func, media in builders:
        for style in (RICH_EMOJI_STYLE, 1 - RICH_EMOJI_STYLE):
            try:
                if media:
                    rich = InputRichMessage(html=html_func(style), media=media)
                else:
                    rich = InputRichMessage(html=html_func(style))
            except Exception as e:  # build doesn't support media= etc.
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
                        "send_rich_message failed (media=%s style=%s extra=%s): %r",
                        bool(media), style, list(extra), e,
                    )
                    continue
    log.warning("rich start failed, using classic fallback: %r", last_err)
    return None


# ================================
#   RICH THINKING DRAFT (from download.py)
# ================================
def _thinking_html(emoji_id: int, text: str, tag_style: int = 0) -> str:
    if tag_style == 0:
        icon = f"<emoji id={emoji_id}>⭐</emoji>"
    else:
        icon = f'<tg-emoji emoji-id="{emoji_id}">⭐</tg-emoji>'
    return f"<tg-thinking>{icon} {text}</tg-thinking>"


class Thinking:
    """
    Rich-message 'Thinking...' draft (send_rich_message_draft + <tg-thinking>). DM only.
    Drafts live ~30s, so a keep-alive task re-sends the current state every 20s.
    If rich drafts fail, falls back to a normal message edited in place.
    """

    def __init__(self, client, chat_id: int):
        self.client = client
        self.chat_id = chat_id
        try:
            self.draft_id = client.rnd_id()
        except Exception:
            self.draft_id = random.randint(1, 2**31 - 1)
        self.tag_style = RICH_EMOJI_STYLE
        self.rich_ok = InputRichMessage is not None and hasattr(client, "send_rich_message_draft")
        self.current = None
        self._flipped = False
        self.fallback_msg = None
        self._keepalive = None

    async def _send_draft(self, emoji_id: int, text: str):
        await self.client.send_rich_message_draft(
            chat_id=self.chat_id,
            draft_id=self.draft_id,
            rich_message=InputRichMessage(html=_thinking_html(emoji_id, text, self.tag_style)),
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
                    self.tag_style = 1 - self.tag_style
                    try:
                        await self._send_draft(emoji_id, text)
                        RICH_EMOJI_STYLE = self.tag_style
                        return
                    except Exception as e2:
                        e = e2
                log.warning("send_rich_message_draft failed, using edit fallback: %r", e)
                self.rich_ok = False

        html_text = f"{_emoji(emoji_id)} {text}"
        try:
            if self.fallback_msg is None:
                self.fallback_msg = await self.client.send_message(
                    self.chat_id, html_text, parse_mode=enums.ParseMode.HTML
                )
            else:
                await self.fallback_msg.edit_text(html_text, parse_mode=enums.ParseMode.HTML)
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


# ================================
#        DM START MESSAGE (rich)
# ================================
def dm_start_html(user, style: int, with_video: bool) -> str:
    e = lambda i: _remoji(i, style)
    emoji_row = "".join(e(i) for i in QUOTE_EMOJIS)

    video = ""
    if with_video:
        cap = f"<figcaption>{html.escape(START_VIDEO_CAPTION)}</figcaption>"
        if START_MEDIA_URL:
            url = html.escape(START_MEDIA_URL)
            if START_MEDIA_URL.lower().split("?")[0].endswith((".mp4", ".mov", ".webm", ".gif")):
                video = f'<figure><video src="{url}" tg-spoiler></video>{cap}</figure>'
            else:
                video = f'<figure><img src="{url}" tg-spoiler/>{cap}</figure>'
        else:
            video = f'<figure><video src="tg://video?id=intro" tg-spoiler></video>{cap}</figure>'
    return (
        f'<h1>Welcome To <a href="{BOT_URL}">Ishani</a>!!</h1>'
        f"<blockquote>{video}"
        f"{emoji_row}"
        f"<cite>{html.escape(DM_TAUTHOR)}{e(EMOJI_SUBDICT)}</cite></blockquote>"
        f"<aside>{e(HEY_EMOJI_LEFT)} <b>ʜєʏ,</b> {_mention_html(user)} {e(HEY_EMOJI_RIGHT)}</aside>"
        f"<blockquote>{DM_TEXT} {e(EMOJI_ANALYSING)}"
        f"<cite>{html.escape(DM_TEXT_AUTHOR)}</cite>"
        f"</blockquote>"
        f"{rich_buttons(DM_BUTTONS)}"
    )


_video_file_id = None


async def _get_video_file_id(client):
    """
    Nested local-file upload inside a rich message isn't supported by every build,
    so upload the video ONCE (to LOGGER_ID), keep its file_id (memory + Mongo) and
    reuse that file_id in the rich message. Re-uploads only if the file changes.
    """
    global _video_file_id
    if _video_file_id:
        return _video_file_id
    if not os.path.isfile(START_VIDEO):
        log.warning("start video not found: %s", START_VIDEO)
        return None
    sig = f"{os.path.getsize(START_VIDEO)}:{int(os.path.getmtime(START_VIDEO))}"
    try:
        data = await welcome_db.find_one({"_id": "start_video"})
        if data and data.get("sig") == sig and data.get("file_id"):
            _video_file_id = data["file_id"]
            return _video_file_id
    except Exception as e:
        log.warning("start video db read failed: %r", e)
    try:
        m = await client.send_video(
            config.LOGGER_ID,
            START_VIDEO,
            caption=START_VIDEO_CAPTION,
            supports_streaming=True,
        )
        _video_file_id = m.video.file_id
        try:
            await welcome_db.update_one(
                {"_id": "start_video"},
                {"$set": {"file_id": _video_file_id, "sig": sig}},
                upsert=True,
            )
        except Exception as e:
            log.warning("start video db write failed: %r", e)
        return _video_file_id
    except Exception as e:
        log.warning("couldn't upload start video to LOGGER_ID: %r", e)
        return None


async def _video_media_variants(client):
    """List of media lists to try, best first: [file_id], [local path]."""
    if InputRichMessageMedia is None or InputMediaVideo is None:
        log.warning("this pyrogram build has no InputRichMessageMedia/InputMediaVideo")
        return []
    variants = []
    file_id = await _get_video_file_id(client)
    sources = [file_id] if file_id else []
    if os.path.isfile(START_VIDEO):
        sources.append(START_VIDEO)
    for src in sources:
        try:
            variants.append([InputRichMessageMedia(id="intro", media=InputMediaVideo(media=src))])
        except Exception as e:
            log.warning("couldn't build rich media (%r): %r", src, e)
    return variants


async def send_dm_start(client, chat_id: int, user):
    builders = []
    if START_MEDIA_URL:  # URL media needs no upload / media list
        builders.append((lambda s: dm_start_html(user, s, True), None))
    else:
        for media in await _video_media_variants(client):
            builders.append((lambda s: dm_start_html(user, s, True), media))
    builders.append((lambda s: dm_start_html(user, s, False), None))

    sent = await send_rich(client, chat_id, builders, effect_id=START_EFFECT_ID)
    if sent is not None:
        return sent

    # classic fallback (photo + normal inline keyboard)
    caption = (
        f'<b>Welcome To <a href="{BOT_URL}">Ishani</a>!!</b>\n'
        "───────────────────•\n\n"
        f"<blockquote>ʜєʏ, {_mention_html(user)}</blockquote>\n"
        f"<blockquote>{DM_TEXT}\n— {html.escape(DM_TEXT_AUTHOR)}</blockquote>"
    )
    return await client.send_photo(
        chat_id,
        random.choice(YUMI_PICS),
        has_spoiler=True,
        caption=caption,
        reply_markup=classic_keyboard(DM_BUTTONS),
        effect_id=START_EFFECT_ID,
    )


# ================================
#   SONG PROMPT (2nd message after DM start)
# ================================
def song_prompt_html(style: int) -> str:
    return f"<p>{_remoji(EMOJI_ANALYSING, style)} <i>{html.escape(SONG_PROMPT_TEXT)}</i></p>"


async def send_song_prompt(client, chat_id: int):
    """Follow-up message: analysing emoji + italic 'send the song name' hint."""
    builders = [(song_prompt_html, None)]
    sent = await send_rich(client, chat_id, builders)
    if sent is not None:
        return sent

    # classic fallback
    try:
        return await client.send_message(
            chat_id,
            f"{_emoji(EMOJI_ANALYSING)} <i>{html.escape(SONG_PROMPT_TEXT)}</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        log.warning("song prompt failed: %r", e)
        return None


async def send_dm_start_full(client, chat_id: int, user):
    """Rich start message, then the song-name prompt right after it."""
    sent = await send_dm_start(client, chat_id, user)
    await send_song_prompt(client, chat_id)
    return sent


# ================================
#        GROUP START MESSAGE (rich buttons)
# ================================
GROUP_VIDEO_URL = "https://files.catbox.moe/w6w3ke.mp4"


def _classic_to_rich(text: str, style: int) -> str:
    """Make the group caption (classic Telegram HTML) safe inside a rich message."""
    # premium emoji: <emoji id=123>x</emoji>  ->  current rich emoji tag style
    text = re.sub(
        r'<emoji id="?(\d+)"?>(.*?)</emoji>',
        lambda m: _remoji(int(m.group(1)), style, m.group(2) or "⭐"),
        text,
        flags=re.S,
    )
    text = re.sub(r"</?spoiler>", lambda m: "</tg-spoiler>" if m.group(0)[1] == "/" else "<tg-spoiler>", text)
    # no quote inside the quote/pullquote
    text = re.sub(r"</?blockquote[^>]*>", "", text)
    return text.replace("\r", "").replace("\n", "<br>")


async def send_group_start(client, message: Message, caption: str):
    def build(style: int, with_video: bool) -> str:
        video = (
            f'<figure><video src="{GROUP_VIDEO_URL}"></video></figure>' if with_video else ""
        )
        return (
            f"<blockquote>{video}"
            f"<aside>{_classic_to_rich(caption, style)}</aside></blockquote>"
            f"{rich_buttons(GROUP_BUTTONS)}"
        )

    builders = [
        (lambda st: build(st, True), None),
        (lambda st: build(st, False), None),
    ]
    sent = await send_rich(client, message.chat.id, builders, reply_to=message.id)
    if sent is not None:
        return sent

    # classic fallback: same video + normal inline keyboard
    try:
        return await message.reply_video(
            GROUP_VIDEO_URL,
            caption=caption,
            reply_markup=classic_keyboard(GROUP_BUTTONS),
        )
    except Exception as e:
        log.warning("group video fallback failed: %r", e)
        return await message.reply_photo(
            random.choice(YUMI_PICS),
            caption=caption,
            reply_markup=classic_keyboard(GROUP_BUTTONS),
        )


async def delete_sticker_after_delay(message, delay):
    await asyncio.sleep(delay)
    await message.delete()


# ================================
#      SET WELCOME COMMAND (GROUP TEXT ONLY)
# ================================
@app.on_message(filters.command(["setwelcome_grp"]) & filters.user(7659846392))
async def set_welcome_msg(client, message):
    cmd = message.command[0].lower()
    msg_type = "welcome_group"

    if len(message.command) < 2 and not message.reply_to_message:
        await message.reply_text(
            f"❌ <b>Usage:</b>\n<code>/{cmd} [Your HTML Message]</code>\n\n"
            "<b>Variables:</b>\n"
            "<code>{name}</code> - First Name\n"
            "<code>{mention}</code> - User Link\n"
            "<code>{username}</code> - @Username\n"
            "<code>{bot_name}</code> - Bot Name\n"
            "<code>{chat_name}</code> - Chat Name (Group only)"
        )
        return

    # Extract Text (Preserving HTML for Premium Emojis)
    try:
        if message.reply_to_message:
            new_msg = message.reply_to_message.text.html or message.reply_to_message.caption.html
        else:
            new_msg = message.text.html.split(None, 1)[1]
    except (IndexError, AttributeError):
        return await message.reply_text("❌ Text extract nahi kar paya. Dobara try karein.")

    await welcome_db.update_one(
        {"_id": msg_type},
        {"$set": {"message": new_msg}},
        upsert=True,
    )

    await message.reply_text(f"✅ <b>{msg_type.replace('_', ' ').upper()} message has been set!</b>")


# Helper to get welcome text (group)
async def get_welcome_caption(msg_type, default_text, user, bot, chat=None):
    data = await welcome_db.find_one({"_id": msg_type})

    if data and "message" in data:
        text = data["message"]
        text = text.replace("{name}", user.first_name)
        text = text.replace("{mention}", user.mention)
        text = text.replace("{username}", f"@{user.username}" if user.username else "No Username")
        text = text.replace("{bot_name}", bot.first_name)
        if chat:
            text = text.replace("{chat_name}", chat.title)
        return text

    return default_text


# ================================
#        START COMMAND (DM)
# ================================
@app.on_message(filters.command(["start"]) & filters.private & ~BANNED_USERS)
@LanguageStart
async def start_pm(client, message: Message, _):

    # --- REACTION START ---
    try:
        await message.react(emoji="😘")
    except Exception:
        pass
    # --- REACTION END ---

    # --- RICH THINKING START ---
    thinking = Thinking(client, message.chat.id)
    try:
        await add_served_user(message.from_user.id)
        await thinking.show(EMOJI_ANALYSING, "Starting....")
        await asyncio.sleep(0.9)
        await thinking.show(EMOJI_ANALYSING, "Hey, Welcome to Ishani")
        await asyncio.sleep(0.9)
        await thinking.show(EMOJI_ANALYSING, "Listen to Free music in telegram VCs and in private")
        await asyncio.sleep(1.2)
    finally:
        # draft vanishes by itself once the real start message is sent;
        # close() stops keep-alive and removes the fallback message if one was used
        await thinking.close()
    # --- RICH THINKING END ---

    if len(message.text.split()) > 1:
        name = message.text.split(None, 1)[1]
        if name[0:4] == "help":
            keyboard = help_pannel(_)
            await message.reply_photo(
                random.choice(YUMI_PICS),
                has_spoiler=True,
                caption=_["help_1"].format(config.SUPPORT_CHAT),
                reply_markup=keyboard,
            )
        elif name[0:3] == "sud":
            await sudoers_list(client=client, message=message, _=_)
        elif name[0:3] == "inf":
            m = await message.reply_text("🔎")
            query = str(name).replace("info_", "", 1)
            query = f"https://www.youtube.com/watch?v={query}"
            results = VideosSearch(query, limit=1)
            for result in (await results.next())["result"]:
                title = result["title"]
                duration = result["duration"]
                views = result["viewCount"]["short"]
                thumbnail = result["thumbnails"][0]["url"].split("?")[0]
                channellink = result["channel"]["link"]
                channel = result["channel"]["name"]
                link = result["link"]
                published = result["publishedTime"]
            searched_text = _["start_6"].format(
                title, duration, views, published, channellink, channel, app.mention
            )
            key = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(text=_["S_B_8"], url=link),
                        InlineKeyboardButton(text=_["S_B_9"], url=config.SUPPORT_CHAT),
                    ],
                ]
            )
            await m.delete()
            await app.send_video(
                chat_id=message.chat.id,
                video=thumbnail,
                caption=searched_text,
                reply_markup=key,
            )
    else:
        # fully hard-coded rich start, followed by the "send the song name" prompt
        await send_dm_start_full(client, message.chat.id, message.from_user)

        if await is_on_off(2):
            await app.send_message(
                chat_id=config.LOGGER_ID,
                text=f"❖ {message.from_user.mention} ᴊᴜsᴛ sᴛᴀʀᴛᴇᴅ ᴛʜᴇ ʙᴏᴛ.\n\n<b>๏ ᴜsᴇʀ ɪᴅ :</b> <code>{message.from_user.id}</code>\n<b>๏ ᴜsᴇʀɴᴀᴍᴇ :</b> @{message.from_user.username}",
            )


# ================================
#   HELP -> BACK  (goes back to the rich start)
# ================================
# The old handler for this callback lives in another plugin and edits the message
# into the OLD start text. group=-1 + stop_propagation makes this one run first,
# so the old one never fires. If your Back button uses another callback_data,
# change BACK_CALLBACK_REGEX.
BACK_CALLBACK_REGEX = r"settingsback_helper"


@app.on_callback_query(filters.regex(BACK_CALLBACK_REGEX) & ~BANNED_USERS, group=-1)
async def back_to_rich_start(client, query):
    try:
        await query.answer()
    except Exception:
        pass
    try:
        # a classic/rich message can't be edited into a rich message with media,
        # so remove the help message and send the fresh rich start
        await query.message.delete()
    except Exception:
        pass
    await send_dm_start_full(client, query.message.chat.id, query.from_user)
    query.stop_propagation()


# ================================
#        START COMMAND (GROUP)
# ================================
@app.on_message(filters.command(["start"]) & filters.group & ~BANNED_USERS)
@LanguageStart
async def start_gp(client, message: Message, _):
    # --- REACTION START ---
    try:
        await message.react(emoji="😘")
    except Exception:
        pass
    # --- REACTION END ---

    uptime = int(time.time() - _boot_)

    # group text stays exactly as before (custom DB text or start_1)
    default_caption = _["start_1"].format(app.mention, get_readable_time(uptime))

    final_caption = await get_welcome_caption(
        "welcome_group",
        default_caption,
        message.from_user,
        await client.get_me(),
        message.chat,
    )

    await send_group_start(client, message, final_caption)
    return await add_served_chat(message.chat.id)


# ================================
#        NEW MEMBER WELCOME
# ================================
@app.on_message(filters.new_chat_members, group=-1)
async def welcome(client, message: Message):
    for member in message.new_chat_members:
        try:
            language = await get_lang(message.chat.id)
            _ = get_string(language)
            if await is_banned_user(member.id):
                try:
                    await message.chat.ban_member(member.id)
                except:
                    pass
            if member.id == app.id:
                if message.chat.type != ChatType.SUPERGROUP:
                    await message.reply_text(_["start_4"])
                    return await app.leave_chat(message.chat.id)
                if message.chat.id in await blacklisted_chats():
                    await message.reply_text(
                        _["start_5"].format(
                            app.mention,
                            f"https://t.me/{app.username}?start=sudolist",
                            config.SUPPORT_CHAT,
                        ),
                        disable_web_page_preview=True,
                    )
                    return await app.leave_chat(message.chat.id)

                out = start_panel(_)

                # --- GET CUSTOM OR DEFAULT CAPTION ---
                default_caption = _["start_3"].format(
                    message.from_user.mention,
                    app.mention,
                    message.chat.title,
                    app.mention,
                )

                final_caption = await get_welcome_caption(
                    "welcome_group",
                    default_caption,
                    member,  # Passing the new member object
                    await client.get_me(),
                    message.chat,
                )

                await message.reply_photo(
                    random.choice(YUMI_PICS),
                    has_spoiler=True,
                    caption=final_caption,
                    reply_markup=InlineKeyboardMarkup(out),
                )
                await add_served_chat(message.chat.id)
                await message.stop_propagation()
        except Exception as ex:
            print(ex)
