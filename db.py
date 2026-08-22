import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from config import DB_PATH


def get_db_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Create and return a SQLite database connection with row factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize SQLite database tables and indices."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # posts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                post_id TEXT PRIMARY KEY,
                target_username TEXT NOT NULL,
                caption TEXT,
                media_type TEXT,
                media_urls TEXT,
                likes_count INTEGER DEFAULT 0,
                comments_count INTEGER DEFAULT 0,
                posted_at DATETIME,
                created_at DATETIME DEFAULT (datetime('now', 'localtime'))
            );
        """)
        
        # execution_logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                executed_at DATETIME DEFAULT (datetime('now', 'localtime')),
                status TEXT NOT NULL,
                new_posts_count INTEGER DEFAULT 0,
                error_message TEXT
            );
        """)
        
        # Create indices for fast lookup
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_target ON posts (target_username);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts (posted_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_status ON execution_logs (status);")
        
        conn.commit()


def is_post_exists(post_id: str, db_path: Path = DB_PATH) -> bool:
    """Check if a post already exists in the database."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM posts WHERE post_id = ? LIMIT 1", (post_id,))
        return cursor.fetchone() is not None


def save_post(post_data: dict[str, Any], db_path: Path = DB_PATH) -> bool:
    """
    Save a single post. Returns True if newly inserted, False if already exists.
    Updates likes_count and comments_count if the post already exists.
    """
    media_urls_raw = post_data.get("media_urls", [])
    if isinstance(media_urls_raw, (list, dict)):
        media_urls_str = json.dumps(media_urls_raw, ensure_ascii=False)
    else:
        media_urls_str = str(media_urls_raw or "[]")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        # Check existence
        cursor.execute("SELECT post_id FROM posts WHERE post_id = ?", (post_data["post_id"],))
        exists = cursor.fetchone() is not None

        if exists:
            # Update engagement stats
            cursor.execute("""
                UPDATE posts
                SET likes_count = ?, comments_count = ?
                WHERE post_id = ?
            """, (
                post_data.get("likes_count", 0),
                post_data.get("comments_count", 0),
                post_data["post_id"]
            ))
            conn.commit()
            return False
        else:
            cursor.execute("""
                INSERT INTO posts (
                    post_id, target_username, caption, media_type,
                    media_urls, likes_count, comments_count, posted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                post_data["post_id"],
                post_data["target_username"],
                post_data.get("caption", ""),
                post_data.get("media_type", "IMAGE"),
                media_urls_str,
                post_data.get("likes_count", 0),
                post_data.get("comments_count", 0),
                post_data.get("posted_at")
            ))
            conn.commit()
            return True


def save_posts_bulk(posts: list[dict[str, Any]], db_path: Path = DB_PATH) -> int:
    """Save multiple posts and return the count of newly inserted posts."""
    new_count = 0
    for post in posts:
        if save_post(post, db_path):
            new_count += 1
    return new_count


def log_execution(
    status: str,
    new_posts_count: int = 0,
    error_message: Optional[str] = None,
    db_path: Path = DB_PATH
) -> int:
    """Record an execution log entry and return its ID."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO execution_logs (status, new_posts_count, error_message)
            VALUES (?, ?, ?)
        """, (status, new_posts_count, error_message))
        conn.commit()
        return cursor.lastrowid or 0


def get_last_successful_scrape_time(db_path: Path = DB_PATH) -> Optional[str]:
    """Retrieve the timestamp of the most recent successful execution."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT executed_at FROM execution_logs
            WHERE status = 'SUCCESS'
            ORDER BY id DESC LIMIT 1
        """)
        row = cursor.fetchone()
        return row["executed_at"] if row else None


def get_latest_execution_logs(limit: int = 15, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Retrieve the latest execution log records."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, executed_at, status, new_posts_count, error_message
            FROM execution_logs
            ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_posts_count(target_username: Optional[str] = None, db_path: Path = DB_PATH) -> int:
    """Get the total count of collected posts."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if target_username:
            cursor.execute("SELECT COUNT(*) as cnt FROM posts WHERE target_username = ?", (target_username,))
        else:
            cursor.execute("SELECT COUNT(*) as cnt FROM posts")
        row = cursor.fetchone()
        return row["cnt"] if row else 0


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at", DB_PATH)
