import json
import logging
from datetime import datetime

from .config import get_log_level

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        if hasattr(record, 'client_ip'):
            log_data['client_ip'] = record.client_ip
        if hasattr(record, 'user_agent'):
            log_data['user_agent'] = record.user_agent
        if hasattr(record, 'bounds'):
            log_data['bounds'] = record.bounds
        return json.dumps(log_data)


# Module-level logger configured with JSONFormatter
logger = logging.getLogger("ogn_server")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(get_log_level())
