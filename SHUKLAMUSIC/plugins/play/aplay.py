import asyncio
import base64
import difflib
import hashlib
import html
import importlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import tempfile
import time
from typing import Optional

import aiohttp
from aiohttp import web

from pyrogram import filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

try:  # kurigram: rich messages (same as play.py / start.py)
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

try:  # classic copy button (fallback keyboard only)
    from pyrogram.types import CopyTextButton
except Exception:  # pragma: no cover
    CopyTextButton = None

import config
from SHUKLAMUSIC import app
from SHUKLAMUSIC.core.call import SHUKLA
from SHUKLAMUSIC.utils.database import is_active_chat
from SHUKLAMUSIC.utils.decorators.language import languageCB
from SHUKLAMUSIC.utils.exceptions import AssistantErr
from SHUKLAMUSIC.utils.inline import aq_markup, close_markup
from SHUKLAMUSIC.utils.stream.queue import put_queue, put_queue_index
from SHUKLAMUSIC.misc import db
from config import BANNED_USERS

log = logging.getLogger("aplay")

# ═══════════════════════════════════════════════════════════════════════════════
#   CONFIGURATION  —  change these to your liking
# ═══════════════════════════════════════════════════════════════════════════════

ANILIST_ENDPOINT = "https://graphql.anilist.co"

# /aplay and /testaplay are restricted to this user only
OWNER_ID = 7659846392

# Test stream used by /testaplay (Blogger video token; may expire -> replace it)
TEST_URL = (
    "https://www.blogger.com/video.g?token="
    "AD6v5dxW3XUO77-5D9y45ZuNrUoD2e22RSxh89srnQTgVPTKhjgmGJzLei4OWPmj-JsI2W0OULkhYDXc2HKCUUH9f2j2uzPc6Nn_ypM9tMY80iCVidl0e1XIXWcthjx04bNeaNbACEEX"
)
TEST_TITLE = "One Piece - Episode 1150 (test)"

# How many episodes per page inside the episode picker (2 per row => 10 rows)
EP_PAGE_SIZE = 20

# Media shown on the now-playing card when the anime has no poster
FALLBACK_POSTER = getattr(config, "PLAYLIST_IMG_URL", "https://files.catbox.moe/wifnjm.jpg")

LBL_BACK = "𝖡𝖺𝖼𝗄"
LBL_CANCEL = "ᑕᒪOՏᗴ"
LBL_RESUME = "𝖱𝖾𝗌𝗎𝗆𝖾"
LBL_PAUSE = "𝖯𝖺𝗎𝗌𝖾"
LBL_STOP = "𝖲𝗍𝗈𝗉"
LBL_CLOSE = "ᑕᒪOՏᗴ"

EMOJI_ANALYSING = 5573473356579078196  # same as play.py

RICH_EMOJI_STYLE = 1  # 1 = <tg-emoji emoji-id="..">, 0 = <emoji id=..>; auto-detected

# In-memory session state: {f"{chat_id}:{user_id}": AnimeSession}
# Nothing touches the DB — everything is parallel and dies with the process.
_SESSIONS = {}


# ═══════════════════════════════════════════════════════════════════════════════
#   DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class Season:
    def __init__(self, index, media_id, title, episodes, cover, year, status, score, genres, desc,
                 titles=None, duration_min=None):
        self.index = index          # 1-based season number shown to the user
        self.media_id = media_id    # AniList media id
        self.title = title
        self.episodes = episodes or 0
        self.cover = cover
        self.year = year
        self.status = status
        self.score = score
        self.genres = genres or []
        self.desc = desc or ""
        self.titles = titles or []          # english + romaji, used for matching on stream sites
        self.duration_min = duration_min    # minutes per episode (AniList)


class AnimeSession:
    """Everything about one user's /aplay flow. Lives only in RAM."""

    def __init__(self, user_id, user_name, chat_id, original_chat_id, video, forceplay):
        self.user_id = user_id
        self.user_name = user_name
        self.chat_id = chat_id
        self.original_chat_id = original_chat_id
        self.video = video
        self.forceplay = forceplay
        self.seasons: list[Season] = []
        self.anime_title = ""
        self.anime_cover = ""
        self.msg = None             # the picker message (rich or classic)

    def key(self):
        return f"{self.original_chat_id}:{self.user_id}"


# ═══════════════════════════════════════════════════════════════════════════════
#   ANILIST  (metadata: search, seasons, episodes, poster)
# ═══════════════════════════════════════════════════════════════════════════════

_ANILIST_SEARCH_QUERY = """
query ($search: String) {
  Page(page: 1, perPage: 1) {
    media(search: $search, type: ANIME, sort: POPULARITY_DESC) {
      id
      title { romaji english }
      coverImage { extraLarge large }
      episodes
      duration
      seasonYear
      status
      averageScore
      genres
      description
      relations {
        edges {
          relationType
          node {
            id
            title { romaji english }
            coverImage { extraLarge large }
            episodes
            duration
            seasonYear
            status
            averageScore
            genres
            description
            format
          }
        }
      }
    }
  }
}
"""

_ANILIST_EPISODES_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    episodes
    airingSchedule(notYetAired: false, perPage: 100) {
      nodes { episode }
    }
  }
}
"""


async def anilist_request(query: str, variables: dict):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ANILIST_ENDPOINT,
                json={"query": query, "variables": variables},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("data")
    except Exception as e:
        log.warning("anilist request failed: %r", e)
        return None


def _media_title(media) -> str:
    t = media.get("title") or {}
    return t.get("english") or t.get("romaji") or "Unknown Anime"


def _media_cover(media) -> str:
    c = media.get("coverImage") or {}
    return c.get("extraLarge") or c.get("large") or FALLBACK_POSTER


def _clean_desc(desc: str) -> str:
    if not desc:
        return ""
    desc = re.sub(r"<br\s*/?>", " ", desc)
    desc = re.sub(r"<[^>]+>", "", desc)
    return desc[:300] + ("…" if len(desc) > 300 else "")


async def search_anime_with_seasons(query: str) -> Optional[tuple]:
    """
    Search AniList for the anime, then walk its relations to collect every
    SEQUEL / PREQUEL as a "season". Returns (main_title, main_cover, [Season, ...])
    or None when nothing is found.
    """
    data = await anilist_request(_ANILIST_SEARCH_QUERY, {"search": query})
    if not data:
        return None
    media_list = (data.get("Page") or {}).get("media") or []
    if not media_list:
        return None
    main = media_list[0]

    seasons: list[Season] = []

    def _add(media, idx):
        seasons.append(Season(
            index=idx,
            media_id=media["id"],
            title=_media_title(media),
            episodes=media.get("episodes") or 0,
            cover=_media_cover(media),
            year=media.get("seasonYear"),
            status=media.get("status") or "UNKNOWN",
            score=media.get("averageScore"),
            genres=media.get("genres") or [],
            desc=_clean_desc(media.get("description") or ""),
            titles=[t for t in ((media.get("title") or {}).get("english"), (media.get("title") or {}).get("romaji")) if t],
            duration_min=media.get("duration"),
        ))

    _add(main, 1)

    # Collect related SEQUEL/PREQUEL entries (skip movies/OVAs as "seasons")
    relations = (main.get("relations") or {}).get("edges") or []
    related = []
    for edge in relations:
        if edge.get("relationType") in ("SEQUEL", "PREQUEL"):
            node = edge.get("node") or {}
            if node.get("format") in ("TV", "TV_SHORT", "ONA") and node.get("id"):
                related.append(node)

    # Sort by year so S1, S2, ... are in order; dedupe by media id
    seen = {main["id"]}
    related.sort(key=lambda m: (m.get("seasonYear") or 0, m.get("id") or 0))
    for node in related:
        if node["id"] in seen:
            continue
        seen.add(node["id"])
        _add(node, len(seasons) + 1)

    # If AniList didn't report episode counts, try the airing schedule
    for s in seasons:
        if s.episodes == 0:
            ep_data = await anilist_request(_ANILIST_EPISODES_QUERY, {"id": s.media_id})
            if ep_data:
                media = ep_data.get("Media") or {}
                s.episodes = media.get("episodes") or 0
                if s.episodes == 0:
                    nodes = ((media.get("airingSchedule") or {}).get("nodes")) or []
                    eps = [n.get("episode") or 0 for n in nodes]
                    s.episodes = max(eps) if eps else 0

    return _media_title(main), _media_cover(main), seasons


# ═══════════════════════════════════════════════════════════════════════════════
#   STREAM ENGINE
#   resolvers (AllAnime / Consumet / yt-dlp)  →  ffmpeg  →  local HTTP "tail" server
#
#   ffmpeg pulls the episode (HLS/mp4) and writes it to a cache file at full
#   speed (the DOWNLOAD half).  A tiny local HTTP server serves that same file
#   while it is still growing (the STREAM half) and the VC engine plays that
#   local URL.  So playback starts after a few MB, and download keeps running in
#   parallel.  Title / show / season / episode are written into the stream
#   metadata with ffmpeg -metadata.
#
#   Needs:  ffmpeg + ffprobe in PATH,  pip install -U yt-dlp aiohttp cryptography curl_cffi
# ═══════════════════════════════════════════════════════════════════════════════

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"

# "sub" or "dub" (the other one is used automatically as a fallback)
ANIME_AUDIO = "sub"
# Soft-sub streams (megaplay) keep subtitles in a separate .vtt; the pipeline drops
# them (-sn), so burn the English track into the video. Needs re-encode (CPU!).
# Set env ANIME_BURN_SUBS=0 to disable.
ANIME_BURN_SUBS = os.getenv("ANIME_BURN_SUBS", "1") != "0"

# Your own self-hosted Consumet (Vercel/Render/Docker) goes FIRST. The public
# instances are mostly dead, they're only here as a last resort.
CONSUMET_BASES = [b for b in [
    os.getenv("CONSUMET_BASE", "").rstrip("/"),          # env override (optional)
    "https://consumet-api-ruby.vercel.app",              # your self-hosted instance
    "https://api.consumet.org",                          # public one, mostly dead
] if b]
# order = priority; drop the ones that fail your curl test
CONSUMET_PROVIDERS = ["animepahe", "zoro", "gogoanime"]

# Optional proxy for AllAnime (Cloudflare often blocks datacenter IPs like Heroku)
# e.g. ALLANIME_PROXY=http://user:pass@host:port
ALLANIME_PROXY = os.getenv("ALLANIME_PROXY") or None

ALLANIME_API = "https://api.allanime.day/api"
ALLANIME_BASE = "allanime.day"
ALLANIME_REFERER = "https://allmanga.to"
ALLANIME_ORIGIN = "https://allmanga.to"
# AllAnime sometimes wraps responses in an AES blob ("tobeparsed"). If they
# rotate this secret the provider just fails and the next provider is tried.
ALLANIME_KEY_SECRET = os.getenv("ALLANIME_KEY_SECRET", "Xot36i3lK3:v1")

CACHE_DIR = os.path.join(tempfile.gettempdir(), "aplay_cache")
BUFFER_BYTES = 4 * 1024 * 1024      # start playing once this much is on disk
BUFFER_TIMEOUT = 60                 # seconds to wait for that buffer
CACHE_TTL = 3 * 3600                # delete an episode's cache after this much idle time
STREAM_BIND_HOST = "127.0.0.1"      # use "0.0.0.0" + STREAM_PUBLIC_HOST if the VC engine is in another container
STREAM_PUBLIC_HOST = "127.0.0.1"

if shutil.which("ffmpeg") is None:
    log.warning("ffmpeg not found in PATH — /aplay streaming will not work")


class StreamSource:
    def __init__(self, url, headers=None, provider="", duration=None, title=None, sub_path=None):
        self.sub_path = sub_path    # local .vtt/.srt to burn in (soft-sub streams)
        self.url = url
        self.headers = dict(headers or {})
        self.provider = provider
        self.duration = duration    # seconds, when the extractor knows it
        self.title = title

    @property
    def is_hls(self):
        return ".m3u8" in self.url.lower()


# ─── helpers ────────────────────────────────────────────────────────────────────

def _fmt_duration(sec) -> str:
    try:
        sec = int(sec)
    except Exception:
        return "24:00"
    if sec <= 0:
        return "24:00"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _ua_of(headers: dict) -> str:
    for k, v in headers.items():
        if k.lower() == "user-agent":
            return v
    return UA


def _ff_input_args(src: StreamSource) -> list:
    if not src.url.lower().startswith("http"):
        return []
    args = ["-user_agent", _ua_of(src.headers)]
    blob = "".join(f"{k}: {v}\r\n" for k, v in src.headers.items() if k.lower() != "user-agent")
    if blob:
        args += ["-headers", blob]
    if src.is_hls:
        args += ["-allowed_extensions", "ALL"]
    return args


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _uniq(items):
    out, seen = [], set()
    for i in items:
        if i and _norm(i) not in seen:
            seen.add(_norm(i))
            out.append(i)
    return out


# ─── yt-dlp (page/embed URL → direct media URL + headers) ───────────────────────

async def ytdlp_resolve(url: str, headers: dict = None) -> Optional[StreamSource]:
    def _run():
        import yt_dlp
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "skip_download": True, "format": "best/bestvideo+bestaudio",
            "http_headers": {"User-Agent": UA, **(headers or {})},
            "socket_timeout": 15,
        }
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
        if info and info.get("entries"):
            info = next((e for e in info["entries"] if e), None)
        return info

    try:
        info = await asyncio.get_running_loop().run_in_executor(None, _run)
    except Exception as e:
        log.warning("yt-dlp failed for %s: %r", url[:80], e)
        return None
    if not info or not info.get("url"):
        return None
    return StreamSource(
        info["url"], info.get("http_headers") or {}, "yt-dlp",
        duration=info.get("duration"), title=info.get("title"),
    )


async def blogger_resolve(url: str) -> Optional[StreamSource]:
    """blogger.com/video.g?token=... → direct googlevideo mp4 (parsed from the player page)."""
    hdrs = {"Referer": "https://www.blogger.com/"}
    page = await _http_text(url, headers=hdrs, tag="blogger")
    if not page:
        return None
    play_url, best = None, -1
    m = re.search(r"VIDEO_CONFIG\s*=\s*(\{.*?\})\s*</script>", page, re.S)
    if m:
        try:
            cfg = json.loads(m.group(1))
            for st in cfg.get("streams") or []:
                u = st.get("play_url")
                try:
                    fid = int(st.get("format_id") or 0)
                except (TypeError, ValueError):
                    fid = 0
                if u and fid >= best:
                    play_url, best = u, fid
        except Exception as e:
            log.warning("blogger VIDEO_CONFIG parse failed: %r", e)
    if not play_url:
        m = re.search(r"https://[^\"'\\\s]*googlevideo\.com/videoplayback[^\"'\\\s]*", page)
        if m:
            play_url = m.group(0).replace("\\u0026", "&").replace("&amp;", "&")
    if not play_url:
        log.warning("blogger: no stream found (token expired?)")
        return None
    return StreamSource(play_url, hdrs, "blogger")


async def resolve_direct(url: str) -> Optional[StreamSource]:
    """A link the user pasted: raw media → use as is, blogger → page parser, anything else → yt-dlp."""
    if re.search(r"\.(m3u8|mp4|mkv|webm)(\?|$)", url.lower()):
        return StreamSource(url, {}, "direct")
    if "blogger.com/video.g" in url:
        src = await blogger_resolve(url)
        if src:
            return src
    return await ytdlp_resolve(url)


# ─── HTTP helper: curl_cffi (Chrome TLS fingerprint) first, aiohttp fallback ────

try:
    from curl_cffi.requests import AsyncSession as _CurlSession
except Exception:  # pragma: no cover
    _CurlSession = None
    logging.getLogger(__name__).info("curl_cffi not installed - Cloudflare-protected APIs may 403")


async def _http_json(method, url, *, params=None, body=None, headers=None,
                     timeout=15, proxy=None, tag="http"):
    """Return parsed JSON or None. Never raises."""
    hdrs = dict(headers or {})
    if _CurlSession is not None:
        try:
            async with _CurlSession(impersonate="chrome") as cs:
                r = await cs.request(
                    method, url, params=params, json=body, headers=hdrs, timeout=timeout,
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                )
                if r.status_code == 200:
                    return r.json()
                log.warning("%s %s -> HTTP %s (curl_cffi)", tag, url[:60], r.status_code)
                return None
        except Exception as e:
            log.warning("%s curl_cffi request failed: %r", tag, e)
    hdrs.setdefault("User-Agent", UA)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.request(
                method, url, params=params, json=body, headers=hdrs, proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                if r.status != 200:
                    log.warning("%s %s -> HTTP %s", tag, url[:60], r.status)
                    return None
                return await r.json(content_type=None)
    except Exception as e:
        log.warning("%s request failed: %r", tag, e)
        return None


async def _http_text(url, *, headers=None, timeout=20, tag="http"):
    hdrs = dict(headers or {})
    if _CurlSession is not None:
        try:
            async with _CurlSession(impersonate="chrome") as cs:
                r = await cs.get(url, headers=hdrs, timeout=timeout)
                if r.status_code == 200:
                    return r.text
                log.warning("%s %s -> HTTP %s (curl_cffi)", tag, url[:60], r.status_code)
                return None
        except Exception as e:
            log.warning("%s curl_cffi request failed: %r", tag, e)
    hdrs.setdefault("User-Agent", UA)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url, headers=hdrs, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status != 200:
                    log.warning("%s %s -> HTTP %s", tag, url[:60], r.status)
                    return None
                return await r.text()
    except Exception as e:
        log.warning("%s request failed: %r", tag, e)
        return None


# ─── provider 1: AllAnime (same API ani-cli uses) ───────────────────────────────

_AA_SEARCH_GQL = (
    "query($search: SearchInput, $limit: Int, $page: Int, "
    "$translationType: VaildTranslationTypeEnumType, $countryOrigin: VaildCountryOriginEnumType) "
    "{ shows(search: $search, limit: $limit, page: $page, translationType: $translationType, "
    "countryOrigin: $countryOrigin) { edges { _id name availableEpisodes __typename } } }"
)
_AA_EP_GQL = (
    "query($showId: String!, $translationType: VaildTranslationTypeEnumType!, $episodeString: String!) "
    "{ episode(showId: $showId, translationType: $translationType, episodeString: $episodeString) "
    "{ episodeString sourceUrls } }"
)
_AA_PRIORITY = ["default", "s-mp4", "luf-mp4", "yt-mp4"]


def _aa_decode(enc: str) -> Optional[str]:
    """sourceUrl '--<hex>' → plain path/URL (each byte XOR 56)."""
    try:
        return "".join(chr(int(enc[i:i + 2], 16) ^ 56) for i in range(0, len(enc), 2))
    except ValueError:
        return None


def _aa_decrypt_blob(blob: str):
    """AES-256-CTR blob. AllAnime rotates the key / trailer length from time to
    time, so try the configured secret (env ALLANIME_KEY_SECRET) and the old one,
    with and without the 16-byte trailer, and keep whichever yields valid JSON."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        raw = base64.b64decode(blob)
    except Exception as e:
        log.warning("allanime blob decode failed: %r", e)
        return None
    secrets_to_try = []
    for k in (ALLANIME_KEY_SECRET, "Xot36i3lK3:v1"):
        if k and k not in secrets_to_try:
            secrets_to_try.append(k)
    for secret in secrets_to_try:
        key = hashlib.sha256(secret.encode()).digest()
        for tail in (16, 0):
            try:
                iv = raw[1:13] + b"\x00\x00\x00\x02"
                body = raw[13:-tail] if tail else raw[13:]
                dec = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
                return json.loads(dec.update(body) + dec.finalize())
            except Exception:
                continue
    log.warning("allanime blob decrypt failed (key/format probably rotated - set ALLANIME_KEY_SECRET)")
    return None


def _aa_headers():
    return {"Referer": ALLANIME_REFERER, "Origin": ALLANIME_ORIGIN,
            "Content-Type": "application/json", "Accept": "application/json"}


async def _aa_get(http, url, params=None):
    return await _http_json("GET", url, params=params, headers=_aa_headers(),
                            proxy=ALLANIME_PROXY, tag="allanime")


async def _aa_gql(http, query: str, variables: dict):
    """POST first (works through the Cloudflare JS challenge in ani-cli >= 4.11),
    plain GET as fallback."""
    data = await _http_json(
        "POST", ALLANIME_API, body={"query": query, "variables": variables},
        headers=_aa_headers(), proxy=ALLANIME_PROXY, tag="allanime",
    )
    if data is not None:
        return data
    return await _aa_get(http, ALLANIME_API, {"variables": json.dumps(variables), "query": query})


def _best_show(edges, titles, ep, trans):
    best, best_score = None, 0.0
    for e in edges or []:
        avail = (e.get("availableEpisodes") or {}).get(trans) or 0
        if avail < ep:
            continue
        n = _norm(e.get("name"))
        score = max(difflib.SequenceMatcher(None, n, _norm(t)).ratio() for t in titles if t)
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 0.55 else None


async def _aa_expand(http, raw: str, name: str) -> list:
    path = _aa_decode(raw[2:]) if raw.startswith("--") else raw
    if not path:
        return []
    if path.startswith("/"):
        if "clock.json" not in path:
            path = path.replace("clock", "clock.json", 1)
        j = await _aa_get(http, f"https://{ALLANIME_BASE}{path}")
        out = []
        for l in (j or {}).get("links") or []:
            link = l.get("link")
            if not link:
                continue
            hdrs = dict(l.get("headers") or {})
            hdrs.setdefault("Referer", ALLANIME_REFERER)
            out.append(StreamSource(link, hdrs, f"allanime/{name}"))
        return out
    if path.startswith("http"):
        r = await ytdlp_resolve(path, {"Referer": ALLANIME_REFERER})
        if r:
            r.provider = f"allanime/{name}"
            return [r]
    return []


async def _prov_allanime(season, titles, ep):
    async with aiohttp.ClientSession() as http:
        for trans in (ANIME_AUDIO, "dub" if ANIME_AUDIO == "sub" else "sub"):
            show = None
            for t in titles[:3]:
                data = await _aa_gql(http, _AA_SEARCH_GQL, {
                    "search": {"allowAdult": False, "allowUnknown": False, "query": t},
                    "limit": 40, "page": 1, "translationType": trans, "countryOrigin": "ALL",
                })
                edges = (((data or {}).get("data") or {}).get("shows") or {}).get("edges")
                show = _best_show(edges, titles, ep, trans)
                if show:
                    break
            if not show:
                continue

            data = await _aa_gql(http, _AA_EP_GQL, {
                "showId": show["_id"], "translationType": trans, "episodeString": str(ep),
            })
            d = (data or {}).get("data") or {}
            if d.get("tobeparsed"):
                d = _aa_decrypt_blob(d["tobeparsed"]) or {}
            urls = ((d.get("episode") or {}).get("sourceUrls")) or []
            if not urls:
                continue

            def _rank(u):
                n = (u.get("sourceName") or "").lower()
                return _AA_PRIORITY.index(n) if n in _AA_PRIORITY else len(_AA_PRIORITY)

            out = []
            for u in sorted(urls, key=_rank)[:5]:
                out += await _aa_expand(http, u.get("sourceUrl") or "", u.get("sourceName") or "?")
                if len(out) >= 3:
                    break
            if out:
                return out
    return []


# ─── embed resolvers (gogoanime wrapper → megaplay player → .m3u8) ─────────────

async def _fetch_sub(url: str) -> Optional[str]:
    txt = await _http_text(url, headers={"Referer": "https://megaplay.buzz/"}, tag="subs")
    if not txt or "-->" not in txt:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{secrets.token_hex(6)}.vtt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    return path


def _pick_sub_track(tracks) -> Optional[str]:
    caps = [t for t in tracks or [] if isinstance(t, dict) and t.get("file")
            and str(t.get("kind", "captions")).lower() in ("captions", "subtitles")]
    for t in caps:                                     # English first
        if "english" in str(t.get("label", "")).lower():
            return t["file"]
    for t in caps:
        if t.get("default"):
            return t["file"]
    return caps[0]["file"] if caps else None


async def _megaplay_resolve(embed_url: str) -> list:
    """megaplay.buzz/stream/... → data-id → /stream/getSources → .m3u8 (+ subtitle track)."""
    m = re.match(r"https?://[^/]+", embed_url)
    if not m:
        return []
    base = m.group(0)
    html_ = await _http_text(embed_url, headers={"Referer": "https://gogoanime.me.uk/"}, tag="megaplay")
    mid = re.search(r"data-id=[\"']?(\w+)", html_ or "")
    if not mid:
        log.warning("megaplay: no data-id in %s", embed_url[:80])
        return []
    data = await _http_json(
        "GET", f"{base}/stream/getSources", params={"id": mid.group(1)},
        headers={"Referer": embed_url, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        tag="megaplay",
    )
    if not isinstance(data, dict):
        return []
    srcs = data.get("sources")
    if isinstance(srcs, dict):
        srcs = [srcs]
    if not isinstance(srcs, list):
        log.warning("megaplay: sources missing/encrypted: %s", str(srcs)[:80])
        return []
    hdrs = {"Referer": base + "/", "Origin": base}
    sub_path = None
    if ANIME_BURN_SUBS and ANIME_AUDIO == "sub":
        sub_url = _pick_sub_track(data.get("tracks"))
        if sub_url:
            sub_path = await _fetch_sub(sub_url)
    out = []
    for x in srcs:
        f = x.get("file") if isinstance(x, dict) else None
        if f:
            out.append(StreamSource(f, hdrs, "megaplay", sub_path=sub_path))
    return out


async def _resolve_embed(url: str) -> list:
    """Embed/player page → [StreamSource]. Follows the wrapper iframe; megaplay is
    handled natively, anything else goes to yt-dlp."""
    target = url
    page = await _http_text(url, headers={"Referer": "https://gogoanime.me.uk/"}, tag="embed")
    if page:
        fr = re.search(r"<iframe[^>]+src=[\"']([^\"']+)", page, re.I)
        if fr:
            target = fr.group(1)
    if "megaplay" in target:
        return await _megaplay_resolve(target)
    m = re.match(r"https?://[^/]+/", target)
    r = await ytdlp_resolve(target, {"Referer": m.group(0)} if m else None)
    return [r] if r else []


# ─── provider 2: Consumet (self-hosted) ─────────────────────────────────────────

_MEDIA_RE = re.compile(r"\.(m3u8|mp4|mkv|webm)(\?|$)", re.I)


async def _consumet_get(path: str, params: dict = None):
    async with aiohttp.ClientSession() as session:
        for base in CONSUMET_BASES:
            try:
                async with session.get(
                    f"{base}{path}", params=params,
                    headers={"User-Agent": UA},
                    timeout=aiohttp.ClientTimeout(total=20),     # vercel cold starts
                ) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    log.warning("consumet %s%s -> HTTP %s", base, path[:50], resp.status)
            except Exception as e:
                log.warning("consumet %s%s failed: %r", base, path[:50], e)
                continue
    return None


async def _consumet_streams(srcs: list, hdrs: dict, prov: str) -> list:
    """Consumet 'sources' → StreamSource list.
    Direct media (isM3U8 / .m3u8 / .mp4) is used as is; embed/player pages
    (isM3U8 false, e.g. gogoanime newplayer.php) are resolved with yt-dlp."""
    def _q(s):
        q = str(s.get("quality") or "")
        m = re.search(r"(\d{3,4})", q)
        return -(int(m.group(1)) if m else 5000)   # master/"auto" first, then 1080 > 720 ...

    out, embed_tries = [], 0
    for s in sorted([x for x in srcs if x.get("url")], key=_q):
        url = s["url"]
        if s.get("isM3U8") or _MEDIA_RE.search(url):
            out.append(StreamSource(url, hdrs, f"consumet/{prov}"))
            continue
        if embed_tries >= 2:
            continue
        embed_tries += 1
        if ANIME_AUDIO == "dub" and "category=sub" in url:
            url = url.replace("category=sub", "category=dub")
        res = await _resolve_embed(url)
        for r in res:
            r.provider = f"consumet/{prov}/{r.provider}"
            out.append(r)
        if not res:
            log.info("consumet/%s embed not resolvable: %s", prov, url[:90])
    return out


async def _prov_consumet(season, titles, ep):
    """Via AniList id: /meta/anilist/info + /meta/anilist/watch."""
    for prov in CONSUMET_PROVIDERS:
        info = await _consumet_get(f"/meta/anilist/info/{season.media_id}", {"provider": prov})
        match = None
        for e in (info or {}).get("episodes") or []:
            try:
                if int(float(e.get("number"))) == ep:
                    match = e
                    break
            except (TypeError, ValueError):
                continue
        if not match:
            continue
        data = await _consumet_get(f"/meta/anilist/watch/{match['id']}", {"provider": prov})
        if not data:
            continue
        out = await _consumet_streams(data.get("sources") or [], data.get("headers") or {}, prov)
        if out:
            return out
    return []


async def _prov_consumet_gogo(season, titles, ep):
    """Direct route: /anime/gogoanime/{search} → /info/{id} → /watch/{episodeId}.
    (the one that returns gogoanime.me.uk/newplayer.php embed pages)"""
    from urllib.parse import quote
    show = None
    for t in titles[:3]:
        res = await _consumet_get(f"/anime/gogoanime/{quote(t)}")
        cands = [r for r in (res or {}).get("results") or [] if r.get("id")]
        if ANIME_AUDIO == "sub":
            cands = [r for r in cands if "dub" not in _norm(r.get("title") or r["id"]).split()] or cands
        best, best_score = None, 0.0
        for r in cands:
            n = _norm(r.get("title") or r["id"])
            score = max(difflib.SequenceMatcher(None, n, _norm(x)).ratio() for x in titles if x)
            if score > best_score:
                best, best_score = r, score
        if best and best_score >= 0.55:
            show = best
            break
    if not show:
        return []

    ep_ids = []
    info = await _consumet_get(f"/anime/gogoanime/info/{show['id']}")
    for e in (info or {}).get("episodes") or []:
        try:
            if int(float(e.get("number"))) == ep and e.get("id"):
                ep_ids.append(e["id"])
                break
        except (TypeError, ValueError):
            continue
    ep_ids.append(f"{show['id']}-episode-{ep}")       # slug fallback

    for eid in _uniq(ep_ids):
        data = await _consumet_get(f"/anime/gogoanime/watch/{quote(eid)}")
        if not data:
            continue
        out = await _consumet_streams(data.get("sources") or [], data.get("headers") or {}, "gogoanime")
        if out:
            return out
    return []


PROVIDERS = [
    ("AllAnime", _prov_allanime),
    ("Consumet", _prov_consumet),
    ("ConsumetGogo", _prov_consumet_gogo),
]


async def iter_sources(sess, season, ep):
    """Yield (provider_name, [StreamSource,…]) one provider at a time."""
    titles = _uniq([season.title, *getattr(season, "titles", []),
                    sess.anime_title if season.index == 1 else None])
    for name, fn in PROVIDERS:
        try:
            srcs = await asyncio.wait_for(fn(season, titles, ep), 45)
        except Exception as e:
            log.warning("provider %s failed: %r", name, e)
            srcs = []
        if srcs:
            yield name, srcs
        else:
            log.info("provider %s returned nothing for %s EP%s", name, season.title, ep)


# ─── ffmpeg writer + local tail server ──────────────────────────────────────────

class LiveStream:
    def __init__(self, token, path):
        self.token = token
        self.path = path
        self.proc = None
        self.done = asyncio.Event()
        self.created = time.time()
        self.last_access = time.time()
        self.duration = None
        self.url = None
        self.error = ""

    def size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0


_STREAMS = {}
_server = {"runner": None, "port": None, "janitor": None}


async def _serve_ts(request):
    ls = _STREAMS.get(request.match_info["token"])
    if ls is None or not os.path.exists(ls.path):
        return web.Response(status=404)
    resp = web.StreamResponse(status=200, headers={
        "Content-Type": "video/mp2t", "Cache-Control": "no-store",
    })
    await resp.prepare(request)
    try:
        with open(ls.path, "rb") as f:
            while True:
                ls.last_access = time.time()
                chunk = f.read(256 * 1024)
                if chunk:
                    await resp.write(chunk)
                    continue
                if ls.done.is_set():
                    chunk = f.read(256 * 1024)      # final trailing bytes
                    if not chunk:
                        break
                    await resp.write(chunk)
                    continue
                await asyncio.sleep(0.3)            # ffmpeg is still writing → wait
        await resp.write_eof()
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        log.debug("tail client ended: %r", e)
    return resp


async def _janitor():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for token, ls in list(_STREAMS.items()):
            idle = now - max(ls.last_access, ls.created)
            if idle > CACHE_TTL:
                _drop_stream(token)


def _drop_stream(token):
    ls = _STREAMS.pop(token, None)
    if not ls:
        return
    try:
        if ls.proc and ls.proc.returncode is None:
            ls.proc.kill()
    except Exception:
        pass
    try:
        os.remove(ls.path)
    except OSError:
        pass


async def _ensure_server():
    if _server["runner"] is not None:
        return
    web_app = web.Application()
    web_app.router.add_get("/s/{token}.ts", _serve_ts)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, STREAM_BIND_HOST, 0)
    await site.start()
    _server["port"] = site._server.sockets[0].getsockname()[1]
    _server["runner"] = runner
    _server["janitor"] = asyncio.create_task(_janitor())
    os.makedirs(CACHE_DIR, exist_ok=True)
    log.info("aplay tail-server on %s:%s", STREAM_BIND_HOST, _server["port"])


async def probe_duration(src: StreamSource) -> Optional[float]:
    if src.duration:
        return float(src.duration)
    cmd = ["ffprobe", "-v", "error", *_ff_input_args(src),
           "-show_entries", "format=duration", "-of", "csv=p=0", src.url]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
        return float(out.decode().strip().splitlines()[0])
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return None


async def _spawn(src: StreamSource, meta: dict, mode: str) -> Optional[LiveStream]:
    burn = bool(getattr(src, "sub_path", None)) and ANIME_BURN_SUBS
    if burn and mode == "copy":
        return None                      # burning subs always needs the transcode pass
    token = secrets.token_hex(6)
    path = os.path.join(CACHE_DIR, f"{token}.ts")

    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if src.url.startswith("http"):
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    cmd += _ff_input_args(src) + ["-i", src.url,
                                   "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                                   "-map_metadata", "-1"]
    if burn:
        sp = src.sub_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        cmd += ["-vf", f"subtitles='{sp}'"]
    if mode == "copy":
        cmd += ["-c", "copy"]
    else:   # codecs mpegts can't hold (vp9/opus…) → re-encode, still faster than real time on most hosts
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k"]

    tags = {
        "title": meta.get("title"),
        "show": meta.get("show"),
        "season_number": meta.get("season"),
        "episode_id": meta.get("episode"),
        "date": meta.get("year"),
        "service_name": meta.get("title"),
        "service_provider": f"aplay/{src.provider}",
        "comment": f"via {src.provider}",
    }
    for k, v in tags.items():
        if v not in (None, ""):
            cmd += ["-metadata", f"{k}={v}"]
    cmd += ["-f", "mpegts", path]

    ls = LiveStream(token, path)
    try:
        ls.proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg is not installed on this host")

    async def _reap():
        try:
            _, err = await ls.proc.communicate()
            ls.error = (err or b"").decode(errors="ignore")[-400:]
        finally:
            ls.done.set()
            if burn:
                try:
                    os.remove(src.sub_path)
                except OSError:
                    pass

    asyncio.create_task(_reap())
    probe = asyncio.create_task(probe_duration(src))

    t0 = time.time()
    while time.time() - t0 < BUFFER_TIMEOUT:
        if ls.size() >= BUFFER_BYTES or ls.done.is_set():
            break
        await asyncio.sleep(0.5)

    failed = ls.size() < 64 * 1024 or (ls.done.is_set() and ls.proc.returncode not in (0, None) and ls.size() < BUFFER_BYTES)
    if failed:
        log.warning("ffmpeg(%s) %s failed: %s", mode, src.provider, ls.error.strip()[-200:])
        probe.cancel()
        try:
            if ls.proc.returncode is None:
                ls.proc.kill()
        except Exception:
            pass
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    try:
        ls.duration = await asyncio.wait_for(probe, 5)
    except Exception:
        ls.duration = None
    _STREAMS[token] = ls
    ls.url = f"http://{STREAM_PUBLIC_HOST}:{_server['port']}/s/{token}.ts"
    return ls


async def start_live_stream(sources: list, meta: dict) -> Optional[LiveStream]:
    """Try each source (copy first, re-encode second) until one starts producing data."""
    await _ensure_server()
    for src in sources[:4]:
        for mode in ("copy", "transcode"):
            try:
                ls = await _spawn(src, meta, mode)
            except RuntimeError:
                raise
            except Exception as e:
                log.warning("spawn failed (%s/%s): %r", src.provider, mode, e)
                ls = None
            if ls:
                log.info("streaming via %s (%s) → %s", src.provider, mode, ls.url)
                return ls
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#   RICH UI  —  same engine as play.py / start.py
# ═══════════════════════════════════════════════════════════════════════════════

def _remoji(emoji_id: int, style: int, fallback: str = "⭐") -> str:
    if style == 0:
        return f"<emoji id={emoji_id}>{fallback}</emoji>"
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def _mention_html(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name or "User", quote=False)}</a>'


def _media_figure(url: str, caption_html: str = "", spoiler: bool = False) -> str:
    cap = f"<figcaption>{caption_html}</figcaption>" if caption_html else ""
    u = html.escape(url)
    sp = " tg-spoiler" if spoiler else ""
    if url.lower().split("?")[0].endswith((".mp4", ".mov", ".webm", ".gif")):
        return f'<figure><video src="{u}"{sp}></video>{cap}</figure>'
    return f'<figure><img src="{u}"{sp}/>{cap}</figure>'


def _info_aside(style: int, duration, mention: str = None) -> str:
    e = _remoji(EMOJI_ANALYSING, style, "📊")
    out = (
        f"{e} <b><u><i>ᴅᴜʀᧁᴛιση</i></u></b>: "
        f"<i>{html.escape(str(duration or '00:00'), quote=False)}</i>"
    )
    if mention:
        out += f"<br>{e} <b><u><i>ʀєǫᴜєsᴛєᴅ</i></u></b>: <i>{mention}</i>"
    return f"<aside>{out}</aside>"


def rich_buttons(rows) -> str:
    out = ""
    for row in rows:
        out += "<tg-button-row>"
        for text, kind, value, style in row:
            st = f' style="{style}"' if style else ""
            label = html.escape(text, quote=False)
            if kind == "url":
                out += f'<tg-button type="url" url="{html.escape(value)}"{st}>{label}</tg-button>'
            elif kind == "copy":
                out += f'<tg-button type="copy_text" text="{html.escape(value)}"{st}>{label}</tg-button>'
            else:
                out += f'<tg-button type="callback_data" data="{html.escape(value)}"{st}>{label}</tg-button>'
        out += "</tg-button-row>"
    return out


def classic_keyboard(rows) -> InlineKeyboardMarkup:
    kb = []
    for row in rows:
        line = []
        for text, kind, value, style in row:
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
                    btn = InlineKeyboardButton(text, style=getattr(ButtonStyle, style.upper()), **kwargs)
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


def _fit_cb(prefix: str, query: str, suffix: str) -> str:
    """callback_data must be <= 64 BYTES; trim the query to fit."""
    q = (query or "")[:20]
    room = 64 - len((prefix + suffix).encode("utf-8"))
    while q and len(q.encode("utf-8")) > room:
        q = q[:-1]
    return f"{prefix}{q}{suffix}"


async def send_rich(client, chat_id: int, builders, reply_to: int = None):
    global RICH_EMOJI_STYLE
    if InputRichMessage is None or not hasattr(client, "send_rich_message"):
        return None

    variants = []
    if reply_to:
        if ReplyParameters is not None:
            variants.append(dict(reply_parameters=ReplyParameters(message_id=reply_to)))
        variants.append(dict(reply_to_message_id=reply_to))
    variants.append({})

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
                    log.warning("send_rich_message failed (style=%s): %r", style, e)
                    continue
    log.warning("rich message failed, using classic fallback: %r", last_err)
    return None


# ─── picker rows ────────────────────────────────────────────────────────────────

def season_rows(sess: AnimeSession):
    """Season buttons, 2 per row, primary style, + danger cancel."""
    rows = []
    row = []
    for s in sess.seasons:
        label = f"S{s.index} • {s.episodes}ep" if s.episodes else f"S{s.index}"
        row.append((label, "callback", f"ANISEASON|{sess.user_id}|{s.index}", "primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([(LBL_CANCEL, "callback", f"ANICLOSE|{sess.user_id}", "danger")])
    return rows


def episode_rows(sess: AnimeSession, season: Season, page: int = 0):
    """Episode buttons for the chosen season, 2 per row, primary style."""
    total = season.episodes or 24
    start = page * EP_PAGE_SIZE + 1
    end = min(start + EP_PAGE_SIZE - 1, total)

    rows = []
    row = []
    for ep in range(start, end + 1):
        row.append((f"EP {ep}", "callback", f"ANIEP|{sess.user_id}|{season.index}|{ep}", "primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # pagination row
    nav = []
    if page > 0:
        nav.append(("◁ Prev", "callback", f"ANIEPPAGE|{sess.user_id}|{season.index}|{page-1}", None))
    if end < total:
        nav.append(("Next ▷", "callback", f"ANIEPPAGE|{sess.user_id}|{season.index}|{page+1}", None))
    if nav:
        rows.append(nav)

    # back to seasons (danger)
    rows.append([(LBL_BACK, "callback", f"ANIBACK|{sess.user_id}", "danger")])
    return rows


# ─── picker HTML builders ───────────────────────────────────────────────────────

def _season_card_html(style: int, sess: AnimeSession) -> str:
    cap = html.escape(sess.anime_title, quote=False)
    fig = _media_figure(sess.anime_cover, cap, spoiler=True)
    e = _remoji(EMOJI_ANALYSING, style, "📊")
    info = (
        f"{e} <b><u><i>sєʟєᴄᴛ sєʌsση</i></u></b>: "
        f"<i>{len(sess.seasons)} sєʌsση(s) ғσυηᴅ</i>"
    )
    return (
        f"<blockquote>{fig}</blockquote>"
        f"<aside>{info}</aside>"
        f"{rich_buttons(season_rows(sess))}"
    )


def _episode_card_html(style: int, sess: AnimeSession, season: Season, page: int) -> str:
    cap = html.escape(f"{sess.anime_title} — S{season.index}", quote=False)
    fig = _media_figure(season.cover or sess.anime_cover, cap, spoiler=True)
    e = _remoji(EMOJI_ANALYSING, style, "📊")
    total = season.episodes or "?"
    info = (
        f"{e} <b><u><i>sєʌsση</i></u></b>: <i>S{season.index}</i><br>"
        f"{e} <b><u><i>єριsσᴅєs</i></u></b>: <i>{total}</i>"
    )
    if season.score:
        info += f"<br>{e} <b><u><i>sᴄσʀє</i></u></b>: <i>{season.score / 10:.1f}/10</i>"
    if season.year:
        info += f"<br>{e} <b><u><i>ʏєʌʀ</i></u></b>: <i>{season.year}</i>"
    return (
        f"<blockquote>{fig}</blockquote>"
        f"<aside>{info}</aside>"
        f"{rich_buttons(episode_rows(sess, season, page))}"
    )


async def _send_picker(sess: AnimeSession, kind: str, season: Season = None, page: int = 0):
    """Send or replace the picker card. kind: 'seasons' | 'episodes'."""
    chat_id = sess.original_chat_id

    if kind == "seasons":
        builders = [
            (lambda s: _season_card_html(s, sess), None),
        ]
        classic_text = (
            f"🎬 <b>{html.escape(sess.anime_title)}</b>\n\n"
            f"Select a season:"
        )
        classic_markup = classic_keyboard(season_rows(sess))
    else:
        builders = [
            (lambda s: _episode_card_html(s, sess, season, page), None),
        ]
        classic_text = (
            f"🎬 <b>{html.escape(sess.anime_title)}</b> — S{season.index}\n\n"
            f"Select an episode:"
        )
        classic_markup = classic_keyboard(episode_rows(sess, season, page))

    # try to edit the existing picker message, else send a new one
    if sess.msg is not None:
        try:
            sent = await send_rich(app, chat_id, builders)
            if sent is not None:
                try:
                    await sess.msg.delete()
                except Exception:
                    pass
                sess.msg = sent
                return sent
        except Exception:
            pass
        # classic edit fallback
        try:
            await sess.msg.edit_text(classic_text, reply_markup=classic_markup)
            return sess.msg
        except Exception:
            pass

    sent = await send_rich(app, chat_id, builders)
    if sent is None:
        sent = await app.send_message(chat_id, classic_text, reply_markup=classic_markup)
    sess.msg = sent
    return sent


# ═══════════════════════════════════════════════════════════════════════════════
#   NOW-PLAYING CARD  (same look as /play, poster in spoiler, no VC-less button)
# ═══════════════════════════════════════════════════════════════════════════════

def anime_stream_rows(chat_id):
    """Same callbacks as the music player, minus the VC-less button."""
    return [
        [
            (LBL_RESUME, "callback", f"ADMIN Resume|{chat_id}", "primary"),
            (LBL_PAUSE, "callback", f"ADMIN Pause|{chat_id}", "primary"),
            (LBL_STOP, "callback", f"ADMIN Stop|{chat_id}", "primary"),
        ],
        [(LBL_CLOSE, "callback", "close", "danger")],
    ]


def now_playing_html(style, poster, caption_title, duration, mention, chat_id) -> str:
    cap = html.escape(caption_title, quote=False)
    fig = _media_figure(poster, cap, spoiler=True) if poster else cap
    return (
        f"<blockquote>{fig}</blockquote>"
        f"{_info_aside(style, duration, mention)}"
        f"{rich_buttons(anime_stream_rows(chat_id))}"
    )


async def send_anime_now_playing(
    original_chat_id, chat_id, poster, caption_title, duration, user_name, user_id
):
    builders = [
        (lambda s: now_playing_html(s, poster, caption_title, duration, _mention_html(user_id, user_name), chat_id), None),
        (lambda s: now_playing_html(s, None, caption_title, duration, _mention_html(user_id, user_name), chat_id), None),
    ]
    msg = await send_rich(app, original_chat_id, builders)
    if msg is not None:
        _freeze_markup(msg)
        return msg, "rich"

    # classic fallback (photo with spoiler)
    classic_cap = (
        f"<b>{html.escape(caption_title)}</b>\n\n"
        f"<b>ᴅᴜʀᧁᴛιση</b>: <i>{html.escape(str(duration))}</i>\n"
        f"<b>ʀєǫᴜєsᴛєᴅ</b>: {_mention_html(user_id, user_name)}"
    )
    msg = await app.send_photo(
        original_chat_id,
        photo=poster or FALLBACK_POSTER,
        has_spoiler=True,
        caption=classic_cap,
        reply_markup=classic_keyboard(anime_stream_rows(chat_id)),
    )
    return msg, "tg"


async def _noop(*args, **kwargs):
    return None


def _freeze_markup(msg):
    """The live timer-bar updater keeps editing reply markup on db[chat]['mystic'];
    rich buttons live inside the message, so make those edits no-ops."""
    for name in ("edit_reply_markup", "edit_message_reply_markup", "edit_caption"):
        try:
            setattr(msg, name, _noop)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#   MUTE / UNMUTE WATCHER  (assistant restricted in the chat)
# ═══════════════════════════════════════════════════════════════════════════════

async def _assistant_is_muted(chat_id: int) -> bool:
    """True when the assistant account is restricted (can't send media) in chat_id."""
    try:
        me = await app.get_me()
        member = await app.get_chat_member(chat_id, me.id)
        # Kicked = obviously not able to stream
        if member.status == ChatMemberStatus.KICKED:
            return True
        # Restricted member: check the send-media permission
        if member.status == ChatMemberStatus.RESTRICTED:
            perms = getattr(member, "permissions", None) or member
            can_send = getattr(perms, "can_send_messages", None)
            can_media = getattr(perms, "can_send_media_messages", None)
            can_other = getattr(perms, "can_send_other_messages", None)
            # if any of the send permissions is explicitly False -> treated as muted
            if can_send is False or can_media is False or can_other is False:
                return True
            return False
        # Left / banned from the chat entirely
        if member.status in (ChatMemberStatus.LEFT,):
            return True
        return False
    except Exception:
        # if we can't even read the member, assume not muted (don't block playback)
        return False


async def watch_assistant_mute(chat_id: int, original_chat_id: int):
    """
    Poll every 5s while this anime plays. When the assistant gets muted/restricted,
    pause the stream and tell the chat; when unmuted, resume. Stops when the queue
    for this chat is gone (song stopped / skipped).
    """
    was_muted = False
    notice = None
    try:
        while True:
            await asyncio.sleep(5)
            # stop watching when nothing is playing in this chat anymore
            if not db.get(chat_id):
                break

            muted = await _assistant_is_muted(chat_id)
            if muted and not was_muted:
                was_muted = True
                try:
                    await SHUKLA.pause_stream(chat_id)
                except Exception:
                    pass
                notice = await app.send_message(
                    original_chat_id,
                    "🔇 <b>Assistant is muted in this chat.</b>\n"
                    "Stream paused — please <b>unmute</b> the assistant to resume.",
                )
            elif not muted and was_muted:
                was_muted = False
                try:
                    await SHUKLA.resume_stream(chat_id)
                except Exception:
                    pass
                try:
                    if notice is not None:
                        await notice.edit_text(
                            "🔊 <b>Assistant unmuted.</b>\nResuming the stream…"
                        )
                except Exception:
                    pass
                notice = None
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning("mute watcher died: %r", e)


# ═══════════════════════════════════════════════════════════════════════════════
#   STREAMING  (reuses your existing queue + call engine, streamtype="index")
# ═══════════════════════════════════════════════════════════════════════════════

async def stream_anime_episode(
    sess: AnimeSession,
    season: Season,
    ep: int,
    stream_url: str,
    ep_duration: str,
    gogo_title: str,
    caption_override: str = None,
):
    """Queue or start the episode exactly like stream.py does for index links."""
    chat_id = sess.chat_id
    original_chat_id = sess.original_chat_id
    user_name = sess.user_name
    user_id = sess.user_id
    forceplay = sess.forceplay
    video = sess.video

    caption_title = caption_override or f"S{season.index} - EP{ep} - {gogo_title or sess.anime_title}"

    if await is_active_chat(chat_id):
        await put_queue_index(
            chat_id,
            original_chat_id,
            "index_url",
            caption_title,
            ep_duration,
            user_name,
            stream_url,
            "video" if video else "audio",
        )
        position = len(db.get(chat_id)) - 1
        await app.send_message(
            original_chat_id,
            f"🎬 <b>{html.escape(caption_title)}</b>\n"
            f"Queued at position <b>#{position}</b>",
        )
    else:
        if not forceplay:
            db[chat_id] = []
        await SHUKLA.join_call(
            chat_id, original_chat_id, stream_url,
            video=True if video else None,
            image=season.cover or sess.anime_cover,
        )
        await put_queue_index(
            chat_id,
            original_chat_id,
            "index_url",
            caption_title,
            ep_duration,
            user_name,
            stream_url,
            "video" if video else "audio",
            forceplay=forceplay,
        )
        run, mk = await send_anime_now_playing(
            original_chat_id, chat_id,
            season.cover or sess.anime_cover,
            caption_title, ep_duration, user_name, user_id,
        )
        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = mk

        # start the mute/unmute watcher for this playback
        asyncio.create_task(watch_assistant_mute(chat_id, original_chat_id))


async def _play_direct_url(message: Message, url: str, video: bool, forceplay: bool, title_override: str = None):
    """/aplay <any link yt-dlp or ffmpeg can read> — skips AniList, streams it the same way."""
    chat_id = message.chat.id
    mystic = await message.reply_text("⏳ Reading the link…")
    src = await resolve_direct(url)
    if not src:
        return await mystic.edit_text("❌ Couldn't extract a stream from that link (token expired?).")
    title = title_override or src.title or "Custom stream"
    try:
        live = await start_live_stream([src], {"title": title, "show": title, "season": 1, "episode": 1})
    except RuntimeError as e:
        return await mystic.edit_text(f"❌ {html.escape(str(e))}")
    if live is None:
        return await mystic.edit_text("❌ ffmpeg couldn't open that stream.")

    sess = AnimeSession(message.from_user.id, message.from_user.first_name, chat_id, chat_id, video, forceplay)
    sess.anime_title, sess.anime_cover = title, FALLBACK_POSTER
    season = Season(1, 0, title, 1, FALLBACK_POSTER, None, "", None, [], "")
    try:
        await mystic.delete()
    except Exception:
        pass
    try:
        await stream_anime_episode(sess, season, 1, live.url, _fmt_duration(live.duration), title,
                                   caption_override=title)
    except AssistantErr as e:
        await app.send_message(chat_id, str(e))
    except Exception as e:
        log.exception("direct stream failed")
        await app.send_message(chat_id, f"❌ Failed to start the stream: `{type(e).__name__}`")



# ═══════════════════════════════════════════════════════════════════════════════
#   /APLAY COMMAND
# ═══════════════════════════════════════════════════════════════════════════════

@app.on_message(
    filters.command(["aplay", "vaplay"], prefixes=["/", "!", "%", ",", "", ".", "@", "#"])
    & filters.group
    & filters.user(OWNER_ID)
)
async def aplay_command(client, message: Message):
    # light version: normal group chat only (channel play can be wired later via PlayWrapper)
    chat_id = message.chat.id
    original_chat_id = message.chat.id
    video = message.command[0].startswith("v")
    forceplay = "force" in message.command[0]

    if len(message.command) < 2:
        return await message.reply_text(
            "**Usage:**\n`/aplay <anime name>`\n\nExample: `/aplay One Piece`"
        )

    query = message.text.split(None, 1)[1].strip()
    user_id = message.from_user.id
    user_name = message.from_user.first_name

    if re.match(r"^https?://\S+$", query):
        return await _play_direct_url(message, query, video, forceplay)

    mystic = await message.reply_text(f"🔎 Searching for anime: **{query}**…")

    result = await search_anime_with_seasons(query)
    if not result:
        return await mystic.edit_text(f"❌ No anime found for: `{query}`")

    anime_title, anime_cover, seasons = result
    if not seasons:
        return await mystic.edit_text(f"❌ Couldn't load seasons for: `{anime_title}`")

    # build the session (RAM only)
    sess = AnimeSession(user_id, user_name, chat_id, original_chat_id, video, forceplay)
    sess.seasons = seasons
    sess.anime_title = anime_title
    sess.anime_cover = anime_cover
    _SESSIONS[sess.key()] = sess

    await mystic.edit_text(f"✅ Anime found: **{anime_title}**")
    await _send_picker(sess, "seasons")


@app.on_message(
    filters.command(["testaplay", "vtestaplay"], prefixes=["/", "!", "%", ",", ".", "@", "#"])
    & filters.group
    & filters.user(OWNER_ID)
)
async def testaplay_command(client, message: Message):
    """Owner-only: plays the hard-coded TEST_URL in the voice chat (video)."""
    await _play_direct_url(message, TEST_URL, True, False, title_override=TEST_TITLE)


# ═══════════════════════════════════════════════════════════════════════════════
#   CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_sess(query) -> Optional[AnimeSession]:
    try:
        parts = query.data.split("|")
        uid = int(parts[1])
    except Exception:
        return None
    key = f"{query.message.chat.id}:{uid}"
    sess = _SESSIONS.get(key)
    if query.from_user.id != OWNER_ID:
        return None
    if sess is None or sess.user_id != query.from_user.id:
        return None
    return sess


async def _reject(query):
    try:
        await query.answer("This menu isn't for you.", show_alert=True)
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^ANISEASON\|") & ~BANNED_USERS)
async def ani_season_cb(client, query):
    sess = _get_sess(query)
    if sess is None:
        return await _reject(query)
    try:
        season_index = int(query.data.split("|")[2])
    except Exception:
        return await _reject(query)
    season = next((s for s in sess.seasons if s.index == season_index), None)
    if season is None:
        return await _reject(query)
    try:
        await query.answer(f"Season {season_index}")
    except Exception:
        pass
    await _send_picker(sess, "episodes", season=season, page=0)


@app.on_callback_query(filters.regex(r"^ANIEPPAGE\|") & ~BANNED_USERS)
async def ani_ep_page_cb(client, query):
    sess = _get_sess(query)
    if sess is None:
        return await _reject(query)
    try:
        _, uid, sidx, page = query.data.split("|")
        season_index, page = int(sidx), int(page)
    except Exception:
        return await _reject(query)
    season = next((s for s in sess.seasons if s.index == season_index), None)
    if season is None:
        return await _reject(query)
    try:
        await query.answer(f"Page {page + 1}")
    except Exception:
        pass
    await _send_picker(sess, "episodes", season=season, page=page)


@app.on_callback_query(filters.regex(r"^ANIBACK\|") & ~BANNED_USERS)
async def ani_back_cb(client, query):
    sess = _get_sess(query)
    if sess is None:
        return await _reject(query)
    try:
        await query.answer("Back to seasons")
    except Exception:
        pass
    await _send_picker(sess, "seasons")


@app.on_callback_query(filters.regex(r"^ANICLOSE\|") & ~BANNED_USERS)
async def ani_close_cb(client, query):
    sess = _get_sess(query)
    if sess is None:
        return await _reject(query)
    _SESSIONS.pop(sess.key(), None)
    try:
        await query.answer("Cancelled")
        await query.message.delete()
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^ANIEP\|") & ~BANNED_USERS)
async def ani_episode_cb(client, query):
    sess = _get_sess(query)
    if sess is None:
        return await _reject(query)
    try:
        _, uid, sidx, ep = query.data.split("|")
        season_index, ep = int(sidx), int(ep)
    except Exception:
        return await _reject(query)
    season = next((s for s in sess.seasons if s.index == season_index), None)
    if season is None:
        return await _reject(query)

    # consume the session — one shot
    _SESSIONS.pop(sess.key(), None)
    try:
        await query.answer(f"Loading EP{ep}…")
    except Exception:
        pass
    try:
        await query.message.delete()
    except Exception:
        pass

    mystic = await app.send_message(
        sess.original_chat_id,
        f"⏳ <b>{html.escape(sess.anime_title)}</b> S{season.index} EP{ep}\nFetching stream link…",
    )

    # 1) resolve sources → 2) ffmpeg downloads + a local server streams the same file
    meta = {
        "title": f"{sess.anime_title} - S{season.index:02d}E{ep:02d}",
        "show": sess.anime_title,
        "season": season.index,
        "episode": ep,
        "year": season.year,
    }
    live = None
    tried = []
    try:
        async for pname, srcs in iter_sources(sess, season, ep):
            tried.append(pname)
            try:
                await mystic.edit_text(
                    f"⏳ <b>{html.escape(sess.anime_title)}</b> S{season.index} EP{ep}\n"
                    f"Buffering from <b>{html.escape(pname)}</b>…"
                )
            except Exception:
                pass
            live = await start_live_stream(srcs, meta)
            if live:
                break
    except RuntimeError as e:          # e.g. ffmpeg missing
        return await mystic.edit_text(f"❌ {html.escape(str(e))}")
    except Exception:
        log.exception("source resolving crashed")

    if live is None:
        return await mystic.edit_text(
            f"❌ No working stream for EP{ep}.\n"
            f"Tried: {', '.join(tried) or 'no provider returned a link'}\n"
            f"Try another episode/season (details are in the bot log)."
        )

    stream_url = live.url
    gogo_title = season.title
    if live.duration:
        ep_duration = _fmt_duration(live.duration)
    elif season.duration_min:
        ep_duration = _fmt_duration(season.duration_min * 60)
    else:
        ep_duration = "24:00"

    try:
        await mystic.delete()
    except Exception:
        pass

    try:
        await stream_anime_episode(sess, season, ep, stream_url, ep_duration, gogo_title)
    except AssistantErr as e:
        return await app.send_message(sess.original_chat_id, str(e))
    except Exception as e:
        log.exception("anime stream failed")
        return await app.send_message(
            sess.original_chat_id,
            f"❌ Failed to start the episode: `{type(e).__name__}`",
        )
