'''
测试连接脚本
'''
import asyncio
from aiogram import Bot
import config


async def main():
    bot = Bot(token=config.BOT_TOKEN)
    me = await bot.get_me()
    print(f"Bot: @{me.username} ({me.first_name})")
    print(f"ID: {me.id}")
    try:
        chat = await bot.get_chat(config.GROUP_ID)
        print(f"群组: {chat.title}")
    except Exception as e:
        print(f"群组访问失败: {e}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
