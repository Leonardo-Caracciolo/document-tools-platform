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
itself (see SSD §5.2). `jpg_to_pdf` lands in a later Sprint 1 PR. Every
operation MUST call `_require_nonempty_file` before opening its
input(s) with `pikepdf`/Pillow — see that method's docstring for why.
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

    def _make_output_dir(self, directory: Path) -> None:
        """Create `directory` (and parents) if needed, or raise `EntradaInvalidaError`."""
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EntradaInvalidaError(
                f"Cannot create output directory {directory.name!r}."
            ) from exc

    def merge(self, inputs: Sequence[Path], output: Path) -> Path:
        """Merge `inputs` into one PDF at `output`, preserving page order.

        A single input is a valid case: `output` then contains exactly
        that input's pages (a content passthrough — no special-cased
        code path is needed since the merge loop handles it uniformly).

        Raises:
            `EntradaInvalidaError`: `inputs` is empty, any input is
                missing/0 bytes (`_require_nonempty_file`), or `output`'s
                parent directory cannot be created.
            `PDFCorruptoError`: any input fails to parse.
        """
        if not inputs:
            raise EntradaInvalidaError("No input files provided for merge.")

        self._log.info("merge start: %d input(s)", len(inputs))

        merged = pikepdf.Pdf.new()
        for source in inputs:
            self._require_nonempty_file(source)
            with self._translate_errors("merge", source), pikepdf.Pdf.open(source) as pdf:
                merged.pages.extend(pdf.pages)

        # Validation happens before any write: an invalid input mustn't
        # leave a newly-created (but never written) output directory behind.
        self._make_output_dir(output.parent)
        merged.save(output)
        self._log.info("merge ok: %d page(s) -> %s", len(merged.pages), output.name)
        return output

    def split(
        self,
        source: Path,
        output_dir: Path,
        ranges: Sequence[tuple[int, int]] | None = None,
    ) -> list[Path]:
        """Split `source` into one output file per entry in `ranges`.

        `ranges` entries are 1-based, inclusive page ranges (`(start,
        end)`). `None` (default) produces one output file per page.
        All ranges are validated (bounds via `_validate_pages`, plus
        `start <= end`) before any chunk is written — a later invalid
        range can never leave an earlier chunk's file orphaned on disk.
        Validation happens after `source` is opened (page count is only
        known then) but is a separate domain-error path from
        `_translate_errors` — `EntradaInvalidaError` is never one of the
        exception types `_translate_errors` maps, so raising it from
        inside that context still propagates unwrapped.

        Raises:
            `EntradaInvalidaError`: `source` is missing/0 bytes, `source`
                has no pages, any range references an out-of-range page
                or has `start > end`, or `output_dir` cannot be created.
            `PDFCorruptoError`: `source` fails to parse.
        """
        self._require_nonempty_file(source)
        self._log.info("split start: %s", source.name)

        outputs: list[Path] = []
        with self._translate_errors("split", source), pikepdf.Pdf.open(source) as pdf:
            page_count = len(pdf.pages)
            if ranges is not None:
                resolved_ranges = ranges
            else:
                resolved_ranges = [(page, page) for page in range(1, page_count + 1)]

            pages_to_validate = [page for start, end in resolved_ranges for page in (start, end)]
            self._validate_pages(pages_to_validate, page_count, source)
            for start, end in resolved_ranges:
                if start > end:
                    raise EntradaInvalidaError(
                        f"Invalid range ({start}, {end}) for {source.name!r}."
                    )

            # All ranges validated: safe to create the output dir and write.
            self._make_output_dir(output_dir)
            for start, end in resolved_ranges:
                chunk = pikepdf.Pdf.new()
                chunk.pages.extend(pdf.pages[start - 1 : end])
                suffix = f"page_{start}" if start == end else f"pages_{start}-{end}"
                output_path = output_dir / f"{source.stem}_{suffix}.pdf"
                chunk.save(output_path)
                outputs.append(output_path)

        self._log.info("split ok: %d output(s) -> %s", len(outputs), output_dir.name)
        return outputs

    def organize(self, source: Path, output: Path, order: Sequence[int]) -> Path:
        """Reorder/remove pages in `source` per `order`, writing the result to `output`.

        `order` is a sequence of 1-based page numbers, order-significant:
        the output contains exactly `len(order)` pages, in that order.
        Each source page may appear at most once — duplicates are
        rejected rather than silently allowed, so a caller can't
        accidentally end up with a page repeated in the output.
        Validation (bounds via `_validate_pages`, plus the duplicate
        check) runs in one pass, after `source` is opened (page count is
        only known then) and before any write — same convention as
        `split`.

        Raises:
            `EntradaInvalidaError`: `source` is missing/0 bytes, `order`
                is empty, any entry is out of range, `order` contains a
                duplicate page number, or `output`'s parent directory
                cannot be created.
            `PDFCorruptoError`: `source` fails to parse.
        """
        self._require_nonempty_file(source)
        self._log.info("organize start: %s", source.name)

        with self._translate_errors("organize", source), pikepdf.Pdf.open(source) as pdf:
            page_count = len(pdf.pages)
            self._validate_pages(order, page_count, source)
            if len(set(order)) != len(order):
                raise EntradaInvalidaError(
                    f"Duplicate page number(s) in order spec for {source.name!r}."
                )

            # All entries validated: safe to create the output dir and write.
            self._make_output_dir(output.parent)
            reorganized = pikepdf.Pdf.new()
            reorganized.pages.extend(pdf.pages[page - 1] for page in order)
            reorganized.save(output)

        self._log.info("organize ok: %d page(s) -> %s", len(order), output.name)
        return output

    def protect(
        self,
        source: Path,
        output: Path,
        owner_password: str,
        user_password: str | None = None,
    ) -> Path:
        """Encrypt `source` with AES-256 (`Encryption(R=6)`), writing to `output`.

        `user_password` defaults to `owner_password` when omitted, so the
        output always requires a password to open. A `source` encrypted
        with a non-empty user password cannot be opened without it, so
        `pikepdf.Pdf.open` raises `pikepdf.PasswordError` for it here
        (mapped to `ArchivoProtegidoError` by `_translate_errors`). But
        owner-only encryption (a blank user password — valid,
        spec-compliant "permissions-only" protection) opens with no
        password at all, so that case needs its own explicit
        `pdf.is_encrypted` check — the same guard `unlock` uses — or an
        already-protected file would be silently re-encrypted instead of
        rejected.

        Raises:
            `EntradaInvalidaError`: `source` is missing/0 bytes,
                `owner_password` is empty/blank, or `output`'s parent
                directory cannot be created.
            `ArchivoProtegidoError`: `source` is already password-protected.
            `PDFCorruptoError`: `source` fails to parse.
        """
        self._require_nonempty_file(source)
        if not owner_password or not owner_password.strip():
            raise EntradaInvalidaError("Password must not be empty.")

        self._log.info("protect start: %s", source.name)

        with self._translate_errors("protect", source), pikepdf.Pdf.open(source) as pdf:
            if pdf.is_encrypted:
                raise ArchivoProtegidoError(
                    f"{source.name!r} is already password-protected; unlock it first."
                )

            # Validated: safe to create the output dir and write.
            self._make_output_dir(output.parent)
            pdf.save(
                output,
                encryption=pikepdf.Encryption(
                    owner=owner_password,
                    user=user_password if user_password is not None else owner_password,
                    R=6,
                ),
            )

        self._log.info("protect ok: %s -> %s", source.name, output.name)
        return output

    def unlock(self, source: Path, output: Path, password: str) -> Path:
        """Remove password protection from `source`, writing to `output`.

        Raises:
            `EntradaInvalidaError`: `source` is missing/0 bytes, `source`
                is not password-protected, or `output`'s parent
                directory cannot be created.
            `ContrasenaInvalidaError`: `password` does not match `source`'s password.
            `PDFCorruptoError`: `source` fails to parse.
        """
        self._require_nonempty_file(source)
        self._log.info("unlock start: %s", source.name)

        with (
            self._translate_errors("unlock", source),
            pikepdf.Pdf.open(source, password=password) as pdf,
        ):
            if not pdf.is_encrypted:
                raise EntradaInvalidaError(f"{source.name!r} is not password-protected.")

            # Password verified and encryption confirmed: safe to write now.
            self._make_output_dir(output.parent)
            pdf.save(output)

        self._log.info("unlock ok: %s -> %s", source.name, output.name)
        return output
