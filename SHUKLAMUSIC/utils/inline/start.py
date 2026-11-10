from pyrogram.types import InlineKeyboardButton

import config
from pyrogram.enums import ButtonStyle
from SHUKLAMUSIC import app

def start_panel(_):
    buttons = [
        [
            InlineKeyboardButton(
                "𝖠𝖽𝖽 𝖬𝖾 𝗂𝗇 𝗒𝗈𝗎𝗋 𝖦𝗋𝗈𝗎𝗉", 
                url=f"https://t.me/{app.username}?startgroup=true", 
                icon_custom_emoji_id=5197162757488678999
            )
        ],
        [
            InlineKeyboardButton(
                "𝖬𝗎𝗌𝗂𝖼 𝖶𝖾𝖻𝖠𝗉𝗉 (VC-less)", 
                url="http://t.me/TidalXMusicBot/tidaltunes",icon_custom_emoji_id=6023674787778271309, style=ButtonStyle.PRIMARY,
        
            ),
        ],
        [
            InlineKeyboardButton(
                "˹ᴘσʟιᴄʏ˼",
                url="https://telegra.ph/Privacy-Policy-08-03-101",
                icon_custom_emoji_id=5409025823388741707,
                style=ButtonStyle.PRIMARY
            ),
            InlineKeyboardButton(
                "˹ᴛιᴅᴧʟ ᴛᴜηєs˼♪",
                url="http://t.me/TidalXMusicBot/tidaltunes",
                icon_custom_emoji_id=5409194306365829029,
                style=ButtonStyle.PRIMARY
            ),
        ],
        
    ]
    return buttons


def private_panel(_):
    buttons = [
        [
            InlineKeyboardButton(
                "𝖠𝖽𝖽 𝖬𝖾 𝗂𝗇 𝗒𝗈𝗎𝗋 𝖦𝗋𝗈𝗎𝗉",
                url=f"https://t.me/{app.username}?startgroup=true",
                icon_custom_emoji_id=5197162757488678999
            )
        ],
        
        [
            InlineKeyboardButton(
                "𝖬𝗎𝗌𝗂𝖼 𝖶𝖾𝖻𝖠𝗉𝗉 (VC-Less)",
                url="http://t.me/TidalXMusicBot/tidaltunes",
                icon_custom_emoji_id=6023674787778271309,
                style=ButtonStyle.SUCCESS
            )
        ],
        [
            InlineKeyboardButton(
                "˹𝖲𝗎𝗉𝗉𝗈𝗋𝗍",
                url="http://t.me/subdict",
                icon_custom_emoji_id=5942877472163892475,
                style=ButtonStyle.PRIMARY

            ),
            InlineKeyboardButton(
                "𝖴𝗉𝖽𝖺𝗍𝖾𝗌",
                url="http://t.me/hmstalker",
                icon_custom_emoji_id=5922612721244704425,
                style=ButtonStyle.PRIMARY
            ),
        
        ],
        [
        
            InlineKeyboardButton(
                "Re𝗉𝗈𝗋𝗍 𝖨𝗌𝗌𝗎𝖾𝗌",
                url=config.SUPPORT_CHANNEL,
                icon_custom_emoji_id=5775887550262546277,
                style=ButtonStyle.DANGER
            ),
            InlineKeyboardButton(
                "𝖲𝗎𝗀𝗀𝖾𝗌𝗍 ",
                url=config.SUPPORT_CHAT,
                icon_custom_emoji_id=5891169510483823323,
                style=ButtonStyle.DANGER
            ),
        ],
        [
    
            InlineKeyboardButton(
                "𝖧𝖾𝗅𝗉 𝖺𝗇𝖽 𝖼𝗈𝗆𝗆𝖺𝗇𝖽𝗌",
                callback_data="settings_back_helper",
                icon_custom_emoji_id=5877597667231534929
            ),
        ],
        
    ]
    return buttons
    
    
