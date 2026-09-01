#!/usr/bin/env python3
"""
Termux-Instagram-Collector: Main Scraping Engine
Executed via Crond on schedule or manually via /run in Telegram bot.
"""

import argparse
import datetime
import fcntl
import json
import logging
import random
import sys
import time
from typing import Any, Optional

import requests

from config import (
    CRON_LOG_PATH,
    JITTER_MAX_SECONDS,
    LOCK_FILE_PATH,
    LOG_DIR,
    REQUEST_TIMEOUT,
    TARGET_USERNAMES,
    validate_config,
)
from db import (
    get_last_successful_scrape_time,
    init_db,
    log_execution,
    save_post,
)
from session_manager import get_sessionid, validate_sessionid_format
from telegram_notifier import (
    notify_new_posts,
    notify_rate_limit,
    notify_session_expired,
)

# Configure logging
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(CRON_LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("scraper")


USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
]


def build_headers(sessionid: str, username: str) -> dict[str, str]:
    """Build request headers mimicking modern mobile browser."""
    # Extract ds_user_id if present in sessionid (e.g. 12345%3A...)
    user_id = sessionid.split("%3A")[0] if "%3A" in sessionid else sessionid.split(":")[0]
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-IG-App-ID": "936619743392459",
        "X-Requested-With": "XMLHttpRequest",
        "X-ASBD-ID": "129477",
        "Referer": f"https://www.instagram.com/{username}/",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Cookie": f"sessionid={sessionid}; ds_user_id={user_id};"
    }
    return headers


def parse_post_node(node: dict[str, Any], target_username: str) -> Optional[dict[str, Any]]:
    """Parse Instagram post node into structured dictionary for DB insertion."""
    try:
        shortcode = node.get("shortcode") or node.get("code") or node.get("id")
        if not shortcode:
            return None

        typename = node.get("__typename", "")
        is_video = node.get("is_video", False)
        
        if typename == "GraphSidecar":
            media_type = "CAROUSEL"
        elif typename == "GraphVideo" or is_video:
            media_type = "VIDEO"
        else:
            media_type = "IMAGE"

        # Caption
        caption = ""
        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        if caption_edges and isinstance(caption_edges, list):
            caption = caption_edges[0].get("node", {}).get("text", "")

        # Media URLs
        media_urls = []
        if media_type == "CAROUSEL":
            children = node.get("edge_sidecar_to_children", {}).get("edges", [])
            for child in children:
                c_node = child.get("node", {})
                if c_node.get("is_video") and c_node.get("video_url"):
                    media_urls.append(c_node.get("video_url"))
                elif c_node.get("display_url"):
                    media_urls.append(c_node.get("display_url"))
        elif media_type == "VIDEO" and node.get("video_url"):
            media_urls.append(node.get("video_url"))
        elif node.get("display_url"):
            media_urls.append(node.get("display_url"))

        # Engagement
        likes_count = (
            node.get("edge_liked_by", {}).get("count")
            or node.get("edge_media_preview_like", {}).get("count")
            or 0
        )
        comments_count = node.get("edge_media_to_comment", {}).get("count") or 0

        # Timestamp
        taken_at_timestamp = node.get("taken_at_timestamp")
        posted_at = None
        if taken_at_timestamp:
            posted_at = datetime.datetime.fromtimestamp(
                int(taken_at_timestamp), tz=datetime.timezone.utc
            ).astimezone().strftime("%Y-%m-%d %H:%M:%S")

        return {
            "post_id": shortcode,
            "target_username": target_username,
            "caption": caption,
            "media_type": media_type,
            "media_urls": media_urls,
            "likes_count": likes_count,
            "comments_count": comments_count,
            "posted_at": posted_at,
        }
    except Exception as e:
        logger.error(f"Error parsing post node: {e}", exc_info=True)
        return None


def fetch_user_posts(
    username: str,
    sessionid: str,
    max_retries: int = 3
) -> tuple[str, list[dict[str, Any]], Optional[str]]:
    """
    Fetch posts for target username from Instagram Web API.
    Returns (status, posts_list, error_message).
    Statuses: 'SUCCESS', 'FAIL_SESSION', 'FAIL_RATE_LIMIT', 'FAIL_NETWORK', 'FAIL_UNKNOWN'
    """
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers = build_headers(sessionid, username)

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Fetching posts for @{username} (Attempt {attempt}/{max_retries})...")
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            
            # Status code checks
            if response.status_code == 401:
                logger.warning(f"401 Unauthorized received for @{username}.")
                return "FAIL_SESSION", [], "401 Unauthorized (Session expired)"
            
            if response.status_code == 429:
                logger.warning(f"429 Too Many Requests received for @{username}.")
                return "FAIL_RATE_LIMIT", [], "429 Too Many Requests (Rate limit)"

            if response.status_code == 200:
                try:
                    data = response.json()
                except Exception as json_err:
                    logger.error(f"Invalid JSON response: {json_err}. Raw text: {response.text[:200]}")
                    if "login" in response.text.lower():
                        return "FAIL_SESSION", [], "Instagram redirected to login page"
                    return "FAIL_UNKNOWN", [], f"JSON parse error: {json_err}"

                # Validate payload structure
                if data.get("message") == "login_required" or data.get("status") == "fail":
                    msg = data.get("message", "login_required")
                    logger.warning(f"API returned failure: {msg}")
                    return "FAIL_SESSION", [], f"API login required: {msg}"

                user_data = data.get("data", {}).get("user")
                if not user_data:
                    logger.warning(f"User @{username} data not found or empty.")
                    return "SUCCESS", [], None

                timeline_media = user_data.get("edge_owner_to_timeline_media", {})
                edges = timeline_media.get("edges", [])
                
                parsed_posts = []
                for edge in edges:
                    node = edge.get("node")
                    if node:
                        post_dict = parse_post_node(node, username)
                        if post_dict:
                            parsed_posts.append(post_dict)
                
                logger.info(f"Successfully fetched {len(parsed_posts)} posts for @{username}.")
                return "SUCCESS", parsed_posts, None

            # Unexpected status code (e.g. 403, 500): back off and retry like a network error
            # instead of hammering Instagram immediately, and don't mislabel this as FAIL_NETWORK.
            logger.warning(f"Unexpected status code {response.status_code}: {response.text[:200]}")
            if attempt < max_retries:
                backoff = 2 ** attempt
                logger.info(f"Retrying in {backoff} seconds...")
                time.sleep(backoff)
                continue
            return "FAIL_UNKNOWN", [], f"Unexpected status code {response.status_code} after {max_retries} attempts"

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
            logger.warning(f"Network error on attempt {attempt}: {net_err}")
            if attempt < max_retries:
                backoff = 2 ** attempt
                logger.info(f"Retrying in {backoff} seconds...")
                time.sleep(backoff)
            else:
                return "FAIL_NETWORK", [], f"Network error after {max_retries} attempts: {net_err}"
        except Exception as e:
            logger.error(f"Unexpected error fetching posts: {e}", exc_info=True)
            return "FAIL_UNKNOWN", [], str(e)

    return "FAIL_UNKNOWN", [], "Max retries exceeded"


def _try_acquire_lock():
    """
    Attempt to acquire an exclusive, non-blocking flock on LOCK_FILE_PATH.

    This is the single locking mechanism for the whole process: both the
    cron-triggered run (scraper.py main()) and the Telegram-triggered /run
    command call run_scraper() directly, so locking here is what actually
    keeps two runs from executing concurrently and racing on session.json /
    the SQLite DB. Returns the open file handle (must be kept referenced to
    hold the lock, then released via _release_lock) on success, or None if
    another run already holds it.
    """
    lock_file = open(LOCK_FILE_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def _release_lock(lock_file) -> None:
    try:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    finally:
        lock_file.close()


def run_scraper(apply_jitter: bool = True) -> tuple[str, int, Optional[str]]:
    """
    Main scraping execution workflow.
    Returns (status, total_new_posts, error_message).
    """
    logger.info("=" * 50)
    logger.info(f"Starting Instagram Scraper run at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 0. Initialize DB
    init_db()

    # 0.5 Acquire single-instance lock (see _try_acquire_lock docstring)
    lock_file = _try_acquire_lock()
    if lock_file is None:
        err_msg = "다른 스크래핑 작업이 이미 실행 중입니다 (lock 획득 실패)."
        logger.warning(err_msg)
        log_execution("FAIL_LOCKED", 0, err_msg)
        return "FAIL_LOCKED", 0, err_msg

    try:
        # 1. Validate Basic Config
        config_errors = validate_config(require_telegram=False, require_target=True)
        if config_errors:
            err_msg = "; ".join(config_errors)
            logger.error(f"Configuration error: {err_msg}")
            log_execution("FAIL_CONFIG", 0, err_msg)
            return "FAIL_CONFIG", 0, err_msg

        # 2. Jitter delay for detection avoidance
        if apply_jitter and JITTER_MAX_SECONDS > 0:
            jitter_delay = random.randint(0, JITTER_MAX_SECONDS)
            logger.info(f"Applying Jitter delay: sleeping for {jitter_delay}s...")
            time.sleep(jitter_delay)

        # 3. Validate Session
        sessionid = get_sessionid()
        if not sessionid or not validate_sessionid_format(sessionid):
            err_msg = "session.json is missing, empty, or has an invalid sessionid format."
            logger.error(err_msg)
            notify_session_expired(err_msg)
            log_execution("FAIL_SESSION", 0, err_msg)
            return "FAIL_SESSION", 0, err_msg

        total_new_posts = 0
        overall_status = "SUCCESS"
        last_error = None

        # 4. Fetch posts for all target usernames
        for username in TARGET_USERNAMES:
            status, posts, err = fetch_user_posts(username, sessionid)

            if status == "FAIL_SESSION":
                overall_status = "FAIL_SESSION"
                last_error = err
                notify_session_expired(f"@{username} 수집 중 세션 오류: {err}")
                break
            elif status == "FAIL_RATE_LIMIT":
                overall_status = "FAIL_RATE_LIMIT"
                last_error = err
                notify_rate_limit(f"@{username} 수집 중 Rate Limit: {err}")
                break
            elif status != "SUCCESS":
                overall_status = status
                last_error = err
                logger.error(f"Failed to fetch posts for @{username}: {err}")
                continue

            # Save to DB
            new_count = 0
            latest_post_id = None
            for post in posts:
                is_new = save_post(post)
                if is_new:
                    new_count += 1
                    if not latest_post_id:
                        latest_post_id = post["post_id"]

            logger.info(f"@{username}: {len(posts)} posts evaluated, {new_count} new posts saved.")
            total_new_posts += new_count

            if new_count > 0:
                notify_new_posts(username, new_count, latest_post_id)

        # 5. Record execution log
        log_execution(overall_status, total_new_posts, last_error)
        logger.info(f"Scraper run finished with status={overall_status}, new_posts={total_new_posts}, error={last_error}")
        logger.info("=" * 50)

        return overall_status, total_new_posts, last_error
    finally:
        _release_lock(lock_file)


def main():
    parser = argparse.ArgumentParser(description="Termux Instagram Collector Scraper")
    parser.add_argument(
        "--no-jitter",
        action="store_true",
        help="Skip jitter random delay and run immediately"
    )
    args = parser.parse_args()

    status, new_posts, err = run_scraper(apply_jitter=not args.no_jitter)
    if status != "SUCCESS":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
