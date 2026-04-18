import asyncio
import signal
import sys
import threading

from src.ogn_server import Config, OGNClient, create_app, start_server_thread, TelegramBot


def setup_signal_handlers():
    """Set up graceful shutdown handler for SIGTERM."""
    def graceful_shutdown(signum, frame):
        print("Received SIGTERM, initiating graceful shutdown...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, graceful_shutdown)


def main():
    setup_signal_handlers()
    
    serverdata = Config.load_serverdata()
    if serverdata is None:
        print("Error: serverdata.txt not found")
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
