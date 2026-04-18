import asyncio
import os
import signal
import sys
import threading
import time

from src.ogn_server import Config, OGNClient, create_app, start_server_thread, TelegramBot


def setup_signal_handlers():
    """Set up graceful shutdown handler for SIGTERM."""
    def graceful_shutdown(signum, frame):
        print("Received SIGTERM, initiating graceful shutdown...")
        
        # Flush all file handles
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Small delay to allow child threads to finish current operations
        time.sleep(0.5)
        
        # Reap any zombie child processes
        try:
            while True:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
        except OSError:
            pass
        
        # Restart the application using execv (replaces current process)
        print("Restarting application...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    
    signal.signal(signal.SIGTERM, graceful_shutdown)


def main():
    setup_signal_handlers()
    
    serverdata = Config.load_serverdata()
    if serverdata is None:
        print("Error: serverserver.txt not found")
        return
    
    ogn_client = OGNClient(serverdata)
    
    app = create_app(ogn_client, serverdata)
    start_server_thread(app, serverdata)
    
    thread = threading.Thread(target=ogn_client.run, kwargs={"autoreconnect": True})
    thread.daemon = True
    thread.start()
    
    telegram_bot = TelegramBot()
    telegram_bot.run()


if __name__ == "__main__":
    main()
