'''
创建话题脚本
'''
import asyncio
from aiogram import Bot
import config


async def main():
    bot = Bot(token=config.BOT_TOKEN)
    topics = ["上传", "视频", "图片", "APK", "EXE", "压缩包", "文档", "音频", "其他", "搜索"]
    print(f"群组 ID: {config.GROUP_ID}")
    created = {}
    for name in topics:
        try:
            topic = await bot.create_forum_topic(chat_id=config.GROUP_ID, name=name)
            created[name] = topic.message_thread_id
            print(f"  ✓ {name}: {topic.message_thread_id}")
        except Exception as e:
            print(f"  ✗ {name}: {e}")
    print(f"\n创建完成！共 {len(created)}/{len(topics)} 个")
    print("\n请将以下配置添加到 .env 文件：")
    for name, tid in created.items():
        env_name = f"TOPIC_{name.upper()}" if name not in ("上传", "搜索") else f"TOPIC_{name}"
        print(f"  {env_name}={tid}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
