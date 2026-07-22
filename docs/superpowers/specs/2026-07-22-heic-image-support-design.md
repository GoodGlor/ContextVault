# HEIC/HEIF image source support — design

- **Date:** 2026-07-22
- **Status:** Approved, ready to implement
- **Feature:** A of a three-feature request (A: HEIC support; B: dynamic LLM model
  dropdown; C: EN/UK i18n). B and C are separate spec cycles, deferred.

## Goal

Let admins upload `.heic` / `.heif` images as sources. They flow through the existing
OCR → chunk → embed → cite pipeline exactly like `.png` / `.jpg` do today, with no new
behavior downstream.

## Background

Commit #100 added image (OCR) sources. Uploaded images are decoded with **Pillow**
(`Image.open`) and OCR'd with **RapidOCR** (`rapidocr-onnxruntime`). The set of accepted
image extensions lives in one place — `IMAGE_SUFFIXES` in
`src/contextvault/ingestion/parsing.py` — which drives both parser routing and the API's
`SourceKind.IMAGE` classification. The frontend mirrors this in an `accept` attribute.

Pillow 12.x does **not** decode HEIC/HEIF without the `pillow-heif` plugin, which is not
yet a dependency.

## Design

The only genuinely new capability is *decoding* HEIC into a PIL image. Everything
downstream (RapidOCR, ingestion service, DB `image` kind, citation view) is format-
agnostic and unchanged. No DB migration (files stay the existing `image` kind).

### Changes

1. **Dependency** — add `pillow-heif` to `pyproject.toml` and `uv lock` it. It is a
   Pillow plugin shipping prebuilt manylinux/macOS wheels bundling libheif.
2. **Register the opener** — call `pillow_heif.register_heif_opener()` once at import
   time in `src/contextvault/ingestion/parsing.py`. After registration, the existing
   `Image.open(BytesIO(data))` in `_parse_image` transparently decodes HEIC — no change
   to `_parse_image`'s logic.
3. **Backend allowlist** — add `".heic"` and `".heif"` to `IMAGE_SUFFIXES`
   (`parsing.py`). This single set feeds `_PARSERS` routing and the API kind
   classification in `api/sources.py`, so nothing else on the backend changes.
4. **Frontend allowlist** — add `.heic,.heif` to the file input's `accept` attribute in
   `frontend/src/pages/AdminSourcesPage.tsx`.

### Error handling (unchanged)

- A HEIC photo with no text OCRs to empty → source fails with the existing
  `"No text found in image."` message (same as a blank PNG today).
- A corrupt/truncated HEIC raises `UnidentifiedImageError` / `OSError`, already caught in
  `_parse_image` → `"Could not read image file."`.

## Testing (TDD)

- **`tests/test_parsing.py`** — a `_heic_bytes()` helper encoding a small image to HEIC
  via `pillow-heif`; assert `.heic` routes to the image parser and returns OCR text
  (monkeypatching `ocr_image`, mirroring the existing PNG test); assert `.heif` is
  accepted (not `UnsupportedDocumentError`).
- **`tests/test_sources_api.py`** — a `.heic` upload asserts `kind == "image"` (mirrors
  `test_image_upload_sets_image_kind`).
- **Frontend** — assert the file input's `accept` includes `.heic` / `.heif` in
  `AdminSourcesPage.test.tsx`.

## Risks

- `pillow-heif` bundles native libheif. It ships wheels for our targets, so `uv` install
  should be clean; the one thing to verify at install time is a successful wheel install.

## Out of scope

- Converting HEIC to other formats for storage or display (we only OCR to text).
- Multi-image HEIC containers / burst photos — we OCR the primary image only.
- Features B (model dropdown) and C (i18n).
