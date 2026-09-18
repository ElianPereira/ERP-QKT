#!/usr/bin/env bash
# Extrae texto plano de un PDF para que Claude lo lea como texto en vez de
# como tokens de imagen por página. Prioriza pdftotext (poppler, sin
# arrancar Python) y cae a pdfplumber (dependencia real del proyecto, ver
# requirements.txt) si pdftotext no está instalado en el sistema.
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Uso: $0 <archivo.pdf> [rango_paginas ej. 1-3]" >&2
  exit 1
fi

archivo="$1"
rango="${2:-}"

if [ ! -f "$archivo" ]; then
  echo "No existe: $archivo" >&2
  exit 1
fi

if command -v pdftotext >/dev/null 2>&1; then
  if [ -n "$rango" ]; then
    inicio="${rango%-*}"
    fin="${rango#*-}"
    exec pdftotext -layout -f "$inicio" -l "$fin" "$archivo" -
  fi
  exec pdftotext -layout "$archivo" -
fi

python3 - "$archivo" "$rango" <<'PY'
import sys

archivo, rango = sys.argv[1], sys.argv[2]

try:
    import pdfplumber
except ImportError:
    sys.exit(
        "Ni 'pdftotext' (instala poppler-utils) ni el paquete 'pdfplumber' "
        "(activa el venv del proyecto: pip install -r requirements.lock) "
        "están disponibles."
    )

inicio, fin = None, None
if rango:
    a, b = rango.split("-")
    inicio, fin = int(a) - 1, int(b)

with pdfplumber.open(archivo) as pdf:
    paginas = pdf.pages[inicio:fin] if rango else pdf.pages
    offset = inicio + 1 if rango else 1
    for i, pagina in enumerate(paginas, start=offset):
        print(f"--- página {i} ---")
        print(pagina.extract_text() or "")
PY
