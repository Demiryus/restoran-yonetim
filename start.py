"""
Railway startup: bot + web app aynı anda çalışır.
"""
import threading
import subprocess
import sys
import os

def run_bot():
    subprocess.run([sys.executable, "bot.py"])

def run_web():
    port = os.getenv("PORT", "8000")
    subprocess.run([
        sys.executable, "-m", "uvicorn", "web_app:app",
        "--host", "0.0.0.0",
        "--port", port,
    ])

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    run_web()
