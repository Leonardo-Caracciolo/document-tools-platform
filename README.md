# Acrobat Tools V2.0

Suite de utilidades PDF de escritorio para Windows, construida con Python y `customtkinter`, que envuelve `pikepdf`, `pymupdf` y librerías relacionadas detrás de una capa de servicios independiente de la interfaz de usuario.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Acerca de

Acrobat Tools V2.0 es, por ahora, un proyecto personal, pensado para evolucionar hacia un uso profesional. La idea de fondo es contar con una suite de herramientas PDF que resguarde y mantenga bajo control propio la información de los documentos, en lugar de depender de servicios online de terceros para operaciones sensibles (editar, proteger, convertir).

Hoy el reconocimiento de texto (OCR) corre de forma local con Tesseract. El plan a futuro es incorporar la IA de Azure (Azure OCR / Azure Document Intelligence) como proveedor alternativo — la arquitectura ya está preparada para esto: `OCRService` y `ExportService` seleccionan su proveedor mediante las variables de entorno `OCR_PROVIDER`/`DOC_CONVERTER_PROVIDER` (ver [Arquitectura](#arquitectura)), por lo que sumar un proveedor Azure no requeriría rediseñar la capa de servicios.

## Tabla de contenidos

- [Acerca de](#acerca-de)
- [Funcionalidades](#funcionalidades)
- [Arquitectura](#arquitectura)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Uso](#uso)
- [Testing](#testing)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Contribuir](#contribuir)
- [Licencia](#licencia)

## Funcionalidades

La barra lateral expone 13 operaciones, definidas como única fuente de verdad en `app/ui/registry.py` (`TOOL_SPECS`), agrupadas de la siguiente manera:

### Organizar
- **Merge PDF** — combina varios PDFs en un solo archivo.
- **Split PDF** — divide un PDF en archivos por rangos de páginas, en un directorio de salida.
- **Organize PDF** — reordena las páginas de un PDF según un orden indicado.

### Seguridad
- **Protect PDF** — agrega una contraseña de propietario (y opcionalmente una de usuario) a un PDF.
- **Unlock PDF** — quita la protección por contraseña de un PDF, dada su contraseña.

### Convertir
- **Word to PDF** — convierte un `.docx` a PDF (mediante un proveedor intercambiable: Word/COM local o Azure Document Converter, seleccionado con la variable de entorno `DOC_CONVERTER_PROVIDER`).
- **PDF to Word** — convierte un PDF a `.docx` usando `pdf2docx`.
- **PDF to Excel** — extrae tablas de un PDF a `.xlsx` usando `pdfplumber` + `openpyxl`.
- **JPG to PDF** — arma un PDF a partir de una o más imágenes JPG.
- **Compress PDF** — recomprime las imágenes embebidas de un PDF (150 DPI / calidad JPEG 75) para reducir el tamaño del archivo.

### Reconocimiento
- **OCR PDF** — agrega una capa de texto invisible y buscable en español a un PDF compuesto solo por imágenes (mediante un proveedor intercambiable: Tesseract local o Azure OCR, seleccionado con la variable de entorno `OCR_PROVIDER`).
- **Scan to PDF** — endereza y recorta una o más imágenes de páginas fotografiadas, las arma en un PDF y luego corre OCR para producir una salida buscable (compone `PDFService.jpg_to_pdf` + `OCRService.ocr`).

### Editar
- **Edit PDF** — una única herramienta con selector de modo, con cuatro modos:
  - **Add text** — coloca texto en una página, en una posición predefinida o en un punto clickeado.
  - **Highlight text** — resalta todas las coincidencias de un texto buscado en una página.
  - **Redact text** — tacha permanentemente todas las coincidencias de un texto buscado en una página.
  - **Replace text** — click en un fragmento de texto de la vista previa de la página para seleccionarlo, y reemplazarlo por texto nuevo. Opcionalmente abre el **Advanced Editor** independiente (ver más abajo), una ventana aparte basada en PySide6 con un flujo de renderizado/click/reemplazo/guardado a mayor escala.

## Arquitectura

El código está organizado en capas de modo que la lógica de PDF/OCR/conversión nunca depende de un toolkit de interfaz:

- **`app/ui/`** — interfaz en `customtkinter`: `MainWindow` (barra lateral + área de contenido), paneles de entrada por familia de herramienta (`app/ui/widgets/panels.py`), y `ToolView`, que despacha un callable `ToolSpec.run` a un `TaskRunner` compartido para que el trabajo de larga duración nunca bloquee el hilo de interfaz.
- **`app/core/services/`** — la capa de lógica de negocio: `PDFService` (operaciones de PDF e imágenes basadas en pikepdf/pymupdf/img2pdf), `ExportService` (Word<->PDF, PDF->Excel), `OCRService` (generación de capa de texto buscable) y `ScanService` (enderezado/recorte + composición de `PDFService`/`OCRService`). Estas clases **no tienen ninguna dependencia de ningún toolkit de interfaz** — verificado por inspección, no hay imports de `customtkinter`/`tkinter`/`PySide6` en ningún lugar bajo `app/core/services/`. `PDFService` en particular es reutilizado directamente tanto por la interfaz en Tkinter como por el proceso independiente del Advanced Editor en PySide6.
- **`app/core/providers/`** — motores intercambiables detrás de interfaces chicas (por ejemplo `TesseractOCRProvider`/`AzureOCRProvider`, `ComWordProvider`/`AzureDocConverterProvider`), seleccionados mediante fábricas controladas por variables de entorno en su servicio dueño.
- **`app/core/concurrency/task_runner.py`** — `TaskRunner` envía las llamadas a servicios a un `ThreadPoolExecutor` y devuelve los resultados al hilo de interfaz mediante un scheduler provisto por el llamador (`root.after`), de modo que ningún hilo de trabajo toca un widget de interfaz directamente.
- **`app/infrastructure/`** — carga de configuración (`AppConfig.load()`, variables de entorno vía `python-dotenv`) y configuración de logging.
- **`app/qt_editor/`** — el "Advanced Editor" opcional: un proceso PySide6 aparte (`python -m app.qt_editor <ruta_pdf> --page N`), lanzado en modo fire-and-forget como subproceso desde el modo Replace-text de `EditPanel`. Reutiliza `PDFService.render_page` para el renderizado y vuelve a llamar a `PDFService` para reemplazar/guardar. Si PySide6 no está instalado, el botón de lanzamiento se degrada de forma controlada con un mensaje en pantalla, sin romper la aplicación.

## Requisitos

- **Python 3.11+** (desarrollado sobre `3.13`).
- **Windows** — la lista de dependencias incluye `pywin32` (automatización COM para el proveedor local de Word a PDF) y manejo de procesos específico de Windows; esta aplicación está pensada para Windows.
- **Tesseract OCR** (binario externo, necesario solo para usar OCR / Scan to PDF de forma local): se instala con `winget install UB-Mannheim.TesseractOCR` o equivalente. La aplicación ubica el binario, en este orden: la variable de entorno `TESSERACT_PATH`, el `PATH`, o la ubicación de instalación típica.
  - `OCRService` tiene el reconocimiento en español fijo (`lang="spa"`). La distribución estándar de Tesseract para Windows solo trae los datos de idioma `eng`/`osd` — `spa.traineddata` debe descargarse aparte desde el [repositorio de tessdata](https://github.com/tesseract-ocr/tessdata/raw/main/spa.traineddata) y colocarse en el directorio `tessdata` de Tesseract (o indicarse mediante `TESSDATA_PREFIX`). Ver `app/config/README.md` para más detalle.

## Instalación

```powershell
git clone https://github.com/Leonardo-Caracciolo/document-tools-platform.git
cd document-tools-platform
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Instalar el paquete en modo editable:

```powershell
pip install -e .
```

Instalar los grupos de herramientas de desarrollo. Los `[dependency-groups]` de este proyecto siguen el PEP 735, que requiere `pip>=25.1` para instalar directamente con `--group`:

```powershell
pip install --group lint
pip install --group test
```

Con `pip<25.1`, instalar las herramientas manualmente en su lugar (el paquete ya quedó instalado en modo editable en el paso anterior):

```powershell
pip install ruff pytest python-docx
```

El **Advanced Editor** es una funcionalidad opcional y requiere su propio grupo/paquete:

```powershell
pip install --group qt
# o, con pip<25.1:
pip install "PySide6>=6.11,<7"
```

### Configuración del entorno

La aplicación requiere un archivo `.env` en la raíz del repositorio (excluido por `.gitignore` — hay que crearlo manualmente). Como mínimo:

```
LOG_LEVEL=INFO
```

Todo lo demás (`OCR_PROVIDER`, `TESSERACT_PATH`, `DOC_CONVERTER_PROVIDER`, `SQLSERVER_HOST`/`SQLSERVER_DB`/`SQLSERVER_USER`/`SQLSERVER_PASSWORD`) es opcional y queda sin definir por defecto — `AppConfig.load()` solo falla de inmediato si falta `LOG_LEVEL`.

**Problema frecuente:** en Windows, crear este archivo con la redirección `echo`/`>` de PowerShell lo escribe en **UTF-16 con BOM**, lo que rompe el parser UTF-8 de `python-dotenv` (la aplicación fallará al iniciar con un confuso error de "falta `LOG_LEVEL`", aunque el archivo parezca correcto). Usar `Set-Content` con una codificación explícita en su lugar:

```powershell
Set-Content -Path .env -Value "LOG_LEVEL=INFO" -Encoding ascii
```

## Uso

Ejecutar la aplicación:

```powershell
python -m app.main
```

El Advanced Editor normalmente se lanza desde el modo Replace-text del panel Edit PDF, pero también puede ejecutarse de forma independiente (útil al trabajar directamente sobre ese módulo):

```powershell
python -m app.qt_editor ruta\al\archivo.pdf --page 1
```

## Testing

```powershell
python -m pytest tests/unit -q
```

Al momento de escribir esto, `tests/unit` reúne 453 tests. También existe una pequeña suite `tests/integration` (4 tests) que ejercita procesos externos reales (Word/COM, Tesseract) y no forma parte del ciclo rápido de tests por defecto:

```powershell
python -m pytest tests/integration -q
```

Lint:

```powershell
ruff check .
```

## Estructura del proyecto

```
app/
├── main.py                  # Punto de entrada: carga la configuración e inicia el loop de Tk
├── core/
│   ├── services/             # Lógica de negocio independiente de la UI (PDFService, ExportService, OCRService, ScanService)
│   ├── providers/             # Motores intercambiables (Tesseract/Azure OCR, COM/Azure doc converter)
│   ├── concurrency/           # TaskRunner — ejecución fuera del hilo de UI
│   └── exceptions.py          # Excepciones de dominio compartidas entre servicios
├── infrastructure/
│   ├── config.py               # AppConfig.load() — configuración tipada desde variables de entorno
│   └── logger.py                # Configuración de logging
├── ui/
│   ├── main_window.py          # Estructura de barra lateral + área de contenido
│   ├── registry.py              # TOOL_SPECS — única fuente de verdad de cada herramienta
│   ├── views/                   # ToolView (despacha un ToolSpec.run vía TaskRunner)
│   └── widgets/                  # Paneles de entrada por familia, filas, vista previa de página PDF
├── qt_editor/                # Subproceso opcional "Advanced Editor" en PySide6
└── config/
    └── README.md               # Notas de despliegue (ej. paquete de idioma español de Tesseract)

tests/
├── unit/                    # Tests rápidos y aislados (453 recolectados)
├── integration/              # Tests contra procesos externos reales (Word/COM, Tesseract)
└── fixtures/                  # Factories sintéticas de PDF/DOCX/imágenes
```

## Contribuir

- Los mensajes de commit siguen [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `test:`, `chore:`, `docs:`), consistente con el historial de este repositorio.
- Correr tests y lint antes de commitear:

```powershell
python -m pytest tests/unit -q
ruff check .
```

## Licencia

Actualmente no hay un archivo de licencia en este repositorio.
