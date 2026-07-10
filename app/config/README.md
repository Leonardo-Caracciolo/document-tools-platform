# app/config/

Holds only the environment variable template (`.env.example`). It does not contain configuration logic.

The actual config loader lives in `app/infrastructure/config.py` — it reads `.env` (via `python-dotenv`) and exposes typed accessors. See `.env.example` for the documented keys.

## Deployment note: Tesseract Spanish language pack

`OCRService` hardcodes Spanish recognition (`lang="spa"`). The standard Windows
Tesseract distribution (`winget install UB-Mannheim.TesseractOCR`) ships only the
`eng` and `osd` language data by default — **`spa.traineddata` is NOT included**
and must be installed separately on every machine that runs OCR:

1. Download `spa.traineddata` (~18MB) from
   `https://github.com/tesseract-ocr/tessdata/raw/main/spa.traineddata`.
2. Place it in Tesseract's `tessdata` directory
   (typically `C:\Program Files\Tesseract-OCR\tessdata\`, requires admin rights
   to write), or point to an alternate directory containing it via the
   `TESSDATA_PREFIX` environment variable.

Without this step, Tesseract raises a "Error opening data file" error the
first time OCR is invoked with `lang="spa"`.
