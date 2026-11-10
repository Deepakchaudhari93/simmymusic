import asyncio
import os
import re
import json
import shutil
import tempfile
from typing import Union
import requests
import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from py_yt import VideosSearch
from SHUKLAMUSIC.utils.database import is_on_off
from SHUKLAMUSIC.utils.formatters import time_to_seconds
import os
import glob
import random
import logging
import aiohttp
import config
from config import API_URL, VIDEO_API_URL, API_KEY


# ─── COOKIES ──────────────────────────────────────────────────────────────────
# Main cookie jar: SHUKLAMUSIC/cookies.txt  (this file sits in SHUKLAMUSIC/platforms/)
_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIES_PATH = os.path.join(_PKG_DIR, "cookies.txt")

log = logging.getLogger("youtube")


def cookie_txt_file():
    """Path of the cookie jar, or None. Prefers SHUKLAMUSIC/cookies.txt, then the
    old <cwd>/cookies/*.txt folder so nothing that already worked stops working."""
    if os.path.isfile(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0:
        return COOKIES_PATH
    cookie_dir = f"{os.getcwd()}/cookies"
    if not os.path.exists(cookie_dir):
        return None
    cookies_files = [f for f in os.listdir(cookie_dir) if f.endswith(".txt")]
    if not cookies_files:
        return None
    return os.path.join(cookie_dir, random.choice(cookies_files))


# ─── YT-DLP ENGINE (same approach as playeon) ─────────────────────────────────
# Audio: pick the best format the player can decode (AAC first, never Dolby/5.1
# which plays as silence), ranked by bitrate.
_DECODABLE = "[audio_channels<=2][acodec!^=ec-3][acodec!^=ac-3]"
AUDIO_FORMAT = f"ba{_DECODABLE}/ba[acodec!^=ec-3][acodec!^=ac-3]/ba/b"
AUDIO_SORT = "acodec:aac,abr,asr,ext:m4a"
VIDEO_FORMAT = "best[height<=?720][width<=?1280]/best[height<=?720]/best"

# Errors a fresh yt-dlp process usually clears (YouTube IP throttle / network blips)
_RETRIABLE = (
    "sign in to confirm", "not a bot", "http error 429", "http error 5",
    "unable to download webpage", "unable to download api page",
    "unable to download player", "failed to extract", "unable to extract",
    "timed out", "connection reset", "connection aborted",
    "temporary failure in name resolution", "requested format is not available",
    "no video formats found",
)
YTDLP_ATTEMPTS = min(max(int(os.getenv("YTDLP_ATTEMPTS", "3") or 3), 1), 6)
YTDLP_TIMEOUT = int(os.getenv("YTDLP_TIMEOUT", "60") or 60)
# Cache the full file in the background while the stream is already playing
BG_CACHE = os.getenv("YT_BG_CACHE", "1") != "0"
_bg_running = set()


def _js_runtime_args():
    rt = os.getenv("YTDLP_JS_RUNTIME")
    if not rt:
        for name in ("deno", "node", "bun"):
            if shutil.which(name):
                rt = name
                break
    args = ["--js-runtimes", rt] if rt else []
    clients = os.getenv("YTDLP_PLAYER_CLIENTS")
    if clients:
        args += ["--extractor-args", f"youtube:player_client={clients}"]
    return args


class YtdlpError(Exception):
    pass


def _clean_err(stderr: str) -> str:
    lines = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    err = next((l for l in reversed(lines) if l.startswith("ERROR:")), lines[0] if lines else "")
    err = err.replace("ERROR:", "", 1).strip()
    return err[:200]


async def _ytdlp(args, timeout=YTDLP_TIMEOUT):
    """Run yt-dlp with a throw-away copy of the cookie jar (yt-dlp rewrites it),
    retrying on throttle/network errors. Returns stdout. Raises YtdlpError."""
    last = "yt-dlp failed"
    for attempt in range(1, YTDLP_ATTEMPTS + 1):
        src = cookie_txt_file()
        tmp = None
        cmd = ["yt-dlp", "--no-warnings", "--no-playlist", *_js_runtime_args()]
        if src:
            fd, tmp = tempfile.mkstemp(prefix="cookies-", suffix=".txt")
            os.close(fd)
            try:
                shutil.copyfile(src, tmp)
                cmd += ["--cookies", tmp]
            except Exception as e:
                log.warning("cookie copy failed: %r", e)
                tmp = None
        cmd += list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                last = f"yt-dlp timed out after {timeout}s"
                out, err = b"", b""
            else:
                text = out.decode(errors="ignore")
                if text.strip():
                    return text
                last = _clean_err(err.decode(errors="ignore")) or "yt-dlp produced no output"
        except FileNotFoundError:
            raise YtdlpError("yt-dlp not installed")
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        if attempt == YTDLP_ATTEMPTS or not any(s in last.lower() for s in _RETRIABLE):
            break
        await asyncio.sleep(min(1.5 * attempt, 5))
    raise YtdlpError(last)


async def ytdlp_direct_url(link: str, video: bool = False) -> str:
    """Resolve a direct googlevideo URL. ffmpeg/py-tgcalls then streams it live -
    playback starts right away and the data is fetched while it plays."""
    if video:
        args = ["-f", VIDEO_FORMAT, "-g", link]
    else:
        args = ["-f", AUDIO_FORMAT, "-S", AUDIO_SORT, "-g", link]
    out = await _ytdlp(args)
    url = next((l.strip() for l in out.splitlines() if l.strip().startswith("http")), None)
    if not url:
        raise YtdlpError("could not resolve a direct url")
    return url


async def _bg_cache_audio(link: str, vidid: str):
    """Parallel (aria2c / concurrent fragments) download into downloads/<id>.<ext>
    while the track is already streaming. Written to a temp name then renamed, so a
    half-written file is never picked up as 'cached'."""
    if not BG_CACHE or vidid in _bg_running:
        return
    for ext in ("m4a", "mp3", "webm"):
        if os.path.exists(f"downloads/{vidid}.{ext}"):
            return
    _bg_running.add(vidid)
    try:
        os.makedirs("downloads", exist_ok=True)
        tmpdir = tempfile.mkdtemp(prefix=f"dl-{vidid}-", dir="downloads")
        try:
            dl = (["--downloader", "aria2c", "--downloader-args", "aria2c:-x8 -s8 -k1M -c --min-split-size=1M"]
                  if shutil.which("aria2c")
                  else ["--concurrent-fragments", "8", "--http-chunk-size", "10M"])
            await _ytdlp(
                ["-f", AUDIO_FORMAT, "-S", AUDIO_SORT, *dl, "--no-part", "--fixup", "never",
                 "-o", os.path.join(tmpdir, "track.%(ext)s"), link],
                timeout=600,
            )
            for f in os.listdir(tmpdir):
                if f.startswith("track."):
                    ext = f.split(".")[-1]
                    os.replace(os.path.join(tmpdir, f), f"downloads/{vidid}.{ext}")
                    break
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        log.info("background cache failed for %s: %s", vidid, e)
    finally:
        _bg_running.discard(vidid)


def _cached_audio(vidid: str):
    for ext in ("m4a", "mp3", "webm", "opus", "mp4a"):
        p = f"downloads/{vidid}.{ext}"
        if os.path.exists(p):
            return p
    return None


# ─── DRX API  (LAST-RESORT FALLBACK, used only if yt-dlp/cookies fail) ────────
DRX_API_BASE = "https://apidrx-music.vercel.app/api"


def _drx_best_url(download_urls):
    q_map = {}
    for item in download_urls or []:
        q = str(item.get("quality", ""))
        if q.endswith("kbps"):
            try:
                q_map[int(q[:-4])] = item["url"]
            except Exception:
                pass
    for pref in (160, 320, 96, 48, 12):
        if pref in q_map:
            return q_map[pref]
    return download_urls[0]["url"] if download_urls else None


async def drx_fallback_audio(title: str):
    """Search DRX for `title`, return an audio url or None."""
    if not title:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{DRX_API_BASE}/search/songs",
                params={"query": title},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        results = (data.get("data") or {}).get("results") or []
        for song in results[:3]:
            url = _drx_best_url(song.get("downloadUrl"))
            if url:
                return url
    except Exception as e:
        log.warning("DRX fallback failed: %r", e)
    return None


async def download_song(link: str):
    video_id = link.split('v=')[-1].split('&')[0]

    download_folder = "downloads"
    for ext in ["mp3", "m4a", "webm"]:
        file_path = f"{download_folder}/{video_id}.{ext}"
        if os.path.exists(file_path):
            #print(f"File already exists: {file_path}")
            return file_path
        
    song_url = f"{API_URL}/song/{video_id}?api={API_KEY}"
    async with aiohttp.ClientSession() as session:
        for attempt in range(10):
            try:
                async with session.get(song_url) as response:
                    if response.status != 200:
                        raise Exception(f"API request failed with status code {response.status}")
                
                    data = await response.json()
                    status = data.get("status", "").lower()

                    if status == "done":
                        download_url = data.get("link")
                        if not download_url:
                            raise Exception("API response did not provide a download URL.")
                        break
                    elif status == "downloading":
                        await asyncio.sleep(4)
                    else:
                        error_msg = data.get("error") or data.get("message") or f"Unexpected status '{status}'"
                        raise Exception(f"API error: {error_msg}")
            except Exception as e:
                print(f"[FAIL] {e}")
                return None
        else:
            print("⏱️ Max retries reached. Still downloading...")
            return None
    

        try:
            file_format = data.get("format", "mp3")
            file_extension = file_format.lower()
            file_name = f"{video_id}.{file_extension}"
            download_folder = "downloads"
            os.makedirs(download_folder, exist_ok=True)
            file_path = os.path.join(download_folder, file_name)

            async with session.get(download_url) as file_response:
                with open(file_path, 'wb') as f:
                    while True:
                        chunk = await file_response.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                return file_path
        except aiohttp.ClientError as e:
            print(f"Network or client error occurred while downloading: {e}")
            return None
        except Exception as e:
            print(f"Error occurred while downloading song: {e}")
            return None
    return None

async def download_video(link: str):
    video_id = link.split('v=')[-1].split('&')[0]

    download_folder = "downloads"
    for ext in ["mp4", "webm", "mkv"]:
        file_path = f"{download_folder}/{video_id}.{ext}"
        if os.path.exists(file_path):
            return file_path
        
    video_url = f"{VIDEO_API_URL}/video/{video_id}?api={API_KEY}"
    async with aiohttp.ClientSession() as session:
        for attempt in range(10):
            try:
                async with session.get(video_url) as response:
                    if response.status != 200:
                        raise Exception(f"API request failed with status code {response.status}")
                
                    data = await response.json()
                    status = data.get("status", "").lower()

                    if status == "done":
                        download_url = data.get("link")
                        if not download_url:
                            raise Exception("API response did not provide a download URL.")
                        break
                    elif status == "downloading":
                        await asyncio.sleep(8)
                    else:
                        error_msg = data.get("error") or data.get("message") or f"Unexpected status '{status}'"
                        raise Exception(f"API error: {error_msg}")
            except Exception as e:
                print(f"[FAIL] {e}")
                return None
        else:
            print("⏱️ Max retries reached. Still downloading...")
            return None
    

        try:
            file_format = data.get("format", "mp4")
            file_extension = file_format.lower()
            file_name = f"{video_id}.{file_extension}"
            download_folder = "downloads"
            os.makedirs(download_folder, exist_ok=True)
            file_path = os.path.join(download_folder, file_name)

            async with session.get(download_url) as file_response:
                with open(file_path, 'wb') as f:
                    while True:
                        chunk = await file_response.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                return file_path
        except aiohttp.ClientError as e:
            print(f"Network or client error occurred while downloading: {e}")
            return None
        except Exception as e:
            print(f"Error occurred while downloading video: {e}")
            return None
    return None

async def check_file_size(link):
    async def get_format_info(link):
        cookie_file = cookie_txt_file()
        if not cookie_file:
            print("No cookies found. Cannot check file size.")
            return None
            
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--cookies", cookie_file,
            "-J",
            link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f'Error:\n{stderr.decode()}')
            return None
        return json.loads(stdout.decode())

    def parse_size(formats):
        total_size = 0
        for format in formats:
            if 'filesize' in format:
                total_size += format['filesize']
        return total_size

    info = await get_format_info(link)
    if info is None:
        return None
    
    formats = info.get('formats', [])
    if not formats:
        print("No formats found.")
        return None
    
    total_size = parse_size(formats)
    return total_size

async def shell_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, errorz = await proc.communicate()
    if errorz:
        if "unavailable videos are hidden" in (errorz.decode("utf-8")).lower():
            return out.decode("utf-8")
        else:
            return errorz.decode("utf-8")
    return out.decode("utf-8")


_bg_tasks = set()


def _vid_from_link(link: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([A-Za-z0-9_-]{11})", link)
    return m.group(1) if m else link.split("v=")[-1].split("&")[0]


def _fmt_dur(sec):
    if not sec:
        return "None"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def _ytdlp_search(query: str, n: int = 1):
    """yt-dlp based search - only used when py_yt fails. Returns list of dicts."""
    if re.match(r"https?://", query):
        out = await _ytdlp(["-J", query])
        info = json.loads(out)
        entries = [info.get("entries", [None])[0]] if info.get("_type") == "playlist" else [info]
    else:
        out = await _ytdlp(["-J", "--flat-playlist", f"ytsearch{n}:{query}"])
        entries = json.loads(out).get("entries") or []
    res = []
    for e in entries:
        if not e or not e.get("id"):
            continue
        res.append({
            "title": e.get("title") or "Unknown",
            "duration": _fmt_dur(e.get("duration")),
            "id": e["id"],
            "link": f"https://www.youtube.com/watch?v={e['id']}",
            "thumb": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg",
        })
    return res


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if re.search(self.regex, link):
            return True
        else:
            return False

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        text = ""
        offset = None
        length = None
        for message in messages:
            if offset:
                break
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        offset, length = entity.offset, entity.length
                        break
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        if offset in (None,):
            return None
        return text[offset : offset + length]

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            vidid = result["id"]
            if str(duration_min) == "None":
                duration_sec = 0
            else:
                duration_sec = int(time_to_seconds(duration_min))
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
        return title

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            duration = result["duration"]
        return duration

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        return thumbnail

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]

        # 1) yt-dlp + cookies (direct stream url)
        try:
            return 1, await ytdlp_direct_url(link, video=True)
        except Exception as e:
            print(f"yt-dlp video failed: {e}")

        # 2) last resort: old video API
        try:
            downloaded_file = await download_video(link)
            if downloaded_file:
                return 1, downloaded_file
        except Exception as e:
            print(f"Video API failed: {e}")
        return 0, "Could not fetch video (cookies expired or API down)."

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        if videoid:
            link = self.listbase + link
        if "&" in link:
            link = link.split("&")[0]
        
        cookie_file = cookie_txt_file()
        if not cookie_file:
            return []
            
        playlist = await shell_cmd(
            f"yt-dlp -i --get-id --flat-playlist --cookies {cookie_file} --playlist-end {limit} --skip-download {link}"
        )
        try:
            result = playlist.split("\n")
            for key in result:
                if key == "":
                    result.remove(key)
        except:
            result = []
        return result

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            results = VideosSearch(link, limit=1)
            for result in (await results.next())["result"]:
                title = result["title"]
                duration_min = result["duration"]
                vidid = result["id"]
                yturl = result["link"]
                thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            track_details = {
                "title": title,
                "link": yturl,
                "vidid": vidid,
                "duration_min": duration_min,
                "thumb": thumbnail,
            }
            return track_details, vidid
        except Exception as e:
            log.warning("py_yt search failed (%r), trying yt-dlp search", e)
        r = (await _ytdlp_search(link, 1))[0]
        duration = r["duration"] if r["duration"] != "None" else None
        return {
            "title": r["title"],
            "link": r["link"],
            "vidid": r["id"],
            "duration_min": duration,
            "thumb": r["thumb"],
        }, r["id"]

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        
        cookie_file = cookie_txt_file()
        if not cookie_file:
            return [], link
            
        ytdl_opts = {"quiet": True, "cookiefile" : cookie_file}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r["formats"]:
                try:
                    str(format["format"])
                except:
                    continue
                if not "dash" in str(format["format"]).lower():
                    try:
                        format["format"]
                        format["filesize"]
                        format["format_id"]
                        format["ext"]
                        format["format_note"]
                    except:
                        continue
                    formats_available.append(
                        {
                            "format": format["format"],
                            "filesize": format["filesize"],
                            "format_id": format["format_id"],
                            "ext": format["ext"],
                            "format_note": format["format_note"],
                            "yturl": link,
                        }
                    )
        return formats_available, link

    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: Union[bool, str] = None,
    ):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            a = VideosSearch(link, limit=10)
            result = (await a.next()).get("result")
            title = result[query_type]["title"]
            duration_min = result[query_type]["duration"]
            vidid = result[query_type]["id"]
            thumbnail = result[query_type]["thumbnails"][0]["url"].split("?")[0]
            return title, duration_min, thumbnail, vidid
        except Exception as e:
            log.warning("py_yt slider failed (%r), trying yt-dlp search", e)
        res = await _ytdlp_search(link, 10)
        r = res[query_type]
        return r["title"], r["duration"], r["thumb"], r["id"]

    async def download(
        self,
        link: str,
        mystic,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> str:
        if videoid:
            link = self.base + link
        loop = asyncio.get_running_loop()
        def audio_dl():
            cookie_file = cookie_txt_file()
            if not cookie_file:
                raise Exception("No cookies found. Cannot download audio.")
                
            ydl_optssx = {
                "format": "bestaudio/best",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "cookiefile" : cookie_file,
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def video_dl():
            cookie_file = cookie_txt_file()
            if not cookie_file:
                raise Exception("No cookies found. Cannot download video.")
                
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "cookiefile" : cookie_file,
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def song_video_dl():
            cookie_file = cookie_txt_file()
            if not cookie_file:
                raise Exception("No cookies found. Cannot download song video.")
                
            formats = f"{format_id}+140"
            fpath = f"downloads/{title}"
            ydl_optssx = {
                "format": formats,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile" : cookie_file,
                "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        def song_audio_dl():
            cookie_file = cookie_txt_file()
            if not cookie_file:
                raise Exception("No cookies found. Cannot download song audio.")
                
            fpath = f"downloads/{title}.%(ext)s"
            ydl_optssx = {
                "format": format_id,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile" : cookie_file,
                "prefer_ffmpeg": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        if songvideo:
            await download_song(link)
            fpath = f"downloads/{link}.mp3"
            return fpath
        elif songaudio:
            await download_song(link)
            fpath = f"downloads/{link}.mp3"
            return fpath
        elif video:
            # 1) yt-dlp + cookies -> direct url, ffmpeg streams it while playing
            try:
                return await ytdlp_direct_url(link, video=True), True
            except Exception as e:
                print(f"yt-dlp video failed: {e}")
            # 2) last resort: old video API
            try:
                downloaded_file = await download_video(link)
                if downloaded_file:
                    return downloaded_file, True
            except Exception as e:
                print(f"Video API failed: {e}")
            raise Exception("video: all sources failed")
        else:
            vidid = _vid_from_link(link)
            # already cached from an earlier play -> instant
            cached = _cached_audio(vidid)
            if cached:
                return cached, True
            # 1) yt-dlp + cookies -> direct url. Playback starts immediately and
            #    ffmpeg pulls the data while it plays; a parallel download caches
            #    the full file in the background.
            try:
                url = await ytdlp_direct_url(link)
                t = asyncio.create_task(_bg_cache_audio(link, vidid))
                _bg_tasks.add(t)
                t.add_done_callback(_bg_tasks.discard)
                return url, True
            except Exception as e:
                print(f"yt-dlp audio failed: {e}")
            # 2) LAST RESORT: DRX API (cookies expired / yt-dlp blocked)
            try:
                title = await self.title(link)
            except Exception:
                title = None
            drx_url = await drx_fallback_audio(title)
            if drx_url:
                print("Using DRX fallback for", title)
                return drx_url, True
            # 3) very last: old song API
            downloaded_file = await download_song(link)
            if downloaded_file:
                return downloaded_file, True
            raise Exception("audio: all sources failed")
