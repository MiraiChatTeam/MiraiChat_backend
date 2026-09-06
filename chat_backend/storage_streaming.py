import asyncio
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol


class AsyncUploadFile(Protocol):
    async def read(self, size: int) -> bytes: ...
    async def close(self) -> None: ...


UPLOAD_CHUNK_BYTES = 1024 * 1024
PARTIAL_FILE_MAX_AGE_SECONDS = 6 * 60 * 60


class UploadSizeExceeded(Exception):
    pass


@dataclass(frozen=True)
class StreamedUpload:
    partial_path: str
    size_bytes: int
    sha256_hex: str


def incoming_directory(upload_dir: str) -> str:
    path = os.path.join(upload_dir, ".incoming")
    os.makedirs(path, exist_ok=True)
    return path


async def stream_upload_to_partial(
    upload: AsyncUploadFile,
    upload_dir: str,
    file_id: str,
    *,
    max_bytes: int,
    chunk_bytes: int = UPLOAD_CHUNK_BYTES,
) -> StreamedUpload:
    if max_bytes <= 0:
        await upload.close()
        raise UploadSizeExceeded("Upload limit exhausted")

    partial_path = os.path.join(incoming_directory(upload_dir), f"{file_id}.part")
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with open(partial_path, "xb") as output:
            while True:
                chunk = await upload.read(chunk_bytes)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise UploadSizeExceeded("Upload exceeds allowed size")
                digest.update(chunk)
                await asyncio.to_thread(output.write, chunk)
            await asyncio.to_thread(output.flush)
            await asyncio.to_thread(os.fsync, output.fileno())
    except BaseException:
        try:
            os.remove(partial_path)
        except FileNotFoundError:
            pass
        raise
    finally:
        await upload.close()

    if size_bytes <= 0:
        try:
            os.remove(partial_path)
        except FileNotFoundError:
            pass
        raise UploadSizeExceeded("Empty uploads are not allowed")

    return StreamedUpload(
        partial_path=partial_path,
        size_bytes=size_bytes,
        sha256_hex=digest.hexdigest(),
    )


def commit_partial_file(partial_path: str, upload_dir: str, file_id: str) -> str:
    final_path = os.path.join(upload_dir, file_id)
    os.replace(partial_path, final_path)
    return final_path


def remove_file_if_present(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def cleanup_stale_partial_files(
    upload_dir: str,
    *,
    max_age_seconds: int = PARTIAL_FILE_MAX_AGE_SECONDS,
) -> int:
    directory = Path(incoming_directory(upload_dir))
    cutoff = time.time() - max_age_seconds
    removed = 0
    for partial in directory.glob("*.part"):
        try:
            if partial.stat().st_mtime < cutoff:
                partial.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed
