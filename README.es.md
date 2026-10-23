# Organizador de Fotos (macOS)

[English](README.md) · **Español**

Herramienta de línea de comandos que toma una carpeta desordenada de fotos y
videos y la organiza en una biblioteca limpia `AAAA/MM`. Pensada para
colecciones grandes en discos locales y externos, donde los duplicados, los
pares RAW+JPG, las secuencias DJI y los metadatos inconsistentes son comunes.

## Características

- **Fecha basada en metadatos** — EXIF para imágenes, `exiftool`/`mdls` para
  videos, con fechas del sistema de archivos como respaldo.
- **Detección de duplicados** — tamaño + hash parcial, y hash completo solo
  cuando hace falta.
- **Índice del destino** — detecta archivos que ya existen en el destino aunque
  hayan sido renombrados o reorganizados.
- **Agrupación de secuencias DJI** — mantiene panoramas, hyperlapses, timelapses
  y ráfagas en la misma carpeta.
- **Emparejado RAW+JPG** — mantiene juntos los archivos asociados.
- **Verificación posterior** — compara hashes después de copiar.
- **Manejo de colisiones** — agrega `_1`, `_2`, etc. en vez de sobrescribir.
- **Dry-run primero** — escanea y reporta sin cambiar nada.
- **Log JSON** — `TRANSFER_LOG_<timestamp>.json` en el destino.
- **Copiar o mover** — copiar es el modo por defecto; mover elimina del origen.

## Requisitos

- Python 3.10+
- [Pillow](https://python-pillow.org/)
- [ExifTool](https://exiftool.org/) (recomendado para metadatos de video)
- macOS para el fallback `mdls` y la prevención de suspensión con `caffeinate`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install exiftool   # opcional, pero recomendado
```

## Uso

Siempre ejecutá primero un dry run:

```bash
python3 photov5.py /Volumes/Fotos /Volumes/BackupFotos --dry-run
```

Después, la copia:

```bash
python3 photov5.py /Volumes/Fotos /Volumes/BackupFotos --mode copy
```

O definí las rutas con variables de entorno:

```bash
export PHOTO_SOURCE="/Volumes/Fotos"
export PHOTO_DEST="/Volumes/BackupFotos"
python3 photov5.py --dry-run
```

### Opciones

| Opción | Descripción |
| --- | --- |
| `--mode {copy,move}` | Copia los archivos (default) o los mueve fuera del origen. |
| `--org-mode {metadata_first,oldest,created,modified}` | Cómo se elige la fecha del archivo. |
| `--sequence-window MINUTOS` | Ventana de tiempo para agrupar secuencias DJI. |
| `--skip-folder NOMBRE` | Omite una carpeta; repetible. |
| `--no-verify` | Omite la verificación de hash posterior. |
| `--no-log` | No escribe el log JSON. |
| `--no-dest-index` | Omite el índice del destino antes de escanear. |
| `--dry-run` | Solo escanea y reporta; nunca transfiere. |
| `--yes` | Omite las confirmaciones. Usalo solo después de revisar el dry run. |

`--yes` también omite la confirmación del modo mover, así que revisá el dry run
antes de usarlo con `--mode move`.

## Salida

Los archivos se colocan en el destino según su fecha resuelta:

```text
BackupFotos/
├── 2023/
│   ├── 05/
│   └── 11/
├── 2024/
│   └── 02/
└── TRANSFER_LOG_20260530_142501.json
```

## Reparación de fechas de WhatsApp

Los nombres de archivo de WhatsApp contienen la fecha original, pero los
metadatos del archivo muchas veces no. `whatsappRename.py` copia esa fecha a las
etiquetas EXIF/video:

```bash
python3 whatsappRename.py ~/Desktop/fotos --dry-run
python3 whatsappRename.py ~/Desktop/fotos
```

Escanea recursivamente y solo toca nombres reconocidos de WhatsApp.

## Cómo funciona

1. Escanea el origen y clasifica imágenes, videos, archivos asociados y
   secuencias DJI.
2. Resuelve la mejor fecha disponible para cada archivo.
3. Agrupa secuencias DJI y pares RAW+JPG.
4. Construye el índice del destino y omite duplicados.
5. Copia o mueve los archivos a `AAAA/MM`.
6. Verifica hashes y escribe el log de transacciones.

## Notas de seguridad

- **Copiar es el modo por defecto.** Mantenelo así hasta verificar el resultado.
- Usá una conexión con alimentación para discos externos y mantené la Mac despierta.
- No desconectes discos durante una transferencia.
- El script nunca sobrescribe un archivo existente; elige un nombre nuevo.

## Licencia

MIT — ver [LICENSE](LICENSE).
