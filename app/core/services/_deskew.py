"""Raw OpenCV deskew/crop primitive for photographed document pages.

`deskew_and_crop` is the single-purpose function `ScanService` (PR2)
calls once per input image, per `sdd/scan-to-pdf/design`'s Decision 1: a
plain module-level function rather than a `Protocol`-based provider,
since — unlike `OCRProvider`/`DocumentConverterProvider` — no second
implementation is planned for deskew. Kept in its own module, separate
from `scan_service.py`, so the raw-`cv2` pixel math here can be reviewed
and unit-tested in isolation from `ScanService`'s cross-service
orchestration/exception-containment logic (PR2).

Confirmed empirically during design (`sdd/scan-to-pdf/design`,
"Empirical status", installed `opencv-python-headless==5.0.0.93`):
`cv2.findContours` returns a 2-tuple `(contours, hierarchy)`, and — most
importantly — `cv2.minAreaRect`'s angle needs correcting with
`norm_angle = angle + 90 if rw > rh else angle`, NOT the more commonly
seen `if angle < -45: angle += 90`. The latter was implemented and
tested during design and found WRONG for large-magnitude rotation
angles of EITHER sign (see `_normalize_angle`'s docstring before
"simplifying" this back).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

#: `area_ratio` (largest contour area / full image area) thresholds that
#: gate the three-way branch in `deskew_and_crop`. Below `MIN_AREA_RATIO`,
#: no document boundary was confidently found -> pass through as-is.
#: Above `MAX_AREA_RATIO`, the page already fills the frame -> deskew
#: only, skip cropping (there is no border left to crop to). Confirmed
#: reasonable starting values during design: a frame-filling synthetic
#: fixture (980x1180 of a 1000x1200 canvas) measured `area_ratio ==
#: 0.964`, inside this band. Non-blocking, tunable against real photos.
MIN_AREA_RATIO = 0.20
MAX_AREA_RATIO = 0.98

#: Gaussian blur kernel applied before Otsu thresholding, to suppress
#: sensor noise/JPEG artifacts that would otherwise fragment the
#: document's contour.
_BLUR_KERNEL = (5, 5)


def _largest_contour(gray: np.ndarray) -> tuple[np.ndarray | None, float]:
    """Return the largest external contour in `gray` and its area ratio.

    Returns `(None, 0.0)` if no contour is found at all (e.g. a uniform-
    color image) rather than raising — `deskew_and_crop` treats that the
    same as an under-`MIN_AREA_RATIO` result.
    """
    blurred = cv2.GaussianBlur(gray, _BLUR_KERNEL, 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, 0.0
    largest = max(contours, key=cv2.contourArea)
    area_ratio = cv2.contourArea(largest) / (gray.shape[0] * gray.shape[1])
    return largest, area_ratio


def _normalize_angle(rect: tuple[tuple[float, float], tuple[float, float], float]) -> float:
    """Return the `cv2.getRotationMatrix2D`-ready angle for `minAreaRect`'s `rect`.

    `rect` is `(center, (rw, rh), angle)`, as returned by
    `cv2.minAreaRect`. The empirically CONFIRMED correct correction is:

        norm_angle = angle + 90 if rw > rh else angle

    NOT the commonly-seen `if angle < -45: angle += 90`. That naive
    version was implemented and tested during design across 15 synthetic
    rotation angles from -89 to +89 degrees: it failed symmetrically for
    large-magnitude angles of BOTH signs (roughly `|angle| > 45`, e.g.
    -89/-76/-63/-51 AND +51/+64/+76/+89 all produced width/height-swapped
    crops), not "negative angles only" — a real, reproducible defect
    caught before any code shipped. The `rw > rh` formula compares the
    box's own measured width/height rather than the angle's numeric sign
    or magnitude, so it is robust regardless of which numeric convention
    the installed OpenCV version uses for `minAreaRect`'s angle.
    Re-validated at a 100% match rate (every recovered crop within 1px of
    the known original size) across the same 15 angles.

    Do not "simplify" this back to `angle < -45` — that was tried and is
    wrong.
    """
    _, (rw, rh), angle = rect
    return angle + 90 if rw > rh else angle


def deskew_and_crop(src: Path, dst: Path) -> bool:
    """Deskew and, if confident, crop the document in `src`, writing to `dst`.

    Reads `src` via `cv2.imdecode(np.fromfile(...))` rather than
    `cv2.imread` directly — confirmed during design to handle a Windows
    path containing spaces (this repo's own working directory has one),
    and defense-in-depth for non-ASCII paths on end-users' machines.
    Writes `dst` symmetrically, via `cv2.imencode(...).tofile(...)`.

    Locates the largest external contour after grayscale + Gaussian blur
    + Otsu threshold, and branches three ways on `area_ratio` (that
    contour's area / the full image's area):

    - `area_ratio < MIN_AREA_RATIO` (or no contour at all): no document
      boundary confidently found. `dst` is a pass-through copy of `src`,
      unmodified. Returns `False`.
    - `area_ratio > MAX_AREA_RATIO`: the page already fills the frame —
      there is no border left to crop to. The angle correction is still
      applied (deskew-only), but no crop. Returns `False`.
    - Otherwise: apply `_normalize_angle`'s corrected rotation, then
      re-measure the boundary on the now-deskewed image and crop to it.
      Returns `True`.

    A `False` return is NOT an error — it signals a best-effort,
    degraded pass-through so `ScanService` (PR2) can log a per-image
    WARNING without failing the batch, per spec's "Per-Image Best-Effort
    Degradation" requirement. Any decode/processing failure below this
    function's control (a raw `cv2` exception) is left to propagate
    unwrapped — `ScanService` is the layer responsible for translating it
    to a domain exception (`_translate_deskew_errors`, PR2).
    """
    image = cv2.imdecode(np.fromfile(str(src), dtype=np.uint8), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    largest, area_ratio = _largest_contour(gray)

    if largest is None or area_ratio < MIN_AREA_RATIO:
        result = image
        confident = False
    else:
        rect = cv2.minAreaRect(largest)
        center = rect[0]
        norm_angle = _normalize_angle(rect)
        matrix = cv2.getRotationMatrix2D(center, norm_angle, 1.0)
        deskewed = cv2.warpAffine(image, matrix, (image.shape[1], image.shape[0]))

        if area_ratio > MAX_AREA_RATIO:
            # Page fills the frame: no border left to crop to. Deskew
            # only, still signal degradation to the caller.
            result = deskewed
            confident = False
        else:
            # Re-measure the boundary on the now-deskewed image — the
            # original contour's coordinates no longer match after
            # rotation, so cropping to a transformed version of the old
            # contour would be wrong.
            deskewed_gray = cv2.cvtColor(deskewed, cv2.COLOR_BGR2GRAY)
            reboxed, _ = _largest_contour(deskewed_gray)
            if reboxed is None:
                result = deskewed
                confident = False
            else:
                x, y, w, h = cv2.boundingRect(reboxed)
                result = deskewed[y : y + h, x : x + w]
                confident = True

    cv2.imencode(dst.suffix, result)[1].tofile(str(dst))
    return confident
