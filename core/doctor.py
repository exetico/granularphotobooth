"""``python run.py --doctor`` — checks the installation and prints a report."""

import importlib
import os
import shutil
import sys

OK, WARN, FAIL = "\033[32m✔\033[0m", "\033[33m!\033[0m", "\033[31m✘\033[0m"


def _line(mark: str, text: str) -> None:
    print(f" {mark} {text}")


def run_doctor(config_path: str, backend_override: str | None = None) -> int:
    from core.config_loader import ConfigLoader
    from core.paths import output_dirs
    from core.version import __version__

    problems = 0
    print(f"GranularPhotoBooth {__version__} — setup check\n")

    v = sys.version_info
    if v >= (3, 10):
        _line(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        _line(FAIL, f"Python {v.major}.{v.minor} — 3.10 or newer is required")
        problems += 1

    for mod, why in [("aiohttp", "web server"), ("cv2", "webcam / IP camera"), ("PIL", "layouts"),
                     ("jsonschema", "config validation"), ("watchfiles", "config hot-reload (optional)"),
                     ("pynput", "global keyboard trigger (optional)"), ("gphoto2", "DSLR (optional)"),
                     ("boto3", "S3 upload (optional)")]:
        try:
            importlib.import_module(mod)
            _line(OK, f"{mod} installed ({why})")
        except Exception:
            optional = "optional" in why
            _line(WARN if optional else FAIL, f"{mod} missing ({why})")
            problems += 0 if optional else 1

    try:
        config = ConfigLoader(config_path).load()
        if backend_override:
            config["camera"]["backend"] = backend_override
        _line(OK, f"Config valid: {config_path}")
    except Exception as exc:
        _line(FAIL, f"Config: {exc}")
        return 1

    for d in output_dirs(config):
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".write_test")
            with open(test, "wb") as fh:
                fh.write(b"ok")
            os.unlink(test)
            free = shutil.disk_usage(d).free / 1024 / 1024
            min_free = config["app"].get("min_free_disk_mb", 0)
            mark = OK if free >= min_free else FAIL
            problems += 0 if free >= min_free else 1
            _line(mark, f"Output folder writable: {d} ({free:,.0f} MB free)")
        except OSError as exc:
            _line(FAIL, f"Output folder {d}: {exc}")
            problems += 1

    comp = config["compositor"]
    if comp.get("enabled") and comp.get("template_map"):
        try:
            from compositor.grid_layout import load_layout
            layout = load_layout(comp["template_map"])
            shots = config["sequence"]["shots"]
            slots = len(layout["slots"])
            _line(OK if slots >= shots else WARN, f"Layout {comp['template_map']}: {slots} slots for {shots} shots")
            for key in ("template_image", "overlay_image"):
                if comp.get(key) and not os.path.isfile(comp[key]):
                    _line(WARN, f"compositor.{key} not found: {comp[key]}")
        except Exception as exc:
            _line(FAIL, f"Layout: {exc}")
            problems += 1

    backend = config["camera"]["backend"]
    print(f"\nCamera backend: {backend}")
    from cameras.manager import BACKEND_MODULES
    try:
        module = importlib.import_module(BACKEND_MODULES[backend])
        cls = module.CameraBackend
        if not cls.is_available():
            _line(FAIL, "Camera not detected")
            if backend == "dslr_gphoto2":
                print("    • Is the camera on, USB connected, and not in mass-storage mode?")
                print("    • Try:  gphoto2 --auto-detect")
                if sys.platform == "darwin":
                    print("    • macOS: close Photos / Image Capture; run: killall PTPCamera")
            problems += 1
        else:
            cam = cls(config["camera"]["backends"].get(backend, {}))
            cam.initialize()
            data = cam.capture()
            cam.release()
            _line(OK, f"Test photo taken ({len(data) / 1024:,.0f} KB)")
    except Exception as exc:
        _line(FAIL, f"Camera test failed: {exc}")
        if sys.platform == "darwin" and backend == "webcam":
            print("    • macOS: allow camera access for your terminal in System Settings → Privacy")
        problems += 1

    host, port = config["app"]["host"], config["app"]["port"]
    import socket
    with socket.socket() as s:
        try:
            s.bind((host, port))
            _line(OK, f"Port {port} is free")
        except OSError:
            _line(WARN, f"Port {port} is in use (is the booth already running?)")

    print()
    if problems:
        print(f"{problems} problem(s) found.")
        return 1
    print("All good — start the booth with:  python run.py")
    return 0
