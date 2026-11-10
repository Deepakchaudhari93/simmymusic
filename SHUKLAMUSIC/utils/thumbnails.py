# Thumbnail generator optimized for speed and JioSaavn custom templates
import os
import re
import random
import logging
import asyncio
import aiohttp
import aiofiles
from io import BytesIO
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from py_yt import VideosSearch

logging.basicConfig(level=logging.INFO)

JIOSAAVN_API = "https://jiosavan-lilac.vercel.app"
CACHE_DIR = "cache"

# Ensure cache directory exists
os.makedirs(CACHE_DIR, exist_ok=True)


def changeImageSize(maxWidth, maxHeight, image):
    widthRatio = maxWidth / image.size[0]
    heightRatio = maxHeight / image.size[1]
    newWidth = int(widthRatio * image.size[0])
    newHeight = int(heightRatio * image.size[1])
    return image.resize((newWidth, newHeight), Image.Resampling.LANCZOS)


def truncate(text, max_length=30):
    words = text.split(" ")
    text1, text2 = "", ""
    for word in words:
        if len(text1) + len(word) < max_length:
            text1 += " " + word
        elif len(text2) + len(word) < max_length:
            text2 += " " + word
    return [text1.strip(), text2.strip()]


def random_color():
    return (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))


def draw_text_with_shadow(background, draw, position, text, font, fill, shadow_offset=(2, 2), shadow_blur=3):
    shadow = Image.new('RGBA', background.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.text(position, text, font=font, fill=(0, 0, 0, 200))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
    background.paste(shadow, shadow_offset, shadow)
    draw.text(position, text, font=font, fill=fill)


async def fetch_image_to_ram(url: str) -> Image.Image:
    """Fetches an image directly into RAM using BytesIO for speed."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                image_data = await resp.read()
                return Image.open(BytesIO(image_data)).convert("RGBA")
            return Image.new("RGBA", (500, 500), "black")


async def search_jiosaavn_song(query: str):
    """Searches for a song on JioSaavn API and returns structured data."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{JIOSAAVN_API}/api/search/songs?query={query}"
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                
                if not data.get("success") or not data.get("data", {}).get("results"):
                    return None
                
                song = data["data"]["results"][0]
                
                # Extract Data
                song_id = song.get("id", "")
                title = song.get("name", "Unknown Title")
                duration = int(song.get("duration", 0))
                
                # Artist
                artists = song.get("artists", {}).get("primary", [])
                artist_name = artists[0].get("name", "Unknown Artist") if artists else "Unknown Artist"
                
                # Image
                images = song.get("image", [])
                image_url = images[-1].get("url") if images else None
                
                # Audio URL (Highest Priority)
                download_urls = song.get("downloadUrl", [])
                audio_url = None
                for quality in ["320kbps", "160kbps", "96kbps", "48kbps", "12kbps"]:
                    for dl in download_urls:
                        if dl.get("quality") == quality:
                            audio_url = dl.get("url")
                            break
                    if audio_url:
                        break
                
                if not audio_url and download_urls:
                    audio_url = download_urls[-1].get("url")
                
                if not audio_url:
                    return None
                
                # Duration Formatting (-M:SS)
                duration_formatted = "-0:00"
                if duration:
                    minutes = duration // 60
                    seconds = duration % 60
                    duration_formatted = f"-{minutes}:{seconds:02d}"
                
                return {
                    "id": song_id,
                    "title": title.replace("&quot;", '"').replace("&#039;", "'"),
                    "artist": artist_name,
                    "duration_sec": duration,
                    "duration_str": duration_formatted,
                    "image_url": image_url,
                    "audio_url": audio_url
                }
    except Exception as e:
        logging.error(f"JioSaavn Search Error: {e}")
        return None


async def generate_jiosaavn_thumbnail(song_data: dict, output_path: str):
    """Generates the premium JioSaavn thumbnail using RAM processing."""
    try:
        template_path = "SHUKLAMUSIC/assets/template.png"
        
        # 1. Fetch raw artwork
        raw_art = await fetch_image_to_ram(song_data["image_url"])
        
        # 2. Create Background (Blurred & Darkened)
        bg = raw_art.resize((1280, 720), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(15))
        dark_overlay = Image.new("RGBA", (1280, 720), (0, 0, 0, 160))
        bg = Image.alpha_composite(bg, dark_overlay)

        # 3. Apply Template overlay
        if os.path.exists(template_path):
            template = Image.open(template_path).convert("RGBA")
            bg = Image.alpha_composite(bg, template)
        else:
            logging.warning("Template not found! Generating without template.")

        # 4. Process Song Image (Rounded Corners)
        thumb_size = (480, 480) # Adjust if needed
        thumb_pos = (90, 120)   # Red area coordinates
        
        art_resized = raw_art.resize(thumb_size, Image.Resampling.LANCZOS)
        mask = Image.new("L", thumb_size, 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.rounded_rectangle((0, 0, thumb_size[0], thumb_size[1]), radius=35, fill=255)
        art_resized.putalpha(mask)
        bg.paste(art_resized, thumb_pos, art_resized)

        # 5. Text Operations
        draw = ImageDraw.Draw(bg)
        
        # Load Fonts
        try:
            font_title = ImageFont.truetype("SHUKLAMUSIC/assets/GoogleSans-Bold.ttf", 45)
            font_artist = ImageFont.truetype("SHUKLAMUSIC/assets/GoogleSans-Medium.ttf", 32)
            font_dur = ImageFont.truetype("SHUKLAMUSIC/assets/GoogleSans-Regular.ttf", 26)
        except OSError:
            logging.warning("Google Sans fonts not found, falling back to default.")
            font_title = ImageFont.load_default()
            font_artist = ImageFont.load_default()
            font_dur = ImageFont.load_default()

        # Format Title (Max 4 words)
        words = song_data["title"].split()
        display_title = " ".join(words[:4]) + ("..." if len(words) > 4 else "")

        # Draw Text
        draw_text_with_shadow(bg, draw, (620, 170), display_title, font_title, (255, 255, 255)) # Green Area
        draw_text_with_shadow(bg, draw, (620, 240), song_data["artist"], font_artist, (180, 180, 180)) # Blue Area
        draw_text_with_shadow(bg, draw, (1100, 310), song_data["duration_str"], font_dur, (180, 180, 180)) # Purple Area

        # 6. Save final output
        bg.convert("RGB").save(output_path, "PNG", optimize=True)
        return output_path

    except Exception as e:
        logging.error(f"Thumbnail Generation Error: {e}")
        return song_data.get("image_url") # Fallback to raw image URL


async def get_jiosaavn_thumb(query: str):
    """Wrapper to search and generate thumbnail."""
    song_data = await search_jiosaavn_song(query)
    if not song_data:
        return None, None
    
    cache_path = f"{CACHE_DIR}/js_{song_data['id']}.png"
    if os.path.exists(cache_path):
        return cache_path, song_data
        
    thumb_path = await generate_jiosaavn_thumbnail(song_data, cache_path)
    return thumb_path, song_data


# Keeps your original YouTube thumbnail generator intact
async def get_thumb(videoid: str):
    cache_path = f"cache/{videoid}_v4.png"
    if os.path.isfile(cache_path):
        return cache_path

    try:
        url = f"https://www.youtube.com/watch?v={videoid}"
        results = VideosSearch(url, limit=1)
        for result in (await results.next())["result"]:
            title = re.sub("\W+", " ", result.get("title", "Unsupported")).title()
            duration = result.get("duration", "Live")
            thumbnail = result.get("thumbnails")[0]["url"].split("?")[0] if result.get("thumbnails") else None
            views = result.get("viewCount", {}).get("short", "Unknown Views")
            channel = result.get("channel", {}).get("name", "Unknown Channel")

        if not thumbnail:
            return None

        async with aiohttp.ClientSession() as session:
            async with session.get(thumbnail) as resp:
                if resp.status == 200:
                    img_data = await resp.read()
                    youtube = Image.open(BytesIO(img_data)).convert("RGBA")
        
        # YouTube Thumb Generation Logic
        image1 = changeImageSize(1280, 720, youtube)
        background = image1.filter(filter=ImageFilter.BoxBlur(20))
        background = ImageEnhance.Brightness(background).enhance(0.6)

        grad = Image.new('RGBA', (1280, 720), random_color())
        background = Image.blend(background, grad, alpha=0.2)
        draw = ImageDraw.Draw(background)

        try:
            arial = ImageFont.truetype("SHUKLAMUSIC/assets/font2.ttf", 30)
            title_font = ImageFont.truetype("SHUKLAMUSIC/assets/font3.ttf", 45)
        except:
            arial = ImageFont.load_default()
            title_font = ImageFont.load_default()

        # Center circle
        mask = Image.new("L", (400, 400), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 400, 400), fill=255)
        circle_thumb = youtube.resize((400, 400), Image.Resampling.LANCZOS)
        circle_thumb.putalpha(mask)
        background.paste(circle_thumb, (120, 160), circle_thumb)

        title1 = truncate(title)
        draw_text_with_shadow(background, draw, (565, 180), title1[0], title_font, (255, 255, 255))
        draw_text_with_shadow(background, draw, (565, 230), title1[1], title_font, (255, 255, 255))
        draw_text_with_shadow(background, draw, (565, 320), f"{channel} | {views[:23]}", arial, (255, 255, 255))

        background.convert("RGB").save(cache_path, "PNG", optimize=True)
        return cache_path

    except Exception as e:
        logging.error(f"YouTube Thumb Error: {e}")
        return None
