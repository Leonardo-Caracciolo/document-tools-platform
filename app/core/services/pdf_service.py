"""PDF/image operations service, per SSD §3.1/§4.1/§5.1.

`PDFService` exposes ten pure, synchronous methods over `pikepdf`
(merge/split/organize/protect/unlock), `img2pdf`+`Pillow`
(jpg_to_pdf), and `pymupdf` (compress, add_text, highlight_text,
redact_text). Every method returns the written output path(s).
Library exceptions are translated to domain exceptions
(`app.core.exceptions`) at scoped boundaries (`_translate_errors` for
pikepdf/img2pdf/Pillow, `_translate_pymupdf_errors` for pymupdf) so no
raw library exception ever reaches a caller.

Stateless and thread-safe: no config injection, no internal
`TaskRunner.submit()` calls — a future UI composes
`runner.submit(service.merge, ...)` instead of the service wrapping
itself (see SSD §5.2). Every operation MUST call
`_require_nonempty_file` before opening its input(s) with
`pikepdf`/Pillow/`pymupdf` — see that method's docstring for why.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import img2pdf
import pikepdf
import pymupdf
from PIL import Image, UnidentifiedImageError

from app.core.exceptions import (
    ArchivoProtegidoError,
    ContrasenaInvalidaError,
    EntradaInvalidaError,
    PDFCorruptoError,
    PDFSinCoincidenciasError,
)
from app.infrastructure.logger import get_logger

#: The ten operations `_translate_errors`/`_translate_pymupdf_errors` can be
#: called for. A closed `Literal` instead of a bare `str` so a typo'd op
#: name (e.g. "unlok") fails type-checking instead of silently falling
#: into the wrong exception branch below.
Operation = Literal[
    "merge",
    "split",
    "organize",
    "protect",
    "unlock",
    "jpg_to_pdf",
    "compress",
    "add_text",
    "highlight_text",
    "redact_text",
]

#: The five preset anchor positions `add_text` can place text at —
#: see `_anchor_point`. Raw `(x, y)` coordinate input is explicitly out
#: of scope (spec's Non-Requirements).
TextAnchor = Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"]

#: `compress` recompression targets — see design's "Balanced constants"
#: decision: 150 DPI is the on-screen/email sweet spot for scans/
#: screenshots, JPEG quality 75 is the standard near-lossless threshold.
_COMPRESS_DPI = 150
_COMPRESS_JPEG_QUALITY = 75

#: `add_text` layout constants. `_EDIT_MARGIN` is 0.5in at 72pt/in — the
#: same margin used for every anchor edge. `_ADD_TEXT_FONTSIZE`/
#: `_ADD_TEXT_FONTNAME` are fixed (no per-call override — spec's
#: Non-Requirements exclude style customization).
_EDIT_MARGIN = 36
_ADD_TEXT_FONTSIZE = 11
_ADD_TEXT_FONTNAME = "helv"


class PDFService:
    """Stateless PDF/image operations over `pikepdf`, `img2pdf`, `Pillow`, and `pymupdf`.

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

    @contextmanager
    def _translate_pymupdf_errors(self, op: Operation, source: Path) -> Generator[None, None, None]:
        """Map `pymupdf` exceptions raised while performing `op` on `source`.

        Separate from `_translate_errors` by design: that context manager
        is a clean, tested `pikepdf`/`img2pdf`/`Pillow` boundary that maps
        a bare `OSError` -> `EntradaInvalidaError`; wrapping `pymupdf`
        calls in it would mis-map `pymupdf`'s own `OSError`/`RuntimeError`
        subclasses and force the `pymupdf` import into unrelated methods.
        A dedicated context manager isolates the new library and keeps
        the six existing operations' `_translate_errors` invariant
        untouched.

        Mapping:
            `pymupdf.FileDataError`, `pymupdf.EmptyFileError` -> `PDFCorruptoError`

        Deliberately NOT a bare `RuntimeError` catch (both exception types
        above are already `RuntimeError` subclasses, so that would be both
        redundant and too broad): this context manager wraps the entire
        `rewrite_images`/`subset_fonts`/`tobytes` pipeline, not just
        `pymupdf.open`, so a blanket `RuntimeError` catch would relabel
        any unrelated failure during recompression as "corrupt PDF" —
        inaccurate and misleading for debugging.
        """
        try:
            yield
        except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
            self._log.warning("%s failed: corrupt PDF (%s)", op, source.name)
            raise PDFCorruptoError(f"{source.name!r} is corrupt or unreadable.") from exc

    def _require_nonempty_file(self, source: Path, op: Operation | None = None) -> None:
        """Raise `EntradaInvalidaError` if `source` is missing or 0 bytes.

        Must run BEFORE any `pikepdf.open()`/Pillow call: `pikepdf` raises
        the identical `PdfError` ("unable to find trailer dictionary...")
        for both a 0-byte file and structurally-corrupt garbage bytes, so
        `_translate_errors` alone cannot tell "empty" and "corrupt" apart —
        checking file size first is the only way an empty input reads as
        invalid input (`EntradaInvalidaError`) rather than `PDFCorruptoError`.

        `op` is optional so this helper stays unit-testable in isolation;
        every real call site in this class passes it so the failure is
        logged, per the spec's "any operation fails -> logged" requirement.
        """
        if not source.is_file() or source.stat().st_size == 0:
            if op is not None:
                self._log.warning("%s failed: missing or empty input (%s)", op, source.name)
            raise EntradaInvalidaError(f"{source.name!r} is empty or does not exist.")

    def _validate_pages(
        self, pages: Sequence[int], page_count: int, source: Path, op: Operation | None = None
    ) -> None:
        """Raise `EntradaInvalidaError` if any 1-based `pages` entry is out of range.

        Called after the source PDF is opened (so `page_count` is known)
        and before any mutation. Does not reject duplicates — callers that
        must forbid duplicate page numbers (e.g. `organize`) apply that
        check separately.

        `op` is optional (same reasoning as `_require_nonempty_file`).
        """
        if not pages:
            if op is not None:
                self._log.warning("%s failed: no pages specified (%s)", op, source.name)
            raise EntradaInvalidaError(f"No pages specified for {source.name!r}.")
        for page in pages:
            if page < 1 or page > page_count:
                if op is not None:
                    self._log.warning(
                        "%s failed: page %d out of range (%s)", op, page, source.name
                    )
                raise EntradaInvalidaError(
                    f"Page {page} is out of range for {source.name!r} ({page_count} page(s))."
                )

    def _make_output_dir(self, directory: Path, op: Operation | None = None) -> None:
        """Create `directory` (and parents) if needed, or raise `EntradaInvalidaError`.

        `op` is optional (same reasoning as `_require_nonempty_file`).
        """
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            if op is not None:
                self._log.warning(
                    "%s failed: cannot create output directory (%s)", op, directory.name
                )
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
            self._log.warning("merge failed: no input files provided")
            raise EntradaInvalidaError("No input files provided for merge.")

        self._log.info("merge start: %d input(s)", len(inputs))

        merged = pikepdf.Pdf.new()
        for source in inputs:
            self._require_nonempty_file(source, "merge")
            with self._translate_errors("merge", source), pikepdf.Pdf.open(source) as pdf:
                merged.pages.extend(pdf.pages)

        # Validation happens before any write: an invalid input mustn't
        # leave a newly-created (but never written) output directory behind.
        self._make_output_dir(output.parent, "merge")
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
        self._require_nonempty_file(source, "split")
        self._log.info("split start: %s", source.name)

        outputs: list[Path] = []
        with self._translate_errors("split", source), pikepdf.Pdf.open(source) as pdf:
            page_count = len(pdf.pages)
            if ranges is not None:
                resolved_ranges = ranges
            else:
                resolved_ranges = [(page, page) for page in range(1, page_count + 1)]

            pages_to_validate = [page for start, end in resolved_ranges for page in (start, end)]
            self._validate_pages(pages_to_validate, page_count, source, "split")
            for start, end in resolved_ranges:
                if start > end:
                    self._log.warning(
                        "split failed: invalid range (%d, %d) (%s)", start, end, source.name
                    )
                    raise EntradaInvalidaError(
                        f"Invalid range ({start}, {end}) for {source.name!r}."
                    )

            # All ranges validated: safe to create the output dir and write.
            self._make_output_dir(output_dir, "split")
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
        self._require_nonempty_file(source, "organize")
        self._log.info("organize start: %s", source.name)

        with self._translate_errors("organize", source), pikepdf.Pdf.open(source) as pdf:
            page_count = len(pdf.pages)
            self._validate_pages(order, page_count, source, "organize")
            if len(set(order)) != len(order):
                self._log.warning(
                    "organize failed: duplicate page number(s) in order spec (%s)", source.name
                )
                raise EntradaInvalidaError(
                    f"Duplicate page number(s) in order spec for {source.name!r}."
                )

            # All entries validated: safe to create the output dir and write.
            self._make_output_dir(output.parent, "organize")
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
        self._require_nonempty_file(source, "protect")
        if not owner_password or not owner_password.strip():
            self._log.warning("protect failed: empty password (%s)", source.name)
            raise EntradaInvalidaError("Password must not be empty.")

        self._log.info("protect start: %s", source.name)

        with self._translate_errors("protect", source), pikepdf.Pdf.open(source) as pdf:
            if pdf.is_encrypted:
                self._log.warning("protect failed: already password-protected (%s)", source.name)
                raise ArchivoProtegidoError(
                    f"{source.name!r} is already password-protected; unlock it first."
                )

            # Validated: safe to create the output dir and write.
            self._make_output_dir(output.parent, "protect")
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
        self._require_nonempty_file(source, "unlock")
        self._log.info("unlock start: %s", source.name)

        with (
            self._translate_errors("unlock", source),
            pikepdf.Pdf.open(source, password=password) as pdf,
        ):
            if not pdf.is_encrypted:
                self._log.warning("unlock failed: not password-protected (%s)", source.name)
                raise EntradaInvalidaError(f"{source.name!r} is not password-protected.")

            # Password verified and encryption confirmed: safe to write now.
            self._make_output_dir(output.parent, "unlock")
            pdf.save(output)

        self._log.info("unlock ok: %s -> %s", source.name, output.name)
        return output

    def jpg_to_pdf(self, images: Sequence[Path], output: Path) -> Path:
        """Convert `images` into one PDF at `output`, one page per image, in order.

        Every image is verified with Pillow's `Image.verify()` BEFORE
        `img2pdf.convert()` is called. `Image.verify()` only checks the
        file header/structure, though — it does NOT guarantee
        `img2pdf.convert()` will succeed (a file can pass `verify()` and
        still fail conversion). So `_make_output_dir` runs only after
        `img2pdf.convert()` itself has returned successfully, not merely
        after the `verify()` pass — otherwise a conversion failure could
        still leave an orphaned, empty output directory behind, same
        validate-then-write convention as the other five operations.

        A conversion failure can't be reliably attributed to one image
        in `images` (`img2pdf.convert()` processes the whole list as a
        unit), so its error message deliberately does not name a
        specific file — naming the wrong one would be worse than naming
        none.

        Raises:
            `EntradaInvalidaError`: `images` is empty, any image is
                missing/0 bytes (`_require_nonempty_file`), any image
                fails `Image.verify()`, `img2pdf.convert()` cannot
                convert one or more images, or `output`'s parent
                directory cannot be created.
        """
        if not images:
            self._log.warning("jpg_to_pdf failed: no input images provided")
            raise EntradaInvalidaError("No input images provided for jpg_to_pdf.")

        self._log.info("jpg_to_pdf start: %d image(s)", len(images))

        for image in images:
            self._require_nonempty_file(image, "jpg_to_pdf")
            with self._translate_errors("jpg_to_pdf", image), Image.open(image) as img:
                img.verify()

        try:
            pdf_bytes = img2pdf.convert([str(image) for image in images])
        except (img2pdf.ImageOpenError, img2pdf.AlphaChannelError) as exc:
            self._log.warning(
                "jpg_to_pdf failed: unsupported image among %d input(s)", len(images)
            )
            raise EntradaInvalidaError(
                "One or more input images cannot be converted to PDF."
            ) from exc

        # Conversion succeeded: only now is it safe to create the output
        # dir and write — see docstring for why this can't happen earlier.
        self._make_output_dir(output.parent, "jpg_to_pdf")
        output.write_bytes(pdf_bytes)

        self._log.info("jpg_to_pdf ok: %d image(s) -> %s", len(images), output.name)
        return output

    def compress(self, source: Path, output: Path) -> Path:
        """Recompress `source`'s embedded images/fonts, writing the smaller
        of the recompressed candidate or the original bytes to `output`.

        The recompressed candidate is built entirely in memory
        (`Document.tobytes(...)`) and its size is compared to `source`'s
        on-disk size BEFORE anything is written — a never-grow guarantee.
        When recompression does not shrink the file (already-optimized
        or text-only input), `output` receives a literal byte-for-byte
        copy of `source`, never a re-save, so it stays bytes-identical
        to the original. The start log deliberately runs BEFORE
        `_require_nonempty_file` (spec-mandated ordering; `jpg_to_pdf`
        has similar list-level-vs-start-log behavior, though this is the
        first operation where it's this explicit).

        Encryption is checked via `pikepdf`, NOT `pymupdf`: empirically,
        `pymupdf.Document.needs_pass`/`is_encrypted` both read `False`
        for an owner-only-encrypted PDF (a blank user password — the
        same "permissions-only" mode that required an explicit
        `pdf.is_encrypted` guard in `protect`), while `pikepdf` correctly
        reports it as encrypted. Trusting `pymupdf`'s signals here would
        silently strip a user's permissions protection during
        recompression, so the already-open-and-reliable `pikepdf` check
        (reusing the existing `_translate_errors` boundary) gates entry
        before `pymupdf` ever touches the file.

        Raises:
            `EntradaInvalidaError`: `source` is missing/0 bytes, or
                `output`'s parent directory cannot be created.
            `ArchivoProtegidoError`: `source` is password-protected.
            `PDFCorruptoError`: `source` fails to parse.
        """
        self._log.info("compress start: %s", source.name)
        self._require_nonempty_file(source, "compress")

        with self._translate_errors("compress", source), pikepdf.Pdf.open(source) as check:
            if check.is_encrypted:
                self._log.warning(
                    "compress failed: password-protected input (%s)", source.name
                )
                raise ArchivoProtegidoError(
                    f"{source.name!r} is password-protected; unlock it first."
                )

        with self._translate_pymupdf_errors("compress", source):
            doc = pymupdf.open(source)
            try:
                doc.rewrite_images(dpi_target=_COMPRESS_DPI, quality=_COMPRESS_JPEG_QUALITY)
                doc.subset_fonts()
                # Structural cleanup, not tunable per-file like the image/font
                # pass above: garbage=4 (max unused-object collection) + full
                # deflate + use_objstms=1 (compress the xref/object streams
                # themselves) — same "squeeze everything" profile regardless
                # of input, so no dedicated constants for these.
                candidate = doc.tobytes(
                    garbage=4,
                    deflate=True,
                    deflate_images=True,
                    deflate_fonts=True,
                    clean=True,
                    use_objstms=1,
                )
            finally:
                doc.close()

        payload = candidate if len(candidate) < source.stat().st_size else source.read_bytes()

        # Validated and decided: safe to create the output dir and write.
        self._make_output_dir(output.parent, "compress")
        output.write_bytes(payload)

        self._log.info("compress ok: %s -> %s", source.name, output.name)
        return output

    def _anchor_point(
        self, position: TextAnchor, rect: pymupdf.Rect, text: str
    ) -> tuple[float, float]:
        """Map `position` to an `(x, y)` baseline point within `rect`.

        `insert_text`'s `point` argument is BASELINE-relative (confirmed
        empirically against pymupdf 1.28.0 — a glyph inserted at
        `point=(72, 50)` renders with its glyph box bottom near `y=53`,
        not its top), so `top`/`bottom` are computed with that in mind:
        `top` sits one `_ADD_TEXT_FONTSIZE` below the margin (so the
        glyph's ascender clears the margin line), `bottom` sits directly
        on the margin (the baseline itself, descenders may dip slightly
        below). Right/center alignment need `text`'s measured width
        (`pymupdf.get_text_length`) since the point is the text's left
        edge, not its bounding box.
        """
        width = pymupdf.get_text_length(
            text, fontname=_ADD_TEXT_FONTNAME, fontsize=_ADD_TEXT_FONTSIZE
        )
        left = rect.x0 + _EDIT_MARGIN
        right = rect.x1 - _EDIT_MARGIN - width
        top = rect.y0 + _EDIT_MARGIN + _ADD_TEXT_FONTSIZE
        bottom = rect.y1 - _EDIT_MARGIN
        center_x = (rect.x0 + rect.x1) / 2 - width / 2
        center_y = (rect.y0 + rect.y1) / 2

        points: dict[TextAnchor, tuple[float, float]] = {
            "top-left": (left, top),
            "top-right": (right, top),
            "bottom-left": (left, bottom),
            "bottom-right": (right, bottom),
            "center": (center_x, center_y),
        }
        return points[position]

    def _resolve_target_pages(
        self, page: int | None, page_count: int, source: Path, op: Operation
    ) -> list[int]:
        """Return the 0-based page indices `highlight_text`/`redact_text` must search.

        `page is None` means "search every page" — every resulting index
        is in range by construction, so no validation is needed. An
        explicit `page` is validated via `_validate_pages` (raises
        `EntradaInvalidaError` for an out-of-range value) BEFORE any
        search is performed, then converted to a single 0-based index.
        """
        if page is None:
            return list(range(page_count))
        self._validate_pages([page], page_count, source, op)
        return [page - 1]

    def add_text(
        self, source: Path, output: Path, page: int, text: str, position: TextAnchor
    ) -> Path:
        """Insert `text` at the point mapped from `position` on `page`, writing to `output`.

        `page` is 1-based. Validation (`_require_nonempty_file`, the
        empty-text guard) runs before `source` is even opened; the
        page-range check (`_validate_pages`) runs after opening, once
        `doc.page_count` is known, before any mutation — same
        validate-then-write convention as every other operation.

        Raises:
            `EntradaInvalidaError`: `source` is missing/0 bytes, `text`
                is empty/blank, `page` is out of range, or `output`'s
                parent directory cannot be created.
            `PDFCorruptoError`: `source` fails to parse.
        """
        self._require_nonempty_file(source, "add_text")
        if not text or not text.strip():
            self._log.warning("add_text failed: empty text (%s)", source.name)
            raise EntradaInvalidaError("Text to add must not be empty.")

        self._log.info("add_text start: %s", source.name)

        with self._translate_pymupdf_errors("add_text", source):
            doc = pymupdf.open(source)
            try:
                self._validate_pages([page], doc.page_count, source, "add_text")
                pg = doc.load_page(page - 1)
                point = self._anchor_point(position, pg.rect, text)
                pg.insert_text(
                    point, text, fontname=_ADD_TEXT_FONTNAME, fontsize=_ADD_TEXT_FONTSIZE
                )

                # Mutation succeeded: safe to create the output dir and write.
                self._make_output_dir(output.parent, "add_text")
                doc.save(output)
            finally:
                doc.close()

        self._log.info("add_text ok: %s -> %s", source.name, output.name)
        return output

    def highlight_text(
        self, source: Path, output: Path, query: str, page: int | None = None
    ) -> Path:
        """Highlight every case-insensitive match of `query`, writing to `output`.

        `page=None` (default) searches every page; an explicit 1-based
        `page` searches only that page. Matches are accumulated across
        the FULL requested scope before deciding whether to raise
        `PDFSinCoincidenciasError` — never after the first empty page —
        so a query that matches on page 3 of a 5-page all-pages search
        still succeeds. That raise (and every validation above it) runs
        BEFORE `_make_output_dir`/`doc.save`, so a zero-match run leaves
        no orphan output file.

        Raises:
            `EntradaInvalidaError`: `source` is missing/0 bytes, `query`
                is empty/blank, `page` is out of range, or `output`'s
                parent directory cannot be created.
            `PDFCorruptoError`: `source` fails to parse.
            `PDFSinCoincidenciasError`: zero matches across the
                requested page scope.
        """
        self._require_nonempty_file(source, "highlight_text")
        if not query or not query.strip():
            self._log.warning("highlight_text failed: empty query (%s)", source.name)
            raise EntradaInvalidaError("Search text must not be empty.")

        self._log.info("highlight_text start: %s", source.name)

        with self._translate_pymupdf_errors("highlight_text", source):
            doc = pymupdf.open(source)
            try:
                indices = self._resolve_target_pages(
                    page, doc.page_count, source, "highlight_text"
                )
                total = 0
                for idx in indices:
                    pg = doc.load_page(idx)
                    rects = pg.search_for(query)
                    for rect in rects:
                        pg.add_highlight_annot(rect)
                    total += len(rects)

                if total == 0:
                    self._log.warning("highlight_text failed: no matches (%s)", source.name)
                    raise PDFSinCoincidenciasError(
                        f"No matches for {query!r} in {source.name!r}."
                    )

                # Matches found and marked: safe to create the output dir and write.
                self._make_output_dir(output.parent, "highlight_text")
                doc.save(output)
            finally:
                doc.close()

        self._log.info("highlight_text ok: %s -> %s", source.name, output.name)
        return output

    def redact_text(self, source: Path, output: Path, query: str, page: int | None = None) -> Path:
        """Permanently remove every case-insensitive match of `query`, writing to `output`.

        Uses the mark-then-apply redaction sequence: every match on a
        page is marked via `add_redact_annot` first, and `apply_redactions()`
        is called ONCE per page after all of that page's matches are
        marked — confirmed empirically (pymupdf 1.28.0) that a single
        `apply_redactions()` call strips every mark made on that page,
        so calling it per-match would be redundant. Same full-scope-
        then-raise zero-match ordering as `highlight_text`.

        Raises:
            `EntradaInvalidaError`: `source` is missing/0 bytes, `query`
                is empty/blank, `page` is out of range, or `output`'s
                parent directory cannot be created.
            `PDFCorruptoError`: `source` fails to parse.
            `PDFSinCoincidenciasError`: zero matches across the
                requested page scope.
        """
        self._require_nonempty_file(source, "redact_text")
        if not query or not query.strip():
            self._log.warning("redact_text failed: empty query (%s)", source.name)
            raise EntradaInvalidaError("Search text must not be empty.")

        self._log.info("redact_text start: %s", source.name)

        with self._translate_pymupdf_errors("redact_text", source):
            doc = pymupdf.open(source)
            try:
                indices = self._resolve_target_pages(page, doc.page_count, source, "redact_text")
                total = 0
                for idx in indices:
                    pg = doc.load_page(idx)
                    rects = pg.search_for(query)
                    for rect in rects:
                        pg.add_redact_annot(rect)
                    if rects:
                        pg.apply_redactions()
                    total += len(rects)

                if total == 0:
                    self._log.warning("redact_text failed: no matches (%s)", source.name)
                    raise PDFSinCoincidenciasError(
                        f"No matches for {query!r} in {source.name!r}."
                    )

                # Matches found and redacted: safe to create the output dir and write.
                self._make_output_dir(output.parent, "redact_text")
                doc.save(output)
            finally:
                doc.close()

        self._log.info("redact_text ok: %s -> %s", source.name, output.name)
        return output
