from flask import Flask, request, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from waitress import serve
import threading
import logging
import json
from datetime import datetime
from prometheus_client import Counter, Histogram, generate_latest

from .config import Config
from .formatters import JSONFormatter


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'HTTP request latency', ['endpoint'])


def create_app(ogn_client, serverdata: list) -> Flask:
    app = Flask(__name__)
    
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["100 per minute"],
        storage_uri="memory://"
    )
    
    @app.route("/health")
    def health():
        return {"status": "healthy"}, 200
    
    @app.route("/metrics")
    def metrics():
        return Response(generate_latest(), mimetype='text/plain')
    
    @app.route("/")
    @limiter.limit("60 per minute")
    def get_all():
        token = request.args.get('access_token')
        client_ip = request.remote_addr
        user_agent = request.headers.get('User-Agent', 'Unknown')
        
        if token is not None:
            if token == serverdata[Config.API_ACCESS_TOKEN_INDEX]:
                bounds = request.args.get('bounds')
                extra = {"client_ip": client_ip, "user_agent": user_agent, "bounds": bounds}
                logger.info(f"API request", extra=extra)
                if bounds is not None:
                    bounds_array = bounds.split(",")
                    if len(bounds_array) == 4:
                        return ogn_client.get_messages_in_bounds(bounds_array)
            else:
                logger.warning(f"Invalid token from {client_ip}", extra={"client_ip": client_ip})
        else:
            logger.warning(f"Missing token from {client_ip}", extra={"client_ip": client_ip})
        return ""
    
    return app


def run_server(app: Flask, serverdata: list):
    host = serverdata[Config.API_HOST_INDEX]
    serve(app, host=host, port=8000)


def start_server_thread(app: Flask, serverdata: list):
    threading.Thread(target=run_server, args=(app, serverdata)).start()
