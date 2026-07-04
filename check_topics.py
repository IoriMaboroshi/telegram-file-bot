'''
检查话题脚本
'''
import asyncio
from aiogram import Bot
import config


async def main():
    bot = Bot(token=config.BOT_TOKEN)
    try:
        chat = await bot.get_chat(config.GROUP_ID)
        print(f"群组: {chat.title}")
    except Exception as e:
        print(f"访问群组失败: {e}")
        await bot.session.close()
        return
    try:
        updates = await bot.get_updates(limit=100)
        topic_ids = set()
        for update in updates:
            if update.message and update.message.chat:
                if update.message.chat.id == config.GROUP_ID:
                    tid = update.message.message_thread_id
                    if tid:
                        topic_ids.add(tid)
        if topic_ids:
            print(f"发现的话题 ID: {sorted(topic_ids)}")
        else:
            print("未发现话题消息。")
    except Exception as e:
        print(f"获取更新失败: {e}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
