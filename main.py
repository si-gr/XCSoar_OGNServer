import asyncio
import os
import signal
import sys
import threading
import time
import logging
import json
from datetime import datetime

from src.ogn_server import Config, OGNClient, create_app, start_server_thread, TelegramBot
from src.ogn_server.formatters import JSONFormatter


def setup_signal_handlers(ogn_client=None,  telegram_bot=None):
    def graceful_shutdown(signum, frame):
        logger.info("Received SIGTERM, initiating graceful shutdown...")
        sys.stdout.flush()
        sys.stderr.flush()
        time.sleep(0.5)
        if ogn_client is not None and hasattr(ogn_client, "shutdown"):
            logger.info("Shutting down OGN client before restart...")
            try:
                ogn_client.shutdown()
            except Exception:
                logger.exception("OGN client shutdown raised an exception")
            logger.info("OGN client shutdown completed")
            th = getattr(ogn_client, "restart_thread", None)
            if isinstance(th, threading.Thread) and th.is_alive():
                logger.info("Waiting for OGN run thread to terminate before restart...")
                th.join(timeout=5)
                logger.info("OGN run thread termination status: alive=%s", th.is_alive())
        if telegram_bot is not None and hasattr(telegram_bot, "shutdown"):
            logger.info("Shutting down Telegram bot before restart...")
            try:
                telegram_bot.shutdown()
            except Exception:
                logger.exception("Telegram bot shutdown raised an exception")
            logger.info("Telegram bot shutdown completed")
        logger.info("Restarting application... (execv will replace current process)")
        logger.info("os.execv arguments: %s", [sys.executable] + sys.argv)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    
    signal.signal(signal.SIGTERM, graceful_shutdown)


def main():
    serverdata = Config.load_serverdata()
    if serverdata is None:
        logger.error("Error: serverdata.txt not found")
        return

    ogn_client = OGNClient(serverdata)

    app = create_app(ogn_client, serverdata)
    start_server_thread(app, serverdata)
    
    thread = threading.Thread(target=ogn_client.run, kwargs={"autoreconnect": True})
    thread.daemon = True
    thread.start()
    try:
        setattr(ogn_client, "restart_thread", thread)
    except Exception:
        pass
    telegram_bot = TelegramBot()
    bot_thread = threading.Thread(target=telegram_bot.run)
    bot_thread.daemon = True
    bot_thread.start()
    try:
        setattr(telegram_bot, "restart_thread", bot_thread)
    except Exception:
        pass
    setup_signal_handlers(ogn_client, telegram_bot)
    thread.join()
    bot_thread.join()


if __name__ == "__main__":
    main()
