# run_bot.py

import logging
import uvicorn
from config.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Alpha Quant starting up.")
    uvicorn.run("web_app.app:app", host="127.0.0.1", port=8000, reload=False)