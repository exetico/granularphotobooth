"""Storage pipeline.

* **Local targets** (``local_disk``) are written first and synchronously —
  the photo is safe on disk before the guest sees it.
* **Remote targets** (S3/MinIO, FTP/NAS) run in background threads with
  retries.  A slow or offline network never blocks or breaks the booth.
"""

import concurrent.futures
import importlib
import logging
import shutil
import time
from typing import Any

logger = logging.getLogger(__name__)

_TARGET_MODULES: dict[str, str] = {
    "local_disk": "storage.local_disk",
    "s3_minio": "storage.s3_minio",
    "ftp_nas": "storage.ftp_nas",
}

_REMOTE_ATTEMPTS = 3


class StoragePipeline:
    """Loads enabled storage targets and fans images out to them."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._targets: list = []
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="storage"
        )
        self._load_targets()

    def _load_targets(self) -> None:
        storage_cfg = self._config.get("storage", {})
        enabled_names: list[str] = storage_cfg.get("targets", [])

        for name in enabled_names:
            target_cfg = storage_cfg.get(name, {})
            if not target_cfg.get("enabled", True):
                logger.debug("Storage target '%s' is disabled — skipping", name)
                continue

            module_path = _TARGET_MODULES.get(name)
            if module_path is None:
                logger.warning("Unknown storage target: '%s'", name)
                continue

            try:
                module = importlib.import_module(module_path)
                target = module.StorageTarget(target_cfg)
                if not target.is_configured():
                    logger.warning("Storage target '%s' is not configured — skipping", name)
                    continue
                self._targets.append(target)
                logger.info("Storage target loaded: %s", name)
            except Exception as exc:
                logger.exception("Failed to load storage target '%s': %s", name, exc)

    @property
    def local_targets(self) -> list:
        return [t for t in self._targets if getattr(t, "is_local", False)]

    @property
    def remote_targets(self) -> list:
        return [t for t in self._targets if not getattr(t, "is_local", False)]

    def store(self, image_bytes: bytes, metadata: dict) -> list[str]:
        """Upload to *all* targets and wait (kept for compatibility/tests)."""
        if not self._targets:
            logger.warning("No storage targets configured — image will not be saved")
            return []
        futures = {self._executor.submit(t.upload, image_bytes, metadata): t for t in self._targets}
        results: list[str] = []
        for future, target in futures.items():
            try:
                results.append(future.result(timeout=60))
            except Exception as exc:
                logger.exception("Storage target %s failed: %s", target.__class__.__module__, exc)
        return results

    def store_local(self, image_bytes: bytes, metadata: dict) -> list[str]:
        """Write to every local target now.  Raises if *no* local copy was made."""
        paths: list[str] = []
        errors: list[str] = []
        for target in self.local_targets:
            try:
                paths.append(target.upload(image_bytes, metadata))
            except Exception as exc:
                logger.exception("Local storage failed: %s", exc)
                errors.append(str(exc))
        if self.local_targets and not paths:
            raise RuntimeError("Could not save photo to disk: " + "; ".join(errors))
        return paths

    def store_remote_background(self, image_bytes: bytes, metadata: dict) -> None:
        """Queue uploads to remote targets; returns immediately."""
        for target in self.remote_targets:
            self._executor.submit(self._upload_with_retry, target, image_bytes, metadata)

    @staticmethod
    def _upload_with_retry(target, image_bytes: bytes, metadata: dict) -> str | None:
        name = target.__class__.__module__
        for attempt in range(1, _REMOTE_ATTEMPTS + 1):
            try:
                uri = target.upload(image_bytes, metadata)
                logger.info("Uploaded via %s: %s", name, uri)
                return uri
            except Exception as exc:
                logger.warning("Upload via %s failed (attempt %d/%d): %s", name, attempt, _REMOTE_ATTEMPTS, exc)
                time.sleep(2 * attempt)
        logger.error("Giving up upload via %s (photo is still saved locally)", name)
        return None

    def output_dirs(self) -> list[str]:
        dirs = []
        for t in self.local_targets:
            for kind in ("raw", "composite"):
                d = t.directory_for(kind)
                if d not in dirs:
                    dirs.append(d)
        return dirs

    def free_disk_mb(self) -> float | None:
        dirs = self.output_dirs()
        if not dirs:
            return None
        import os
        values = []
        for d in dirs:
            os.makedirs(d, exist_ok=True)
            values.append(shutil.disk_usage(d).free / (1024 * 1024))
        return min(values)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
