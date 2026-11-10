
import asyncio
import html
import io
import logging
import os
import random
import re
import secrets
import time
from difflib import get_close_matches

import aiohttp
from pyrogram import enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:  # kurigram: rich messages
    from pyrogram.types import InputRichMessage
except Exception:  # pragma: no cover
    InputRichMessage = None

try:  # media inside rich messages
    from pyrogram.types import InputMediaAudio, InputRichMessageMedia
except Exception:  # pragma: no cover
    InputRichMessageMedia = None
    InputMediaAudio = None

try:
    from pyrogram.types import ReplyParameters
except Exception:  # pragma: no cover
    ReplyParameters = None

try:  # kurigram: coloured buttons
    from pyrogram.enums import ButtonStyle
except Exception:  # pragma: no cover
    ButtonStyle = None

try:  # translation (async wrapper around the free Google web endpoint)
    from gpytranslate import Translator as _GPTranslator
except Exception:  # pragma: no cover
    _GPTranslator = None

try:  # free text-to-speech
    from gtts import gTTS
    from gtts.lang import tts_langs
except Exception:  # pragma: no cover
    gTTS = None
    tts_langs = None

import config  # noqa: F401
from SHUKLAMUSIC import app
from config import BANNED_USERS

log = logging.getLogger("tr")

# ================================
#        SETTINGS
# ================================
ALLOW_ANYONE_TO_CLICK = False   # False = only the user who ran /tr can press the buttons
STATE_TTL = 3600                # seconds a /tr request stays clickable
MAX_CHARS = 3000                # max characters translated
TTS_MAX_CHARS = 1500            # max characters read out loud
AUDIO_PERFORMER = "Tidal Translate"
# If the rich audio message fails with an in-memory upload on your build, set this to
# config.LOGGER_ID: the mp3 is uploaded there once to get a file_id, then reused in the
# rich message (same trick start.py uses for the video). None = off.
TTS_CACHE_CHAT = None
# Hinglish (roman) input is converted to Devanagari through Google Input Tools before
# translating - far better results. Set False to use gpytranslate + gTTS only.
USE_INPUT_TOOLS = True

# buttons shown in the picker (the detected language is removed automatically)
LANG_BUTTONS = [
    ("Hindi", "hi"),
    ("Hinglish", "hinglish"),
    ("English", "en"),
    ("Russian", "ru"),
    ("Urdu", "ur"),
    ("Bengali", "bn"),
]

# language name -> google code  (used by the Custom Language reply)
LANGS = {
    "hinglish": "hinglish", "hindi": "hi", "english": "en", "russian": "ru", "urdu": "ur",
    "bengali": "bn", "bangla": "bn", "afrikaans": "af", "albanian": "sq", "amharic": "am",
    "arabic": "ar", "armenian": "hy", "azerbaijani": "az", "basque": "eu", "belarusian": "be",
    "bosnian": "bs", "bulgarian": "bg", "burmese": "my", "catalan": "ca", "cebuano": "ceb",
    "chinese": "zh-CN", "mandarin": "zh-CN", "chinese traditional": "zh-TW", "croatian": "hr",
    "czech": "cs", "danish": "da", "dutch": "nl", "esperanto": "eo", "estonian": "et",
    "filipino": "tl", "tagalog": "tl", "finnish": "fi", "french": "fr", "galician": "gl",
    "georgian": "ka", "german": "de", "greek": "el", "gujarati": "gu", "haitian creole": "ht",
    "hausa": "ha", "hebrew": "he", "hungarian": "hu", "icelandic": "is", "indonesian": "id",
    "irish": "ga", "italian": "it", "japanese": "ja", "javanese": "jv", "kannada": "kn",
    "kazakh": "kk", "khmer": "km", "korean": "ko", "kurdish": "ku", "kyrgyz": "ky", "lao": "lo",
    "latin": "la", "latvian": "lv", "lithuanian": "lt", "macedonian": "mk", "malagasy": "mg",
    "malay": "ms", "malayalam": "ml", "maltese": "mt", "maori": "mi", "marathi": "mr",
    "mongolian": "mn", "nepali": "ne", "norwegian": "no", "odia": "or", "oriya": "or",
    "pashto": "ps", "persian": "fa", "farsi": "fa", "polish": "pl", "portuguese": "pt",
    "punjabi": "pa", "romanian": "ro", "samoan": "sm", "scots gaelic": "gd", "serbian": "sr",
    "sindhi": "sd", "sinhala": "si", "slovak": "sk", "slovenian": "sl", "somali": "so",
    "spanish": "es", "sundanese": "su", "swahili": "sw", "swedish": "sv", "tajik": "tg",
    "tamil": "ta", "telugu": "te", "thai": "th", "turkish": "tr", "ukrainian": "uk",
    "uzbek": "uz", "vietnamese": "vi", "welsh": "cy", "xhosa": "xh", "yiddish": "yi",
    "yoruba": "yo", "zulu": "zu",
}
NAMES = {}
for _n, _c in LANGS.items():
    NAMES.setdefault(_c, _n.title())
NAMES["auto"] = "Unknown"
_CODES = {c.lower(): c for c in set(LANGS.values())}

# common Hinglish words (English look-alikes like to/me/is/the/hi/do are left out on purpose)
HINGLISH_WORDS = set("""
hai hain ho hu hun hoon tha thi thay raha rahi rahe hoga hogi honge kya kaise kaisa kaisi kyu kyun
kyunki nahi nahin nhi mat haan han ji jee mai main mujhe mujhko mera meri mere tum tumhe tumhara
tumhari tumse tu tujhe tujhko tera teri tere aap aapko aapka aapki hum humko hume humein hamara
unko unhe usko isko uska uski uske iska iski ye yeh wo woh kuch koi kisi kaun kahan kab kitna kitne
kitni sab sabhi apna apni apne ka ki ke ko se mein pe aur ya lekin magar toh agar warna isliye
abhi ab jab tab kal aaj phir fir kabhi bahut bohot bhot thoda zyada jyada bilkul zaroor jaldi der
accha acha achha theek thik sahi galat bas sirf yaar bhai bhaiya didi dost chahiye chahta chahte
chahti milega milenge karna karo karte kiya kare karunga kar jana jaana jao jaa gaya gayi gaye aana
aao aaya aayi aate dekho dekha dekhna bolo bol bola bata batao bataya suno suna sunao samajh samjha
pata khana khaana pani chai ghar kaam naam paisa paise paison log din raat subah shaam liye saath
sath wala wali wale chal chalo chalte chalna pyaar pyar dil zindagi shukriya dhanyavaad namaste
salaam waise vaise jaise aise jaisa arre arey oye abe mujhse tumhein
""".split())

# ================================
#        STATE
# ================================
_STATE = {}   # token -> entry
_AWAIT = {}   # (chat_id, bot_msg_id) -> token   (waiting for the custom-language reply)


def _purge():
    now = time.time()
    for tok in [t for t, e in _STATE.items() if now - e["ts"] > STATE_TTL]:
        e = _STATE.pop(tok)
        _AWAIT.pop((e["chat_id"], e["msg_id"]), None)


# ================================
#        TRANSLATION  (gpytranslate)
# ================================
_IT_URL = "https://inputtools.google.com/request"
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

_TRANSLATOR = None
_GT_SEM = asyncio.Semaphore(2)
_GT_CACHE = {}
_GT_CACHE_MAX = 300


def _norm_code(code: str) -> str:
    return {"iw": "he", "jw": "jv"}.get(code, code)


def _parse_raw(data) -> dict:
    """Google's raw response array -> romanisation rows (if gpytranslate exposes it)."""
    roman, roman_src = [], []
    try:
        for seg in data[0] or []:
            if seg and not seg[0] and len(seg) > 3:
                if seg[2]:
                    roman.append(seg[2])
                if seg[3]:
                    roman_src.append(seg[3])
    except Exception:
        pass
    return {"roman": " ".join(roman).strip(), "roman_src": " ".join(roman_src).strip()}


def _pick(obj, *names):
    """Read a field from a gpytranslate result whether it's an attribute or a dict key."""
    for n in names:
        v = None
        if isinstance(obj, dict):
            v = obj.get(n)
        if v is None:
            v = getattr(obj, n, None)
        if v:
            return v
    return None


def _get_translator():
    global _TRANSLATOR
    if _TRANSLATOR is None:
        if _GPTranslator is None:
            raise RuntimeError("gpytranslate is not installed (pip install gpytranslate)")
        _TRANSLATOR = _GPTranslator()
    return _TRANSLATOR


async def _gt(text: str, tl: str, sl: str = "auto") -> dict:
    """Translate with gpytranslate. Returns {text, roman, roman_src, src}."""
    ck = (text, tl, sl)
    if ck in _GT_CACHE:
        return _GT_CACHE[ck]

    last = None
    async with _GT_SEM:
        for attempt in range(3):
            try:
                tr = _get_translator()
                obj = await asyncio.wait_for(
                    tr.translate(text, targetlang=tl, sourcelang=sl), 20
                )
                out_text = str(_pick(obj, "text", "translation") or "").strip()
                src = _pick(obj, "lang", "src", "source", "orig_lang", "sourcelang", "detected_language")
                raw = _pick(obj, "raw")
                extra = _parse_raw(raw) if isinstance(raw, (list, tuple)) else {"roman": "", "roman_src": ""}
                if not src and isinstance(raw, (list, tuple)) and len(raw) > 2 and isinstance(raw[2], str):
                    src = raw[2]
                if not isinstance(src, str) or not src:
                    src = sl if sl != "auto" else "auto"
                res = {
                    "text": out_text,
                    "roman": str(_pick(obj, "pronunciation", "translit", "roman") or extra["roman"]).strip(),
                    "roman_src": extra["roman_src"],
                    "src": _norm_code(src),
                }
                if len(_GT_CACHE) >= _GT_CACHE_MAX:
                    _GT_CACHE.pop(next(iter(_GT_CACHE)))
                _GT_CACHE[ck] = res
                return res
            except Exception as e:
                last = e
                log.warning("gpytranslate attempt %s failed: %r", attempt + 1, e)
                await asyncio.sleep(1.0 * (attempt + 1) + random.random())
    raise last or RuntimeError("translation failed")


# ---------- Devanagari -> roman (used for the Hinglish output when Google gives no romanisation)
_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
    "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d",
    "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r",
    "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "\ue000": "ksh", "\ue001": "gy", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}
_MATRA = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai",
          "ो": "o", "ौ": "au", "ॅ": "e", "ॉ": "o"}
_VOWEL = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e",
          "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o"}
_DIGITS = {c: str(i) for i, c in enumerate("०१२३४५६७८९")}


def _deva_word(w: str) -> str:
    w = w.replace("क्ष", "\ue000").replace("ज्ञ", "\ue001")
    units = []   # [consonant, vowel]  vowel: "a" inherent, "" virama, else matra/explicit
    i = 0
    while i < len(w):
        c = w[i]
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if nxt == "\u093c" and (c + nxt) in _CONS:        # nukta
            c, i = c + nxt, i + 1
        if c in _CONS:
            units.append([_CONS[c], "a", True])
        elif c in _VOWEL:
            units.append(["", _VOWEL[c], False])
        elif c in _MATRA and units:
            units[-1][1] = _MATRA[c]
        elif c == "्" and units:
            units[-1][1] = ""
        elif c in ("ं", "ँ") and units:
            units[-1][1] = (units[-1][1] or "") + "n"
        elif c == "ः" and units:
            units[-1][1] = (units[-1][1] or "") + "h"
        elif c in _DIGITS:
            units.append([_DIGITS[c], "", False])
        else:
            units.append([c, "", False])
        i += 1
    n, out = len(units), []
    for k, (cons, vow, is_c) in enumerate(units):
        if is_c and vow == "a":
            last = k == n - 1
            nv = units[k + 1][1] if k + 1 < n else None
            medial = (
                0 < k < n - 1 and units[k - 1][1] not in ("", None) and nv not in ("", "a", None)
            )
            if last or medial:
                vow = ""
        out.append(cons + vow)
    r = "".join(out)
    r = re.sub(r"chchh", "chh", r)
    r = re.sub(r"aa$", "a", r)      # hamaaraa -> hamaara style, "kaam" keeps its medial aa
    r = re.sub(r"een$", "in", r)    # nahin
    r = re.sub(r"ee$", "i", r)      # nahee -> nahi
    return r


def _deva_to_roman(text: str) -> str:
    return re.sub(r"[\u0900-\u097F]+", lambda m: _deva_word(m.group(0)), text)


async def _latin_to_deva(text: str):
    """Hinglish (roman) -> Devanagari via Google Input Tools. Much better source text for
    translating Hinglish. Returns None if it fails (caller then falls back to the raw text)."""

    async def one(session, line):
        if not line.strip():
            return line, False
        try:
            params = {"text": line[:400], "itc": "hi-t-i0-und", "num": 1, "cp": 0, "cs": 1,
                      "ie": "utf-8", "oe": "utf-8", "app": "demopage"}
            async with session.get(_IT_URL, params=params) as r:
                data = await r.json(content_type=None)
            if data[0] == "SUCCESS":
                return data[1][0][1][0], True
        except Exception as e:
            log.debug("input tools failed: %r", e)
        return line, False

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), headers=_UA) as s:
            res = await asyncio.gather(*[one(s, ln) for ln in text.split("\n")])
    except Exception as e:
        log.debug("input tools session failed: %r", e)
        return None
    if not any(ok for _, ok in res):
        return None
    return "\n".join(t for t, _ in res)


# ================================
#        DETECTION
# ================================
def _is_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(ord(c) < 0x250 for c in letters) / len(letters) > 0.8


def _looks_hinglish(text: str) -> bool:
    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return False
    hits = sum(w in HINGLISH_WORDS for w in words)
    ratio = hits / len(words)
    return ratio >= 0.3 or (hits >= 2 and ratio >= 0.2)


def _guess_lang_local(text: str) -> str:
    """No-network guess used when Google is rate-limiting us. 'auto' = let Google decide later."""
    counts = {"hi": 0, "ru": 0, "ar": 0, "bn": 0, "lat": 0}
    for c in text:
        o = ord(c)
        if 0x0900 <= o <= 0x097F:
            counts["hi"] += 1
        elif 0x0400 <= o <= 0x04FF:
            counts["ru"] += 1
        elif 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
            counts["ar"] += 1
        elif 0x0980 <= o <= 0x09FF:
            counts["bn"] += 1
        elif c.isalpha() and o < 0x250:
            counts["lat"] += 1
    top = max(counts, key=counts.get)
    if counts[top] == 0:
        return "auto"
    if top == "ar":
        return "ur"
    if top == "lat":
        return "hinglish" if _looks_hinglish(text) else "auto"
    return top


async def detect_lang(text: str) -> str:
    try:
        r = await _gt(text[:800], "en")
    except Exception as e:
        log.warning("detect failed, using local guess: %r", e)
        return _guess_lang_local(text)
    code = r["src"]
    if _is_latin(text) and (_looks_hinglish(text) or code == "hi"):
        return "hinglish"
    return code


def _name(code: str) -> str:
    return NAMES.get(code) or code


def _resolve_lang(s: str):
    s = re.sub(r"[^\w\- ]", "", s.strip().lower()).strip()
    if not s:
        return None
    if s in LANGS:
        return LANGS[s]
    if s in _CODES:
        return _CODES[s]
    m = get_close_matches(s, list(LANGS), n=1, cutoff=0.8)
    return LANGS[m[0]] if m else None


# ================================
#        TRANSLATE
# ================================
async def do_translate(entry: dict, target: str) -> dict:
    text, src, sl = entry["text"], entry["src"], "auto"

    if src == "hinglish":
        if target == "hinglish":
            return {"text": text, "tts_text": text, "tts_lang": "hi"}
        if USE_INPUT_TOOLS:
            text = await _latin_to_deva(text) or text
        sl = "hi"

    if target == "hinglish":
        r = await _gt(text, "hi", sl)
        hindi = r["text"] or text
        roman = r["roman"] or (r["roman_src"] if src == "hi" else "") or _deva_to_roman(hindi)
        return {"text": roman or hindi, "tts_text": hindi, "tts_lang": "hi"}

    r = await _gt(text, target, sl)
    if not r["text"]:
        raise RuntimeError("empty translation")
    return {"text": r["text"], "tts_text": r["text"], "tts_lang": target}


# ================================
#        TTS (gTTS, free)
# ================================
def _tts_ok(lang: str) -> bool:
    if gTTS is None or tts_langs is None:
        return False
    try:
        return lang in tts_langs()
    except Exception:
        return False


def _tts_sync(text: str, lang: str) -> bytes:
    buf = io.BytesIO()
    gTTS(text=text, lang=lang).write_to_fp(buf)
    return buf.getvalue()


# ================================
#        RICH HELPERS (same as start.py)
# ================================
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


def classic_keyboard(rows):
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
    return InlineKeyboardMarkup(kb) if kb else None


def _reply_variants(reply_to):
    if not reply_to:
        return [{}]
    v = []
    if ReplyParameters is not None:
        v.append({"reply_parameters": ReplyParameters(message_id=reply_to)})
    v.append({"reply_to_message_id": reply_to})
    return v


async def send_rich(client, chat_id, html_text, reply_to=None, media=None):
    """Send a rich message. Returns the Message or None if every attempt failed."""
    if InputRichMessage is None or not hasattr(client, "send_rich_message"):
        return None
    try:
        rich = InputRichMessage(html=html_text, media=media) if media else InputRichMessage(html=html_text)
    except Exception as e:
        log.warning("couldn't build rich message: %r", e)
        return None
    for extra in _reply_variants(reply_to):
        try:
            return await client.send_rich_message(chat_id, rich, **extra)
        except Exception as e:
            log.warning("send_rich_message failed (media=%s extra=%s): %r", bool(media), list(extra), e)
    return None


async def edit_rich(client, chat_id, msg_id, html_text) -> bool:
    if InputRichMessage is None:
        return False
    try:
        rich = InputRichMessage(html=html_text)
    except Exception:
        return False
    last = None
    for kw in ({}, {"text": ""}):
        try:
            await client.edit_message_text(chat_id, msg_id, rich_message=rich, **kw)
            return True
        except MessageNotModified:
            return True
        except Exception as e:
            last = e
    log.warning("rich edit failed: %r", last)
    return False


async def show(client, entry, view):
    """Edit the bot message to `view` = (rich_html, classic_html, rows)."""
    rich_html, classic_html, rows = view
    cid, mid = entry["chat_id"], entry["msg_id"]
    if entry["rich"] and await edit_rich(client, cid, mid, rich_html):
        return
    try:
        await client.edit_message_text(
            cid, mid, classic_html,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=classic_keyboard(rows),
        )
    except MessageNotModified:
        pass
    except Exception as e:
        log.warning("classic edit failed: %r", e)


# ================================
#        VIEWS
# ================================
def _rich_text(t: str) -> str:
    return html.escape(t, quote=False).replace("\r", "").replace("\n", "<br>")


def _plain(t: str) -> str:
    return html.escape(t, quote=False)


def _picker_view(entry):
    tok = entry["token"]
    btns = [
        (label, "callback", f"trx:{tok}:{code}", "primary")
        for label, code in LANG_BUTTONS
        if code != entry["src"]
    ]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([("✏️ Custom Language", "callback", f"trx:{tok}:custom", "danger")])

    text = entry["text"]
    preview = text[:150] + ("…" if len(text) > 150 else "")
    det = _name(entry["src"])
    rich = (
        f"<p><b>Message Lang detected as :</b> {_rich_text(det)}</p>"
        f"<aside>{_rich_text(preview)}</aside>"
        f"<p>Select the language you want to get the translation in:</p>"
        f"{rich_buttons(rows)}"
    )
    classic = (
        f"<b>Message Lang detected as :</b> {_plain(det)}\n"
        f"<blockquote>{_plain(preview)}</blockquote>\n"
        "Select the language you want to get the translation in:"
    )
    return rich, classic, rows


def _custom_view(entry):
    rows = [[("⬅️ Back", "callback", f"trx:{entry['token']}:back", None)]]
    msg = "Reply to this message with the language name (e.g. Spanish, Tamil, Japanese) or its code (es, ta, ja)."
    rich = f"<p><b>Custom language</b></p><p>{_rich_text(msg)}</p>{rich_buttons(rows)}"
    classic = f"<b>Custom language</b>\n{_plain(msg)}"
    return rich, classic, rows


def _result_view(entry, listen=True):
    rows = []
    if listen and entry.get("can_tts"):
        rows.append([("🎧 Listen to this Translation", "callback", f"trx:{entry['token']}:listen", "primary")])
    dst, src = _name(entry["dst_code"]), _name(entry["src"])
    result = entry["result"][:3500]
    rich = (
        f"<p><b>{_rich_text(dst)}</b> translation (from {_rich_text(src)})</p>"
        f"<aside>{_rich_text(result)}</aside>"
        f"{rich_buttons(rows)}"
    )
    classic = f"<b>{_plain(dst)}</b> translation (from {_plain(src)})\n\n<blockquote>{_plain(result)}</blockquote>"
    return rich, classic, rows


# ================================
#        /tr COMMAND
# ================================
@app.on_message(filters.command(["tr", "translate", "trr", "t"]) & ~BANNED_USERS, group=-10)
async def tr_cmd(client, message):
    # own group + stop_propagation: no other plugin (chatbot / afk / reply handlers...) can
    # swallow a "/tr" sent as a reply before this runs
    try:
        await _tr_cmd(client, message)
    except Exception:
        log.exception("tr command crashed")
    message.stop_propagation()


async def _tr_cmd(client, message):
    reply = message.reply_to_message
    if reply is not None and getattr(reply, "empty", False):
        reply = None
    if reply is None and getattr(message, "reply_to_message_id", None):
        # fetch it ourselves: first "the message this one replied to", then by plain id
        for kw in ({"reply_to_message_ids": message.id}, {"message_ids": message.reply_to_message_id}):
            try:
                got = await client.get_messages(message.chat.id, **kw)
                if got is not None and not getattr(got, "empty", False):
                    reply = got
                    break
            except Exception as e:
                log.warning("couldn't fetch replied message (%s): %r", list(kw), e)

    text = None
    if reply is not None:
        text = reply.text or reply.caption
        if not text and getattr(reply, "poll", None):
            text = reply.poll.question
    if not text:
        parts = (message.text or "").split(None, 1)
        if len(parts) > 1:
            text = parts[1]
    log.info("tr: replied=%s has_text=%s", reply is not None, bool(text))
    if not text or not str(text).strip():
        if reply is not None:
            return await message.reply_text("❌ That message has no text to translate.")
        return await message.reply_text(
            "Reply to a message with <code>/tr</code>, or use <code>/tr your text here</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    text = str(text)
    text = text.strip()[:MAX_CHARS]

    try:
        src = await detect_lang(text)
    except Exception as e:
        log.warning("detect failed: %r", e)
        return await message.reply_text("❌ Couldn't reach the translator right now, try again in a moment.")

    _purge()
    entry = {
        "token": secrets.token_hex(4),
        "user_id": message.from_user.id if message.from_user else 0,
        "text": text,
        "src": src,
        "ts": time.time(),
        "busy": False,
        "rich": False,
        "chat_id": message.chat.id,
        "msg_id": 0,
    }
    rich_html, classic_html, rows = _picker_view(entry)
    # when /tr is a reply, answer UNDER the original message (the one being translated)
    anchor = reply if reply is not None else message
    sent = await send_rich(client, message.chat.id, rich_html, reply_to=anchor.id)
    entry["rich"] = sent is not None
    if sent is None:
        try:
            sent = await anchor.reply_text(
                classic_html, parse_mode=enums.ParseMode.HTML, reply_markup=classic_keyboard(rows)
            )
        except Exception as e:
            log.warning("classic picker failed: %r", e)
            return
    entry["msg_id"] = sent.id
    _STATE[entry["token"]] = entry


# ================================
#        BUTTONS
# ================================
async def _run_translation(client, entry, code) -> bool:
    entry["busy"] = True
    try:
        res = await do_translate(entry, code)
        entry.update(
            result=res["text"],
            tts_text=res["tts_text"],
            tts_lang=res["tts_lang"],
            dst_code=code,
            can_tts=_tts_ok(res["tts_lang"]),
        )
        await show(client, entry, _result_view(entry))
        return True
    except Exception as e:
        log.warning("translate failed: %r", e)
        return False
    finally:
        entry["busy"] = False


@app.on_callback_query(filters.regex(r"^trx:") & ~BANNED_USERS, group=-10)
async def tr_callback(client, query):
    try:
        await _tr_callback(client, query)
    except Exception:
        log.exception("tr callback crashed")
    query.stop_propagation()


async def _tr_callback(client, query):
    try:
        _, token, action = query.data.split(":", 2)
    except ValueError:
        return await query.answer()
    entry = _STATE.get(token)
    if not entry:
        return await query.answer("This request expired. Send /tr again.", show_alert=True)
    if (
        not ALLOW_ANYONE_TO_CLICK
        and entry["user_id"]
        and query.from_user.id != entry["user_id"]
    ):
        return await query.answer("Only the person who used /tr can press these.", show_alert=True)
    if entry["busy"]:
        return await query.answer("Please wait…")

    key = (entry["chat_id"], entry["msg_id"])

    if action == "custom":
        _AWAIT[key] = token
        await query.answer()
        return await show(client, entry, _custom_view(entry))

    if action == "back":
        _AWAIT.pop(key, None)
        await query.answer()
        return await show(client, entry, _picker_view(entry))

    if action == "listen":
        return await _listen(client, query, entry)

    if action not in NAMES:
        return await query.answer()
    await query.answer("Translating…")
    if not await _run_translation(client, entry, action):
        await query.message.reply_text("❌ Translation failed, please try again.")


# ---------- custom language: catch exactly ONE reply ----------
async def _custom_filter(_, __, m):
    r = m.reply_to_message
    if not r or not m.text or not m.from_user:
        return False
    token = _AWAIT.get((m.chat.id, r.id))
    entry = _STATE.get(token) if token else None
    if not entry:
        return False
    return ALLOW_ANYONE_TO_CLICK or not entry["user_id"] or m.from_user.id == entry["user_id"]


awaiting_custom = filters.create(_custom_filter)


@app.on_message(awaiting_custom & ~BANNED_USERS, group=-9)
async def tr_custom_reply(client, message):
    key = (message.chat.id, message.reply_to_message.id)
    entry = _STATE.get(_AWAIT.pop(key, None))   # popped -> only one reply is ever caught
    if entry:
        code = _resolve_lang(message.text)
        if not code:
            await show(client, entry, _picker_view(entry))
            await message.reply_text(
                "❌ I couldn't recognise that language. Pick one above or press Custom Language again."
            )
        elif not await _run_translation(client, entry, code):
            await show(client, entry, _picker_view(entry))
            await message.reply_text("❌ Translation failed, please try again.")
    message.stop_propagation()


# ---------- listen ----------
async def _send_audio(client, entry, data: bytes) -> bool:
    chat_id, reply_to = entry["chat_id"], entry["msg_id"]
    title = f"Translation ({_name(entry['dst_code'])})"

    def mem():
        b = io.BytesIO(data)
        b.name = "translation.mp3"
        return b

    # 1) rich message with InputMediaAudio (in-memory upload, then cached file_id)
    if InputRichMessageMedia is not None and InputMediaAudio is not None:
        body = (
            f'<figure><audio src="tg://audio?id=tts"></audio>'
            f"<figcaption>{_rich_text(title)}</figcaption></figure>"
        )
        for kind in ("mem", "cache"):
            if kind == "mem":
                src = mem()
            else:
                if not TTS_CACHE_CHAT:
                    break
                try:
                    m = await client.send_audio(TTS_CACHE_CHAT, mem(), title=title, file_name="translation.mp3")
                    src = m.audio.file_id
                except Exception as e:
                    log.warning("tts cache upload failed: %r", e)
                    break
            try:
                media = [
                    InputRichMessageMedia(
                        id="tts",
                        media=InputMediaAudio(
                            media=src, title=title, performer=AUDIO_PERFORMER, file_name="translation.mp3"
                        ),
                    )
                ]
            except Exception as e:
                log.warning("couldn't build rich audio media: %r", e)
                break
            if await send_rich(client, chat_id, body, reply_to=reply_to, media=media):
                return True

    # 2) classic audio message
    for extra in _reply_variants(reply_to):
        try:
            await client.send_audio(
                chat_id, mem(), title=title, performer=AUDIO_PERFORMER,
                file_name="translation.mp3", caption=f"🎧 {title}", **extra,
            )
            return True
        except Exception as e:
            log.warning("send_audio failed (%s): %r", list(extra), e)
    return False


async def _listen(client, query, entry):
    if not entry.get("result") or not entry.get("can_tts"):
        return await query.answer("Audio isn't available for this language.", show_alert=True)
    entry["busy"] = True
    try:
        await query.answer("🎧 Generating audio…")
        try:
            data = await asyncio.to_thread(
                _tts_sync, entry["tts_text"][:TTS_MAX_CHARS], entry["tts_lang"]
            )
        except Exception as e:
            log.warning("tts failed: %r", e)
            return await query.message.reply_text("❌ Couldn't make the audio, please try again.")
        if await _send_audio(client, entry, data):
            await show(client, entry, _result_view(entry, listen=False))   # button removed
        else:
            await query.message.reply_text("❌ Couldn't send the audio, please try again.")
    finally:
        entry["busy"] = False
