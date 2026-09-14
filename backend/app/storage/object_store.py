"""
MinIO object storage wrapper.
Handles bundle (raw uploaded data from edge node) and asset (processed PLY/GLB outputs) buckets.
Supports presigned PUT/GET URLs, chunked uploads, ETag verification.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ..config import Settings

logger = logging.getLogger(__name__)

try:
    from minio import Minio
    from minio.error import S3Error
    _MINIO_AVAILABLE = True
except ImportError:
    _MINIO_AVAILABLE = False
    logger.warning(
        "minio package not installed — falling back to local filesystem storage. "
        "Install with: pip install minio"
    )


@dataclass
class StoredObject:
    bucket: str
    key: str
    size_bytes: int
    etag: str
    version_id: Optional[str] = None


class ObjectStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Optional["Minio"] = None
        self._fs_root = settings.temp_dir / "minio_mock"
        self._use_fs = False

    def initialize(self) -> None:
        if self._client is not None or self._use_fs:
            return

        if _MINIO_AVAILABLE:
            try:
                self._client = Minio(
                    endpoint=self.settings.minio_endpoint,
                    access_key=self.settings.minio_access_key,
                    secret_key=self.settings.minio_secret_key,
                    secure=self.settings.minio_secure,
                )
                for bucket in (self.settings.minio_bundle_bucket, self.settings.minio_asset_bucket):
                    if not self._client.bucket_exists(bucket):
                        self._client.make_bucket(bucket)
                logger.info(f"MinIO connected: {self.settings.minio_endpoint}")
                return
            except Exception as exc:
                logger.warning(f"MinIO unreachable ({exc}) — falling back to FS storage")

        self._use_fs = True
        for bucket in (self.settings.minio_bundle_bucket, self.settings.minio_asset_bucket):
            (self._fs_root / bucket).mkdir(parents=True, exist_ok=True)
        logger.info(f"FS-backed object store ready at {self._fs_root}")

    # ------------------------------------------------------------------
    # Core PUT
    # ------------------------------------------------------------------
    def put_bytes(
        self,
        bucket: str,
        key: str,
        data: Union[bytes, bytearray, io.BytesIO, Path],
        content_type: str = "application/octet-stream",
        expected_etag: Optional[str] = None,
    ) -> StoredObject:
        if isinstance(data, Path):
            raw = data.read_bytes()
        elif isinstance(data, io.BytesIO):
            raw = data.getvalue()
        else:
            raw = bytes(data)

        etag = hashlib.sha256(raw).hexdigest()
        if expected_etag and expected_etag != etag:
            raise ValueError(
                f"ETag mismatch: expected {expected_etag[:12]}…, got {etag[:12]}…"
            )

        if self._use_fs:
            out = self._resolve(bucket, key)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(raw)
            return StoredObject(bucket=bucket, key=key, size_bytes=len(raw), etag=etag)

        assert self._client is not None
        stream = io.BytesIO(raw)
        result = self._client.put_object(
            bucket_name=bucket,
            object_name=key,
            data=stream,
            length=len(raw),
            content_type=content_type,
        )
        return StoredObject(
            bucket=bucket,
            key=key,
            size_bytes=len(raw),
            etag=etag,
            version_id=getattr(result, "version_id", None) or None,
        )

    def put_file(
        self,
        bucket: str,
        key: str,
        file_path: Path,
        content_type: str = "application/octet-stream",
        expected_etag: Optional[str] = None,
    ) -> StoredObject:
        return self.put_bytes(bucket, key, file_path, content_type, expected_etag)

    # ------------------------------------------------------------------
    # Core GET
    # ------------------------------------------------------------------
    def get_bytes(self, bucket: str, key: str) -> bytes:
        if self._use_fs:
            return self._resolve(bucket, key).read_bytes()
        assert self._client is not None
        response = self._client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def download_to_file(self, bucket: str, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._use_fs:
            src = self._resolve(bucket, key)
            dest.write_bytes(src.read_bytes())
            return dest
        assert self._client is not None
        self._client.fget_object(bucket, key, str(dest))
        return dest

    # ------------------------------------------------------------------
    # Existence / listing / deletion
    # ------------------------------------------------------------------
    def exists(self, bucket: str, key: str) -> bool:
        if self._use_fs:
            return self._resolve(bucket, key).exists()
        assert self._client is not None
        try:
            self._client.stat_object(bucket, key)
            return True
        except S3Error:
            return False

    def presigned_get_url(self, bucket: str, key: str, expires_seconds: int = 86400) -> str:
        if self._use_fs:
            return f"file://{self._resolve(bucket, key).resolve()}"
        assert self._client is not None
        return self._client.presigned_get_object(bucket, key, expires=expires_seconds)

    def presigned_put_url(self, bucket: str, key: str, expires_seconds: int = 3600) -> str:
        if self._use_fs:
            self._resolve(bucket, key).parent.mkdir(parents=True, exist_ok=True)
            return f"file://{self._resolve(bucket, key).resolve()}"
        assert self._client is not None
        return self._client.presigned_put_object(bucket, key, expires=expires_seconds)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve(self, bucket: str, key: str) -> Path:
        return self._fs_root / bucket / key.lstrip("/")

    @staticmethod
    def bundle_key(bundle_id: str, shot_id: str, role: str) -> str:
        return f"{bundle_id}/shots/{shot_id}/{role}"

    @staticmethod
    def manifest_key(bundle_id: str) -> str:
        return f"{bundle_id}/manifest.json"

    @staticmethod
    def asset_key(job_id: str, fmt: str) -> str:
        return f"{job_id}/scan.{fmt}"

    @staticmethod
    def etag_sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()
