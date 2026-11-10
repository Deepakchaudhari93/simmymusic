import asyncio
import base64
import logging
import os
from io import BytesIO

from httpx import AsyncClient, Timeout
from pyrogram import Client, filters
from pyrogram.types import Message

from SHUKLAMUSIC import app

try:  # used for the webp sticker + the local renderer
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None

log = logging.getLogger("quote")

# -----------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------
# The public quote APIs are tried first. If they are down / blocked
# (they answer with an HTML error page), the quote is drawn locally
# with Pillow - that path also draws premium (custom) emoji.
# Set PREFER_LOCAL = True to always draw locally.
PREFER_LOCAL = False
QUOTLY_URLS = [
    "https://bot.lyo.su/quote/generate.png",
    "https://quotly.netfox.im/generate",
]
BG_COLOR = "#1b1429"

fetch = AsyncClient(
    http2=False,
    verify=False,
    follow_redirects=True,
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
        ),
    },
    timeout=Timeout(15),
)


class QuotlyException(Exception):
    pass


# -----------------------------------------------------------------
# SENDER INFO (works with old forward_* fields and new forward_origin)
# -----------------------------------------------------------------
def _full_name(u) -> str:
    first = getattr(u, "first_name", None) or ""
    last = getattr(u, "last_name", None) or ""
    return f"{first} {last}".strip()


def _from_user(u) -> dict:
    status = getattr(u, "emoji_status", None)
    emoji = getattr(status, "custom_emoji_id", None) if status else None
    return {
        "id": u.id,
        "name": _full_name(u),
        "username": getattr(u, "username", None) or "",
        "photo": getattr(u, "photo", None),
        "emoji": str(emoji) if emoji else "",
    }


def _from_chat(c) -> dict:
    return {
        "id": c.id,
        "name": getattr(c, "title", None) or "",
        "username": getattr(c, "username", None) or "",
        "photo": getattr(c, "photo", None),
        "emoji": "",
    }


def _forward_info(m):
    """-> (user, chat, hidden_name, is_forward)"""
    origin = getattr(m, "forward_origin", None)
    if origin is not None:
        return (
            getattr(origin, "sender_user", None),
            getattr(origin, "sender_chat", None) or getattr(origin, "chat", None),
            getattr(origin, "sender_user_name", None),
            True,
        )
    if getattr(m, "forward_date", None):
        return (
            getattr(m, "forward_from", None),
            getattr(m, "forward_from_chat", None),
            getattr(m, "forward_sender_name", None),
            True,
        )
    return None, None, None, False


def get_sender(m) -> dict:
    fu, fc, fname, is_fwd = _forward_info(m)
    if is_fwd:
        if fu:
            return _from_user(fu)
        if fc:
            return _from_chat(fc)
        return {"id": 1, "name": fname or "", "username": "", "photo": None, "emoji": ""}
    if m.from_user:
        return _from_user(m.from_user)
    if m.sender_chat:
        return _from_chat(m.sender_chat)
    return {"id": 1, "name": "", "username": "", "photo": None, "emoji": ""}


# -----------------------------------------------------------------
# TEXT / ENTITIES
# -----------------------------------------------------------------
MEDIA_LABELS = [
    ("photo", "🖼 Photo"),
    ("video", "🎬 Video"),
    ("animation", "🎞 GIF"),
    ("audio", "🎵 Audio"),
    ("voice", "🎤 Voice message"),
    ("video_note", "📹 Video message"),
    ("document", "📎 Document"),
    ("poll", "📊 Poll"),
    ("location", "📍 Location"),
    ("venue", "📍 Venue"),
    ("contact", "👤 Contact"),
    ("dice", "🎲 Dice"),
]


def get_text(m) -> str:
    text = getattr(m, "text", None) or getattr(m, "caption", None)
    if text:
        return str(text)
    sticker = getattr(m, "sticker", None)
    if sticker:
        return f"{getattr(sticker, 'emoji', None) or ''} Sticker".strip()
    for attr, label in MEDIA_LABELS:
        if getattr(m, attr, None):
            return label
    if getattr(m, "media", None):
        return "📎 Media"
    return ""


VALID_ENTITIES = {
    "mention", "hashtag", "cashtag", "bot_command", "url", "email", "phone_number",
    "bold", "italic", "underline", "strikethrough", "spoiler", "code", "pre",
    "text_link", "text_mention", "custom_emoji", "blockquote",
}
ENTITY_ALIASES = {"expandable_blockquote": "blockquote"}


def get_entities(m, plain: bool = False) -> list:
    if getattr(m, "text", None):
        ents = m.entities
    elif getattr(m, "caption", None):
        ents = m.caption_entities
    else:
        ents = None
    out = []
    for e in ents or []:
        name = getattr(e.type, "name", str(e.type)).lower()
        name = ENTITY_ALIASES.get(name, name)
        if name not in VALID_ENTITIES:
            continue
        item = {"type": name, "offset": e.offset, "length": e.length}
        if name == "custom_emoji":
            cid = getattr(e, "custom_emoji_id", None)
            if not cid or plain:
                continue
            item["custom_emoji_id"] = str(cid)
        elif name == "text_link":
            item["url"] = getattr(e, "url", None) or ""
        out.append(item)
    return out


# -----------------------------------------------------------------
# AVATAR (downloaded with our own client, sent as a data: URI)
# -----------------------------------------------------------------
async def get_avatar(client: Client, photo, cache: dict):
    if not photo:
        return None
    file_id = getattr(photo, "small_file_id", None)
    if not file_id:
        return None
    if file_id in cache:
        return cache[file_id]
    uri = None
    try:
        buf = await asyncio.wait_for(client.download_media(file_id, in_memory=True), 15)
        if buf:
            data = bytes(buf.getbuffer())
            uri = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    except Exception as e:
        log.debug("avatar download failed: %r", e)
    cache[file_id] = uri
    return uri


# -----------------------------------------------------------------
# PAYLOAD
# -----------------------------------------------------------------
async def build_message(client, m, cache, plain: bool = False, with_reply: bool = False) -> dict:
    s = get_sender(m)
    item = {
        "entities": get_entities(m, plain),
        "chatId": s["id"],
        "text": get_text(m),
        "avatar": True,
        "from": {
            "id": s["id"],
            "name": s["name"],
            "username": s["username"],
            "type": m.chat.type.name.lower(),
        },
        "replyMessage": {},
    }
    if s["emoji"] and not plain:
        item["from"]["emoji_status"] = s["emoji"]
    if not plain:
        uri = await get_avatar(client, s["photo"], cache)
        if uri:
            item["from"]["photo"] = {"url": uri}

    if with_reply:
        r = getattr(m, "reply_to_message", None)
        if r is not None and not getattr(r, "empty", False):
            r_text = get_text(r)
            if r_text:
                rs = get_sender(r)
                item["replyMessage"] = {
                    "name": rs["name"],
                    "text": r_text,
                    "entities": get_entities(r, plain),
                    "chatId": rs["id"],
                }
    return item


# -----------------------------------------------------------------
# API RENDERER
# -----------------------------------------------------------------
def _decode_api_response(r):
    """Return PNG bytes from an API response, or None if it isn't an image."""
    if r.is_error:
        return None
    data = r.content
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"RIFF":
        return data
    ctype = r.headers.get("content-type", "")
    if "json" in ctype:
        try:
            j = r.json()
            b64 = (j.get("result") or {}).get("image")
            if b64:
                return base64.b64decode(b64)
        except Exception:
            return None
    return None


async def _try_api(payload: dict):
    for url in QUOTLY_URLS:
        try:
            r = await fetch.post(url, json=payload)
        except Exception as e:
            log.warning("quote api %s unreachable: %r", url, e)
            continue
        img = _decode_api_response(r)
        if img:
            return img
        log.warning("quote api %s gave no image (status %s)", url, r.status_code)
    return None


# -----------------------------------------------------------------
# LOCAL RENDERER (Pillow) - used when the APIs are down.
# Draws avatar, name, bubble, reply preview, bold/links,
# normal emoji (twemoji) and PREMIUM custom emoji.
# -----------------------------------------------------------------
S = 2  # supersampling factor
FONT_DIR = "quote_fonts"
FONT_URLS = {
    "regular": "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/hinted/ttf/NotoSans-Regular.ttf",
    "bold": "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/hinted/ttf/NotoSans-Bold.ttf",
}
SYS_FONTS = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
}
TWEMOJI_URL = "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/{}.png"
NAME_COLORS = ["#ff885e", "#ff516a", "#a695e7", "#6ec9cb", "#6cc4ff", "#ffa357", "#7bc862"]
LINK_COLOR = "#6ab3f3"
TEXT_COLOR = "#ffffff"
BUBBLE_COLOR = "#2b2142"

_font_paths = None
_font_cache = {}
_custom_cache = {}   # custom_emoji_id -> PIL image or None
_twemoji_cache = {}  # cluster -> PIL image or None

LINKY = {"mention", "hashtag", "cashtag", "bot_command", "url", "email",
         "phone_number", "text_link", "text_mention"}


async def _ensure_fonts():
    global _font_paths
    if _font_paths is not None:
        return
    paths = {}
    for kind in ("regular", "bold"):
        found = next((p for p in SYS_FONTS[kind] if os.path.exists(p)), None)
        if not found:
            local = os.path.join(FONT_DIR, f"NotoSans-{kind}.ttf")
            if not os.path.exists(local):
                try:
                    os.makedirs(FONT_DIR, exist_ok=True)
                    r = await fetch.get(FONT_URLS[kind])
                    if r.status_code == 200 and len(r.content) > 10000:
                        with open(local, "wb") as f:
                            f.write(r.content)
                except Exception as e:
                    log.warning("font download failed: %r", e)
            if os.path.exists(local):
                found = local
        paths[kind] = found
    _font_paths = paths


def _font(kind: str, size: int):
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    path = (_font_paths or {}).get(kind)
    try:
        f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except Exception:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _w16(ch: str) -> int:
    return 2 if ord(ch) > 0xFFFF else 1


def _emojiish(ch: str) -> bool:
    o = ord(ch)
    return (
        0x1F000 <= o <= 0x1FAFF
        or 0x2600 <= o <= 0x27BF
        or 0x2300 <= o <= 0x23FF
        or 0x2B00 <= o <= 0x2BFF
        or o in (0xA9, 0xAE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299)
    )


def tokenize(text: str, entities):
    bold, link, custom = set(), set(), {}
    for e in entities or []:
        t, a = e["type"], e["offset"]
        b = a + e["length"]
        if t == "custom_emoji":
            custom[a] = (e["length"], str(e.get("custom_emoji_id")))
        elif t == "bold":
            bold.update(range(a, b))
        elif t in LINKY:
            link.update(range(a, b))

    toks, i, pos, n = [], 0, 0, len(text)
    while i < n:
        ch = text[i]
        if pos in custom:
            ln, cid = custom[pos]
            start, used = i, 0
            while i < n and used < ln:
                used += _w16(text[i])
                i += 1
            pos += used
            toks.append({"t": "custom", "id": cid, "fallback": text[start:i]})
            continue
        if ch == "\n":
            toks.append({"t": "nl"})
            i += 1
            pos += 1
            continue
        if _emojiish(ch):
            j = i + 1
            if 0x1F1E6 <= ord(ch) <= 0x1F1FF:  # flag = two regional indicators
                if j < n and 0x1F1E6 <= ord(text[j]) <= 0x1F1FF:
                    j += 1
            else:
                while j < n:
                    c = text[j]
                    if c == "\ufe0f" or 0x1F3FB <= ord(c) <= 0x1F3FF:
                        j += 1
                    elif c == "\u200d" and j + 1 < n and _emojiish(text[j + 1]):
                        j += 2
                    else:
                        break
            cluster = text[i:j]
            toks.append({"t": "e", "key": cluster})
            pos += sum(_w16(c) for c in cluster)
            i = j
            continue
        toks.append({
            "t": "c", "ch": ch,
            "bold": pos in bold,
            "color": LINK_COLOR if pos in link else TEXT_COLOR,
        })
        pos += _w16(ch)
        i += 1
    return toks


async def _get_twemoji(key: str):
    if key in _twemoji_cache:
        return
    img = None
    stripped = "-".join(f"{ord(c):x}" for c in key if ord(c) != 0xFE0F)
    full = "-".join(f"{ord(c):x}" for c in key)
    for code in dict.fromkeys([stripped, full]):
        try:
            r = await fetch.get(TWEMOJI_URL.format(code))
            if r.status_code == 200:
                img = Image.open(BytesIO(r.content)).convert("RGBA")
                break
        except Exception:
            continue
    _twemoji_cache[key] = img


async def _fetch_custom_emoji(client, ids):
    ids = [i for i in set(ids) if i and i not in _custom_cache]
    if not ids:
        return
    stickers = []
    try:
        stickers = await client.get_custom_emoji_stickers([int(i) for i in ids])
    except Exception as e:
        log.warning("get_custom_emoji_stickers failed: %r", e)
    for st in stickers or []:
        cid = str(getattr(st, "custom_emoji_id", "") or "")
        if not cid:
            continue
        img = None
        try:
            animated = getattr(st, "is_animated", False) or getattr(st, "is_video", False)
            fid = None
            if animated:
                thumbs = getattr(st, "thumbs", None)
                if thumbs:
                    fid = thumbs[-1].file_id  # animated -> use the static preview
            else:
                fid = st.file_id
            if fid:
                buf = await asyncio.wait_for(client.download_media(fid, in_memory=True), 15)
                img = Image.open(BytesIO(bytes(buf.getbuffer()))).convert("RGBA")
        except Exception as e:
            log.debug("custom emoji %s failed: %r", cid, e)
        _custom_cache[cid] = img
    for i in ids:  # anything not returned -> remember as missing
        _custom_cache.setdefault(i, None)


async def _prefetch(toks):
    for tk in toks:
        if tk["t"] == "custom":
            im = _custom_cache.get(tk["id"])
            if im is not None:
                tk["img"] = im
            else:  # fall back to the normal emoji written in the text
                tk["t"] = "e"
                tk["key"] = tk["fallback"] or "⭐"
    keys = {tk["key"] for tk in toks if tk["t"] == "e"}
    if keys:
        await asyncio.gather(*[_get_twemoji(k) for k in keys])
    for tk in toks:
        if tk["t"] == "e":
            im = _twemoji_cache.get(tk["key"])
            if im is not None:
                tk["img"] = im
            else:
                tk["t"] = "c"
                tk["ch"] = tk["key"]
                tk["bold"] = False
                tk["color"] = TEXT_COLOR


def _tok_w(tk, fs):
    if tk["t"] in ("e", "custom"):
        return int(fs * 1.2)
    return _font("bold" if tk.get("bold") else "regular", fs).getlength(tk["ch"])


def _layout(toks, fs, max_w):
    """-> (lines, widest) ; lines = [[(token, x), ...], ...]"""
    words, cur = [], []
    for tk in toks:
        if tk["t"] == "nl":
            words.append(cur)
            words.append("NL")
            cur = []
        elif tk["t"] == "c" and tk["ch"] == " ":
            cur.append(tk)
            words.append(cur)
            cur = []
        else:
            cur.append(tk)
    words.append(cur)

    lines, x, widest = [[]], 0, 0
    for word in words:
        if word == "NL":
            lines.append([])
            x = 0
            continue
        core = [t for t in word if not (t["t"] == "c" and t["ch"] == " ")]
        core_w = sum(_tok_w(t, fs) for t in core)
        if x + core_w > max_w and lines[-1]:
            lines.append([])
            x = 0
        for tk in word:
            w = _tok_w(tk, fs)
            if x + w > max_w and lines[-1] and not (tk["t"] == "c" and tk["ch"] == " "):
                lines.append([])
                x = 0
            lines[-1].append((tk, x))
            x += w
            widest = max(widest, x)
    return lines, widest


def _draw_lines(img, draw, lines, x0, y0, fs, lh):
    for row, line in enumerate(lines):
        y = y0 + row * lh
        for tk, x in line:
            if tk["t"] in ("e", "custom"):
                size = int(fs * 1.2)
                emj = tk["img"].resize((size, size), Image.LANCZOS)
                img.paste(emj, (int(x0 + x), int(y + (lh - size) / 2)), emj)
            elif tk["ch"] != " ":
                f = _font("bold" if tk.get("bold") else "regular", fs)
                draw.text((x0 + x, y), tk["ch"], font=f, fill=tk["color"])


def _circle_avatar(data, size, name, color):
    img = None
    if data:
        try:
            img = Image.open(BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            img = None
    if img is None:
        img = Image.new("RGBA", (size, size), color)
        d = ImageDraw.Draw(img)
        f = _font("bold", int(size * 0.5))
        letter = (name or "?").strip()[:1].upper() or "?"
        w = f.getlength(letter)
        d.text(((size - w) / 2, size * 0.2), letter, font=f, fill="#ffffff")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


async def _render_block(m):
    fs, nfs, rfs = 17 * S, 15 * S, 13 * S
    pad, av, gap, max_w = 12 * S, 40 * S, 8 * S, 320 * S
    lh, nlh, rlh = int(fs * 1.4), int(nfs * 1.45), int(rfs * 1.4)

    uid = int(m["from"].get("id") or 0)
    name = m["from"].get("name") or ""
    color = NAME_COLORS[abs(uid) % len(NAME_COLORS)]

    toks = tokenize(m["text"], m["entities"])
    await _prefetch(toks)
    lines, text_w = _layout(toks, fs, max_w)

    reply = m.get("replyMessage") or {}
    r_lines, r_w, r_name = None, 0, ""
    if reply.get("text"):
        rtoks = tokenize(reply["text"].replace("\n", " "), reply.get("entities", []))
        await _prefetch(rtoks)
        rl, _ = _layout(rtoks, rfs, max_w - 10 * S)
        r_lines = rl[:1]
        r_name = reply.get("name") or ""
        r_w = max([x + _tok_w(t, rfs) for t, x in r_lines[0]] or [0]) + 10 * S

    name_f = _font("bold", nfs)
    name_w = name_f.getlength(name)
    status_img = None
    es = m["from"].get("emoji_status")
    if es and _custom_cache.get(str(es)) is not None:
        status_img = _custom_cache[str(es)]
        name_w += int(nfs * 1.2) + 4 * S
    r_name_w = _font("bold", rfs).getlength(r_name) + 10 * S if r_lines else 0

    content_w = max(text_w, name_w, r_w, r_name_w, 40 * S)
    bw = int(content_w + 2 * pad)
    bh = pad + nlh + (rlh * 2 + 6 * S if r_lines else 0) + max(1, len(lines)) * lh + int(pad * 0.8)

    img = Image.new("RGBA", (av + gap + bw, max(bh, av)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bx = av + gap
    by = img.height - bh
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=16 * S, fill=BUBBLE_COLOR)

    # avatar
    av_data = None
    photo = (m["from"].get("photo") or {}).get("url")
    if photo and "," in photo:
        try:
            av_data = base64.b64decode(photo.split(",", 1)[1])
        except Exception:
            av_data = None
    a_img = _circle_avatar(av_data, av, name, color)
    img.paste(a_img, (0, img.height - av), a_img)

    # name (+ premium emoji status)
    y = by + pad
    d.text((bx + pad, y), name, font=name_f, fill=color)
    if status_img is not None:
        size = int(nfs * 1.2)
        st = status_img.resize((size, size), Image.LANCZOS)
        img.paste(st, (int(bx + pad + name_f.getlength(name) + 4 * S), int(y + (nlh - size) / 2)), st)
    y += nlh

    # reply preview
    if r_lines:
        d.rounded_rectangle((bx + pad, y, bx + pad + 3 * S, y + rlh * 2), radius=2 * S, fill=color)
        d.text((bx + pad + 10 * S, y), r_name, font=_font("bold", rfs), fill=color)
        _draw_lines(img, d, r_lines, bx + pad + 10 * S, y + rlh, rfs, rlh)
        y += rlh * 2 + 6 * S

    _draw_lines(img, d, lines, bx + pad, y, fs, lh)
    return img


async def render_local(client, messages: list) -> bytes:
    if Image is None:
        raise QuotlyException("Pillow is not installed (pip install pillow)")
    await _ensure_fonts()

    ids = set()
    for m in messages:
        for e in m["entities"]:
            if e["type"] == "custom_emoji":
                ids.add(str(e["custom_emoji_id"]))
        for e in (m.get("replyMessage") or {}).get("entities", []):
            if e["type"] == "custom_emoji":
                ids.add(str(e["custom_emoji_id"]))
        if m["from"].get("emoji_status"):
            ids.add(str(m["from"]["emoji_status"]))
    await _fetch_custom_emoji(client, ids)

    blocks = [await _render_block(m) for m in messages]
    margin, spacing = 10 * S, 6 * S
    width = max(b.width for b in blocks) + margin * 2
    height = sum(b.height for b in blocks) + spacing * (len(blocks) - 1) + margin * 2
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y = margin
    for b in blocks:
        canvas.paste(b, (margin, y), b)
        y += b.height + spacing

    out = BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


# -----------------------------------------------------------------
# ENTRY: API first, local renderer as the safety net
# -----------------------------------------------------------------
async def pyrogram_to_quotly(client, messages, with_reply: bool = False):
    if not isinstance(messages, list):
        messages = [messages]

    cache = {}
    full = [await build_message(client, m, cache, False, with_reply) for m in messages]

    if not PREFER_LOCAL:
        for plain in (False, True):
            if plain:
                msgs = [await build_message(client, m, {}, True, with_reply) for m in messages]
            else:
                msgs = full
            img = await _try_api({
                "type": "quote",
                "format": "png",
                "backgroundColor": BG_COLOR,
                "messages": msgs,
            })
            if img:
                return img

    return await render_local(client, full)


def make_sticker(data: bytes) -> BytesIO:
    """PNG from the API -> 512px webp (valid sticker). Falls back to raw bytes without Pillow."""
    bio = None
    if Image is not None:
        try:
            img = Image.open(BytesIO(data)).convert("RGBA")
            w, h = img.size
            scale = 512 / max(w, h)
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
            bio = BytesIO()
            img.save(bio, "WEBP")
        except Exception as e:
            log.warning("webp convert failed: %r", e)
            bio = None
    if bio is None:
        bio = BytesIO(data)
    bio.name = "quote.webp"
    bio.seek(0)
    return bio


# -----------------------------------------------------------------
# COMMANDS
#   /q        -> quote only
#   /r        -> quote + the message it replied to
#   /q r      -> same as /r
#   /q 3      -> quote 3 messages (also /q 3 r, /r 3, /q r 3)
# prefixes: / ! .
# -----------------------------------------------------------------
USAGE = (
    "Reply to a message with:\n"
    "/q - quote\n"
    "/r or /q r - quote with its reply\n"
    "/q 3 - quote 3 messages (1-10)"
)


@app.on_message(filters.command(["q", "r"], prefixes=["/", "!", "."]), group=7)
async def msg_quotly_cmd(client: Client, ctx: Message):
    if not ctx.reply_to_message:
        return await ctx.reply_text(USAGE)

    cmd = (ctx.command[0] or "").lower()
    with_reply = cmd == "r"
    count = 1

    for arg in ctx.command[1:]:
        a = arg.lower()
        if a == "r":
            with_reply = True
        elif a.lstrip("-").isdigit():
            n = int(a)
            if n < 1 or n > 10:
                return await ctx.reply_text("Invalid range, use 1 to 10.")
            count = n

    target = ctx.reply_to_message
    try:
        if count == 1:
            fetched = [target]
        else:
            fetched = await client.get_messages(
                ctx.chat.id, list(range(target.id, target.id + count + 10))
            )
        messages = [
            m for m in fetched
            if m and not getattr(m, "empty", False)
            and not getattr(m, "service", None)
            and get_text(m)
        ][:count]
    except Exception as e:
        return await ctx.reply_text(f"❌ Couldn't fetch the message(s): {e}")

    if not messages:
        return await ctx.reply_text("❌ Nothing to quote in that message.")

    try:
        image = await pyrogram_to_quotly(client, messages, with_reply)
        return await ctx.reply_sticker(make_sticker(image))
    except Exception as e:
        log.exception("quote failed")
        return await ctx.reply_text(f"❌ Quote failed: {e}")
