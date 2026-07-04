'''
数据库模块 - SQLite 文件数据库操作
'''
import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            uploader_id INTEGER,
            uploader_name TEXT,
            forward_from TEXT,
            tags TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("ALTER TABLE files ADD COLUMN message_id INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE files ADD COLUMN topic_id INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def next_file_id() -> int:
    conn = get_connection()
    cursor = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM files")
    new_id = cursor.fetchone()[0]
    conn.close()
    return new_id


def add_file(file_id, category, filename, uploader_id=None, uploader_name=None,
             forward_from=None, tags="", note="", message_id=None, topic_id=None) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO files (id, category, filename, uploader_id, uploader_name, forward_from, tags, note, message_id, topic_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, category, filename, uploader_id, uploader_name, forward_from, tags, note, message_id, topic_id)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[数据库] 添加文件失败: {e}")
        return False
    finally:
        conn.close()


def get_file(file_id: int) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_file_tags(file_id: int, tags: str) -> bool:
    conn = get_connection()
    try:
        conn.execute("UPDATE files SET tags = ? WHERE id = ?", (tags, file_id))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def update_file_note(file_id: int, note: str) -> bool:
    conn = get_connection()
    try:
        conn.execute("UPDATE files SET note = ? WHERE id = ?", (note, file_id))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_file(file_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def list_files(category=None, limit=20, offset=0) -> list[dict]:
    conn = get_connection()
    if category:
        cursor = conn.execute("SELECT * FROM files WHERE category = ? ORDER BY id DESC LIMIT ? OFFSET ?", (category, limit, offset))
    else:
        cursor = conn.execute("SELECT * FROM files ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def search_files(keyword: str) -> list[dict]:
    rows, _ = search_files_advanced(keyword=keyword, limit=20)
    return rows


def search_files_advanced(category=None, keyword=None, limit=10, offset=0) -> tuple[list[dict], int]:
    conditions, params = [], []
    if category:
        conditions.append("category = ?")
        params.append(category)
    if keyword:
        for word in keyword.split():
            conditions.append("(filename LIKE ? OR tags LIKE ? OR note LIKE ?)")
            w = f"%{word}%"
            params.extend([w, w, w])
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    conn = get_connection()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM files WHERE {where_clause}", params).fetchone()[0]
        cursor = conn.execute(f"SELECT * FROM files WHERE {where_clause} ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset])
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
    return rows, total


def get_stats() -> dict:
    conn = get_connection()
    stats = {}
    stats["total"] = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    cursor = conn.execute("SELECT category, COUNT(*) as cnt FROM files GROUP BY category ORDER BY cnt DESC")
    stats["categories"] = {row["category"]: row["cnt"] for row in cursor.fetchall()}
    conn.close()
    return stats
