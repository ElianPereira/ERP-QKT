---
name: extraer-pdf
description: Extrae el texto plano de un PDF (contrato, guía pre-evento, estado de cuenta, comprobante, acuse) antes de analizarlo, en vez de cargar el binario completo como tokens de imagen por página. Úsalo cuando la tarea sea leer, verificar o resumir el contenido de un PDF del repo o subido por el propietario.
tools: Bash, Read
disable-model-invocation: true
---

# Extraer PDF (ERP-QKT)

Subir un PDF directo al contexto (`Read` sobre el binario) lo convierte en
tokens de imagen por página — caro y no hace falta cuando lo que se
necesita es el texto: folio, montos, cláusulas, nombre del cliente, RFC.
Este skill extrae el texto plano primero.

## Uso

```bash
bash ${CLAUDE_SKILL_DIR}/extraer_pdf.sh <ruta.pdf> [rango_paginas]
```

Ejemplos:

```bash
bash ${CLAUDE_SKILL_DIR}/extraer_pdf.sh media/contratos/contrato_123.pdf
bash ${CLAUDE_SKILL_DIR}/extraer_pdf.sh guia_pasadia.pdf 1-2
```

La salida es texto plano por stdout — léela directamente, no vuelvas a
abrir el PDF con `Read`.

## Cuándo NO usarlo

- **El PDF es un escaneo o una identificación (ARCO)**: el texto no es
  seleccionable, la extracción da vacío. Ahí sí hace falta leer la imagen
  directamente, pero solo si la tarea es literalmente sobre ese documento
  — sigue aplicando la regla de `CLAUDE.md` de no leer binarios/imágenes
  por defecto.
- **El dato ya vive en la base de datos**: un PDF de `Cotizacion`,
  `SolicitudFactura` o contrato se genera desde datos que ya están en el
  modelo (WeasyPrint) — consulta el modelo/Django admin en vez de releer
  el PDF que él mismo produjo.

## Cómo funciona

Prioriza `pdftotext` (poppler, si está instalado en el sistema — no
arranca Python, más rápido) y cae a `pdfplumber` (ya es dependencia
directa del proyecto, ver `requirements.txt` — el mismo paquete que ya se
usó para verificar folios de PDFs fiscales, ver Memoria 2026-08-17 de
`CLAUDE.md`) si `pdftotext` no está disponible. Si ninguno lo está, falla
con un mensaje explícito en vez de intentar otra vía silenciosa.

## Imágenes grandes (capturas de bug, evidencia de Playwright)

Para una captura de pantalla que no necesita leerse a resolución completa
(ej. confirmar que un layout no se rompió, no leer texto fino), redimensiona
antes de pasarla a `Read` con `redimensionar_imagen.py` de este mismo
skill (usa Pillow, ya es dependencia del proyecto):

```bash
python3 ${CLAUDE_SKILL_DIR}/redimensionar_imagen.py captura.png captura_chica.png
```

No hace falta para imágenes ya excluidas por `CLAUDE.md`/`.claudeignore`
(fotos de producto, landing, binarios) — esas simplemente no se leen salvo
que la tarea sea literalmente sobre esa imagen.
