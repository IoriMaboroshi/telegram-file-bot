'''
查找群组信息脚本
'''
import asyncio
from aiogram import Bot
import config


async def main():
    bot = Bot(token=config.BOT_TOKEN)
    me = await bot.get_me()
    print(f"Bot: @{me.username}\n")
    try:
        updates = await bot.get_updates(limit=100)
        groups = set()
        for update in updates:
            if update.message and update.message.chat:
                chat = update.message.chat
                if chat.type in ("supergroup", "group"):
                    groups.add((chat.id, chat.title))
        if groups:
            print("从更新中发现的群组:")
            for gid, title in sorted(groups):
                print(f"  {title}: {gid}")
        else:
            print("未从更新中发现群组。")
    except Exception as e:
        print(f"获取更新失败: {e}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
