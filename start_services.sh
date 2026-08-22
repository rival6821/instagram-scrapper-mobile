#!/data/data/com.termux/files/usr/bin/sh
# Termux-Instagram-Collector: Service Start Script
# Initializes wake lock, crond, and telegram_bot background daemon.

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

echo "=========================================="
echo " Starting Termux Instagram Collector Services "
echo "=========================================="

# 1. Ensure required directories exist
mkdir -p data logs

# 2. Acquire Termux Wake-lock if running in Termux
if command -v termux-wake-lock >/dev/null 2>&1; then
    echo "[+] Enabling Termux Wake-Lock..."
    termux-wake-lock
fi

# 3. Start crond daemon if not already running
if ! pgrep -x "crond" > /dev/null; then
    echo "[+] Starting crond daemon..."
    crond
else
    echo "[*] crond daemon is already running."
fi

# 4. Check & start Telegram Bot Daemon
BOT_PID=$(pgrep -f "python.*telegram_bot.py")
if [ -z "$BOT_PID" ]; then
    echo "[+] Starting telegram_bot.py background daemon..."
    nohup python telegram_bot.py >> logs/bot.log 2>&1 &
    NEW_PID=$!
    echo "[+] Telegram Bot started with PID: $NEW_PID"
else
    echo "[*] telegram_bot.py is already running (PID: $BOT_PID)."
fi

echo "=========================================="
echo " Service Status Summary:"
echo " - Crond PID: $(pgrep -x crond || echo 'Not running')"
echo " - Bot PID:   $(pgrep -f 'python.*telegram_bot.py' || echo 'Not running')"
echo " Logs available at: $SCRIPT_DIR/logs/"
echo "=========================================="
