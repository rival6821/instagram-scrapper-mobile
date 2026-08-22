#!/data/data/com.termux/files/usr/bin/sh
# Termux-Instagram-Collector: Service Stop Script

echo "Stopping Telegram Bot Daemon..."
BOT_PID=$(pgrep -f "python.*telegram_bot.py")

if [ -n "$BOT_PID" ]; then
    kill $BOT_PID
    echo "Killed Telegram Bot PID: $BOT_PID"
else
    echo "Telegram Bot is not running."
fi
