#!/usr/bin/env python3
"""
Termux-Instagram-Collector: Telegram Daemon
Runs continuously in the background (Long-polling) to handle admin commands and session updates.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    ADMIN_CHAT_ID,
    BASE_DIR,
    BOT_LOG_PATH,
    CRON_LOG_PATH,
    LOG_DIR,
    TARGET_USERNAMES,
    TELEGRAM_BOT_TOKEN,
    validate_config,
)
from db import (
    get_last_successful_scrape_time,
    get_latest_execution_logs,
    get_posts_count,
    init_db,
)
from scraper import run_scraper
from session_manager import (
    get_sessionid,
    load_session,
    save_session_atomic,
    validate_sessionid_format,
)

# Configure logging
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BOT_LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("telegram_bot")


def admin_only(func: Callable):
    """Decorator to enforce that only ADMIN_CHAT_ID can execute the command."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id if update.effective_user else None
        chat_id = update.effective_chat.id if update.effective_chat else None

        if chat_id != ADMIN_CHAT_ID and user_id != ADMIN_CHAT_ID:
            logger.warning(f"Unauthorized access attempt from chat_id={chat_id}, user_id={user_id}")
            # Silently drop or ignore unauthorized requests
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def get_battery_info() -> str:
    """Retrieve device battery information on Termux or fallback OS."""
    # 1. Try termux-battery-status CLI
    try:
        res = subprocess.run(
            ["termux-battery-status"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0:
            data = json.loads(res.stdout)
            pct = data.get("percentage", "N/A")
            status = data.get("status", "N/A")
            plugged = data.get("plugged", "N/A")
            temp = data.get("temperature", "")
            temp_str = f" ({temp}°C)" if temp else ""
            return f"{pct}% [{status} / {plugged}]{temp_str}"
    except Exception:
        pass

    # 2. Try psutil if available
    try:
        import psutil
        battery = psutil.sensors_battery()
        if battery:
            plugged_str = "충전 중" if battery.power_plugged else "배터리"
            return f"{battery.percent:.0f}% [{plugged_str}]"
    except Exception:
        pass

    return "조회 불가 (Termux API 필요: `pkg install termux-api`)"


def get_disk_info() -> str:
    """Retrieve storage capacity details."""
    try:
        total, used, free = shutil.disk_usage(BASE_DIR)
        gb = 1024 ** 3
        pct_free = (free / total) * 100
        return f"{free / gb:.2f} GB 남음 ({pct_free:.1f}% 여유 / 총 {total / gb:.2f} GB)"
    except Exception as e:
        return f"디스크 조회 실패 ({e})"


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    text = (
        "🤖 <b>Termux Instagram Collector 관리 봇</b>\n\n"
        "사용 가능한 명령어:\n"
        "• <code>/status</code> : 기기 상태 및 수집 통계 확인\n"
        "• <code>/session &lt;sessionid&gt;</code> : 새 세션 쿠키 등록 및 갱신\n"
        "• <code>/run</code> : 즉시 스크래핑 1회 실행\n"
        "• <code>/log [lines]</code> : 최근 실행 로그 확인 (기본 15줄)\n"
        "• <code>/help</code> : 도움말 표시"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await cmd_start(update, context)


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    battery_info = get_battery_info()
    disk_info = get_disk_info()
    last_success = get_last_successful_scrape_time() or "이력 없음"
    total_posts = get_posts_count()

    session_data = load_session()
    has_session = bool(session_data.get("sessionid"))
    session_updated_at = session_data.get("updated_at", "알 수 없음")
    session_status = f"✅ 등록됨 (갱신: {session_updated_at})" if has_session else "❌ 미등록 또는 만료"

    targets_str = ", ".join([f"@{u}" for u in TARGET_USERNAMES]) if TARGET_USERNAMES else "없음"

    msg = (
        "📊 <b>[시스템 상태 리포트]</b>\n\n"
        f"🔋 <b>배터리:</b> {battery_info}\n"
        f"💾 <b>저장공간:</b> {disk_info}\n"
        f"🔑 <b>세션 상태:</b> {session_status}\n"
        f"🎯 <b>수집 대상:</b> {targets_str}\n"
        f"📦 <b>누적 저장 포스트:</b> {total_posts:,}건\n"
        f"🕒 <b>최근 성공 시각:</b> {last_success}\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /session <sessionid> command for atomic session update."""
    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ <b>사용법 오류</b>\n"
            "세션 ID를 함께 입력해주세요.\n\n"
            "예시:\n"
            "<code>/session 123456789%3Aabcdef123456...</code>",
            parse_mode=ParseMode.HTML
        )
        return

    new_sessionid = args[0].strip()

    if not validate_sessionid_format(new_sessionid):
        await update.message.reply_text(
            "❌ <b>유효하지 않은 sessionid 형식입니다.</b>\n"
            "문자열 길이나 형식을 다시 확인해주세요.",
            parse_mode=ParseMode.HTML
        )
        return

    success = save_session_atomic(new_sessionid)
    if success:
        logger.info("Session updated successfully via Telegram bot command.")
        await update.message.reply_text(
            "✅ <b>세션 갱신 완료!</b>\n\n"
            "새로운 sessionid가 <code>session.json</code>에 안전하게 저장되었습니다.\n"
            "이제 <code>/run</code> 명령어로 정상 작동하는지 테스트해보실 수 있습니다.",
            parse_mode=ParseMode.HTML
        )
    else:
        logger.error("Failed to save session atomically from Telegram bot.")
        await update.message.reply_text(
            "❌ <b>세션 파일 저장 중 오류가 발생했습니다.</b>\n"
            "서버 로그를 확인해주세요.",
            parse_mode=ParseMode.HTML
        )


@admin_only
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /run command to trigger immediate scraping."""
    await update.message.reply_text("⏳ <b>스크래핑 작업을 즉시 시작합니다...</b>", parse_mode=ParseMode.HTML)
    
    # Run in worker thread to prevent blocking asyncio loop
    loop = asyncio.get_running_loop()
    try:
        status, new_count, err = await loop.run_in_executor(
            None, run_scraper, False  # apply_jitter=False for immediate run
        )

        if status == "SUCCESS":
            msg = (
                "✅ <b>수집 작업 완료</b>\n\n"
                f"• 결과: <b>성공 (SUCCESS)</b>\n"
                f"• 신규 수집 포스트: <b>{new_count}건</b>\n"
                f"• 총 누적 포스트: <b>{get_posts_count():,}건</b>"
            )
        else:
            msg = (
                f"⚠️ <b>수집 작업 완료 (주의/실패)</b>\n\n"
                f"• 상태: <b>{status}</b>\n"
                f"• 에러 메시지: <code>{err or 'None'}</code>\n"
                f"• 신규 수집 포스트: <b>{new_count}건</b>"
            )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error during manual /run execution: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ <b>실행 중 예외가 발생했습니다:</b>\n<code>{e}</code>",
            parse_mode=ParseMode.HTML
        )


@admin_only
async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /log [lines] command."""
    lines_count = 15
    if context.args:
        try:
            lines_count = max(1, min(100, int(context.args[0])))
        except ValueError:
            pass

    # Read from cron.log or DB execution logs
    db_logs = get_latest_execution_logs(limit=lines_count)
    
    # Also check tail of cron.log if available
    file_tail_text = ""
    if CRON_LOG_PATH.exists():
        try:
            with open(CRON_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                tail_lines = all_lines[-lines_count:]
                file_tail_text = "".join(tail_lines).strip()
        except Exception as e:
            file_tail_text = f"로그 파일 읽기 실패: {e}"

    # Format DB logs summary
    db_summary = []
    for r in reversed(db_logs):
        status_icon = "✅" if r["status"] == "SUCCESS" else "❌"
        err = f" ({r['error_message']})" if r.get("error_message") else ""
        db_summary.append(
            f"{status_icon} [{r['executed_at']}] {r['status']} | 신규 {r['new_posts_count']}건{err}"
        )
    db_text = "\n".join(db_summary) if db_summary else "기록 없음"

    msg = (
        f"📋 <b>[최근 실행 이력 (DB)]</b> (최근 {len(db_logs)}회)\n"
        f"<code>{db_text}</code>\n\n"
    )

    if file_tail_text:
        # Limit text length for Telegram message (max 4096)
        if len(file_tail_text) > 2000:
            file_tail_text = "..." + file_tail_text[-2000:]
        msg += (
            f"📄 <b>[최근 cron.log 내용]</b> (최근 {lines_count}줄)\n"
            f"<pre>{file_tail_text}</pre>"
        )

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


def main():
    """Main entry point for Telegram bot daemon."""
    init_db()
    
    errors = validate_config(require_telegram=True, require_target=False)
    if errors:
        for err in errors:
            logger.critical(f"Config error: {err}")
        print("\n".join(errors))
        sys.exit(1)

    logger.info("Initializing Telegram Bot Daemon...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("session", cmd_session))
    application.add_handler(CommandHandler("run", cmd_run))
    application.add_handler(CommandHandler("log", cmd_log))

    logger.info(f"Bot started successfully. Listening for admin chat_id={ADMIN_CHAT_ID}...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
