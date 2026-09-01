import html
import logging
from typing import Optional
import requests
from config import TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


def send_telegram_message(
    text: str,
    chat_id: Optional[int] = ADMIN_CHAT_ID,
    parse_mode: str = "HTML"
) -> bool:
    """
    Send a message to the specified Telegram chat using standard Bot API.
    Used by scraper and daemons for direct notifications.
    """
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        logger.warning("Telegram Bot Token or Admin Chat ID not configured. Message skipped.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            return True
        else:
            logger.error(f"Telegram API error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def notify_session_expired(reason: str = "세션 쿠키가 만료되었거나 유효하지 않습니다.") -> bool:
    """Send high-priority alert for session expiration."""
    msg = (
        "⚠️ <b>[Instagram Scraper] 세션 만료 감지</b>\n\n"
        f"<b>원인:</b> {html.escape(str(reason))}\n"
        "인스타그램 세션이 만료되어 스크래핑이 중단되었습니다.\n\n"
        "👉 텔레그램 봇으로 새 세션을 입력해주세요:\n"
        "<code>/session &lt;새로운_sessionid&gt;</code>"
    )
    return send_telegram_message(msg)


def notify_rate_limit(details: str = "429 Too Many Requests") -> bool:
    """Send high-priority alert for rate limits."""
    msg = (
        "🚨 <b>[Instagram Scraper] Rate Limit (요청 제한) 감지</b>\n\n"
        f"<b>상세:</b> {html.escape(str(details))}\n"
        "인스타그램 요청 제한(429)이 감지되어 이번 수집 작업을 중단했습니다.\n"
        "계정 보호를 위해 잠시 대기 후 다음 스케줄에 재시도합니다."
    )
    return send_telegram_message(msg)


def notify_new_posts(username: str, count: int, sample_post_id: Optional[str] = None) -> bool:
    """Send notification when new posts are collected."""
    msg = (
        f"📸 <b>[Instagram Scraper] 신규 포스트 수집</b>\n\n"
        f"• 대상 계정: <b>@{html.escape(str(username))}</b>\n"
        f"• 수집 건수: <b>{count}건</b>\n"
    )
    if sample_post_id:
        msg += f"• 최신 링크: https://www.instagram.com/p/{html.escape(str(sample_post_id))}/\n"
    return send_telegram_message(msg)
