import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import auto_short


class ImageRenderSafetyTests(unittest.TestCase):
    def test_mislabeled_tiff_is_normalized_to_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "archive_photo.jpg"
            output = root / "output"
            output.mkdir()
            Image.new("RGB", (12, 8), (20, 40, 60)).save(source, format="TIFF")

            with patch.object(auto_short, "OUT_DIR", output):
                normalized = auto_short._prepare_raster_image_for_ffmpeg(source, 3)

            self.assertIsNotNone(normalized)
            self.assertEqual(normalized.suffix, ".png")
            self.assertTrue(normalized.exists())
            with Image.open(normalized) as img:
                self.assertEqual(img.format, "PNG")
                self.assertEqual(img.mode, "RGB")

    def test_valid_jpeg_is_used_without_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "photo.jpg"
            Image.new("RGB", (12, 8), (20, 40, 60)).save(source, format="JPEG")

            prepared = auto_short._prepare_raster_image_for_ffmpeg(source, 1)

            self.assertEqual(prepared, source)

    def test_corrupt_image_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "broken.jpg"
            source.write_bytes(b"not an image")

            prepared = auto_short._prepare_raster_image_for_ffmpeg(source, 1)

            self.assertIsNone(prepared)


if __name__ == "__main__":
    unittest.main()
