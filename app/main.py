import logging

from app.adapters.inbound.http.app import create_app
from app.bootstrap import build_container
from app.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = create_app(Settings(), build_container)
