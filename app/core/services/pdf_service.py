"""PDF/image operations service, per SSD §3.1/§4.1/§5.1.

`PDFService` exposes six pure, synchronous methods over `pikepdf`
(merge/split/organize/protect/unlock) and `img2pdf`+`Pillow`
(jpg_to_pdf). Every method returns the written output path(s). Library
exceptions are translated to domain exceptions (`app.core.exceptions`)
at a single, scoped boundary (`_translate_errors`) so no raw
`pikepdf`/`img2pdf`/`Pillow` exception ever reaches a caller.

Stateless and thread-safe: no config injection, no internal
`TaskRunner.submit()` calls — a future UI composes
`runner.submit(service.merge, ...)` instead of the service wrapping
itself (see SSD §5.2). Operation methods (merge/split/organize/protect/
unlock/jpg_to_pdf) land in later Sprint 1 PRs; this module only
scaffolds the constructor, the exception-translation boundary, and the
shared empty-file/page-validation helpers. Every operation MUST call
`_require_nonempty_file` before opening its input(s) with `pikepdf`/
Pillow — see that method's docstring for why.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import img2pdf
import pikepdf
from PIL import UnidentifiedImageError

from app.core.exceptions import (
    ArchivoProtegidoError,
    ContrasenaInvalidaError,
    EntradaInvalidaError,
    PDFCorruptoError,
)
from app.infrastructure.logger import get_logger

#: The six operations `_translate_errors` can be called for. A closed
#: `Literal` instead of a bare `str` so a typo'd op name (e.g. "unlok")
#: fails type-checking instead of silently falling into the wrong
#: exception branch below.
Operation = Literal["merge", "split", "organize", "protect", "unlock", "jpg_to_pdf"]


class PDFService:
    """Stateless PDF/image operations over `pikepdf`, `img2pdf`, and `Pillow`.

    Holds only a logger — every method takes its input/output paths
    per-call, per SSD §8 (no hardcoded/default paths).
    """

    def __init__(self) -> None:
        self._log = get_logger(__name__)

    @contextmanager
    def _translate_errors(self, op: Operation, source: Path) -> Generator[None, None, None]:
        """Map library exceptions raised while performing `op` on `source`.

        Scoped to wrap ONLY the library call(s) that can raise — our own
        pre-validation (`EntradaInvalidaError`) runs outside this context
        manager and propagates unwrapped.

        Mapping:
            `pikepdf.PasswordError`, `op == "unlock"` -> `ContrasenaInvalidaError`
            `pikepdf.PasswordError`, any other op     -> `ArchivoProtegidoError`
            `pikepdf.PdfError` (parse failure)        -> `PDFCorruptoError`
            `PIL.UnidentifiedImageError`, `OSError`   -> `EntradaInvalidaError`
            `img2pdf.ImageOpenError`, `img2pdf.AlphaChannelError` -> `EntradaInvalidaError`
        """
        try:
            yield
        except pikepdf.PasswordError as exc:
            if op == "unlock":
                self._log.warning("%s failed: wrong password (%s)", op, source.name)
                raise ContrasenaInvalidaError(
                    f"Incorrect password for {source.name!r}."
                ) from exc
            self._log.warning("%s failed: password-protected input (%s)", op, source.name)
            raise ArchivoProtegidoError(
                f"{source.name!r} is password-protected; unlock it first."
            ) from exc
        except pikepdf.PdfError as exc:
            self._log.warning("%s failed: corrupt PDF (%s)", op, source.name)
            raise PDFCorruptoError(f"{source.name!r} is corrupt or unreadable.") from exc
        except (UnidentifiedImageError, OSError) as exc:
            self._log.warning("%s failed: invalid image (%s)", op, source.name)
            raise EntradaInvalidaError(f"{source.name!r} is not a valid image.") from exc
        except (img2pdf.ImageOpenError, img2pdf.AlphaChannelError) as exc:
            self._log.warning("%s failed: unsupported image (%s)", op, source.name)
            raise EntradaInvalidaError(f"{source.name!r} cannot be converted to PDF.") from exc

    def _require_nonempty_file(self, source: Path) -> None:
        """Raise `EntradaInvalidaError` if `source` is missing or 0 bytes.

        Must run BEFORE any `pikepdf.open()`/Pillow call: `pikepdf` raises
        the identical `PdfError` ("unable to find trailer dictionary...")
        for both a 0-byte file and structurally-corrupt garbage bytes, so
        `_translate_errors` alone cannot tell "empty" and "corrupt" apart —
        checking file size first is the only way an empty input reads as
        invalid input (`EntradaInvalidaError`) rather than `PDFCorruptoError`.
        """
        if not source.is_file() or source.stat().st_size == 0:
            raise EntradaInvalidaError(f"{source.name!r} is empty or does not exist.")

    def _validate_pages(self, pages: Sequence[int], page_count: int, source: Path) -> None:
        """Raise `EntradaInvalidaError` if any 1-based `pages` entry is out of range.

        Called after the source PDF is opened (so `page_count` is known)
        and before any mutation. Does not reject duplicates — callers that
        must forbid duplicate page numbers (e.g. `organize`) apply that
        check separately.
        """
        if not pages:
            raise EntradaInvalidaError(f"No pages specified for {source.name!r}.")
        for page in pages:
            if page < 1 or page > page_count:
                raise EntradaInvalidaError(
                    f"Page {page} is out of range for {source.name!r} ({page_count} page(s))."
                )
