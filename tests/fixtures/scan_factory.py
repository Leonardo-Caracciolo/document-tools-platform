"""Synthetic test-image factory for `_deskew.deskew_and_crop`'s test suite.

Confirmed during design (`sdd/scan-to-pdf/design`, "Empirical status"
(2)) as the exact methodology that surfaced and validated the fix for the
`cv2.minAreaRect` angle-normalization bug: a light document rectangle of
a KNOWN size, drawn on a larger, contrasting dark background, then
rotated by a KNOWN angle via `cv2.warpAffine` — giving `test_deskew.py`
a deterministic expected output to assert against, per spec's
"Deskew/Crop Testability" requirement (no real photographed image, no CI
flakiness tied to photo quality).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

#: Default document/canvas sizes (width, height) for `make_rotated_document`.
#: The canvas is deliberately much larger than the document on every side
#: so a rotated document is never clipped by the canvas edge (which would
#: corrupt the expected-size assertion) at any angle in [-89, 89].
_DEFAULT_DOC_SIZE = (600, 800)
_DEFAULT_CANVAS_SIZE = (1400, 1600)

#: Background/document fill colors — high contrast so Otsu thresholding
#: finds a clean boundary.
_BACKGROUND_COLOR = (20, 20, 20)
_DOCUMENT_COLOR = (240, 240, 240)


def make_rotated_document(
    path: Path,
    angle: float,
    doc_size: tuple[int, int] = _DEFAULT_DOC_SIZE,
    canvas_size: tuple[int, int] = _DEFAULT_CANVAS_SIZE,
) -> tuple[Path, tuple[int, int]]:
    """Write a light `doc_size` rectangle on a dark `canvas_size` canvas, rotated by `angle`.

    Returns `(path, doc_size)` — `doc_size` is the caller's expected,
    known document dimensions (width, height) that `deskew_and_crop`'s
    recovered crop should match within a small pixel tolerance, for both
    a positive and a negative `angle`.
    """
    doc_w, doc_h = doc_size
    canvas_w, canvas_h = canvas_size

    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:] = _BACKGROUND_COLOR

    left = (canvas_w - doc_w) // 2
    top = (canvas_h - doc_h) // 2
    canvas[top : top + doc_h, left : left + doc_w] = _DOCUMENT_COLOR

    center = (canvas_w / 2, canvas_h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(canvas, matrix, (canvas_w, canvas_h))

    cv2.imencode(path.suffix, rotated)[1].tofile(str(path))
    return path, doc_size


def make_uniform_image(
    path: Path,
    size: tuple[int, int] = (800, 1000),
    color: tuple[int, int, int] = (128, 128, 128),
) -> Path:
    """Write a uniform-color `size` image with no detectable document contour to `path`.

    Used to exercise `deskew_and_crop`'s no-confident-contour degradation
    path: Otsu thresholding a uniform-color image finds no boundary at
    all (`_deskew._largest_contour` returns `(None, 0.0)`).
    """
    width, height = size
    canvas = np.full((height, width, 3), color, dtype=np.uint8)
    cv2.imencode(path.suffix, canvas)[1].tofile(str(path))
    return path


def make_frame_filling_document(
    path: Path,
    canvas_size: tuple[int, int] = (1000, 1200),
    margin: int = 2,
) -> Path:
    """Write a light document that fills nearly all of `canvas_size` to `path`.

    `margin` is the dark border left on each side, kept small enough
    that the document's area ratio lands safely above
    `_deskew.MAX_AREA_RATIO` (0.98) even after `_deskew`'s Gaussian blur
    softens the boundary slightly — exercising the deskew-only (no-crop)
    branch. Not rotated: this fixture targets the area-ratio branch, not
    the angle-correction path (see `make_rotated_document` for that).
    """
    canvas_w, canvas_h = canvas_size
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:] = _BACKGROUND_COLOR
    canvas[margin : canvas_h - margin, margin : canvas_w - margin] = _DOCUMENT_COLOR
    cv2.imencode(path.suffix, canvas)[1].tofile(str(path))
    return path
