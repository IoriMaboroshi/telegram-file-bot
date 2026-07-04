# Telegram File Bot

Telegram 话题群组文件管理 Bot。自动分类存储文件，支持搜索、标签、备注等交互式操作。

## 功能

- 自动分类 - 根据文件扩展名自动归类到对应话题
- 文件搜索 - 按文件名、标签、备注模糊搜索
- 标签管理 - 为文件添加标签
- 备注系统 - 为文件添加说明备注
- 统计面板 - 一键查看各分类文件数量
- 管理命令 - 删除文件记录（带确认）

## 快速开始

```bash
git clone https://github.com/IoriMaboroshi/telegram-file-bot.git
cd telegram-file-bot
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入你的配置
python bot.py
```

## Docker 部署

```bash
docker compose up -d
```

## License

MIT