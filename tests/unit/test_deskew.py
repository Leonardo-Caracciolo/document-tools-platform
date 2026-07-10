"""Tests for `app.core.services._deskew` — PR1 scope.

`deskew_and_crop` is exercised entirely against deterministic synthetic
fixtures from `tests.fixtures.scan_factory` (per spec's "Deskew/Crop
Testability" requirement) — no real photographed image, no CI flakiness
tied to photo quality.

Both a NEGATIVE and a POSITIVE rotation angle are required (not
optional), including magnitudes past 45 degrees: the naive
`if angle < -45: angle += 90` normalization — tried and rejected during
design — fails symmetrically for large-magnitude angles of BOTH signs
(roughly `|angle| > 45`), producing a width/height-swapped crop. A
suite that only tested small angles, or only one sign, would not have
caught that bug; see `sdd/scan-to-pdf/design`'s "Empirical status" (2).
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.core.services._deskew import MAX_AREA_RATIO, deskew_and_crop
from tests.fixtures.scan_factory import (
    make_frame_filling_document,
    make_rotated_document,
    make_uniform_image,
)

#: Recovered crop dimensions must match the fixture's known document
#: size within this many pixels — accounts for Otsu-threshold/Gaussian-
#: blur edge softening, not for any real inaccuracy in the angle math
#: (verified empirically at <1px across 15 angles during design).
_DIMENSION_TOLERANCE_PX = 6


@pytest.mark.parametrize("angle", [30.0, -30.0])
def test_deskew_and_crop_recovers_known_document_size(tmp_path, angle):
    """Both a positive and a negative rotation angle recover the known document size."""
    src, (expected_w, expected_h) = make_rotated_document(tmp_path / f"doc_{angle}.png", angle)
    dst = tmp_path / f"out_{angle}.png"

    confident = deskew_and_crop(src, dst)

    assert confident is True
    result = cv2.imread(str(dst))
    assert result is not None
    height, width = result.shape[:2]
    assert abs(width - expected_w) <= _DIMENSION_TOLERANCE_PX
    assert abs(height - expected_h) <= _DIMENSION_TOLERANCE_PX


@pytest.mark.parametrize("angle", [5.0, -5.0, 75.0, -75.0])
def test_deskew_and_crop_recovers_size_across_angle_range(tmp_path, angle):
    """Sweeps a few more angles across the range to guard against a narrow fix."""
    src, (expected_w, expected_h) = make_rotated_document(tmp_path / f"doc_{angle}.png", angle)
    dst = tmp_path / f"out_{angle}.png"

    confident = deskew_and_crop(src, dst)

    assert confident is True
    result = cv2.imread(str(dst))
    height, width = result.shape[:2]
    assert abs(width - expected_w) <= _DIMENSION_TOLERANCE_PX
    assert abs(height - expected_h) <= _DIMENSION_TOLERANCE_PX


def test_deskew_and_crop_degrades_gracefully_on_no_confident_contour(tmp_path):
    """A uniform-color image (no detectable document contour) passes through unchanged."""
    src = make_uniform_image(tmp_path / "uniform.png")
    dst = tmp_path / "uniform_out.png"

    confident = deskew_and_crop(src, dst)

    assert confident is False
    assert dst.is_file()
    source_pixels = cv2.imread(str(src))
    result_pixels = cv2.imread(str(dst))
    assert np.array_equal(source_pixels, result_pixels)


def test_deskew_and_crop_frame_filling_document_is_deskew_only(tmp_path):
    """A page that fills the frame (`area_ratio > MAX_AREA_RATIO`) is deskewed but not cropped."""
    src = make_frame_filling_document(tmp_path / "frame_filling.png")
    dst = tmp_path / "frame_filling_out.png"

    source_image = cv2.imread(str(src))

    confident = deskew_and_crop(src, dst)

    assert confident is False
    result_image = cv2.imread(str(dst))
    assert result_image is not None
    # Deskew-only: no crop, so canvas dimensions are preserved exactly.
    assert result_image.shape == source_image.shape


def test_deskew_and_crop_reads_source_path_with_space_in_filename(tmp_path):
    """`deskew_and_crop` correctly reads a source path containing a space in the filename."""
    spaced_dir = tmp_path / "dir with space"
    spaced_dir.mkdir()
    src, (expected_w, expected_h) = make_rotated_document(
        spaced_dir / "scan with space.png", 30.0
    )
    dst = spaced_dir / "output with space.png"

    confident = deskew_and_crop(src, dst)

    assert confident is True
    result = cv2.imread(str(dst))
    assert result is not None
    height, width = result.shape[:2]
    assert abs(width - expected_w) <= _DIMENSION_TOLERANCE_PX
    assert abs(height - expected_h) <= _DIMENSION_TOLERANCE_PX


def test_max_area_ratio_constant_is_the_confirmed_value():
    """Guards the empirically-confirmed threshold from an accidental future edit."""
    assert MAX_AREA_RATIO == 0.98
