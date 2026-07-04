"""
Telegram 文件管理 Bot - 主逻辑
在话题群组中管理文件，自动分类存储，支持交互式操作
"""
import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram import BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import Command, CommandStart
from aiogram.enums import ChatType
from aiogram.enums import UpdateType
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import database as db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("file-bot")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
router = Router()
fsm_router = Router()  # FSM handlers 优先级更高

# ============ DEBUG MIDDLEWARE: 记录所有收到的 update ============
class DebugAllUpdatesMiddleware(BaseMiddleware):
    """在所有 handler 之前记录每条消息的完整信息，用于排查 topic_id 不匹配等问题"""
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.chat and event.chat.id == config.GROUP_ID:
            user_id = event.from_user.id if event.from_user else "?"
            username = event.from_user.username if event.from_user else "?"
            thread_id = event.message_thread_id
            content = event.content_type
            text_preview = ""
            if event.text:
                text_preview = event.text[:80]
            elif event.caption:
                text_preview = event.caption[:80]
            logger.warning(
                "[MIDDLEWARE] GROUP_MSG | chat=%s thread_id=%s user=%s(@%s) "
                "content_type=%s msg_id=%s text=%r",
                event.chat.id, thread_id, user_id, username,
                content, event.message_id, text_preview or "(no text)",
            )
            # 额外打印文件信息
            if event.document:
                logger.warning(
                    "[MIDDLEWARE] DOCUMENT file_id=%s name=%s size=%s",
                    event.document.file_id[:20], event.document.file_name, event.document.file_size,
                )
            elif event.photo:
                logger.warning(
                    "[MIDDLEWARE] PHOTO file_id=%s sizes=%s",
                    event.photo[-1].file_id[:20], [p.file_size for p in event.photo],
                )
            elif event.video:
                logger.warning(
                    "[MIDDLEWARE] VIDEO file_id=%s name=%s size=%s",
                    event.video.file_id[:20], event.video.file_name, event.video.file_size,
                )
            elif event.audio:
                logger.warning(
                    "[MIDDLEWARE] AUDIO file_id=%s name=%s",
                    event.audio.file_id[:20], event.audio.file_name,
                )
            elif event.voice:
                logger.warning("[MIDDLEWARE] VOICE file_id=%s", event.voice.file_id[:20])
            elif event.sticker:
                logger.warning(
                    "[MIDDLEWARE] STICKER file_id=%s emoji=%s",
                    event.sticker.file_id[:20], event.sticker.emoji,
                )
            elif event.animation:
                logger.warning(
                    "[MIDDLEWARE] ANIMATION file_id=%s",
                    event.animation.file_id[:20],
                )
            elif event.video_note:
                logger.warning(
                    "[MIDDLEWARE] VIDEO_NOTE file_id=%s",
                    event.video_note.file_id[:20],
                )
        return await handler(event, data)


debug_middleware = DebugAllUpdatesMiddleware()

pending_uploads: dict[int, dict] = {}

# 搜索结果缓存（用于分页）
search_cache: dict[int, dict] = {}  # user_id -> {category, keyword, results, total, page}
SEARCH_PAGE_SIZE = 10

# ============ 话题ID诊断：记录所有已知话题 ============
KNOWN_TOPIC_IDS = {
    config.TOPIC_UPLOAD: "上传",
    config.TOPIC_VIDEO: "视频",
    config.TOPIC_IMAGE: "图片",
    config.TOPIC_APK: "APK",
    config.TOPIC_EXE: "EXE",
    config.TOPIC_ARCHIVE: "压缩包",
    config.TOPIC_DOCUMENT: "文档",
    config.TOPIC_OTHER: "其他",
    config.TOPIC_SEARCH: "搜索",
    config.TOPIC_AUDIO: "音频",
}


class TagNoteState(StatesGroup):
    waiting_for_tags = State()
    waiting_for_note = State()


def classify_by_message(message: Message) -> str:
    if message.photo:
        return "图片"
    if message.video or message.video_note:
        return "视频"
    if message.animation:
        return "图片"
    if message.sticker:
        return "其他"
    if message.voice:
        return "音频"
    if message.audio:
        return "音频"
    filename = extract_filename(message)
    ext = os.path.splitext(filename.lower())[1]
    for category, extensions in config.EXTENSION_MAP.items():
        if ext in extensions:
            return category
    return "其他"


def get_category_topic(category: str) -> int:
    return config.CATEGORY_TO_TOPIC.get(category, 0)


def extract_filename(message: Message) -> str:
    if message.document:
        return message.document.file_name or "未知文件"
    if message.video:
        return message.video.file_name or "视频文件"
    if message.audio:
        return message.audio.file_name or "音频文件"
    if message.voice:
        return "语音文件"
    if message.photo:
        return "图片文件"
    if message.video_note:
        return "视频消息"
    if message.sticker:
        return f"贴纸_{message.sticker.emoji or 'unknown'}"
    if message.animation:
        return message.animation.file_name or "GIF动画"
    return "未知文件"


def get_uploader_info(message: Message) -> str:
    if message.forward_origin:
        origin = message.forward_origin
        from aiogram.enums import MessageOriginType
        if origin.type == MessageOriginType.USER:
            user = origin.sender_user
            return f"来源: {user.full_name} (ID: {user.id})"
        elif origin.type == MessageOriginType.CHAT:
            chat = getattr(origin, "sender_chat", None)
            return f"来源群组: {chat.title if chat else "未知群组"}"
        elif origin.type == MessageOriginType.CHANNEL:
            chat = getattr(origin, "chat", None)
            return f"来源频道: {chat.title if chat else "未知频道"}"
        elif origin.type == MessageOriginType.HIDDEN_USER:
            return f"来源: {origin.sender_user_name}"
        else:
            return "来源: 未知"
    user = message.from_user
    if user:
        name = user.full_name or user.username or str(user.id)
        return f"上传者: {name} (ID: {user.id})"
    return "上传者: 未知"


def parse_search_query(query: str):
    """解析搜索查询，提取分类、ID 和关键词

    Returns:
        (category, keyword, search_id) - category 为已知分类名或 None，
        keyword 为剩余搜索词，search_id 为文件 ID 或 None
    """
    q = query.strip()

    # 支持 #3 或 id:3 按 ID 搜索（裸数字按关键词搜索）
    import re
    m = re.match(r"^#(\d+)$", q)
    if m:
        return None, None, int(m.group(1))
    m = re.match(r"^id:(\d+)$", q, re.IGNORECASE)
    if m:
        return None, None, int(m.group(1))

    parts = q.split()
    if not parts:
        return None, None, None
    category = None
    remaining = []
    for part in parts:
        if category is None and part in config.CATEGORY_TO_TOPIC:
            category = part
        else:
            remaining.append(part)
    keyword = " ".join(remaining) if remaining else None
    return category, keyword, None


# ============ 拒绝私信 ============
@router.message(F.chat.type == ChatType.PRIVATE)
async def reject_private(message: Message):
    logger.warning("[HANDLER] reject_private triggered for user=%s", message.from_user.id if message.from_user else "?")
    await message.reply("❌ 本 Bot 仅在群组话题中工作，不接受私信。")


@router.callback_query(F.chat.type == ChatType.PRIVATE)
async def reject_private_cb(callback: CallbackQuery):
    await callback.answer("本 Bot 仅在群组话题中工作", show_alert=True)


# ============ 上传流程 ============
@router.message(F.chat.id == config.GROUP_ID, F.message_thread_id == config.TOPIC_UPLOAD)
async def handle_upload(message: Message, state: FSMContext):
    # 日志：记录这条消息的详细信息（这个 handler 只有在 topic_id 匹配时才会触发）
    logger.warning(
        "[UPLOAD] MATCHED! thread_id=%s expected=%s user=%s content_type=%s",
        message.message_thread_id, config.TOPIC_UPLOAD,
        message.from_user.id if message.from_user else "?",
        message.content_type,
    )

    # 检测转发消息
    is_forwarded = bool(message.forward_origin)
    if is_forwarded:
        origin = message.forward_origin
        from aiogram.enums import MessageOriginType
        if origin.type == MessageOriginType.USER:
            logger.warning("[UPLOAD] FORWARDED from user: %s (ID: %s)",
                           origin.sender_user.full_name, origin.sender_user.id)
        elif origin.type == MessageOriginType.CHAT:
            logger.warning("[UPLOAD] FORWARDED from chat: %s", origin.sender_chat.title)
        elif origin.type == MessageOriginType.CHANNEL:
            chat_title = getattr(origin, "chat", None)
            chat_title = chat_title.title if chat_title else "未知频道"
            logger.warning("[UPLOAD] FORWARDED from channel: %s", chat_title)
        elif origin.type == MessageOriginType.HIDDEN_USER:
            logger.warning("[UPLOAD] FORWARDED from hidden user: %s", origin.sender_user_name)

    # 话题群里每条消息都自动带 reply_to_message 指向话题头消息（msg_id == thread_id）
    # 只跳过用户主动回复 Bot 消息的情况（Bot 的 reply_to_message 的 from_user.is_bot=True）
    is_reply_to_bot = (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.is_bot
    )
    logger.warning("[UPLOAD] reply_to_bot=%s has_document=%s has_photo=%s has_video=%s is_forwarded=%s",
        is_reply_to_bot, bool(message.document),
        bool(message.photo), bool(message.video), is_forwarded)

    if is_reply_to_bot:
        logger.warning("[UPLOAD] SKIPPED: reply to bot msg_id=%s", message.reply_to_message.message_id)
        return
    # 检查是否有文件内容（包括转发的消息）
    has_file = (message.document or message.video or message.audio or
                message.voice or message.photo or message.video_note or
                message.sticker or message.animation)
    # 转发消息可能同时有 forward 属性和文件属性
    if not has_file:
        logger.warning("[UPLOAD] SKIPPED: no file content found, forward_origin=%s", bool(message.forward_origin))
        sent = await message.reply("⚠️ 请发送文件，不支持纯文本消息。")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id], 20))
        return

    # 转发消息处理：确保 from_user 存在（转发者就是 from_user）
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        logger.warning("[UPLOAD] SKIPPED: no from_user on forwarded message")
        sent = await message.reply("⚠️ 无法识别发送者信息，请直接发送文件而非转发。")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id], 20))
        return

    filename = extract_filename(message)
    category = classify_by_message(message)
    categories = list(config.CATEGORY_TO_TOPIC.keys())
    logger.warning("[UPLOAD] PROCESSING file=%s category=%s thread_id=%s is_forwarded=%s",
                   filename, category, message.message_thread_id, is_forwarded)

    cat_buttons = []
    row = []
    for cat in categories:
        marker = "✅ " if cat == category else ""
        row.append(InlineKeyboardButton(text=f"{marker}{cat}", callback_data=f"set_cat:{cat}"))
        if len(row) == 2:
            cat_buttons.append(row)
            row = []
    if row:
        cat_buttons.append(row)
    cat_buttons.append([
        InlineKeyboardButton(text="🏷 设置标签/备注", callback_data="upload_settings"),
        InlineKeyboardButton(text="✅ 直接确认", callback_data="upload_confirm"),
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=cat_buttons)
    uploader_info = get_uploader_info(message)
    question = f"📁 文件: {filename}\n📂 自动分类: {category}\n👤 {uploader_info}\n\n选择分类或直接确认："

    sent = await message.reply(question, reply_markup=kb)
    pending_uploads[user_id] = {
        "user_message_id": message.message_id,
        "bot_message_id": sent.message_id,
        "category": category,
        "filename": filename,
        "uploader_info": uploader_info,
        "message": message,
    }
    asyncio.create_task(upload_timeout(user_id))


async def upload_timeout(user_id: int):
    await asyncio.sleep(config.UPLOAD_TIMEOUT)
    if user_id in pending_uploads:
        await process_upload(user_id)


async def process_upload(user_id: int):
    data = pending_uploads.pop(user_id, None)
    if not data:
        return

    message = data["message"]
    category = data["category"]
    topic_id = get_category_topic(category)

    if not topic_id:
        logger.warning(f"分类 {category} 的话题 ID 未配置，跳过")
        return

    file_id = db.next_file_id()
    filename = data["filename"]

    tags_str = data.get("tags", "")
    note_str = data.get("note", "")
    info_text = (
        f"#{file_id} {filename}\n\n"
        f"📂 分类: {category}\n"
        f"👤 {data['uploader_info']}\n"
        f"🕐 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if tags_str:
        info_text += f"\n🏷 标签: {tags_str}"
    if note_str:
        info_text += f"\n📝 备注: {note_str}"

    try:
        sent_msg = await bot.copy_message(
            chat_id=config.GROUP_ID,
            from_chat_id=config.GROUP_ID,
            message_id=message.message_id,
            message_thread_id=topic_id,
            caption=info_text,
        )
    except TelegramAPIError as e:
        logger.error(f"copyMessage 失败: {e}")
        # 通知用户失败原因
        try:
            await message.reply(f"❌ 上传失败: {e}")
            asyncio.create_task(delayed_delete(message.chat.id, [message.message_id], 20))
        except Exception:
            pass
        return

    db.add_file(
        file_id=file_id, category=category, filename=filename,
        uploader_id=message.from_user.id if message.from_user else None,
        uploader_name=message.from_user.full_name if message.from_user else None,
        forward_from=data["uploader_info"],
        tags=data.get("tags", ""), note=data.get("note", ""),
        message_id=sent_msg.message_id,
        topic_id=topic_id,
    )
    try:
        await bot.delete_message(config.GROUP_ID, data["user_message_id"])
    except TelegramBadRequest:
        pass
    try:
        await bot.delete_message(chat_id=config.GROUP_ID, message_id=data["bot_message_id"])
    except TelegramBadRequest:
        pass


# ============ 回调处理 ============
@router.callback_query(F.data.startswith("set_cat:"))
async def cb_set_category(callback: CallbackQuery):
    if callback.from_user.id not in pending_uploads:
        await callback.answer("⚠️ 此操作已过期", show_alert=True)
        return
    new_cat = callback.data.split(":", 1)[1]
    if new_cat not in config.CATEGORY_TO_TOPIC:
        await callback.answer("⚠️ 无效分类", show_alert=True)
        return
    data = pending_uploads[callback.from_user.id]
    data["category"] = new_cat
    categories = list(config.CATEGORY_TO_TOPIC.keys())
    cat_buttons = []
    row = []
    for cat in categories:
        marker = "✅ " if cat == new_cat else ""
        row.append(InlineKeyboardButton(text=f"{marker}{cat}", callback_data=f"set_cat:{cat}"))
        if len(row) == 2:
            cat_buttons.append(row)
            row = []
    if row:
        cat_buttons.append(row)
    cat_buttons.append([
        InlineKeyboardButton(text="🏷 设置标签/备注", callback_data="upload_settings"),
        InlineKeyboardButton(text="✅ 确认上传", callback_data="upload_confirm"),
    ])
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=cat_buttons))
    await callback.answer(f"📂 分类已切换为: {new_cat}")


@router.callback_query(F.data == "upload_confirm")
async def cb_upload_confirm(callback: CallbackQuery):
    if callback.from_user.id not in pending_uploads:
        await callback.answer("⚠️ 此操作已过期", show_alert=True)
        return
    await callback.answer("✅ 正在处理...")
    await process_upload(callback.from_user.id)


@router.callback_query(F.data == "upload_settings")
async def cb_upload_settings(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in pending_uploads:
        await callback.answer("⚠️ 此操作已过期", show_alert=True)
        return
    await callback.answer()
    await state.set_state(TagNoteState.waiting_for_tags)
    await callback.message.edit_text("🏷 请发送标签（多个用空格分隔）\n或发送 /skip 跳过标签设置")


@fsm_router.message(TagNoteState.waiting_for_tags, F.chat.id == config.GROUP_ID)
async def handle_tags_input(message: Message, state: FSMContext):
    logger.warning("[FSM_TAGS] handler triggered! user=%s text=%r pending_keys=%s",
        message.from_user.id if message.from_user else "?",
        message.text, list(pending_uploads.keys()))
    user_id = message.from_user.id
    if user_id not in pending_uploads:
        logger.warning("[FSM_TAGS] user %s not in pending_uploads, clearing state", user_id)
        await state.clear()
        return
    tags = ""
    if message.text and not message.text.strip().startswith("/skip"):
        tags = message.text.strip()
    pending_uploads[user_id]["tags"] = tags
    await state.set_state(TagNoteState.waiting_for_note)
    note_prompt = await message.reply("📝 请发送备注信息\n或发送 /skip 跳过备注设置")
    pending_uploads[user_id]["note_prompt_msg_id"] = note_prompt.message_id
    try:
        await bot.delete_message(config.GROUP_ID, message.message_id)
    except TelegramBadRequest:
        pass


@fsm_router.message(TagNoteState.waiting_for_note, F.chat.id == config.GROUP_ID)
async def handle_note_input(message: Message, state: FSMContext):
    logger.warning("[FSM_NOTE] handler triggered! user=%s text=%r pending_keys=%s",
        message.from_user.id if message.from_user else "?",
        message.text, list(pending_uploads.keys()))
    user_id = message.from_user.id
    if user_id not in pending_uploads:
        logger.warning("[FSM_NOTE] user %s not in pending_uploads, clearing state", user_id)
        await state.clear()
        return
    note = ""
    if message.text and not message.text.strip().startswith("/skip"):
        note = message.text.strip()
    pending_uploads[user_id]["note"] = note
    await state.clear()
    try:
        await bot.delete_message(config.GROUP_ID, message.message_id)
    except TelegramBadRequest:
        pass
    # 删除之前的提示消息
    prompt_msg_id = pending_uploads[user_id].get("note_prompt_msg_id")
    if prompt_msg_id:
        try:
            await bot.delete_message(config.GROUP_ID, prompt_msg_id)
        except TelegramBadRequest:
            pass
    data = pending_uploads[user_id]
    tags_display = data.get("tags", "") or "无"
    note_display = data.get("note", "") or "无"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ 确认上传", callback_data="upload_confirm")],
    ])
    try:
        await bot.edit_message_text(text=
            f"📋 上传信息确认\n\n📂 分类: {data['category']}\n🏷 标签: {tags_display}\n📝 备注: {note_display}\n\n确认上传？",
            chat_id=config.GROUP_ID, message_id=data["bot_message_id"], reply_markup=kb,
        )
    except TelegramBadRequest:
        pass


# ============ 管理命令 ============
def is_manage_topic(message: Message) -> bool:
    return bool(message.message_thread_id)


@router.message(F.chat.id == config.GROUP_ID, Command("info"))
async def cmd_info(message: Message):
    if not is_manage_topic(message):
        return
    args = message.text.split()
    if len(args) < 2:
        sent = await message.reply("用法: /info <文件ID>")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    try:
        file_id = int(args[1])
    except ValueError:
        sent = await message.reply("⚠️ 文件 ID 必须是数字")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    file_info = db.get_file(file_id)
    if not file_info:
        sent = await message.reply(f"❌ 未找到文件 ID: {file_id}")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    text = (
        f"📋 文件 #{file_info['id']} 信息\n\n"
        f"📁 文件名: {file_info['filename']}\n"
        f"📂 分类: {file_info['category']}\n"
        f"👤 {file_info.get('forward_from', '未知')}\n"
        f"🏷 标签: {file_info.get('tags', '无')}\n"
        f"📝 备注: {file_info.get('note', '无')}\n"
        f"🕐 时间: {file_info.get('created_at', '未知')}"
    )
    sent = await message.reply(text)
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))


@router.message(F.chat.id == config.GROUP_ID, Command("tag"))
async def cmd_tag(message: Message):
    if not is_manage_topic(message):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        sent = await message.reply("用法: /tag <文件ID> <标签>")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    try:
        file_id = int(args[1])
    except ValueError:
        sent = await message.reply("⚠️ 文件 ID 必须是数字")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    tags = args[2].strip()
    file_info = db.get_file(file_id)
    if not file_info:
        sent = await message.reply(f"❌ 未找到文件 ID: {file_id}")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    existing_tags = file_info.get("tags", "")
    new_tags = f"{existing_tags} {tags}" if existing_tags else tags
    if db.update_file_tags(file_id, new_tags):
        sent = await message.reply(f"✅ 已更新文件 #{file_id} 标签: {new_tags}")
    else:
        sent = await message.reply("❌ 更新标签失败")
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))


@router.message(F.chat.id == config.GROUP_ID, Command("note"))
async def cmd_note(message: Message):
    if not is_manage_topic(message):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        sent = await message.reply("用法: /note <文件ID> <备注>")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    try:
        file_id = int(args[1])
    except ValueError:
        sent = await message.reply("⚠️ 文件 ID 必须是数字")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    note = args[2].strip()
    file_info = db.get_file(file_id)
    if not file_info:
        sent = await message.reply(f"❌ 未找到文件 ID: {file_id}")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    if db.update_file_note(file_id, note):
        sent = await message.reply(f"✅ 已更新文件 #{file_id} 备注: {note}")
    else:
        sent = await message.reply("❌ 更新备注失败")
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))


@router.message(F.chat.id == config.GROUP_ID, Command("del"))
async def cmd_del(message: Message):
    if not is_manage_topic(message):
        return
    args = message.text.split()
    if len(args) < 2:
        sent = await message.reply("用法: /del <文件ID>")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    try:
        file_id = int(args[1])
    except ValueError:
        sent = await message.reply("⚠️ 文件 ID 必须是数字")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    file_info = db.get_file(file_id)
    if not file_info:
        sent = await message.reply(f"❌ 未找到文件 ID: {file_id}")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    # 尝试删除 Telegram 分类话题中的消息
    if file_info.get("message_id") and file_info.get("topic_id"):
        try:
            await bot.delete_message(config.GROUP_ID, file_info["message_id"])
        except Exception:
            pass  # 消息可能已被删除
    if db.delete_file(file_id):
        sent = await message.reply(f"✅ 已删除文件 #{file_id}: {file_info['filename']}")
    else:
        sent = await message.reply("❌ 删除文件失败")
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))


@router.message(F.chat.id == config.GROUP_ID, Command("list"))
async def cmd_list(message: Message):
    if not is_manage_topic(message):
        return
    args = message.text.split()
    category = args[1] if len(args) > 1 else None
    files = db.list_files(category=category)
    if not files:
        sent = await message.reply("📬 暂无文件" + (f"（分类: {category}）" if category else ""))
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return
    lines = ["📋 文件列表" + (f"（分类: {category}）" if category else "")]
    for f in files:
        tags = f" 🏷{f['tags']}" if f.get('tags') else ""
        lines.append(f"#{f['id']} {f['filename']} [{f['category']}]{tags}")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3997] + "..."
    sent = await message.reply(text)
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))


@router.message(F.chat.id == config.GROUP_ID, Command("stats"))
async def cmd_stats(message: Message):
    if not is_manage_topic(message):
        return
    stats = db.get_stats()
    lines = [f"📊 统计信息\n\n总文件数: {stats['total']}"]
    if stats.get("categories"):
        lines.append("\n📂 分类统计:")
        for cat, cnt in stats["categories"].items():
            lines.append(f"  {cat}: {cnt}")
    sent = await message.reply("\n".join(lines))
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))




@router.message(F.chat.id == config.GROUP_ID, Command("settings"))
async def cmd_settings(message: Message):
    if not is_manage_topic(message):
        return
    # 统计信息
    stats = db.get_stats()
    total = stats.get("total", 0)
    categories = stats.get("categories", {})

    # 话题配置
    topic_lines = []
    for cat, tid in config.CATEGORY_TO_TOPIC.items():
        count = categories.get(cat, 0)
        topic_lines.append(f"  {cat}: 话题#{tid} ({count}个文件)")

    text = (
        "⚙️ **Bot 设置**\n\n"
        f"📊 **统计**: 共 {total} 个文件\n\n"
        "📂 **分类话题**:\n"
        + "\n".join(topic_lines) +
        "\n\n"
        "📋 **可用命令**:\n"
        "/info <ID> - 查看文件详情\n"
        "/tag <ID> <标签> - 设置标签\n"
        "/note <ID> <备注> - 设置备注\n"
        "/del <ID> - 删除文件\n"
        "/list [分类] - 列出文件\n"
        "/stats - 详细统计\n"
        "/settings - 显示此设置\n"
        "/help - 帮助信息\n\n"
        "💡 **使用方式**:\n"
        "• 在「上传」话题发送文件即可上传\n"
        "• 在「搜索」话题输入关键词搜索\n"
        "• 支持 #ID 按ID搜索\n"
        "• 支持分类名/标签/文件名搜索"
    )
    sent = await message.reply(text)
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))

@router.message(F.chat.id == config.GROUP_ID, Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 文件管理 Bot 帮助\n\n"
        "📤 上传文件: 在上传话题中发送文件（支持转发消息）\n"
        "🔍 搜索文件: 在搜索话题中输入关键词\n\n"
        "📋 命令:\n"
        "/info <ID> - 查看文件信息\n"
        "/tag <ID> <标签> - 添加标签\n"
        "/note <ID> <备注> - 添加备注\n"
        "/del <ID> - 删除文件\n"
        "/list [分类] - 列出文件\n"
        "/stats - 统计信息\n"
        "/help - 显示此帮助\n\n"
        "🔍 搜索技巧:\n"
        "• 直接输入关键词（如：报告）\n"
        "• 输入分类名（如：图片、视频、文档）\n"
        "• 组合搜索（如：图片 标签名）\n"
        "• 支持分页浏览搜索结果"
    )
    sent = await message.reply(text)
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))


# ============ 搜索话题 ============
@router.message(F.chat.id == config.GROUP_ID, F.message_thread_id == config.TOPIC_SEARCH)
async def handle_search(message: Message):
    logger.warning("[SEARCH] handler triggered | thread_id=%s expected=%s", message.message_thread_id, config.TOPIC_SEARCH)
    query = message.text
    if not query or not query.strip():
        sent = await message.reply(
            "🔍 **搜索文件**\n\n"
            "支持的搜索方式：\n"
            "• 按ID搜索：#3 或 id:3\n"
            "• 关键词搜索（模糊匹配文件名/标签/备注）\n"
            "• 分类筛选（如：图片、视频、APK、EXE、压缩包、文档、其他）\n"
            "• 组合搜索（如：图片 漂亮）\n\n"
            "💡 提示：输入分类名可查看该分类下所有文件"
        )
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id], 30))
        return

    category, keyword, search_id = parse_search_query(query.strip())
    logger.warning("[SEARCH] query=%r category=%r keyword=%r search_id=%r", query.strip(), category, keyword, search_id)

    # 按 ID 搜索
    if search_id is not None:
        row = db.get_file(search_id)
        if not row:
            sent = await message.reply(f"❌ 未找到 ID={search_id} 的文件")
            asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id], 20))
            return
        tags = f" 🏷{row['tags']}" if row.get("tags") else ""
        note = f" 📝{row['note']}" if row.get("note") else ""
        uploader = row.get("uploader_name") or row.get("forward_from") or "未知"
        time_str = row.get("created_at", "未知")
        if time_str and len(time_str) > 16:
            time_str = time_str[:16]
        link = ""
        msg_id = row.get("message_id")
        if msg_id:
            link = f"\n🔗 [查看文件](https://t.me/c/{abs(config.GROUP_ID)}/{msg_id})"
        text = (
            f"📁 #{row['id']} {row['filename']}\n"
            f"📂 分类: {row['category']}{tags}{note}\n"
            f"👤 上传者: {uploader}\n"
            f"🕐 时间: {time_str}{link}"
        )
        await message.reply(text)
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id], 30))
        return

    if not category and not keyword:
        sent = await message.reply("⚠️ 请输入有效的搜索内容")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id], 20))
        return

    user_id = message.from_user.id if message.from_user else 0
    page = 0
    offset = 0

    results, total = db.search_files_advanced(
        category=category, keyword=keyword,
        limit=SEARCH_PAGE_SIZE, offset=offset,
    )

    # 缓存搜索结果用于分页
    search_cache[user_id] = {
        "category": category,
        "keyword": keyword,
        "total": total,
        "page": 0,
    }

    # 构建搜索描述
    filter_desc = []
    if category:
        filter_desc.append(f"📂 分类: {category}")
    if keyword:
        filter_desc.append(f"🔍 关键词: {keyword}")
    filter_text = " | ".join(filter_desc)

    if not results:
        sent = await message.reply(f"🔍 搜索结果\n{filter_text}\n\n❌ 未找到匹配的文件")
        asyncio.create_task(delayed_delete(message.chat.id, [message.message_id, sent.message_id]))
        return

    text = _format_search_results(results, total, 0, SEARCH_PAGE_SIZE, filter_text)
    kb = _build_search_keyboard(user_id, total, 0, SEARCH_PAGE_SIZE)
    sent = await message.reply(text, reply_markup=kb)
    asyncio.create_task(delayed_delete(message.chat.id, [message.message_id]))


def _format_search_results(results: list[dict], total: int, page: int, page_size: int, filter_text: str = "") -> str:
    """格式化搜索结果"""
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    lines = [f"🔍 搜索结果  (共 {total} 条, 第 {page + 1}/{total_pages} 页)"]
    if filter_text:
        lines.append(filter_text)
    lines.append("")

    for f in results:
        tags = f" 🏷{f['tags']}" if f.get('tags') else ""
        note = f" 📝{f['note']}" if f.get('note') else ""
        uploader = f.get('forward_from') or f.get('uploader_name') or '未知'
        if len(uploader) > 30:
            uploader = uploader[:27] + "..."
        time_str = f.get('created_at', '')
        if time_str and len(time_str) > 16:
            time_str = time_str[:16]
        # 构建深链接
        link = ""
        msg_id = f.get('message_id')
        if msg_id:
            link = f"\n   🔗 [查看文件](https://t.me/c/{abs(config.GROUP_ID)}/{msg_id})"
        lines.append(
            f"**#{f['id']}** {f['filename']}\n"
            f"   📂 {f['category']}{tags}{note}\n"
            f"   👤 {uploader}\n"
            f"   🕐 {time_str}{link}"
        )
        lines.append("")

    return "\n".join(lines)


def _build_search_keyboard(user_id: int, total: int, page: int, page_size: int) -> InlineKeyboardMarkup:
    """构建搜索结果分页键盘"""
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    buttons = []

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"search_page:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️ 下一页", callback_data=f"search_page:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


@router.callback_query(F.data.startswith("search_page:"))
async def cb_search_page(callback: CallbackQuery):
    """搜索结果分页回调"""
    user_id = callback.from_user.id
    if user_id not in search_cache:
        await callback.answer("⚠️ 搜索已过期，请重新搜索", show_alert=True)
        return

    page = int(callback.data.split(":")[1])
    cache = search_cache[user_id]
    category = cache["category"]
    keyword = cache["keyword"]
    total = cache["total"]

    page_size = SEARCH_PAGE_SIZE
    offset = page * page_size

    results, new_total = db.search_files_advanced(
        category=category, keyword=keyword,
        limit=page_size, offset=offset,
    )

    cache["page"] = page
    cache["total"] = new_total

    filter_desc = []
    if category:
        filter_desc.append(f"📂 分类: {category}")
    if keyword:
        filter_desc.append(f"🔍 关键词: {keyword}")
    filter_text = " | ".join(filter_desc)

    text = _format_search_results(results, new_total, page, page_size, filter_text)
    kb = _build_search_keyboard(user_id, new_total, page, page_size)

    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer()


# ============ 辅助函数 ============
async def delayed_delete(chat_id: int, message_ids: list[int], delay: float = None):
    if delay is None:
        delay = config.DELETE_DELAY
    await asyncio.sleep(delay)
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id, msg_id)
        except TelegramBadRequest:
            pass


# ============ DEBUG: 全量日志 (在所有 handler 之后作为 fallback) ============
@router.message(F.chat.id == config.GROUP_ID)
async def debug_log_all(message: Message):
    logger.warning(
        "[FALLBACK] ALL_MSG topic=%s user=%s text=%r doc=%s photo=%s video=%s",
        message.message_thread_id,
        message.from_user.id if message.from_user else "?",
        message.text or "(media)",
        bool(message.document),
        bool(message.photo),
        bool(message.video),
    )

# ============ 启动 ============
async def main():
    db.init_db()
    logger.info("数据库初始化完成")

    # 注册调试中间件（在所有 handler 之前运行）
    router.message.middleware(debug_middleware)
    logger.info("调试中间件已注册")

    dp.include_router(fsm_router)  # FSM 优先
    dp.include_router(router)
    logger.info(f"Bot 启动中... 群组: {config.GROUP_ID}")
    logger.info("=" * 60)
    logger.info("已配置的话题 ID:")
    for tid, name in KNOWN_TOPIC_IDS.items():
        logger.info(f"  {name}: {tid}")
    logger.info("=" * 60)
    logger.info("⚠️  注意: 实际的 topic_id 可能与配置不同!")
    logger.info("⚠️  发送任意消息到群组后，检查 [MIDDLEWARE] 日志中的真实 thread_id")
    logger.info("=" * 60)
    # 注册 BotFather 命令列表
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="info", description="查看文件详情"),
        BotCommand(command="tag", description="设置标签"),
        BotCommand(command="note", description="设置备注"),
        BotCommand(command="del", description="删除文件"),
        BotCommand(command="list", description="列出文件"),
        BotCommand(command="stats", description="统计信息"),
        BotCommand(command="settings", description="Bot设置"),
        BotCommand(command="help", description="帮助信息"),
    ]
    try:
        await bot.set_my_commands(commands)
        logger.info("Bot 命令列表已注册")
    except Exception as e:
        logger.warning(f"注册命令列表失败: {e}")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())


