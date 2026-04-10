from __future__ import annotations

import logging
import os
import signal

from .config import load_config
from .service import GatewayService


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    service = GatewayService(config)
    try:
        service.run_forever()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("shutdown requested")
    finally:
        service.stop()


if __name__ == "__main__":
    main()
