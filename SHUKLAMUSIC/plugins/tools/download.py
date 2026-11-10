
import asyncio
import glob
import logging
import os
import random
import re
import shutil
import tempfile
import time
from urllib.parse import urlparse

from pyrogram import filters
from pyrogram.enums import ChatAction, ChatType, ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from SHUKLAMUSIC import app

try:  # kurigram: button styles + rich blocks (names per your docs)
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

try:
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

import yt_dlp

log = logging.getLogger("download")

# ───────────────────────────── CONFIG ─────────────────────────────
COOLDOWN_SECONDS = 10                 # 1 link per user per 10s
DELETE_AFTER_DM = 600                 # DM: delete sent video after 10 minutes
DELETE_AFTER_GROUP = 300              # group: delete sent video after 5 minutes
MAX_FILESIZE = 1_900_000_000          # ~1.9 GB (MTProto upload limit is 2 GB)
MAX_PARALLEL_DOWNLOADS = 3            # global concurrency cap
NOTIFY_ON_PLAIN_TEXT = False          # True = also answer DMs that contain no link at all
COOKIES_FILE = os.getenv("YTDLP_COOKIES")

EMOJI_NOT_FOUND = 5535088426400874513
EMOJI_ANALYSING = 5573473356579078196
EMOJI_DOWNLOADED = 5537203062138994712

JOIN_TEXT = "Join For No reason"
JOIN_URL = "https://t.me/hmstalker"

# ───────────────────────────── URL DETECTION ─────────────────────────────
_URL_RE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)

PLATFORMS = {
    "youtube": ("youtube.com", "youtu.be"),
    "x": ("x.com", "twitter.com"),
    "instagram": ("instagram.com", "instagr.am"),
}
PLATFORM_LABEL = {"youtube": "YouTube", "x": "X", "instagram": "Instagram"}

DM_PLATFORMS = ("youtube", "x", "instagram")
GROUP_PLATFORMS = ("x", "instagram")   # groups: NO youtube


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _valid_video_path(platform: str, host: str, path: str) -> bool:
    path = path or "/"
    if platform == "youtube":
        if _host_matches(host, "youtu.be"):
            return len(path.strip("/")) >= 6
        return path.startswith(("/watch", "/shorts/", "/live/", "/embed/"))
    if platform == "x":
        return "/status/" in path or "/statuses/" in path
    if platform == "instagram":
        return path.startswith(
            ("/reel/", "/reels/", "/p/", "/tv/", "/stories/", "/share/")
        ) or bool(re.match(r"^/[^/]+/(reel|p|tv)/", path))
    return False


def classify(url: str, allowed=DM_PLATFORMS):
    """Return (platform, clean_url) or (None, None). Only platforms in `allowed` match."""
    raw = url.strip().rstrip(".,;:!?)\"'")
    if not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return None, None
    host = (parsed.hostname or "").lower()
    for platform, domains in PLATFORMS.items():
        if any(_host_matches(host, d) for d in domains):
            if platform in allowed and _valid_video_path(platform, host, parsed.path):
                return platform, raw
            return None, None
    return None, None


def extract_urls(message: Message) -> list:
    urls = []
    text = message.text or ""
    urls.extend(m.group(0) for m in _URL_RE.finditer(text))
    for ent in message.entities or []:
        link = getattr(ent, "url", None)
        if link:
            urls.append(link)
    return urls


# ───────────────────────────── SPAM PROTECTION ─────────────────────────────
_last_request: dict = {}
_active_users: set = set()
_semaphore = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
_bg_tasks: set = set()


def _spawn(coro):
    """Run a coroutine in the background and keep a reference so it isn't GC'd."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def _cooldown_left(user_id: int) -> float:
    now = time.monotonic()
    if len(_last_request) > 5000:  # prune stale entries
        for uid, ts in list(_last_request.items()):
            if now - ts > COOLDOWN_SECONDS:
                _last_request.pop(uid, None)
    last = _last_request.get(user_id)
    if last is None:
        return 0.0
    return max(0.0, COOLDOWN_SECONDS - (now - last))


# ───────────────────────────── RICH THINKING DRAFT (DM only) ─────────────────────────────
def _emoji(emoji_id: int, fallback: str = "⭐") -> str:
    return f"<emoji id={emoji_id}>{fallback}</emoji>"


RICH_EMOJI_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected


def _rich_emoji(emoji_id: int) -> str:
    if RICH_EMOJI_STYLE == 0:
        return f"<emoji id={emoji_id}>⭐</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">⭐</tg-emoji>'


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

        html = f"{_emoji(emoji_id)} {text}"
        try:
            if self.fallback_msg is None:
                self.fallback_msg = await self.client.send_message(
                    self.chat_id, html, parse_mode=ParseMode.HTML
                )
            else:
                await self.fallback_msg.edit_text(html, parse_mode=ParseMode.HTML)
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


# ───────────────────────────── KEYBOARD ─────────────────────────────
def join_keyboard() -> InlineKeyboardMarkup:
    if ButtonStyle is not None:
        try:
            btn = InlineKeyboardButton(JOIN_TEXT, url=JOIN_URL, style=ButtonStyle.PRIMARY)
            return InlineKeyboardMarkup([[btn]])
        except Exception:
            pass
    return InlineKeyboardMarkup([[InlineKeyboardButton(JOIN_TEXT, url=JOIN_URL)]])


# ───────────────────────────── DOWNLOADER ─────────────────────────────
class DownloadFailed(Exception):
    pass


def _ydl_download(url: str, out_dir: str) -> dict:
    opts = {
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "format": (
            "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/"
            "bv*[height<=1080]+ba/b[height<=1080]/b"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "playlist_items": "1",
        "max_filesize": MAX_FILESIZE,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 20,
        "restrictfilenames": True,
        "geo_bypass": True,
        "cachedir": False,          # don't write yt-dlp cache to disk
        "writethumbnail": False,
        "writeinfojson": False,
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if "no video" in msg or "there is no video" in msg:
            raise DownloadFailed("That post has no video in it.")
        if "login" in msg or "cookies" in msg or "private" in msg or "sign in" in msg:
            raise DownloadFailed("That video is private or needs login.")
        if "larger than" in msg or "max-filesize" in msg:
            raise DownloadFailed("That video is too large for me.")
        raise DownloadFailed("Couldn't download that video.")
    if info and "entries" in info:
        entries = [e for e in info["entries"] if e]
        info = entries[0] if entries else None
    if not info:
        raise DownloadFailed("Couldn't download that video.")

    path = None
    for d in info.get("requested_downloads") or []:
        if d.get("filepath") and os.path.exists(d["filepath"]):
            path = d["filepath"]
            break
    if not path:
        files = [f for f in glob.glob(os.path.join(out_dir, "*")) if os.path.isfile(f)]
        if not files:
            raise DownloadFailed("Couldn't download that video.")
        path = max(files, key=os.path.getsize)
    return {
        "path": path,
        "duration": int(info.get("duration") or 0),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
    }


async def _make_thumb(video_path: str, out_dir: str):
    thumb = os.path.join(out_dir, "thumb.jpg")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-ss", "1", "-i", video_path,
            "-vframes", "1", "-vf", "scale=320:-2", thumb,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=20)
        if os.path.exists(thumb) and os.path.getsize(thumb) > 0:
            return thumb
    except Exception:
        pass
    return None


async def _delete_later(client, chat_id: int, msgs: list, delay: int):
    await asyncio.sleep(delay)
    ids = [getattr(m, "id", None) for m in msgs if m is not None]
    ids = [i for i in ids if i]
    if ids:
        try:
            await client.delete_messages(chat_id, ids)
        except Exception:
            pass


# ───────────────────────────── MESSAGES ─────────────────────────────
NOT_FOUND_TEXT = (
    f"<b>{_emoji(EMOJI_NOT_FOUND)} Didn't find anything</b>\n\n"
    "<blockquote>Can you try sending a url from <b>X</b>, <b>Instagram</b> or <b>YouTube</b>? "
    "I'll download it for you.</blockquote>"
)

GROUP_DOWNLOADING_TEXT = (
    f"<blockquote>{_emoji(EMOJI_ANALYSING)} <b>Downloading...</b></blockquote>"
)


def caption_rich_html(minutes: int) -> str:
    """Rich HTML document: text + <tg-button-row> with a primary URL button (per Bot API 10.3)."""
    return (
        f"<p><b>{_rich_emoji(EMOJI_DOWNLOADED)} Downloaded</b></p>"
        f"<blockquote>This message will be deleted in {minutes} minutes, "
        "send it to your saved messages to save it!!</blockquote>"
        f'<tg-button-row><tg-button type="url" url="{JOIN_URL}" style="primary">'
        f"{JOIN_TEXT}</tg-button></tg-button-row>"
    )


async def send_caption_message(client, chat_id: int, minutes: int, reply_to: int = None):
    """Caption as a real rich message with a rich button inside it (replying to the video if given)."""
    global RICH_EMOJI_STYLE
    last_err = None
    if InputRichMessage is not None and hasattr(client, "send_rich_message"):
        for _ in range(2):  # 2nd pass = other emoji tag style
            rich = InputRichMessage(html=caption_rich_html(minutes))
            # try every reply flavour kurigram builds may expose, first one that works wins
            attempts = []
            if reply_to:
                if ReplyParameters is not None:
                    attempts.append(dict(reply_parameters=ReplyParameters(message_id=reply_to)))
                attempts.append(dict(reply_to_message_id=reply_to))
            else:
                attempts.append({})
            style_rejected = False
            for extra in attempts:
                try:
                    return await client.send_rich_message(chat_id, rich, **extra)
                except TypeError as e:  # kwarg not supported by this build
                    last_err = e
                    continue
                except Exception as e:  # real API error (e.g. bad emoji tag style)
                    last_err = e
                    style_rejected = True
                    break
            if style_rejected:
                RICH_EMOJI_STYLE = 1 - RICH_EMOJI_STYLE
            else:
                break  # no reply kwarg worked at all, flipping emoji style won't help
        log.warning("rich reply failed, using plain reply fallback: %r", last_err)
    icon = _emoji(EMOJI_DOWNLOADED)
    return await client.send_message(
        chat_id,
        f"<b>{icon} Downloaded</b>\n\n<blockquote>This message will be deleted in {minutes} minutes, "
        "send it to your saved messages to save it!!</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=join_keyboard(),
        reply_to_message_id=reply_to,
    )


# ───────────────────────────── CORE WORKER ─────────────────────────────
async def _process(client, message: Message, clean_url: str, platform: str, is_group: bool):
    """Runs in the background so the update handler returns instantly (never blocks commands)."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    label = PLATFORM_LABEL[platform]
    delete_after = DELETE_AFTER_GROUP if is_group else DELETE_AFTER_DM
    minutes = delete_after // 60

    thinking = None
    status_msg = None
    work_dir = tempfile.mkdtemp(prefix="dl_")

    try:
        # ── status ──
        if is_group:
            try:
                status_msg = await message.reply_text(GROUP_DOWNLOADING_TEXT, parse_mode=ParseMode.HTML)
            except Exception:
                status_msg = None
        else:
            thinking = Thinking(client, chat_id)
            await thinking.show(EMOJI_ANALYSING, "Analysing....")
            await asyncio.sleep(0.8)
            await thinking.show(EMOJI_ANALYSING, f"Oh, it's an {label} url, let me download it for you")

        # ── download ──
        async with _semaphore:
            result = await asyncio.to_thread(_ydl_download, clean_url, work_dir)

        if thinking:
            await thinking.show(EMOJI_DOWNLOADED, "Downloaded!")
            await asyncio.sleep(1)
            await thinking.show(EMOJI_ANALYSING, "Sending file to you....")

        try:
            await client.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
        except Exception:
            pass

        # ── send video first ──
        thumb = await _make_thumb(result["path"], work_dir)
        kwargs = dict(
            video=result["path"],
            supports_streaming=True,
            duration=result["duration"],
            width=result["width"],
            height=result["height"],
        )
        if thumb:
            kwargs["thumb"] = thumb
        video_msg = await message.reply_video(**kwargs)

        # video is uploaded -> wipe the local file right now (nothing kept on server)
        shutil.rmtree(work_dir, ignore_errors=True)

        # ── then the rich "Downloaded" message (reply to the video in groups) ──
        caption_msg = await send_caption_message(
            client, chat_id, minutes, reply_to=video_msg.id
        )

        # group "Downloading..." message is no longer needed
        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                pass
            status_msg = None

        _spawn(_delete_later(client, chat_id, [video_msg, caption_msg], delete_after))

    except DownloadFailed as e:
        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                pass
            status_msg = None
        try:
            await message.reply_text(
                f"<b>{_emoji(EMOJI_NOT_FOUND)} {e}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=join_keyboard(),
            )
        except Exception:
            pass
    except Exception:
        log.exception("download handler failed")
        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                pass
            status_msg = None
        try:
            await message.reply_text(
                f"<b>{_emoji(EMOJI_NOT_FOUND)} Something went wrong, please try again later.</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    finally:
        _active_users.discard(user_id)
        if thinking:
            await thinking.close()
        shutil.rmtree(work_dir, ignore_errors=True)


# ───────────────────────────── HANDLER ─────────────────────────────
def _not_command(_, __, m: Message) -> bool:
    return bool(m.text) and not m.text.startswith("/")


not_command = filters.create(_not_command)

# Own handler group (-10) + the real work runs in a background task, and
# stop_propagation is never called -> your commands / other handlers are never blocked.
@app.on_message(
    (filters.private | filters.group) & filters.text & not_command & ~filters.bot & ~filters.via_bot,
    group=-10,
)
async def social_downloader(client, message: Message):
    if not message.from_user:
        return

    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    allowed = GROUP_PLATFORMS if is_group else DM_PLATFORMS

    candidates = extract_urls(message)
    if not candidates:
        if not is_group and NOTIFY_ON_PLAIN_TEXT:
            await message.reply_text(NOT_FOUND_TEXT, parse_mode=ParseMode.HTML, reply_markup=join_keyboard())
        return

    platform, clean_url = None, None
    for c in candidates:
        platform, clean_url = classify(c, allowed)
        if platform:
            break

    if not platform:
        # groups: any other url (youtube, random links...) -> no reply at all
        if not is_group:
            await message.reply_text(NOT_FOUND_TEXT, parse_mode=ParseMode.HTML, reply_markup=join_keyboard())
        return

    user_id = message.from_user.id

    if user_id in _active_users:
        if not is_group:  # keep groups quiet
            await message.reply_text(
                "⏳ <b>Hold on</b>, I'm still downloading your previous link.", parse_mode=ParseMode.HTML
            )
        return

    left = _cooldown_left(user_id)
    if left > 0:
        if not is_group:
            await message.reply_text(
                f"⏳ <b>Slow down!</b> You can send 1 link every {COOLDOWN_SECONDS} seconds. "
                f"Try again in <b>{int(left) + 1}s</b>.",
                parse_mode=ParseMode.HTML,
            )
        return

    _last_request[user_id] = time.monotonic()
    _active_users.add(user_id)

    # hand off to a background task and return immediately
    _spawn(_process(client, message, clean_url, platform, is_group))
