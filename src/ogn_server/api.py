from flask import Flask, request, Response
import time
import os
import psutil
from pathlib import Path
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
        # Lightweight, non-blocking health checks with dependency validation
        now = datetime.utcnow()
        timestamp = now.isoformat() + "Z"

        # 1) OGN connection status
        conn_status = "disconnected"
        last_seen_iso = None
        client_obj = getattr(ogn_client, "client", None)
        if client_obj is not None:
            # Try common attribute names used by ogn client implementations
            connected = False
            for attr in ("connected", "is_connected"):
                if hasattr(client_obj, attr):
                    try:
                        connected = bool(getattr(client_obj, attr))
                        break
                    except Exception:
                        pass
            if connected:
                conn_status = "connected"
            # Derive last seen beacon time from in-memory beacons if available
            current_msgs = getattr(ogn_client, "current_messages", [])
            if current_msgs:
                try:
                    last_seen_dt = max(b.reference_timestamp for b in current_msgs)
                    last_seen_iso = last_seen_dt.isoformat()
                except Exception:
                    last_seen_iso = None

        # 2) DDB cache status
        cache_path = Path(getattr(Config, "DDB_CACHE_FILE", "ddb.json"))
        if cache_path.exists():
            try:
                mtime = cache_path.stat().st_mtime
                age_minutes = int((time.time() - mtime) / 60)
            except OSError:
                age_minutes = None
        else:
            age_minutes = None
        if age_minutes is None:
            ddb_status = "unknown"
        else:
            ddb_status = "fresh" if age_minutes <= getattr(Config, "DDB_CACHE_TTL_MINUTES", 60) else "stale"
        device_count = len(getattr(ogn_client, "ddb_devices", {}))

        # 3) Disk space check (non-blocking)
        disk = psutil.disk_usage("/")
        free_gb = round(disk.free / (1024**3), 2)
        total_gb = round(disk.total / (1024**3), 2)
        percent_free = round((disk.free / disk.total) * 100, 1)
        # Heuristic health for disk space
        if free_gb < 0.5 or percent_free < 5:
            disk_status = "unhealthy"
        elif free_gb < 2 or percent_free < 15:
            disk_status = "degraded"
        else:
            disk_status = "healthy"

        # 4) Beacon rate calculation from recent processing (non-blocking, in-memory only)
        msgs = getattr(ogn_client, "current_messages", []) or []
        if len(msgs) == 0:
            beacon_rate_status = "degraded"
            per_minute = 0.0
            avg_per_second = 0.0
        else:
            try:
                times = [m.reference_timestamp for m in msgs]
                oldest = min(times)
                newest = max(times)
                span_seconds = max((newest - oldest).total_seconds(), 0.001)
                per_second = len(msgs) / span_seconds
                per_minute = per_second * 60.0
                avg_per_second = per_second
            except Exception:
                per_minute = 0.0
                avg_per_second = 0.0
            beacon_rate_status = "healthy" if per_minute >= 1.0 else "degraded"

        # Determine overall status
        overall_unhealthy = (
            conn_status == "disconnected" or
            disk_status == "unhealthy" or
            ddb_status == "unknown" or
            ddb_status == "stale" and age_minutes is not None and age_minutes > getattr(Config, "DDB_CACHE_TTL_MINUTES", 60) or
            beacon_rate_status == "degraded"
        )
        overall_status = "unhealthy" if overall_unhealthy else ("degraded" if (
            beacon_rate_status == "degraded" or disk_status == "degraded" or ddb_status == "stale") else "healthy")

        checks = {
            "ogn_connection": {
                "status": conn_status,
                "last_seen": last_seen_iso,
            },
            "ddb_cache": {
                "status": ddb_status,
                "age_minutes": age_minutes if age_minutes is not None else None,
                "device_count": device_count,
            },
            "disk_space": {
                "free_gb": free_gb,
                "total_gb": total_gb,
                "percent_free": percent_free,
            },
            "beacon_rate": {
                "per_minute": round(per_minute, 2),
                "avg_per_second": round(avg_per_second, 2),
                "status": beacon_rate_status,
            },
        }

        return {
            "status": overall_status,
            "timestamp": timestamp,
            "checks": checks,
        }, 200
    
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
