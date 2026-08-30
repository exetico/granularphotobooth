"""GranularPhotoBooth — entry point.

Usage:
    python run.py [--config path/to/config.json]
"""

import argparse
import asyncio
import logging
import signal
import sys

from core.config_loader import ConfigLoader
from core.broker import Broker
from web.server import WebServer


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GranularPhotoBooth")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the JSON configuration file (default: config.json)",
    )
    return parser


async def main(config_path: str) -> None:
    loader = ConfigLoader(config_path)
    config = loader.load()

    log_level = logging.DEBUG if config["app"].get("debug") else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("run")
    logger.info("Configuration loaded from %s", config_path)
    logger.debug("Full config: %s", config)

    broker = Broker(config)
    server = WebServer(config, broker)

    loop = asyncio.get_running_loop()

    def _handle_signal(sig_name: str) -> None:
        logger.info("Received %s — shutting down gracefully…", sig_name)
        broker.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig.name: _handle_signal(s))
        except NotImplementedError:
            # Windows does not support add_signal_handler
            pass

    logger.info(
        "Starting GranularPhotoBooth on http://%s:%s",
        config["app"]["host"],
        config["app"]["port"],
    )

    await asyncio.gather(
        broker.run(),
        server.run(),
    )


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    try:
        asyncio.run(main(args.config))
    except KeyboardInterrupt:
        sys.exit(0)
