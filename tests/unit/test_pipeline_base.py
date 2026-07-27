"""Tests for shared pipeline CLI pass-through behavior."""

from __future__ import annotations

import unittest

from tests.unit import _path  # noqa: F401

from _pipeline_base import build_stage1_extra, build_standard_parser


class PipelineBaseTests(unittest.TestCase):
    def test_reuse_script_flag_is_forwarded_to_generator(self) -> None:
        parser = build_standard_parser()
        args = parser.parse_args(["Topic", "--reuse-script", "--no-interactive"])

        extra = build_stage1_extra(args)

        self.assertIn("--reuse-script", extra)
        self.assertIn("--no-interactive", extra)


if __name__ == "__main__":
    unittest.main()
