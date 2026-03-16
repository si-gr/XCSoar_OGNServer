from flask import Flask, request
from waitress import serve
import threading

from .config import Config


def create_app(ogn_client, serverdata: list) -> Flask:
    app = Flask(__name__)
    
    @app.route("/")
    def get_all():
        token = request.args.get('access_token')
        if token is not None:
            if token == serverdata[Config.API_ACCESS_TOKEN_INDEX]:
                bounds = request.args.get('bounds')
                if bounds is not None:
                    bounds_array = bounds.split(",")
                    if len(bounds_array) == 4:
                        return ogn_client.get_messages_in_bounds(bounds_array)
        return ""
    
    return app


def run_server(app: Flask, serverdata: list):
    host = serverdata[Config.API_HOST_INDEX]
    serve(app, host=host, port=8000)


def start_server_thread(app: Flask, serverdata: list):
    threading.Thread(target=run_server, args=(app, serverdata)).start()
