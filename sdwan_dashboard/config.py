import os
from dotenv import load_dotenv

load_dotenv()

VMANAGE_HOST = os.getenv("VMANAGE_HOST", "localhost")
VMANAGE_PORT = int(os.getenv("VMANAGE_PORT", "8443"))
VMANAGE_USER = os.getenv("VMANAGE_USER", "admin")
VMANAGE_PASS = os.getenv("VMANAGE_PASS", "admin")
VMANAGE_VERIFY_SSL = os.getenv("VMANAGE_VERIFY_SSL", "false").lower() == "true"

# Set to "mock" to use demo data without a real vManage instance
MODE = os.getenv("SDWAN_MODE", "mock")

FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "30"))
