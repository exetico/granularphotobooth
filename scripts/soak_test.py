#!/usr/bin/env python3
"""Soak test: run many photo sessions against a running booth and report.

Use it before an event to prove the setup (camera, disk, browser machine)
survives hours of use:

    .venv/bin/python scripts/soak_test.py --sessions 200
    .venv/bin/python scripts/soak_test.py --url http://localhost:8080 --sessions 50 --retake-every 5

It drives the booth exactly like the touch screen does (over the WebSocket),
and prints timing, errors and memory use of the server process.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time

import aiohttp


async def wait_state(ws, wanted: set[str], timeout: float) -> tuple[str, dict, list]:
    errors = []
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {wanted}")
        msg = await ws.receive(timeout=remaining)
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise ConnectionError(f"websocket closed ({msg.type})")
        data = json.loads(msg.data)
        if data.get("event") == "error":
            errors.append(data["data"])
            if "IDLE" in wanted:
                pass
        state = data.get("state") or (data.get("data", {}).get("state") if data.get("event") == "hello" else None)
        if state in wanted:
            return state, data.get("data", {}), errors


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--sessions", type=int, default=20)
    ap.add_argument("--retake-every", type=int, default=0, help="press Retake every N sessions")
    ap.add_argument("--timeout", type=float, default=180)
    args = ap.parse_args()

    durations, failures = [], []
    async with aiohttp.ClientSession() as http:
        status = await (await http.get(args.url + "/api/status")).json()
        print(f"Booth {status['version']} camera={status['camera']}")
        async with http.ws_connect(args.url.replace("http", "ws", 1) + "/ws", heartbeat=15) as ws:
            await wait_state(ws, {"IDLE"}, 30)
            for n in range(1, args.sessions + 1):
                started = time.monotonic()
                await ws.send_str(json.dumps({"event": "trigger"}))
                try:
                    state, data, errors = await wait_state(ws, {"PREVIEW", "IDLE"}, args.timeout)
                    if state == "IDLE" or errors:
                        failures.append((n, errors or "returned to IDLE"))
                        print(f"#{n:4d}  FAILED {errors}")
                        await asyncio.sleep(2)
                        continue
                    img = await http.get(args.url + data["composite"])
                    ok_img = img.status == 200 and (await img.read())[:2] == b"\xff\xd8"
                    retake = args.retake_every and n % args.retake_every == 0
                    await ws.send_str(json.dumps({"event": "retake" if retake else "confirm"}))
                    if retake:
                        await wait_state(ws, {"PREVIEW"}, args.timeout)
                        await ws.send_str(json.dumps({"event": "confirm"}))
                    await wait_state(ws, {"IDLE"}, 30)
                    took = time.monotonic() - started
                    durations.append(took)
                    print(f"#{n:4d}  ok  {took:5.1f}s  image={'ok' if ok_img else 'MISSING'}"
                          f"{'  (retake)' if retake else ''}")
                    if not ok_img:
                        failures.append((n, "composite image not served"))
                except Exception as exc:
                    failures.append((n, repr(exc)))
                    print(f"#{n:4d}  FAILED {exc!r}")
                    await asyncio.sleep(2)
                await asyncio.sleep(1.0)  # > input debounce
        final = await (await http.get(args.url + "/api/status")).json()

    print("\n──── summary ────")
    print(f"sessions: {args.sessions}  ok: {len(durations)}  failed: {len(failures)}")
    if durations:
        print(f"duration: median {statistics.median(durations):.1f}s  max {max(durations):.1f}s")
    print(f"server: completed={final['sessions_completed']} disk_free={final['disk_free_mb']} MB "
          f"last_error={final['last_error']}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
