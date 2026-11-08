"""GranularPhotoBooth — entry point.

Usage:
    python run.py [--config path/to/config.json]
    python run.py --doctor        # check camera, disk, dependencies and exit
"""

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
RESTART = 75  # internal: main() asks to be re-executed


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GranularPhotoBooth")
    parser.add_argument("--config", default="config.json",
                        help="Path to the JSON configuration file (default: config.json)")
    parser.add_argument("--doctor", action="store_true",
                        help="Check the setup (config, camera, disk, dependencies) and exit")
    parser.add_argument("--port", type=int, help="Override app.port")
    parser.add_argument("--camera", help="Override camera.backend (webcam, dslr_gphoto2, ip_cam, dummy)")
    return parser


def _setup_logging(config: dict) -> None:
    app_cfg = config.get("app", {})
    level = logging.DEBUG if app_cfg.get("debug") else logging.INFO
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    log_file = app_cfg.get("log_file")
    if log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError as exc:
            logging.getLogger("run").warning("Cannot write log file %s: %s", log_file, exc)
    # Quiet noisy libraries
    for noisy in ("aiohttp.access", "watchfiles", "PIL", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _apply_overrides(config: dict, args) -> dict:
    if args.port:
        config["app"]["port"] = args.port
    if args.camera:
        config["camera"]["backend"] = args.camera
    return config


async def main(config_path: str, args=None) -> int:
    from core.broker import Broker
    from core.config_loader import ConfigLoader
    from core.version import __version__
    from web.server import WebServer

    loader = ConfigLoader(config_path)
    try:
        config = loader.load()
    except (FileNotFoundError, ValueError) as exc:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("run").critical("%s", exc)
        return 2
    if args is not None:
        config = _apply_overrides(config, args)

    _setup_logging(config)
    logger = logging.getLogger("run")
    logger.info("GranularPhotoBooth %s — config %s", __version__, config_path)

    from web.admin import load_or_create_token

    broker = Broker(config)

    def _on_reload(new_cfg: dict) -> None:
        broker.reload_config(_apply_overrides(new_cfg, args) if args else new_cfg)

    try:
        admin_token = load_or_create_token(os.path.join(APP_DIR, "admin_token.txt"))
    except OSError as exc:
        logger.warning("Cannot store admin token (%s) — using a temporary one", exc)
        admin_token = None
    server = WebServer(config, broker, config_path=config_path, on_config=_on_reload,
                       admin_token=admin_token)
    shown_host = "localhost" if config["app"]["host"] in ("0.0.0.0", "127.0.0.1", "::") else config["app"]["host"]
    logger.info("Admin panel: http://%s:%s/admin#token=%s", shown_host, config["app"]["port"],
                server.admin_auth.token)
    loop = asyncio.get_running_loop()

    def _handle_signal(sig_name: str) -> None:
        logger.info("Received %s — shutting down gracefully…", sig_name)
        broker.request_shutdown()

    def _handle_sighup() -> None:
        logger.info("Received SIGHUP — reloading config…")
        try:
            broker.reload_config(_apply_overrides(loader.load(), args) if args else loader.load())
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Config reload failed: %s", exc)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig.name: _handle_signal(s))
        except NotImplementedError:
            pass
    try:
        loop.add_signal_handler(signal.SIGHUP, _handle_sighup)
    except (NotImplementedError, AttributeError):
        pass

    def _exception_handler(_loop, context) -> None:
        logger.error("Unhandled asyncio error: %s", context.get("message"),
                     exc_info=context.get("exception"))
    loop.set_exception_handler(_exception_handler)

    broker_task = asyncio.create_task(broker.run(), name="broker")
    server_task = asyncio.create_task(server.run(), name="web")
    watch_task = asyncio.create_task(loader.watch(_on_reload), name="config-watch")

    exit_code = 0
    done, _ = await asyncio.wait({broker_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if task.exception() is not None:
            logger.critical("Fatal error in %s: %s", task.get_name(), task.exception(),
                            exc_info=task.exception())
            exit_code = 1
    broker.request_shutdown()
    watch_task.cancel()
    pending = [t for t in (broker_task, server_task, watch_task) if not t.done()]
    if pending:
        _, still = await asyncio.wait(pending, timeout=10)
        for t in still:
            t.cancel()
    if broker.restart_requested:
        logger.info("Restarting…")
        return RESTART
    logger.info("Bye")
    return exit_code


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    config_path = os.path.abspath(args.config)
    if not os.path.isfile(config_path) and not os.path.isabs(args.config):
        config_path = os.path.join(APP_DIR, args.config)
    # Relative paths in config.json (output/, assets/, logs/) are relative to
    # the app folder, no matter where the command is started from.
    os.chdir(APP_DIR)
    sys.path.insert(0, APP_DIR)
    if args.doctor:
        from core.doctor import run_doctor
        sys.exit(run_doctor(config_path, args.camera))
    try:
        code = asyncio.run(main(config_path, args))
        if code == RESTART:
            logging.shutdown()
            os.execv(sys.executable, [sys.executable, os.path.join(APP_DIR, "run.py")] + sys.argv[1:])
        sys.exit(code)
    except KeyboardInterrupt:
        sys.exit(0)
