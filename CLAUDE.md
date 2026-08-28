# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Termux-Instagram-Collector: a small Python system meant to run unattended on an Android device inside Termux. It periodically scrapes public post metadata for configured Instagram accounts via the Instagram web API (using a manually-supplied `sessionid` cookie, not the official API), stores results in SQLite, and exposes a Telegram bot for remote status checks, session-cookie rotation, and on-demand runs. Deployment model is `git pull` on a cron schedule directly on the device — there is no build/package step.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full test suite
python -m unittest discover tests

# Run a single test file / case / method
python -m unittest tests.test_collector
python -m unittest tests.test_collector.TestCollectorSystem.test_parse_post_node

# Run the scraper once, manually (bypasses cron)
python scraper.py --no-jitter

# Run the Telegram bot daemon (long-polling, stays in foreground)
python telegram_bot.py

# Initialize/inspect the SQLite schema directly
python db.py

# Start/stop both services together (Termux-oriented, but shell-portable)
./start_services.sh
./stop_services.sh
```

There is no linter or formatter configured in this repo.

Tests set dummy env vars (`TELEGRAM_BOT_TOKEN`, `ADMIN_CHAT_ID`, `TARGET_USERNAME`, `JITTER_MAX_SECONDS=0`) at the top of `tests/test_collector.py` before importing app modules — because `config.py` reads env vars at import time, any new test file that imports `config`/`db`/`scraper`/`telegram_bot` needs those env vars set first too.

## Architecture

**Config is loaded once at import time.** `config.py` reads `.env` (via `python-dotenv`, falling back to a hand-rolled parser if the package isn't installed yet) and derives all paths/settings as module-level constants (`DB_PATH`, `SESSION_FILE_PATH`, `TARGET_USERNAMES`, etc.). Every other module imports these constants directly rather than reading `os.environ` itself. `validate_config(require_telegram, require_target)` is called separately by each entry point (`scraper.py` requires target only; `telegram_bot.py` requires telegram only) since the two processes have different hard requirements.

**Two independent entry points share the same DB/session state:**
- `scraper.py` — run by cron (see README for the crontab line, which also does `git pull --rebase` before running). `run_scraper()` is the orchestration function: init DB → validate config → optional random jitter sleep (anti-detection) → load+validate session cookie → loop over `TARGET_USERNAMES` calling `fetch_user_posts()` → `save_post()` each result → `log_execution()` a summary row → fire Telegram notifications on new posts / session failure / rate limit. Every run records one row in `execution_logs` regardless of outcome.
- `telegram_bot.py` — long-running daemon, admin-gated (all handlers wrapped in `@admin_only`, which silently drops any update not from `ADMIN_CHAT_ID`; unauthorized attempts are logged, not rejected loudly). `/run` calls the *same* `run_scraper()` via `loop.run_in_executor` so it doesn't block the asyncio event loop. `/session <id>` writes new session cookies through `session_manager.save_session_atomic()`.

**Session cookie handling is intentionally atomic.** `session_manager.py` never writes `session.json` in place — it writes to a `.json.tmp` sibling, fsyncs, then `os.replace()`s over the real file, so a crash mid-write (or a concurrent cron run) can't corrupt the session the bot depends on. `validate_sessionid_format()` is a cheap sanity check (charset + min length), not a live verification against Instagram — actual validity is only discovered when a scrape request comes back 401.

**Failure modes are typed as string status codes**, not exceptions, and propagate from `fetch_user_posts()` up through `run_scraper()` to both the cron log and Telegram notifications: `SUCCESS`, `FAIL_SESSION` (401 / login-required payload → triggers `notify_session_expired`), `FAIL_RATE_LIMIT` (429 → triggers `notify_rate_limit`), `FAIL_NETWORK` (timeout/connection error, retried with exponential backoff up to `max_retries`), `FAIL_UNKNOWN`, `FAIL_CONFIG`. When adding a new failure path, follow this pattern rather than raising — callers (bot, cron log formatting) branch on these strings.

**`db.py` upserts, it doesn't just insert.** `save_post()` returns `True` only for a genuinely new `post_id`; if the post already exists it updates `likes_count`/`comments_count` in place and returns `False`. `save_posts_bulk()`/the scraper loop use that boolean to count "new posts" for notifications — engagement-count updates on existing posts are silent by design.

**Instagram response parsing is isolated in `parse_post_node()`** (in `scraper.py`), which maps raw GraphQL-shaped node JSON (carousel/video/image variants, caption edges, engagement edges, `taken_at_timestamp`) into the flat dict shape `db.save_post()` expects. This is the piece most likely to break when Instagram changes its response schema — it fails soft (catches exceptions, returns `None`, logs) rather than aborting the whole run.

## Notes for changes

- `data/`, `logs/`, `.env`, and `session.json` are gitignored and expected to exist only on the deployed device — don't assume they're present when reasoning about the repo, and don't add real credentials to example files.
- Multiple target accounts are supported by comma-separating `TARGET_USERNAME` in `.env`; `TARGET_USERNAMES` (plural) is the parsed list everywhere in code.
- A `FAIL_SESSION` or `FAIL_RATE_LIMIT` on any one target account aborts the rest of the loop for that run (`break`, not `continue`) — other failure statuses just skip to the next username.
