import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# Set dummy env vars for testing before importing modules
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN"
os.environ["ADMIN_CHAT_ID"] = "987654321"
os.environ["TARGET_USERNAME"] = "test_user_1,test_user_2"
os.environ["JITTER_MAX_SECONDS"] = "0"

import db
import scraper
import session_manager
import telegram_notifier
from scraper import parse_post_node, fetch_user_posts, run_scraper
from telegram_bot import admin_only, get_disk_info, get_battery_info


class TestCollectorSystem(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_scraper.db"
        self.session_path = self.test_dir / "test_session.json"
        db.init_db(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_database_operations(self):
        # 1. Test is_post_exists on empty DB
        self.assertFalse(db.is_post_exists("post_123", self.db_path))

        # 2. Test save_post
        post_data = {
            "post_id": "post_123",
            "target_username": "test_user_1",
            "caption": "Hello #instagram",
            "media_type": "IMAGE",
            "media_urls": ["https://example.com/img.jpg"],
            "likes_count": 100,
            "comments_count": 5,
            "posted_at": "2026-08-22 10:00:00"
        }
        is_new = db.save_post(post_data, self.db_path)
        self.assertTrue(is_new)
        self.assertTrue(db.is_post_exists("post_123", self.db_path))
        self.assertEqual(db.get_posts_count("test_user_1", self.db_path), 1)

        # 3. Test duplicate save (should update counts and return False)
        post_data["likes_count"] = 150
        is_new_again = db.save_post(post_data, self.db_path)
        self.assertFalse(is_new_again)
        self.assertEqual(db.get_posts_count(db_path=self.db_path), 1)

        # 4. Test save_posts_bulk
        bulk_posts = [
            {
                "post_id": "post_123",  # duplicate
                "target_username": "test_user_1",
                "caption": "duplicate",
                "media_type": "IMAGE",
                "media_urls": ["https://example.com/img.jpg"],
                "likes_count": 160,
                "comments_count": 10,
                "posted_at": "2026-08-22 10:00:00"
            },
            {
                "post_id": "post_456",  # new
                "target_username": "test_user_2",
                "caption": "Video post",
                "media_type": "VIDEO",
                "media_urls": ["https://example.com/vid.mp4"],
                "likes_count": 50,
                "comments_count": 2,
                "posted_at": "2026-08-22 10:05:00"
            }
        ]
        new_cnt = db.save_posts_bulk(bulk_posts, self.db_path)
        self.assertEqual(new_cnt, 1)
        self.assertEqual(db.get_posts_count(db_path=self.db_path), 2)

        # 5. Test execution logs
        db.log_execution("SUCCESS", new_posts_count=2, error_message=None, db_path=self.db_path)
        db.log_execution("FAIL_SESSION", new_posts_count=0, error_message="Session expired", db_path=self.db_path)
        
        last_success = db.get_last_successful_scrape_time(self.db_path)
        self.assertIsNotNone(last_success)

        logs = db.get_latest_execution_logs(limit=5, db_path=self.db_path)
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["status"], "FAIL_SESSION")
        self.assertEqual(logs[1]["status"], "SUCCESS")

    def test_session_manager(self):
        # 1. Validation test
        self.assertFalse(session_manager.validate_sessionid_format(""))
        self.assertFalse(session_manager.validate_sessionid_format("short"))
        self.assertFalse(session_manager.validate_sessionid_format("invalid spaces here!"))
        self.assertTrue(session_manager.validate_sessionid_format("123456789%3Aabcdefghijklmnopqrstuvwxyz"))
        self.assertTrue(session_manager.validate_sessionid_format("valid_session_token_1234567890"))

        # 2. Atomic save test
        test_sessionid = "987654321%3Atest_atomic_session_token"
        saved = session_manager.save_session_atomic(test_sessionid, path=self.session_path)
        self.assertTrue(saved)
        self.assertTrue(self.session_path.exists())

        # 3. Read session test
        loaded_id = session_manager.get_sessionid(path=self.session_path)
        self.assertEqual(loaded_id, test_sessionid)

        session_data = session_manager.load_session(path=self.session_path)
        self.assertIn("updated_at", session_data)
        self.assertEqual(session_data["sessionid"], test_sessionid)

    def test_parse_post_node(self):
        # 1. Test Carousel post parsing
        node_carousel = {
            "__typename": "GraphSidecar",
            "shortcode": "C_test123",
            "edge_media_to_caption": {"edges": [{"node": {"text": "Carousel post caption"}}]},
            "edge_sidecar_to_children": {
                "edges": [
                    {"node": {"is_video": False, "display_url": "https://example.com/slide1.jpg"}},
                    {"node": {"is_video": True, "video_url": "https://example.com/slide2.mp4"}}
                ]
            },
            "edge_liked_by": {"count": 999},
            "edge_media_to_comment": {"count": 42},
            "taken_at_timestamp": 1700000000
        }
        parsed = parse_post_node(node_carousel, "target_user")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["post_id"], "C_test123")
        self.assertEqual(parsed["media_type"], "CAROUSEL")
        self.assertEqual(len(parsed["media_urls"]), 2)
        self.assertEqual(parsed["likes_count"], 999)
        self.assertEqual(parsed["comments_count"], 42)
        self.assertIn("2023", parsed["posted_at"])

        # 2. Test Video post parsing
        node_video = {
            "__typename": "GraphVideo",
            "is_video": True,
            "shortcode": "V_vid456",
            "video_url": "https://example.com/main_video.mp4",
            "edge_media_to_caption": {"edges": [{"node": {"text": "Video post"}}]},
            "edge_liked_by": {"count": 120},
            "edge_media_to_comment": {"count": 3},
            "taken_at_timestamp": 1700000000
        }
        parsed_vid = parse_post_node(node_video, "target_user")
        self.assertIsNotNone(parsed_vid)
        self.assertEqual(parsed_vid["media_type"], "VIDEO")
        self.assertEqual(parsed_vid["media_urls"], ["https://example.com/main_video.mp4"])

    @patch("requests.get")
    def test_fetch_user_posts_status_codes(self, mock_get):
        # Test 401 Unauthorized
        mock_resp_401 = MagicMock()
        mock_resp_401.status_code = 401
        mock_get.return_value = mock_resp_401
        status, posts, err = fetch_user_posts("user1", "dummy_session")
        self.assertEqual(status, "FAIL_SESSION")

        # Test 429 Rate Limit
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_get.return_value = mock_resp_429
        status, posts, err = fetch_user_posts("user1", "dummy_session")
        self.assertEqual(status, "FAIL_RATE_LIMIT")

        # Test 200 Success with posts
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = {
            "data": {
                "user": {
                    "edge_owner_to_timeline_media": {
                        "edges": [
                            {
                                "node": {
                                    "__typename": "GraphImage",
                                    "shortcode": "C_img999",
                                    "display_url": "https://example.com/img999.jpg",
                                    "edge_media_to_caption": {"edges": [{"node": {"text": "Test image"}}]},
                                    "edge_liked_by": {"count": 10},
                                    "edge_media_to_comment": {"count": 1},
                                    "taken_at_timestamp": 1700000000
                                }
                            }
                        ]
                    }
                }
            }
        }
        mock_get.return_value = mock_resp_200
        status, posts, err = fetch_user_posts("user1", "dummy_session")
        self.assertEqual(status, "SUCCESS")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["post_id"], "C_img999")

    def test_admin_filter(self):
        # Define dummy async handler
        called = False
        @admin_only
        async def dummy_handler(update, context):
            nonlocal called
            called = True
            return "OK"

        import asyncio

        # Case 1: Unauthorized user
        unauth_update = MagicMock()
        unauth_update.effective_user.id = 11111111
        unauth_update.effective_chat.id = 11111111
        context = MagicMock()
        
        asyncio.run(dummy_handler(unauth_update, context))
        self.assertFalse(called)

        # Case 2: Authorized admin
        auth_update = MagicMock()
        auth_update.effective_user.id = 987654321
        auth_update.effective_chat.id = 987654321
        
        asyncio.run(dummy_handler(auth_update, context))
        self.assertTrue(called)

    def test_disk_info(self):
        info = get_disk_info()
        self.assertIn("GB", info)

    def test_scraper_lock_prevents_concurrent_acquire(self):
        lock_path = self.test_dir / "test_scraper.lock"
        with patch("scraper.LOCK_FILE_PATH", lock_path):
            first = scraper._try_acquire_lock()
            self.assertIsNotNone(first)

            # A second attempt while the first is still held must fail.
            second = scraper._try_acquire_lock()
            self.assertIsNone(second)

            scraper._release_lock(first)

            # Once released, a new attempt should succeed again.
            third = scraper._try_acquire_lock()
            self.assertIsNotNone(third)
            scraper._release_lock(third)

    @patch("scraper.time.sleep", return_value=None)
    @patch("requests.get")
    def test_fetch_user_posts_unexpected_status_retries_then_fail_unknown(self, mock_get, mock_sleep):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_get.return_value = mock_resp

        status, posts, err = fetch_user_posts("user1", "dummy_session", max_retries=2)

        self.assertEqual(status, "FAIL_UNKNOWN")
        self.assertEqual(posts, [])
        self.assertEqual(mock_get.call_count, 2)
        self.assertTrue(mock_sleep.called)

    @patch("telegram_notifier.send_telegram_message")
    def test_notify_functions_escape_html(self, mock_send):
        mock_send.return_value = True

        telegram_notifier.notify_session_expired("<script>bad</script> & stuff")
        sent_text = mock_send.call_args[0][0]
        self.assertNotIn("<script>", sent_text)
        self.assertIn("&lt;script&gt;", sent_text)

        telegram_notifier.notify_new_posts("user<b>x", 1, "abc<>def")
        sent_text = mock_send.call_args[0][0]
        self.assertNotIn("<b>x", sent_text)
        self.assertNotIn("abc<>def", sent_text)


if __name__ == "__main__":
    unittest.main()
