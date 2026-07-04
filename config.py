'''
配置模块 - 从环境变量加载所有配置
'''
import os
from dotenv import load_dotenv

load_dotenv()

# Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# 群组 ID
GROUP_ID = int(os.getenv("GROUP_ID", "-1000000000000"))

# 话题 ID 映射（Forum Topics）
TOPIC_UPLOAD = int(os.getenv("TOPIC_UPLOAD", "2"))
TOPIC_VIDEO = int(os.getenv("TOPIC_VIDEO", "0"))
TOPIC_IMAGE = int(os.getenv("TOPIC_IMAGE", "0"))
TOPIC_APK = int(os.getenv("TOPIC_APK", "0"))
TOPIC_EXE = int(os.getenv("TOPIC_EXE", "0"))
TOPIC_ARCHIVE = int(os.getenv("TOPIC_ARCHIVE", "0"))
TOPIC_DOCUMENT = int(os.getenv("TOPIC_DOCUMENT", "0"))
TOPIC_OTHER = int(os.getenv("TOPIC_OTHER", "0"))
TOPIC_SEARCH = int(os.getenv("TOPIC_SEARCH", "0"))
TOPIC_AUDIO = int(os.getenv("TOPIC_AUDIO", "12"))

# 分类名称 -> 话题 ID 映射
CATEGORY_TO_TOPIC = {
    "视频": TOPIC_VIDEO,
    "图片": TOPIC_IMAGE,
    "APK": TOPIC_APK,
    "EXE": TOPIC_EXE,
    "压缩包": TOPIC_ARCHIVE,
    "文档": TOPIC_DOCUMENT,
    "音频": TOPIC_AUDIO,
    "其他": TOPIC_OTHER,
}

# 所有上传话题的 ID 列表，用于判断消息来源
UPLOAD_TOPIC_IDS = {
    TOPIC_VIDEO, TOPIC_IMAGE, TOPIC_APK, TOPIC_EXE,
    TOPIC_ARCHIVE, TOPIC_DOCUMENT, TOPIC_AUDIO, TOPIC_OTHER,
}
UPLOAD_TOPIC_IDS.discard(0)  # 移除未配置的话题

# 文件扩展名分类规则
EXTENSION_MAP = {
    "视频": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".rmvb", ".rm"},
    "图片": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff", ".psd"},
    "APK": {".apk"},
    "EXE": {".exe", ".msi", ".bat", ".cmd", ".com", ".scr"},
    "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "文档": {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".md", ".txt", ".csv", ".json", ".xml", ".html", ".css",
        ".js", ".py", ".java", ".cpp", ".c", ".h", ".sh", ".log",
        ".ini", ".cfg", ".conf", ".yaml", ".yml", ".toml",
    },
    "音频": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus", ".ape"},
}

# 管理命令处理后延迟删除的时间（秒）
DELETE_DELAY = 10

# 上传流程等待回复超时（秒）
UPLOAD_TIMEOUT = 60
