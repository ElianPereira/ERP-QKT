# CLAUDE.md

Referencia operativa para trabajar en ERP-QKT. Léela antes de explorar el
repo por tu cuenta — la mayoría de las preguntas de "¿cómo corro X?" o
"¿dónde vive Y?" ya están respondidas aquí.

## Reglas de respuesta

- Sé directo: ve al resultado, sin narrar "voy a hacer X" y luego "hice X".
  Una frase de estado basta si hace falta.
- No expliques conceptos básicos de Django/Python salvo que se pida.
- No leas archivos completos si un `grep`/`glob` dirigido resuelve la duda.
- **Nunca leas por defecto**: `*/migrations/*.py` (histórico, sin valor de
  arquitectura — lee `models.py` en su lugar), `graphify-out/`, `static/`,
  binarios/imágenes. Solo ábrelos si la tarea es literalmente sobre eso.
- Confía primero en este archivo y en `PROJECT_CONTEXT.md` antes de
  re-explorar algo que ya documentan.
- Ediciones quirúrgicas: no refactorices código no relacionado con la tarea.
- Responde en español. Commits, nombres de variables y código siguen el
  estilo ya existente en el repo (mezcla ES/EN, no lo normalices).
- No hagas `commit`/`push` sin que se pida explícitamente.

## Contexto técnico

**Stack**: Django 6 · PostgreSQL (prod, Railway) / SQLite (dev) · admin
Jazzmin (sin frontend SPA) · WeasyPrint (PDFs) · Cloudinary (storage) ·
django-anymail/Brevo (email) · Openpay vía REST directo, sin SDK · ruff +
pre-commit · CI en GitHub Actions.

**Comandos esenciales**:
| Acción | Comando |
|---|---|
| Test de una app | `python manage.py test <app>` |
| Test completo (= CI) | `python manage.py test comercial contabilidad airbnb facturacion nomina` |
| Lint | `ruff check .` / autofix: `ruff check --fix .` |
| Chequeo Django | `python manage.py check` |
| Detectar migraciones faltantes | `python manage.py makemigrations --check --dry-run` |
| Servidor local | requiere `.env` desde `.env.example` (`SECRET_KEY` sin default) |

**Estructura clave** (8 apps Django):
- `comercial/` — núcleo: cotizaciones, clientes, inventario, pagos, portal
  cliente, cotizador público. La más grande, con diferencia.
- `contabilidad/` — cuentas, pólizas (generadas por *signals*, nunca a mano
  desde `comercial`), conciliación bancaria.
- `airbnb/`, `nomina/`, `facturacion/`, `comunicacion/`, `reportes/` — un
  dominio cada una; `reportes/services/*.py` centraliza reportes PDF/Excel.
- `core_erp/` — `settings.py`, `urls.py` raíz, rate limiting.

Detalle de modelos/rutas/convenciones completo → `PROJECT_CONTEXT.md`.
Hooks automáticos (ruff autofix, tests por app, confirmación en `.env` y
código de pagos) → `.claude/settings.json`.

## Memoria

Registro de decisiones técnicas y errores resueltos. Formato:
`FECHA — decisión/error → resolución o estado`. Agrega una línea nueva
arriba cada vez que se resuelva algo no obvio; no borres entradas viejas
salvo que queden obsoletas.

- 2026-07-26 — Se evaluó instalar `claude-mem` (memoria de terceros vía
  hooks) → descartado: guarda datos en `~/.claude-mem` (no en el repo, no
  es memoria realmente compartida) e instala runtimes extra (Bun, `uv`) en
  la máquina de cada quien. Ver hilo de la sesión si se reconsidera.
- 2026-07-26 — Bug detectado, **sin corregir**: `comercial/admin.py` usa
  `get_object_or_404` en 4 sitios (líneas 757, 763, 774, 815) sin
  importarlo — `NameError` en runtime al aplicar/revertir descuentos o
  generar contrato desde el admin.
- 2026-07-26 — Se configuraron hooks, `.mcp.json`, este archivo, el skill
  `create-migration` y los subagentes `security-reviewer`/`code-reviewer`
  (ver PR #111).
