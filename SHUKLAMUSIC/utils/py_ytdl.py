import asyncio
import yt_dlp

async def search_tracks(query: str, limit: int = 5):
    """Search YouTube (yt-dlp) and return list of simplified song dicts similar to DRX API.

    Each result dict has keys:
      - name
      - duration (seconds)
      - image: list of dicts with 'quality' and 'url'
      - id (video id)
      - downloadUrl: list of {quality, url} entries (audio formats)
      - url: webpage url
    """
    loop = asyncio.get_event_loop()

    def sync_search():
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            # increase retries or user-agent here if needed
        }
        query_str = f"ytsearch{limit}:{query}"
        results = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query_str, download=False)
            entries = info.get("entries") or []
            for e in entries:
                title = e.get("title")
                duration = e.get("duration") or 0
                thumb = e.get("thumbnail")
                vid = e.get("id")
                webpage = e.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
                formats = e.get("formats") or []
                audio_formats = [f for f in formats if f.get("acodec") and not f.get("vcodec")]
                download_urls = []
                for f in audio_formats:
                    abr = f.get("abr") or f.get("tbr") or 0
                    quality = f"{int(abr)}kbps" if abr else "unknown"
                    url = f.get("url")
                    if url:
                        download_urls.append({"quality": quality, "url": url})
                image_list = []
                if thumb:
                    image_list.append({"quality": "500x500", "url": thumb})
                results.append({
                    "name": title,
                    "duration": duration,
                    "image": image_list,
                    "id": vid,
                    "downloadUrl": download_urls,
                    "url": webpage,
                })
        return results

    try:
        return await loop.run_in_executor(None, sync_search)
    except Exception:
        return []
