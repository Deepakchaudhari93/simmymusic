import os
import asyncio
from typing import Tuple
import yt_dlp
from pytube import YouTube as PyTube
import config

# Ensure YTDL cache dir exists
os.makedirs(config.YTDL_CACHE_DIR, exist_ok=True)


class YouTubeHandler:
    def __init__(self):
        self.ydl_opts = getattr(config, "YTDL_OPTS", {}).copy()
        # ensure some sane defaults
        self.ydl_opts.setdefault("format", "bestaudio/best")
        self.ydl_opts.setdefault("noplaylist", True)
        self.ydl_opts.setdefault("quiet", True)
        self.ydl_opts.setdefault("skip_download", True)
        self.ydl_opts.setdefault("cachedir", config.YTDL_CACHE_DIR)

    async def _extract_info(self, url: str, download: bool = False) -> dict:
        loop = asyncio.get_event_loop()
        opts = self.ydl_opts.copy()
        opts["skip_download"] = not download

        def sync_extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=download)

        return await loop.run_in_executor(None, sync_extract)

    async def _pytube_stream_url(self, url: str, audio_only: bool = True) -> str:
        loop = asyncio.get_event_loop()

        def sync_get():
            yt = PyTube(url)
            if audio_only:
                stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
            else:
                stream = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc().first()
            return stream.url if stream else None

        return await loop.run_in_executor(None, sync_get)

    async def video(self, videoid: str, live: bool = False) -> Tuple[int, str]:
        """
        Returns (status, url)
        status: 1 = ok, 0 = fail
        url: direct streamable url (or m3u8) suitable for ffmpeg/AudioPiped
        """
        if not videoid:
            return 0, None
        if not videoid.startswith("http"):
            url = f"https://www.youtube.com/watch?v={videoid}"
        else:
            url = videoid

        # First try yt-dlp
        try:
            info = await self._extract_info(url, download=False)
            if info:
                formats = info.get("formats") or []
                # prefer audio-only formats
                audio_formats = [f for f in formats if f.get("acodec") and not f.get("vcodec")]
                if audio_formats:
                    best = sorted(audio_formats, key=lambda f: f.get("abr") or 0, reverse=True)[0]
                    return 1, best.get("url")
                if formats:
                    # fallback to any format
                    best = formats[-1]
                    return 1, best.get("url") or info.get("url")
                return 1, info.get("url")
        except Exception:
            pass

        # Fallback to pytube
        try:
            purl = await self._pytube_stream_url(url, audio_only=True)
            if purl:
                return 1, purl
        except Exception:
            pass

        return 0, None

    async def download(self, videoid: str, progress_message=None, videoid_flag: bool = True, video: bool = False) -> Tuple[str, bool]:
        """
        Returns (file_path_or_direct_url, direct_flag)
        If direct_flag is True the caller should treat the returned string as a direct-stream URL.
        """
        if not videoid:
            return None, False
        if not videoid.startswith("http"):
            url = f"https://www.youtube.com/watch?v={videoid}"
        else:
            url = videoid

        # Try to return a direct URL first
        status, direct = await self.video(videoid, live=False)
        if status == 1 and direct:
            return direct, True

        # Else download into cache
        outtmpl = os.path.join(config.YTDL_CACHE_DIR, "%(id)s.%(ext)s")
        opts = getattr(config, "YTDL_OPTS", {}).copy()
        opts.update({
            "format": "bestaudio/best" if not video else "best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
        })

        loop = asyncio.get_event_loop()

        def sync_download():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                return filename

        try:
            filename = await loop.run_in_executor(None, sync_download)
            return filename, False
        except Exception:
            # Last-ditch pytube download
            try:
                def sync_pytube_download():
                    yt = PyTube(url)
                    if video:
                        stream = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc().first()
                    else:
                        stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
                    out = stream.download(output_path=config.YTDL_CACHE_DIR)
                    return out

                outpath = await loop.run_in_executor(None, sync_pytube_download)
                return outpath, False
            except Exception:
                return None, False


# Module-level instance named YouTube so existing imports work
YouTube = YouTubeHandler()
