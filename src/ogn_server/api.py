from flask import Flask, request
from waitress import serve
import threading
import logging

from .config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(ogn_client, serverdata: list) -> Flask:
    app = Flask(__name__)
    
    @app.route("/")
    def get_all():
        token = request.args.get('access_token')
        client_ip = request.remote_addr
        user_agent = request.headers.get('User-Agent', 'Unknown')
        
        if token is not None:
            if token == serverdata[Config.API_ACCESS_TOKEN_INDEX]:
                bounds = request.args.get('bounds')
                logger.info(f"Request from {client_ip} | UA: {user_agent} | bounds: {bounds}")
                if bounds is not None:
                    bounds_array = bounds.split(",")
                    if len(bounds_array) == 4:
                        return ogn_client.get_messages_in_bounds(bounds_array)
            else:
                logger.warning(f"Invalid token from {client_ip}")
        else:
            logger.warning(f"Missing token from {client_ip}")
        return ""
    
    return app


def run_server(app: Flask, serverdata: list):
    host = serverdata[Config.API_HOST_INDEX]
    serve(app, host=host, port=8000)


def start_server_thread(app: Flask, serverdata: list):
    threading.Thread(target=run_server, args=(app, serverdata)).start()
