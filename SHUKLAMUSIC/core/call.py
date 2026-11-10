# -----------------------------------------------
# 🔸 StrangerMusic Project
# 🔹 Developed & Maintained by: Shashank Shukla (https://github.com/itzshukla)
# 📅 Copyright © 2022 – All Rights Reserved
#
# 📖 License:
# This source code is open for educational and non-commercial use ONLY.
# You are required to retain this credit in all copies or substantial portions of this file.
# Commercial use, redistribution, or removal of this notice is strictly prohibited
# without prior written permission from the author.
#
# ❤️ Made with dedication and love by ItzShukla
# -----------------------------------------------
import asyncio
import os
import re
from datetime import datetime, timedelta
from typing import Union

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, LinkPreviewOptions
from pytgcalls import PyTgCalls, filters
from pytgcalls.exceptions import NoActiveGroupCall
from pytgcalls.types import (
    AudioQuality,
    ChatUpdate,
    MediaStream,
    StreamEnded,
    VideoQuality,
)

# These exceptions were removed/renamed in py-tgcalls 2.x, so fall back safely
try:
    from pytgcalls.exceptions import TelegramServerError
except ImportError:
    class TelegramServerError(Exception):
        pass

try:
    from pytgcalls.exceptions import AlreadyJoinedError
except ImportError:
    class AlreadyJoinedError(Exception):
        pass

import config
from SHUKLAMUSIC import LOGGER, YouTube, app
from SHUKLAMUSIC.misc import db
from SHUKLAMUSIC.core.mongo import mongodb
from SHUKLAMUSIC.utils.database import (
    add_active_chat,
    add_active_video_chat,
    get_lang,
    get_loop,
    group_assistant,
    is_active_chat,
    is_autoend,
    music_on,
    remove_active_chat,
    remove_active_video_chat,
    set_loop,
)
from SHUKLAMUSIC.utils.exceptions import AssistantErr
from SHUKLAMUSIC.utils.formatters import check_duration, seconds_to_min, speed_converter
from SHUKLAMUSIC.utils.inline.play import stream_markup
from SHUKLAMUSIC.utils.stream.autoclear import auto_clean
from SHUKLAMUSIC.utils.thumbnails import get_thumb
from strings import get_string

autoend = {}
counter = {}

# --- CUSTOM CAPTION LOGIC ---
captiondb = mongodb.stream_captions


async def get_stored_caption():
    """Fetches the custom caption from MongoDB."""
    data = await captiondb.find_one({"chat_id": "GLOBAL_CAPTION"})
    if data and "text" in data:
        return data["text"]
    return None


async def get_caption(_, link, title, duration, user):
    """Generates the final caption string, formatted with arguments."""
    custom_html = await get_stored_caption()
    if custom_html:
        try:
            return custom_html.format(link, title, duration, user)
        except Exception:
            pass
    return _["stream_1"].format(link, title, duration, user)


async def _clear_(chat_id):
    db[chat_id] = []
    await remove_active_video_chat(chat_id)
    await remove_active_chat(chat_id)


def make_stream(path, video=False, ffmpeg_params=None):
    """Builds a py-tgcalls 2.x MediaStream (replaces AudioPiped/AudioVideoPiped)."""
    kwargs = {}
    if ffmpeg_params:
        kwargs["ffmpeg_parameters"] = ffmpeg_params
    if video:
        return MediaStream(
            path,
            audio_parameters=AudioQuality.HIGH,
            video_parameters=VideoQuality.SD_480p,
            **kwargs,
        )
    return MediaStream(
        path,
        audio_parameters=AudioQuality.HIGH,
        video_flags=MediaStream.Flags.IGNORE,
        **kwargs,
    )


# ─── SECURITY: speed value whitelist ──────────────────────────────────────────
_ALLOWED_SPEEDS = {"0.5", "0.75", "1.0", "1.5", "2.0"}
_SPEED_RE = re.compile(r"^\d+(\.\d+)?$")


def _safe_speed(speed) -> str:
    s = str(speed).strip()
    if s not in _ALLOWED_SPEEDS:
        raise AssistantErr("Umm")
    return s


class Call:
    def __init__(self):
        self.userbot1 = Client(
            name="SHUKLAAss1",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING1),
        )
        self.one = PyTgCalls(self.userbot1)
        self.userbot2 = Client(
            name="SHUKLAAss2",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING2),
        )
        self.two = PyTgCalls(self.userbot2)
        self.userbot3 = Client(
            name="SHUKLAAss3",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING3),
        )
        self.three = PyTgCalls(self.userbot3)
        self.userbot4 = Client(
            name="SHUKLAAss4",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING4),
        )
        self.four = PyTgCalls(self.userbot4)
        self.userbot5 = Client(
            name="SHUKLAAss5",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING5),
        )
        self.five = PyTgCalls(self.userbot5)
        # chat_id -> asyncio.Task of a silent "pre-join" that is still running / just finished
        self._prejoin = {}

    # ─── PRE-RESOLVE (resolve assistant early so join_call is faster) ───────
    async def _prejoin_run(self, chat_id: int):
        """Resolve which assistant handles this chat and warm the connection.
        py-tgcalls 2.x requires a MediaStream to actually join — we can't join
        silently without one. So we just pre-resolve the assistant here (a fast
        DB/cache call) so join_call() doesn't have to do it under time pressure."""
        try:
            await group_assistant(self, chat_id)
            return True
        except Exception as e:
            LOGGER(__name__).warning(f"prejoin resolve failed in {chat_id}: {e!r}")
            return False

    async def start_prejoin(self, chat_id: int):
        """Fire-and-forget: pre-resolve the assistant for this chat.
        Skipped when the chat is already active (assistant already in call)."""
        try:
            if chat_id in self._prejoin:
                return
            if await is_active_chat(chat_id):
                return
            if chat_id in self._prejoin:  # race guard
                return
            self._prejoin[chat_id] = asyncio.create_task(self._prejoin_run(chat_id))
        except Exception as e:
            LOGGER(__name__).warning(f"start_prejoin error in {chat_id}: {e!r}")

    async def finish_prejoin(self, chat_id: int):
        """Wait until a pending pre-join is done (so play() can't race with it)."""
        task = self._prejoin.get(chat_id)
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=25)
        except Exception:
            pass
        finally:
            if self._prejoin.get(chat_id) is task:
                self._prejoin.pop(chat_id, None)

    async def cancel_prejoin(self, chat_id: int):
        """Cancel a pending pre-resolve task. No leave_call needed since we
        no longer actually join in _prejoin_run."""
        if chat_id not in self._prejoin:
            return
        task = self._prejoin.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass

    async def pause_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        await assistant.pause(chat_id)

    async def resume_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        await assistant.resume(chat_id)

    async def stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        try:
            await _clear_(chat_id)
            await assistant.leave_call(chat_id)
        except:
            pass

    async def stop_stream_force(self, chat_id: int):
        try:
            if config.STRING1:
                await self.one.leave_call(chat_id)
        except:
            pass
        try:
            if config.STRING2:
                await self.two.leave_call(chat_id)
        except:
            pass
        try:
            if config.STRING3:
                await self.three.leave_call(chat_id)
        except:
            pass
        try:
            if config.STRING4:
                await self.four.leave_call(chat_id)
        except:
            pass
        try:
            if config.STRING5:
                await self.five.leave_call(chat_id)
        except:
            pass
        try:
            await _clear_(chat_id)
        except:
            pass

    async def speedup_stream(self, chat_id: int, file_path, speed, playing):
        """Speed up / slow down a stream.

        SECURITY: the old code used asyncio.create_subprocess_shell with the raw
        file_path concatenated into a shell string — any query / URL containing
        ; | $() etc. executed on the server. We now:
          1. whitelist `speed` to a fixed set,
          2. pass file_path as a *separate argument* to create_subprocess_exec
             (no shell, no word splitting, no command injection).
        """
        speed = _safe_speed(speed)  # raises AssistantErr on anything unexpected
        assistant = await group_assistant(self, chat_id)

        if str(speed) != "1.0":
            if str(file_path).startswith("http"):
                import hashlib
                base = hashlib.md5(str(file_path).encode()).hexdigest()[:16] + ".mka"
            else:
                base = os.path.basename(file_path)
            chatdir = os.path.join(os.getcwd(), "playback", str(speed))
            if not os.path.isdir(chatdir):
                os.makedirs(chatdir)
            out = os.path.join(chatdir, base)
            if not os.path.isfile(out):
                vs_map = {
                    "0.5": 2.0,
                    "0.75": 1.35,
                    "1.5": 0.68,
                    "2.0": 0.5,
                }
                vs = vs_map.get(speed, 1.0)
                # ── SECURITY: create_subprocess_exec (list args, no shell) ──
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg",
                    "-i", str(file_path),
                    "-filter:v", f"setpts={vs}*PTS",
                    "-filter:a", f"atempo={speed}",
                    str(out),
                    stdin=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
        else:
            out = file_path

        dur = await asyncio.get_event_loop().run_in_executor(None, check_duration, out)
        dur = int(dur)
        played, con_seconds = speed_converter(playing[0]["played"], speed)
        duration = seconds_to_min(dur)
        stream = make_stream(
            out,
            video=playing[0]["streamtype"] == "video",
            ffmpeg_params=f"-ss {played} -to {duration}",
        )
        if str(db[chat_id][0]["file"]) == str(file_path):
            await assistant.play(chat_id, stream)
        else:
            raise AssistantErr("Umm")
        if str(db[chat_id][0]["file"]) == str(file_path):
            exis = (playing[0]).get("old_dur")
            if not exis:
                db[chat_id][0]["old_dur"] = db[chat_id][0]["dur"]
                db[chat_id][0]["old_second"] = db[chat_id][0]["seconds"]
            db[chat_id][0]["played"] = con_seconds
            db[chat_id][0]["dur"] = duration
            db[chat_id][0]["seconds"] = dur
            db[chat_id][0]["speed_path"] = out
            db[chat_id][0]["speed"] = speed

    async def force_stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        # nothing was playing but the assistant has just pre-joined for this request:
        # keep it in the call, join_call() will only swap the stream
        keep_joined = False
        try:
            keep_joined = chat_id in self._prejoin and not await is_active_chat(chat_id)
        except Exception:
            pass
        try:
            check = db.get(chat_id)
            check.pop(0)
        except:
            pass
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        if keep_joined:
            await self.finish_prejoin(chat_id)
            return
        try:
            await assistant.leave_call(chat_id)
        except:
            pass

    async def skip_stream(
        self,
        chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        stream = make_stream(link, video=bool(video))
        await assistant.play(chat_id, stream)

    async def seek_stream(self, chat_id, file_path, to_seek, duration, mode):
        assistant = await group_assistant(self, chat_id)
        stream = make_stream(
            file_path,
            video=(mode == "video"),
            ffmpeg_params=f"-ss {to_seek} -to {duration}",
        )
        await assistant.play(chat_id, stream)

    async def stream_call(self, link):
        assistant = await group_assistant(self, config.LOGGER_ID)
        await assistant.play(config.LOGGER_ID, make_stream(link, video=True))
        await asyncio.sleep(0.2)
        await assistant.leave_call(config.LOGGER_ID)

    async def join_call(
        self,
        chat_id: int,
        original_chat_id: int,
        link,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        language = await get_lang(chat_id)
        _ = get_string(language)
        # finish any pending pre-resolve before we play
        await self.finish_prejoin(chat_id)
        stream = make_stream(link, video=bool(video))
        try:
            await assistant.play(chat_id, stream)
        except NoActiveGroupCall:
            raise AssistantErr(_["call_8"])
        except AlreadyJoinedError:
            # already in call (e.g. leftover from a previous session) — swap stream
            try:
                await assistant.play(chat_id, stream)
            except Exception:
                raise AssistantErr(_["call_9"])
        except TelegramServerError:
            raise AssistantErr(_["call_10"])
        # Mark active AFTER play succeeds so state never goes dirty on error
        await add_active_chat(chat_id)
        await music_on(chat_id)
        if video:
            await add_active_video_chat(chat_id)
        if await is_autoend():
            counter[chat_id] = {}
            try:
                users = len(await assistant.get_participants(chat_id))
            except Exception:
                users = 2  # assume occupied; don't start autoend timer
            if users == 1:
                autoend[chat_id] = datetime.now() + timedelta(minutes=1)

    async def change_stream(self, client, chat_id):
        check = db.get(chat_id)
        popped = None
        loop = await get_loop(chat_id)
        try:
            if loop == 0:
                popped = check.pop(0)
            else:
                loop = loop - 1
                await set_loop(chat_id, loop)
            await auto_clean(popped)
            if not check:
                await _clear_(chat_id)
                try:
                    await client.leave_call(chat_id)
                except Exception:
                    pass
                return
        except Exception:
            await _clear_(chat_id)
            try:
                await client.leave_call(chat_id)
            except Exception:
                pass
            return
        else:
            queued = check[0]["file"]
            language = await get_lang(chat_id)
            _ = get_string(language)
            title = (check[0]["title"]).title()
            user = check[0]["by"]
            original_chat_id = check[0]["chat_id"]
            streamtype = check[0]["streamtype"]
            videoid = check[0]["vidid"]
            db[chat_id][0]["played"] = 0
            exis = (check[0]).get("old_dur")
            if exis:
                db[chat_id][0]["dur"] = exis
                db[chat_id][0]["seconds"] = check[0]["old_second"]
                db[chat_id][0]["speed_path"] = None
                db[chat_id][0]["speed"] = 1.0
            video = True if str(streamtype) == "video" else False

            # Link preview settings for "above text" logic
            preview_options = LinkPreviewOptions(is_disabled=False, show_above_text=True)

            if "live_" in queued:
                n, link = await YouTube.video(videoid, True)
                if n == 0:
                    return await app.send_message(
                        original_chat_id,
                        text=_["call_6"],
                    )
                stream = make_stream(link, video=video)
                try:
                    await client.play(chat_id, stream)
                except Exception:
                    return await app.send_message(
                        original_chat_id,
                        text=_["call_6"],
                    )

                button = stream_markup(_, chat_id)
                vid_link = f"https://t.me/{app.username}?start=info_{videoid}"
                cap = await get_caption(_, vid_link, title[:23], check[0]["dur"], user)

                run = await app.send_message(
                    chat_id=original_chat_id,
                    text=cap,
                    link_preview_options=preview_options,
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"
            elif "vid_" in queued:
                mystic = await app.send_message(original_chat_id, _["call_7"])
                try:
                    file_path, direct = await YouTube.download(
                        videoid,
                        mystic,
                        videoid=True,
                        video=True if str(streamtype) == "video" else False,
                    )
                except:
                    return await mystic.edit_text(
                        _["call_6"], link_preview_options=LinkPreviewOptions(is_disabled=True)
                    )
                stream = make_stream(file_path, video=video)
                try:
                    await client.play(chat_id, stream)
                except:
                    return await app.send_message(
                        original_chat_id,
                        text=_["call_6"],
                    )

                button = stream_markup(_, chat_id)
                await mystic.delete()
                vid_link = f"https://t.me/{app.username}?start=info_{videoid}"
                cap = await get_caption(_, vid_link, title[:23], check[0]["dur"], user)

                run = await app.send_message(
                    chat_id=original_chat_id,
                    text=cap,
                    link_preview_options=preview_options,
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"
            elif "index_" in queued:
                stream = make_stream(videoid, video=(str(streamtype) == "video"))
                try:
                    await client.play(chat_id, stream)
                except:
                    return await app.send_message(
                        original_chat_id,
                        text=_["call_6"],
                    )
                button = stream_markup(_, chat_id)
                run = await app.send_message(
                    chat_id=original_chat_id,
                    text=_["stream_2"].format(user),
                    link_preview_options=preview_options,
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"
            else:
                stream = make_stream(queued, video=video)
                try:
                    await client.play(chat_id, stream)
                except:
                    return await app.send_message(
                        original_chat_id,
                        text=_["call_6"],
                    )
                if videoid == "telegram":
                    button = stream_markup(_, chat_id)
                    cap = await get_caption(_, config.SUPPORT_CHAT, title[:23], check[0]["dur"], user)

                    run = await app.send_message(
                        chat_id=original_chat_id,
                        text=cap,
                        link_preview_options=preview_options,
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "tg"
                elif videoid == "soundcloud":
                    button = stream_markup(_, chat_id)
                    cap = await get_caption(_, config.SUPPORT_CHAT, title[:23], check[0]["dur"], user)

                    run = await app.send_message(
                        chat_id=original_chat_id,
                        text=cap,
                        link_preview_options=preview_options,
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "tg"
                else:
                    button = stream_markup(_, chat_id)
                    vid_link = f"https://t.me/{app.username}?start=info_{videoid}"
                    cap = await get_caption(_, vid_link, title[:23], check[0]["dur"], user)

                    run = await app.send_message(
                        chat_id=original_chat_id,
                        text=cap,
                        link_preview_options=preview_options,
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "stream"

    async def ping(self):
        pings = []
        if config.STRING1:
            pings.append(self.one.ping)
        if config.STRING2:
            pings.append(self.two.ping)
        if config.STRING3:
            pings.append(self.three.ping)
        if config.STRING4:
            pings.append(self.four.ping)
        if config.STRING5:
            pings.append(self.five.ping)
        return str(round(sum(pings) / len(pings), 3))

    async def start(self):
        LOGGER(__name__).info("Starting PyTgCalls Client...\n")
        if config.STRING1:
            await self.one.start()
        if config.STRING2:
            await self.two.start()
        if config.STRING3:
            await self.three.start()
        if config.STRING4:
            await self.four.start()
        if config.STRING5:
            await self.five.start()

    async def decorators(self):
        async def stream_services_handler(client, update: ChatUpdate):
            await self.stop_stream(update.chat_id)

        async def stream_end_handler(client, update: StreamEnded):
            if not isinstance(update, StreamEnded):
                return
            if update.stream_type != StreamEnded.Type.AUDIO:
                return
            await self.change_stream(client, update.chat_id)

        chat_filter = filters.chat_update(
            ChatUpdate.Status.KICKED
            | ChatUpdate.Status.LEFT_GROUP
            | ChatUpdate.Status.CLOSED_VOICE_CHAT
        )

        # some py-tgcalls 2.x versions expose stream_end as a class that must be
        # instantiated, others as a ready filter object - handle both
        end_filter = filters.stream_end
        if isinstance(end_filter, type):
            end_filter = end_filter()

        for assistant in (self.one, self.two, self.three, self.four, self.five):
            assistant.on_update(chat_filter)(stream_services_handler)
            assistant.on_update(end_filter)(stream_end_handler)


SHUKLA = Call()