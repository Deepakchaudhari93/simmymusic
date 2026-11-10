from typing import Union
from SHUKLAMUSIC import app
from SHUKLAMUSIC.utils.formatters import time_to_seconds
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ButtonStyle

def queue_markup(
    _,
    DURATION,
    CPLAY,
    videoid,
    played: Union[bool, int] = None,
    dur: Union[bool, int] = None,
):
    not_dur = [
        [
            InlineKeyboardButton(
                text=_["QU_B_1"],
                callback_data=f"GetQueued {CPLAY}|{videoid}",
            ),
            InlineKeyboardButton(
                text=_["CLOSE_BUTTON"],
                callback_data="close",
                style=ButtonStyle.DANGER, 
                icon_custom_emoji_id=5408832111773757273,
            ),
        ]
    ]
    dur = [
        [
            InlineKeyboardButton(
                text=_["QU_B_2"].format(played, dur),
                callback_data="GetTimer",
            )
        ],
        [
            InlineKeyboardButton(
                text=_["QU_B_1"],
                callback_data=f"GetQueued {CPLAY}|{videoid}",
            ),
            InlineKeyboardButton(
                text=_["CLOSE_BUTTON"],
                callback_data="close",
                style=ButtonStyle.DANGER, 
                icon_custom_emoji_id=5408832111773757273,
            ),
        ],
    ]
    upl = InlineKeyboardMarkup(not_dur if DURATION == "Unknown" else dur)
    return upl


def queue_back_markup(_, CPLAY):
    upl = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=_["BACK_BUTTON"],
                    callback_data=f"queue_back_timer {CPLAY}",
                ),
                InlineKeyboardButton(
                    text=_["CLOSE_BUTTON"],
                    callback_data="close",
                    style=ButtonStyle.DANGER, 
                    icon_custom_emoji_id=5408832111773757273,
                ),
            ]
        ]
    )
    return upl


def aq_markup(_, chat_id):
    buttons = [
        [
            InlineKeyboardButton(text="", callback_data=f"ADMIN Resume|{chat_id}", icon_custom_emoji_id=5409222721869459068),
            InlineKeyboardButton(text="", callback_data=f"ADMIN Pause|{chat_id}", icon_custom_emoji_id=5409042015415448331),
            InlineKeyboardButton(text="", callback_data=f"ADMIN Stop|{chat_id}", icon_custom_emoji_id=5408832111773757273),
        ],
        [InlineKeyboardButton(text=" ᴄʟᴏsᴇ ▣", callback_data="close", style=ButtonStyle.DANGER, icon_custom_emoji_id=5408832111773757273)],
    ]
    return buttons
