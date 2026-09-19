import logging
import sys

from config.settings import settings


def setup_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    for noisy in ("yfinance", "urllib3", "httpx", "httpcore",
                  "peewee", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)