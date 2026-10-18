"""Constants for the 3D Print Log integration."""

DOMAIN = "printlog"

DEFAULT_BASE_URL = "https://api.3dprintlog.com"
PROD_API_HOST = "api.3dprintlog.com"
PROD_WEB_BASE = "https://www.3dprintlog.com"

CONF_BASE_URL = "base_url"
CONF_AUTH_TYPE = "auth_type"
AUTH_TYPE_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 300
FORCED_SNAPSHOT_SECONDS = 300
SLOW_INTERVAL_SECONDS = 300
REQUEST_TIMEOUT = 15
EVENT_WINDOW_MINUTES = 60
SEEN_LIMIT = 50
