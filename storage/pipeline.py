"""Threaded storage pipeline.

Fans out the final image to every enabled ``StorageTarget`` in parallel
background threads so the main event loop is never blocked.
"""

import importlib
import logging
import concurrent.futures
from typing import Any

logger = logging.getLogger(__name__)

_TARGET_MODULES: dict[str, str] = {
    "local_disk": "storage.local_disk",
    "s3_minio": "storage.s3_minio",
    "ftp_nas": "storage.ftp_nas",
}


class StoragePipeline:
    """Loads enabled storage targets and uploads to all of them concurrently."""

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

    def store(self, image_bytes: bytes, metadata: dict) -> list[str]:
        """Upload ``image_bytes`` to all enabled targets concurrently.

        Returns a list of result URIs / paths from each target.
        Failures in individual targets are logged but do not propagate.
        """
        if not self._targets:
            logger.warning("No storage targets configured — image will not be saved")
            return []

        futures = {
            self._executor.submit(t.upload, image_bytes, metadata): t
            for t in self._targets
        }

        results: list[str] = []
        for future, target in futures.items():
            try:
                path = future.result(timeout=60)
                results.append(path)
                logger.info("Stored via %s: %s", target.__class__.__module__, path)
            except Exception as exc:
                logger.exception(
                    "Storage target %s failed: %s", target.__class__.__module__, exc
                )

        return results

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
