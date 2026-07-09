"""JSON metadata persistence.

Writes are atomic and reads are retry-tolerant so callers do not observe a
partially written metadata file during filesystem queue operations.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from autovideo.domain.errors import ValidationError
from autovideo.domain.metadata import UploadMetadata
from autovideo.storage.schemas import validate_upload_metadata_dict


class MetadataNotFoundError(FileNotFoundError):
    """Raised when metadata.json is missing."""


class MetadataCorruptError(ValueError):
    """Raised when metadata.json exists but cannot be decoded."""


class JsonMetadataStore:
    """Read/write current metadata.json files without changing their shape."""

    filename = "metadata.json"

    def _validate_typed_shape(self, data: object) -> None:
        if not isinstance(data, dict):
            raise MetadataCorruptError("Metadata must be a JSON object")
        UploadMetadata.from_legacy_dict(data)

    def path_for(self, folder: Path) -> Path:
        return Path(folder) / self.filename

    def read_dict(self, folder: Path, *, validate: bool = False, retries: int = 2) -> dict:
        path = Path(folder) / self.filename
        if not path.exists():
            raise MetadataNotFoundError(str(path))

        last_error: Exception | None = None
        for attempt in range(max(retries, 0) + 1):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._validate_typed_shape(data)
                if validate:
                    validate_upload_metadata_dict(data)
                return data
            except (json.JSONDecodeError, PermissionError) as e:
                last_error = e
                if attempt < retries:
                    time.sleep(0.05)
                    continue
                break
            except ValidationError:
                raise
            except (MetadataCorruptError, TypeError, ValueError) as e:
                raise MetadataCorruptError(f"Invalid metadata file: {path}") from e

        raise MetadataCorruptError(f"Corrupt metadata file: {path}") from last_error

    def write_dict(self, folder: Path, metadata: dict, *, validate: bool = False) -> Path:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / self.filename
        self._validate_typed_shape(metadata)
        if validate:
            validate_upload_metadata_dict(metadata)

        with NamedTemporaryFile("w", encoding="utf-8", dir=folder, delete=False) as tmp:
            json.dump(metadata, tmp, indent=2, ensure_ascii=False)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        last_error: PermissionError | None = None
        for attempt in range(6):
            try:
                tmp_path.replace(path)
                return path
            except PermissionError as e:
                last_error = e
                if attempt < 5:
                    time.sleep(0.05)
                    continue
                break
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        if last_error:
            raise last_error
        return path

    def read(self, folder: Path) -> UploadMetadata:
        return UploadMetadata.from_legacy_dict(self.read_dict(folder))

    def write(self, folder: Path, metadata: UploadMetadata) -> Path:
        return self.write_dict(folder, metadata.to_legacy_dict())
