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

# Initialize a JSON-formatted logger (used throughout main.py)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)


def setup_signal_handlers(ogn_client=None):
    """Set up graceful shutdown handler for SIGTERM."""
    def graceful_shutdown(signum, frame):
        # Capture OGN client reference for closure readability
        ogn_client_ref = ogn_client
        logger.info("Received SIGTERM, initiating graceful shutdown...")
        
        # Flush all file handles
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Small delay to allow child threads to finish current operations
        time.sleep(0.5)
        
        # Shutdown OGN client with explicit logs
        if ogn_client_ref is not None and hasattr(ogn_client_ref, "shutdown"):
            logger.info("Shutting down OGN client before restart...")
            try:
                # Use the outer ogn_client reference to perform the actual shutdown (as required by tests)
                ogn_client.shutdown()
            except Exception:
                logger.exception("OGN client shutdown raised an exception")
            logger.info("OGN client shutdown completed")

            # If a run thread reference was attached to the client, wait for it to terminate
            th = getattr(ogn_client_ref, "restart_thread", None)
            if isinstance(th, threading.Thread) and th.is_alive():
                logger.info("Waiting for OGN run thread to terminate before restart...")
                th.join(timeout=5)
                logger.info("OGN run thread termination status: alive=%s", th.is_alive())
        
        # Note: Zombie reaping is skipped here to avoid potential deadlocks during restart.
        # Restart the application using execv (replaces current process)
        logger.info("Restarting application... (execv will replace current process)")
        logger.info("os.execv arguments: %s", [sys.executable] + sys.argv)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    
    signal.signal(signal.SIGTERM, graceful_shutdown)


def main():
    serverdata = Config.load_serverdata()
    if serverdata is None:
        logger.error("Error: serverserver.txt not found")
        return

    ogn_client = OGNClient(serverdata)

    # Wire SIGTERM handler with access to the ogn_client for cleanup
    setup_signal_handlers(ogn_client)

    app = create_app(ogn_client, serverdata)
    start_server_thread(app, serverdata)
    
    thread = threading.Thread(target=ogn_client.run, kwargs={"autoreconnect": True})
    thread.daemon = True
    thread.start()
    # Expose the run thread on the OGN client for graceful shutdown coordination
    try:
        setattr(ogn_client, "restart_thread", thread)
    except Exception:
        # If the client doesn't support arbitrary attributes, ignore gracefully
        pass
    
    telegram_bot = TelegramBot()
    telegram_bot.run()


if __name__ == "__main__":
    main()
