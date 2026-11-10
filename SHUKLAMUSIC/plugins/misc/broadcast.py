import asyncio
import html
import logging
import re

from pyrogram import filters
from pyrogram.enums import ChatMembersFilter, ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from SHUKLAMUSIC import app
from SHUKLAMUSIC.misc import SUDOERS
from SHUKLAMUSIC.utils.database import (
    get_active_chats,
    get_authuser_names,
    get_served_chats,
    get_served_users,
)
from SHUKLAMUSIC.utils.formatters import alpha_to_int
from config import adminlist

try:  # kurigram: button styles
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

try:  # kurigram: rich messages
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

log = logging.getLogger("broadcast")

# ───────────────────────────── CONFIG ─────────────────────────────
IS_BROADCASTING = False  # kept as a module global (other plugins may read it)

TEST_USER_ID = 7659846392
TEST_GROUP_ID = -1002329141957

SEND_DELAY = 0.2          # seconds between recipients
MAX_FLOOD_WAIT = 200      # skip a recipient if Telegram asks for more than this
PROGRESS_EVERY = 50       # update the status message every N recipients

VALID_STYLES = ("primary", "success", "danger")

# 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>  (same convention as download.py, auto-detected)
RICH_EMOJI_STYLE = 1

SESSIONS: dict = {}       # admin user_id -> session dict
_tasks: set = set()


# ───────────────────────────── SMALL HELPERS ─────────────────────────────
def _is_forwarded(m: Message) -> bool:
    for attr in (
        "forward_origin",
        "forward_date",
        "forward_from",
        "forward_from_chat",
        "forward_sender_name",
    ):
        if getattr(m, attr, None):
            return True
    return False


def _rich_supported(client) -> bool:
    return InputRichMessage is not None and hasattr(client, "send_rich_message")


def _style_enum(name):
    if not name or ButtonStyle is None:
        return None
    return getattr(ButtonStyle, name.upper(), None)


def _cb(text: str, action: str, uid: int, style: str = None) -> InlineKeyboardButton:
    data = f"bc:{action}:{uid}"
    st = _style_enum(style)
    if st is not None:
        try:
            return InlineKeyboardButton(text, callback_data=data, style=st)
        except Exception:
            pass
    return InlineKeyboardButton(text, callback_data=data)


def _kb(uid: int, rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_cb(t, a, uid, s) for (t, a, s) in row] for row in rows]
    )


def _yes_no(uid: int, yes: str, no: str) -> InlineKeyboardMarkup:
    return _kb(uid, [[("✅ Yes", yes, "success"), ("❌ No", no, "danger")]])


async def _edit(msg, text, kb=None):
    try:
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        log.debug("edit failed: %r", e)


# ───────────────────────────── BUTTON PARSING ─────────────────────────────
_EMOJI_RE = re.compile(r'<emoji id="?(\d+)"?>.*?</emoji>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'<a href="([^"]+)">')


def _norm_url(raw: str):
    raw = (raw or "").strip()
    if not raw or " " in raw:
        return None
    if raw.startswith("@") and len(raw) > 1:
        return "https://t.me/" + raw[1:]
    if raw.lower().startswith(("http://", "https://", "tg://")):
        return raw
    if "." in raw:
        return "https://" + raw
    return None


def parse_buttons(message: Message, rich: bool):
    """
    One button per line, buttons on the same row separated by '&&'.
      simple: text | link
      rich  : text | link | style      (primary / success / danger, default primary)
    A premium emoji anywhere in the text part becomes the button's custom emoji.
    Returns rows: list[list[dict(text, url, style, emoji)]]
    """
    if not message.text:
        raise ValueError("Send the buttons as a plain text message.")

    rows = []
    for line in message.text.html.split("\n"):
        if not line.strip():
            continue
        row = []
        for part in re.split(r"\s*&amp;&amp;\s*", line):
            if not part.strip():
                continue
            emoji_ids = [int(x) for x in _EMOJI_RE.findall(part)]
            hrefs = _HREF_RE.findall(part)
            clean = html.unescape(_TAG_RE.sub("", _EMOJI_RE.sub("", part)))
            fields = [f.strip() for f in clean.split("|")]
            if len(fields) < 2:
                raise ValueError(
                    f"Wrong format: <code>{html.escape(clean.strip())}</code>\n"
                    + ("Use <code>text | link | style</code>" if rich else "Use <code>text | link</code>")
                )
            text = fields[0]
            url = _norm_url(fields[1]) or (html.unescape(hrefs[0]) if hrefs else None)
            if not text:
                raise ValueError(f"Button text is empty in: <code>{html.escape(clean.strip())}</code>")
            if not url:
                raise ValueError(f"Invalid link: <code>{html.escape(fields[1])}</code>")
            style = None
            if rich:
                style = (fields[2].lower() if len(fields) > 2 and fields[2] else "primary")
                if style not in VALID_STYLES:
                    raise ValueError(
                        f"Unknown style <code>{html.escape(style)}</code>. "
                        "Use primary, success or danger."
                    )
            row.append(
                {"text": text, "url": url, "style": style, "emoji": emoji_ids[0] if emoji_ids else None}
            )
        if row:
            if len(row) > 8:
                raise ValueError("Max 8 buttons per row.")
            rows.append(row)
    if not rows:
        raise ValueError("I couldn't find any button in your message.")
    return rows


# ───────────────────────────── KEYBOARD (simple / styled) ─────────────────────────────
def _make_button(b: dict, styled: bool, use_emoji: bool) -> InlineKeyboardButton:
    full = {}
    st = _style_enum(b["style"]) if styled else None
    if st is not None:
        full["style"] = st
    if use_emoji and b["emoji"]:
        full["icon_custom_emoji_id"] = b["emoji"]
    candidates = (
        full,
        {k: v for k, v in full.items() if k != "icon_custom_emoji_id"},
        {k: v for k, v in full.items() if k != "style"},
        {},
    )
    for kw in candidates:
        try:
            return InlineKeyboardButton(b["text"], url=b["url"], **kw)
        except TypeError:  # this Kurigram build doesn't know that kwarg
            continue
    return InlineKeyboardButton(b["text"], url=b["url"])


def _keyboard(sess: dict, styled: bool) -> InlineKeyboardMarkup:
    use_emoji = sess.get("btn_emoji_ok", True)
    return InlineKeyboardMarkup(
        [[_make_button(b, styled, use_emoji) for b in row] for row in sess["buttons"]]
    )


# ───────────────────────────── RICH HTML ─────────────────────────────
def _rich_emoji(emoji_id: int, style: int, fallback: str = "⭐") -> str:
    if style == 0:
        return f"<emoji id={emoji_id}>{fallback}</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def _body_to_rich(src_html: str, style: int) -> str:
    """Pyrogram .html of the original text/caption -> rich-message HTML."""
    s = _EMOJI_RE.sub(
        lambda m: _rich_emoji(int(m.group(1)), style, re.sub(r"<[^>]+>", "", m.group(0)) or "⭐"),
        src_html,
    )
    s = s.replace("<spoiler>", "<tg-spoiler>").replace("</spoiler>", "</tg-spoiler>")
    out = []
    for part in re.split(r"(<blockquote.*?</blockquote>|<pre.*?</pre>)", s, flags=re.S):
        if not part or not part.strip():
            continue
        if part.startswith(("<blockquote", "<pre")):
            out.append(part)
        else:
            out.append("<p>" + part.strip("\n").replace("\n", "<br>") + "</p>")
    return "".join(out)


def _rich_buttons(buttons, style: int, with_emoji: bool) -> str:
    rows = []
    for row in buttons:
        items = []
        for b in row:
            attrs = f'type="url" url="{html.escape(b["url"], quote=True)}"'
            if b["style"]:
                attrs += f' style="{b["style"]}"'
            label = html.escape(b["text"])
            if with_emoji and b["emoji"]:
                label = _rich_emoji(b["emoji"], style) + " " + label
            items.append(f"<tg-button {attrs}>{label}</tg-button>")
        rows.append("<tg-button-row>" + "".join(items) + "</tg-button-row>")
    return "".join(rows)


def _src_html(src: Message) -> str:
    if src.text:
        return src.text.html
    if src.caption:
        return src.caption.html
    return ""


def build_rich_html(sess: dict, style: int, with_btn_emoji: bool) -> str:
    return _body_to_rich(_src_html(sess["src"]), style) + _rich_buttons(
        sess["buttons"], style, with_btn_emoji
    )


# ───────────────────────────── DELIVERY ─────────────────────────────
async def _copy(client, chat_id, src: Message, kb=None, caption=None):
    kwargs = {}
    if kb is not None:
        kwargs["reply_markup"] = kb
    if caption is not None:
        kwargs["caption"] = caption
    return await client.copy_message(chat_id, src.chat.id, src.id, **kwargs)


async def _probe(client, chat_id: int, sess: dict):
    """
    Sends ONE preview to chat_id and decides how the real broadcast will be delivered:
      plain  -> copy as is
      simple -> copy + normal inline keyboard
      rich   -> rich message (HTML + <tg-button-row>)   [media is sent first, rich message below it]
      styled -> copy + inline keyboard with button styles (fallback when rich is not available)
    Raises on errors the admin should fix (e.g. bad URL).
    """
    global RICH_EMOJI_STYLE
    src: Message = sess["src"]
    sess.pop("note", None)

    if not sess["buttons"]:
        sess["mode"] = "plain"
        return await _copy(client, chat_id, src)

    has_btn_emoji = any(b["emoji"] for row in sess["buttons"] for b in row)

    # ── simple ──
    if sess["btn_type"] == "simple":
        sess["btn_emoji_ok"] = True
        kb = _keyboard(sess, styled=False)
        try:
            m = await _copy(client, chat_id, src, kb)
        except FloodWait:
            raise
        except Exception:
            if not has_btn_emoji:
                raise
            sess["btn_emoji_ok"] = False
            kb = _keyboard(sess, styled=False)
            m = await _copy(client, chat_id, src, kb)
            sess["note"] = "⚠️ Custom emoji in buttons isn't supported here, so they were removed."
        sess["kb"] = kb
        sess["mode"] = "simple"
        return m

    # ── rich ──
    media_msg = None
    if _rich_supported(client):
        if not src.text:  # media: send it without caption, caption goes into the rich message
            media_msg = await _copy(client, chat_id, src, caption="")
        attempts = [(RICH_EMOJI_STYLE, True), (1 - RICH_EMOJI_STYLE, True)]
        if has_btn_emoji:
            attempts.append((RICH_EMOJI_STYLE, False))
        seen, last_err = set(), None
        for st, be in attempts:
            doc = build_rich_html(sess, st, be)
            if doc in seen:
                continue
            seen.add(doc)
            try:
                m = await client.send_rich_message(chat_id, InputRichMessage(html=doc))
            except FloodWait:
                raise
            except Exception as e:
                last_err = e
                continue
            RICH_EMOJI_STYLE = st
            sess.update(mode="rich", rich_html=doc)
            if has_btn_emoji and not be:
                sess["note"] = "⚠️ Custom emoji inside rich buttons was rejected, so they were removed."
            return m
        log.warning("rich message rejected, falling back to styled keyboard: %r", last_err)
        sess["note"] = "⚠️ Rich messages were rejected here, using styled inline buttons instead."
    else:
        sess["note"] = "⚠️ This Kurigram build has no rich messages, using styled inline buttons instead."

    if media_msg is not None:  # redo with caption + keyboard
        try:
            await media_msg.delete()
        except Exception:
            pass
    sess["btn_emoji_ok"] = True
    kb = _keyboard(sess, styled=True)
    try:
        m = await _copy(client, chat_id, src, kb)
    except FloodWait:
        raise
    except Exception:
        if not has_btn_emoji:
            raise
        sess["btn_emoji_ok"] = False
        kb = _keyboard(sess, styled=True)
        m = await _copy(client, chat_id, src, kb)
    sess["kb"] = kb
    sess["mode"] = "styled"
    return m


async def _send_one(client, chat_id: int, sess: dict):
    """Deliver to one chat using the already-decided mode. Returns the last sent Message or None."""
    src: Message = sess["src"]
    media_done = False
    for _ in range(3):
        try:
            mode = sess["mode"]
            if mode == "forward":
                m = await client.forward_messages(chat_id, src.chat.id, src.id)
            elif mode == "plain":
                m = await _copy(client, chat_id, src)
            elif mode in ("simple", "styled"):
                m = await _copy(client, chat_id, src, sess["kb"])
            else:  # rich
                if not src.text and not media_done:
                    await _copy(client, chat_id, src, caption="")
                    media_done = True
                m = await client.send_rich_message(
                    chat_id, InputRichMessage(html=sess["rich_html"])
                )
            return m[-1] if isinstance(m, list) else m
        except FloodWait as fw:
            if int(fw.value) > MAX_FLOOD_WAIT:
                return None
            await asyncio.sleep(int(fw.value) + 1)
        except Exception:
            return None
    return None


async def _pin(client, chat_id: int, m) -> bool:
    for _ in range(2):
        try:
            await client.pin_chat_message(chat_id, m.id, disable_notification=True)
            return True
        except FloodWait as fw:
            if int(fw.value) > MAX_FLOOD_WAIT:
                return False
            await asyncio.sleep(int(fw.value) + 1)
        except Exception:
            return False
    return False


async def _collect_targets(sess: dict):
    if sess["test"]:
        return [(TEST_GROUP_ID, "group"), (TEST_USER_ID, "user")]
    targets, seen = [], set()
    if sess["target"] in ("groups", "both"):
        try:
            for c in await get_served_chats():
                cid = int(c["chat_id"])
                if cid not in seen:
                    seen.add(cid)
                    targets.append((cid, "group"))
        except Exception:
            log.exception("get_served_chats failed")
    if sess["target"] in ("users", "both"):
        try:
            for u in await get_served_users():
                uid = int(u["user_id"])
                if uid not in seen:
                    seen.add(uid)
                    targets.append((uid, "user"))
        except Exception:
            log.exception("get_served_users failed")
    return targets


# ───────────────────────────── TEXTS ─────────────────────────────
STEP_ACTIONS = {
    "fwd_confirm": {"fwd_yes", "fwd_no"},
    "ask_buttons": {"btn_yes", "btn_no"},
    "ask_type": {"type_simple", "type_rich"},
    "ask_pin": {"pin_yes", "pin_no"},
    "ask_target": {"tgt_users", "tgt_groups", "tgt_both"},
    "final": {"go_yes", "go_no"},
    "running": {"stop"},
}

TARGET_LABEL = {"users": "Users only", "groups": "Groups only", "both": "Users + Groups"}

PROMPT_CONTENT = (
    "📢 <b>Broadcast</b>\n\n"
    "Send or forward the message you want to broadcast:\n"
    "• <b>Forward</b> a message → it gets forwarded as is\n"
    "• <b>Send</b> text / image with caption / any media → sent with formatting preserved\n\n"
    "/cancel to abort."
)

PROMPT_SIMPLE = (
    "🔘 <b>Simple buttons</b>\n\n"
    "Send your buttons, one per line:\n"
    "<code>text | link</code>\n\n"
    "• Same row: <code>Text | link &amp;&amp; Text 2 | link 2</code>\n"
    "• Custom emoji: just put a premium emoji in the text\n"
    "• <code>@username</code> works as a link too\n\n"
    "/cancel to abort."
)

PROMPT_RICH = (
    "✨ <b>Rich buttons</b>\n\n"
    "Send your buttons, one per line:\n"
    "<code>text | link | style</code>\n\n"
    "• Styles: <code>primary</code>, <code>success</code>, <code>danger</code> (default primary)\n"
    "• Same row: <code>Text | link | success &amp;&amp; Text 2 | link 2 | danger</code>\n"
    "• Custom emoji: just put a premium emoji in the text\n\n"
    "/cancel to abort."
)


async def _counts_text(sess: dict) -> str:
    if sess["test"]:
        return f"🧪 TEST → user <code>{TEST_USER_ID}</code> + group <code>{TEST_GROUP_ID}</code>"
    parts = []
    try:
        if sess["target"] in ("groups", "both"):
            parts.append(f"{len(await get_served_chats())} groups")
        if sess["target"] in ("users", "both"):
            parts.append(f"{len(await get_served_users())} users")
    except Exception:
        pass
    label = TARGET_LABEL[sess["target"]]
    return f"👥 Send to: <b>{label}</b>" + (f" ({', '.join(parts)})" if parts else "")


async def _summary(sess: dict) -> str:
    lines = []
    if sess["forwarded"]:
        lines.append("📨 Type: <b>Forward</b> (original sender stays visible)")
    else:
        lines.append("📝 Type: <b>New message</b> (formatting preserved)")
        if sess["buttons"]:
            n = sum(len(r) for r in sess["buttons"])
            kind = {"simple": "simple", "rich": "rich"}[sess["btn_type"]]
            mode = sess.get("mode")
            extra = " → styled fallback" if mode == "styled" else ""
            lines.append(f"🔘 Buttons: <b>{n}</b> ({kind}{extra})")
        else:
            lines.append("🔘 Buttons: <b>none</b>")
    lines.append(f"📌 Pin in groups: <b>{'Yes' if sess['pin'] else 'No'}</b>")
    lines.append(await _counts_text(sess))
    return "\n".join(lines)


# ───────────────────────────── FLOW STEPS ─────────────────────────────
async def _ask_pin(sess: dict, send):
    sess["step"] = "ask_pin"
    await send(
        "📌 <b>Pin the message in groups?</b>",
        _yes_no(sess["uid"], "pin_yes", "pin_no"),
    )


async def _after_pin(sess: dict, send):
    if sess["test"]:
        return await _ask_final(sess, send)
    sess["step"] = "ask_target"
    uid = sess["uid"]
    await send(
        "👥 <b>Whom should I send it to?</b>",
        _kb(
            uid,
            [
                [("👤 Users only", "tgt_users", None), ("👥 Groups only", "tgt_groups", None)],
                [("🌐 Both", "tgt_both", "primary")],
            ],
        ),
    )


async def _ask_final(sess: dict, send):
    sess["step"] = "final"
    title = "🧪 <b>Test broadcast – final confirmation</b>" if sess["test"] else "⚠️ <b>Final confirmation</b>"
    await send(
        f"{title}\n\n{await _summary(sess)}\n\nStart the broadcast?",
        _yes_no(sess["uid"], "go_yes", "go_no"),
    )


async def _receive_content(client, admin_msg: Message, src: Message, sess: dict):
    sess["src"] = src
    sess["forwarded"] = _is_forwarded(src)
    uid = sess["uid"]
    if sess["forwarded"]:
        sess["step"] = "fwd_confirm"
        sess["mode"] = "forward"
        await src.reply_text(
            "📨 <b>You forwarded a message.</b>\nDo you want me to broadcast it?",
            parse_mode=ParseMode.HTML,
            reply_markup=_yes_no(uid, "fwd_yes", "fwd_no"),
        )
    else:
        sess["step"] = "ask_buttons"
        await src.reply_text(
            "✅ <b>Message received</b> (formatting will be preserved).\n"
            "Do you want to add buttons to it?",
            parse_mode=ParseMode.HTML,
            reply_markup=_yes_no(uid, "btn_yes", "btn_no"),
        )


def _new_session(message: Message, test: bool) -> dict:
    return {
        "uid": message.from_user.id,
        "chat_id": message.chat.id,
        "test": test,
        "step": "await_content",
        "src": None,
        "forwarded": False,
        "buttons": [],
        "btn_type": None,
        "pin": False,
        "target": "both",
        "mode": None,
        "stop": False,
    }


async def _start(client, message: Message, test: bool):
    if not message.from_user:
        return
    if IS_BROADCASTING:
        return await message.reply_text("⏳ A broadcast is already running. Use /cancel to stop it.")
    sess = _new_session(message, test)
    SESSIONS[sess["uid"]] = sess
    if message.reply_to_message:  # /broadcast as a reply = use that message directly
        return await _receive_content(client, message, message.reply_to_message, sess)
    head = "🧪 <b>TEST MODE</b> – only the test user & group will receive it.\n\n" if test else ""
    await message.reply_text(head + PROMPT_CONTENT, parse_mode=ParseMode.HTML)


# ───────────────────────────── COMMANDS ─────────────────────────────
@app.on_message(filters.command("broadcast") & SUDOERS)
async def broadcast_cmd(client, message: Message):
    await _start(client, message, test=False)


@app.on_message(filters.command("testbroadcast") & SUDOERS)
async def testbroadcast_cmd(client, message: Message):
    await _start(client, message, test=True)


async def _has_session(_, __, m: Message) -> bool:
    return bool(m.from_user) and m.from_user.id in SESSIONS


@app.on_message(filters.command("cancel") & filters.create(_has_session), group=-20)
async def cancel_cmd(client, message: Message):
    sess = SESSIONS.get(message.from_user.id)
    if sess:
        if sess["step"] == "running":
            sess["stop"] = True
            await message.reply_text("🛑 Stopping the broadcast…")
        else:
            SESSIONS.pop(message.from_user.id, None)
            await message.reply_text("❌ Broadcast cancelled.")
    message.stop_propagation()


# ───────────────────────────── MESSAGE CAPTURE ─────────────────────────────
def _waiting(step: str):
    async def _f(_, __, m: Message) -> bool:
        if not m.from_user or getattr(m, "service", None):
            return False
        s = SESSIONS.get(m.from_user.id)
        if not s or s["chat_id"] != m.chat.id or s["step"] != step:
            return False
        if (m.text or "").startswith("/"):
            return False  # let commands through (/cancel etc.)
        return True

    return filters.create(_f)


# group=-20 runs before the download.py handler (group -10), so a link sent as broadcast
# text is not picked up by the downloader.
@app.on_message(_waiting("await_content"), group=-20)
async def got_content(client, message: Message):
    sess = SESSIONS.get(message.from_user.id)
    if sess:
        await _receive_content(client, message, message, sess)
    message.stop_propagation()


@app.on_message(_waiting("await_buttons"), group=-20)
async def got_buttons(client, message: Message):
    sess = SESSIONS.get(message.from_user.id)
    if not sess:
        return message.stop_propagation()

    try:
        sess["buttons"] = parse_buttons(message, rich=sess["btn_type"] == "rich")
    except ValueError as e:
        await message.reply_text(f"❌ {e}\n\nTry again, or /cancel.", parse_mode=ParseMode.HTML)
        return message.stop_propagation()

    sess["step"] = "previewing"
    try:
        await message.reply_text("👀 <b>Preview</b> – this is how it will look:", parse_mode=ParseMode.HTML)
        await _probe(client, message.chat.id, sess)
    except FloodWait as fw:
        sess["step"] = "await_buttons"
        await message.reply_text(f"⏳ Flood wait, try again in {fw.value}s.")
        return message.stop_propagation()
    except Exception as e:
        sess["step"] = "await_buttons"
        await message.reply_text(
            f"❌ Telegram rejected those buttons: <code>{html.escape(str(e)[:300])}</code>\n\n"
            "Send them again (check the links), or /cancel.",
            parse_mode=ParseMode.HTML,
        )
        return message.stop_propagation()

    note = sess.pop("note", None)
    if note:
        await message.reply_text(note)

    async def send(text, kb):
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    await _ask_pin(sess, send)
    message.stop_propagation()


# ───────────────────────────── CALLBACKS ─────────────────────────────
def _stop_kb(uid: int) -> InlineKeyboardMarkup:
    return _kb(uid, [[("🛑 Stop", "stop", "danger")]])


@app.on_callback_query(filters.regex(r"^bc:"))
async def broadcast_cb(client, cq):
    try:
        _, action, uid_s = cq.data.split(":")
        uid = int(uid_s)
    except Exception:
        return await cq.answer()

    sess = SESSIONS.get(uid)
    if cq.from_user.id != uid:
        return await cq.answer("This isn't your broadcast.", show_alert=True)
    if not sess:
        return await cq.answer("Session expired. Use /broadcast again.", show_alert=True)
    if action not in STEP_ACTIONS.get(sess["step"], ()):
        return await cq.answer("This button is outdated.")

    await cq.answer()
    msg = cq.message

    async def send(text, kb):
        await _edit(msg, text, kb)

    if action in ("fwd_no", "go_no"):
        SESSIONS.pop(uid, None)
        return await _edit(msg, "❌ Broadcast cancelled.")

    if action == "fwd_yes":
        return await _ask_pin(sess, send)

    if action == "btn_yes":
        sess["step"] = "ask_type"
        return await _edit(
            msg,
            "🔘 <b>Which kind of buttons?</b>\n\n"
            "• <b>Simple</b> – normal inline buttons\n"
            "• <b>Rich</b> – rich message with coloured buttons",
            _kb(uid, [[("Simple", "type_simple", None), ("✨ Rich", "type_rich", "primary")]]),
        )

    if action == "btn_no":
        sess["buttons"] = []
        sess["mode"] = "plain"
        return await _ask_pin(sess, send)

    if action in ("type_simple", "type_rich"):
        sess["btn_type"] = "simple" if action == "type_simple" else "rich"
        sess["step"] = "await_buttons"
        return await _edit(msg, PROMPT_SIMPLE if action == "type_simple" else PROMPT_RICH)

    if action in ("pin_yes", "pin_no"):
        sess["pin"] = action == "pin_yes"
        return await _after_pin(sess, send)

    if action.startswith("tgt_"):
        sess["target"] = action[4:]
        return await _ask_final(sess, send)

    if action == "go_yes":
        if IS_BROADCASTING:
            return await _edit(msg, "⏳ Another broadcast is already running.")
        sess["step"] = "running"
        await _edit(msg, "🚀 <b>Broadcast started…</b>", _stop_kb(uid))
        task = asyncio.create_task(_run(client, sess, msg))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return

    if action == "stop":
        sess["stop"] = True
        return await cq.answer("Stopping…")


# ───────────────────────────── RUNNER ─────────────────────────────
async def _run(client, sess: dict, status):
    global IS_BROADCASTING
    IS_BROADCASTING = True
    uid = sess["uid"]
    stats = {"groups": 0, "users": 0, "pinned": 0, "failed": 0}
    stopped = False
    try:
        targets = await _collect_targets(sess)
        total = len(targets)
        for i, (cid, kind) in enumerate(targets, 1):
            if sess["stop"]:
                stopped = True
                break
            m = await _send_one(client, cid, sess)
            if m is None:
                stats["failed"] += 1
            else:
                stats["groups" if kind == "group" else "users"] += 1
                if sess["pin"] and kind == "group" and await _pin(client, cid, m):
                    stats["pinned"] += 1
            await asyncio.sleep(SEND_DELAY)
            if i % PROGRESS_EVERY == 0:
                await _edit(
                    status,
                    f"🚀 <b>Broadcasting…</b> {i}/{total}\n"
                    f"👥 Groups: {stats['groups']} • 👤 Users: {stats['users']} • ⚠️ Failed: {stats['failed']}",
                    _stop_kb(uid),
                )
        head = "🛑 <b>Broadcast stopped</b>" if stopped else "✅ <b>Broadcast finished</b>"
        if sess["test"]:
            head = "🧪 " + head
        text = (
            f"{head}\n\n"
            f"👥 Groups: <b>{stats['groups']}</b>\n"
            f"👤 Users: <b>{stats['users']}</b>\n"
            f"📌 Pinned: <b>{stats['pinned']}</b>\n"
            f"⚠️ Failed: <b>{stats['failed']}</b>"
        )
    except Exception:
        log.exception("broadcast crashed")
        text = (
            "❌ <b>Broadcast crashed</b> – check the logs.\n"
            f"Sent so far: groups {stats['groups']}, users {stats['users']}."
        )
    finally:
        IS_BROADCASTING = False
        SESSIONS.pop(uid, None)
    try:
        await status.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await status.reply_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ───────────────────────────── AUTO CLEAN (unchanged) ─────────────────────────────
async def auto_clean():
    while not await asyncio.sleep(10):
        try:
            served_chats = await get_active_chats()
            for chat_id in served_chats:
                if chat_id not in adminlist:
                    adminlist[chat_id] = []
                    async for user in app.get_chat_members(
                        chat_id, filter=ChatMembersFilter.ADMINISTRATORS
                    ):
                        if user.privileges.can_manage_video_chats:
                            adminlist[chat_id].append(user.user.id)
                    authusers = await get_authuser_names(chat_id)
                    for user in authusers:
                        user_id = await alpha_to_int(user)
                        adminlist[chat_id].append(user_id)
        except:
            continue


asyncio.create_task(auto_clean())
