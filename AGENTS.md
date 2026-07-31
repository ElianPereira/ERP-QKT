# Guía para agentes de implementación

Este repositorio usa un flujo colaborativo: Claude analiza y publica un plan en
un GitHub Issue; Codex implementa ese plan, valida los cambios y abre el Pull
Request. Lee también `CLAUDE.md` y `PROJECT_CONTEXT.md` antes de trabajar.

## Arquitectura y estructura

- Aplicación monolítica Django 6, ejecutada desde `manage.py` y configurada en
  `core_erp/`. Usa SQLite en desarrollo y PostgreSQL en producción (Railway).
- `comercial/` contiene cotizaciones, clientes, inventario, pagos, portal y el
  cotizador público; es el núcleo funcional.
- `contabilidad/` gestiona cuentas, pólizas y conciliación. Las pólizas se
  generan mediante señales, no directamente desde `comercial/`.
- `airbnb/`, `nomina/`, `facturacion/`, `comunicacion/`, `reportes/` y `legal/`
  contienen sus respectivos dominios. Los reportes PDF/Excel se concentran en
  `reportes/services/`.
- `core_erp/impuestos.py` es la única fuente para IVA y retenciones.
- Las plantillas viven en `templates/`, los estáticos en `static/` y cada app
  conserva sus migraciones en `<app>/migrations/`.

## Instalación y validaciones

Usa Python 3.12 para reproducir CI; la imagen de despliegue usa Python 3.13.
El gestor existente es `pip` y `requirements.txt` es la fuente de dependencias:
no existe un archivo de bloqueo ni una comprobación estática de tipos
configurada.

```bash
python -m pip install -r requirements.txt
python -m pip install ruff coverage
ruff check .
SECRET_KEY=local-check-key DEBUG=True ALLOWED_HOSTS='*' python manage.py check
SECRET_KEY=local-check-key DEBUG=True ALLOWED_HOSTS='*' python manage.py makemigrations --check --dry-run
SECRET_KEY=local-test-key DEBUG=True ALLOWED_HOSTS='*' python manage.py test comercial contabilidad airbnb facturacion nomina legal core_erp
docker build -t erp-qkt .
```

Para ejecutar una sola app usa `python manage.py test <app>`. Antes de asumir
que una comprobación falló por tus cambios, compárala con la rama base y
documenta por separado cualquier deuda preexistente. Nunca la corrijas fuera
del alcance del Issue. El build
desplegable es la imagen definida por `Dockerfile`; no hay un comando separado
de build de frontend. `pre-commit run --all-files` es una comprobación opcional
si `pre-commit` está instalado.

## Convenciones de trabajo

- Sigue PEP 8 y la configuración de Ruff en `pyproject.toml` (Python 3.12,
  máximo 120 caracteres). Mantén el estilo y el idioma existentes; no
  normalices nombres en español/inglés sin necesidad.
- Haz cambios quirúrgicos. No refactorices ni cambies lógica de negocio fuera
  del objetivo. No agregues dependencias salvo que sean imprescindibles.
- Reutiliza servicios de dominio y acompaña los cambios de comportamiento con
  pruebas. No edites migraciones históricas; crea una migración nueva cuando
  corresponda.
- Respeta estrictamente el alcance incluido y excluido del GitHub Issue, sus
  criterios de aceptación y su plan. Si el plan resulta inviable, inseguro o
  incompleto, detén esa parte e informa en el Pull Request toda decisión o
  desviación, su motivo y su impacto.
- Si no existe un Issue con plan ejecutable y criterios verificables, no
  implementes: solicita que Claude complete primero la fase de planificación.
- Ejecuta las validaciones aplicables y registra comandos y resultados en el
  Pull Request. Incluye capturas cuando haya cambios visuales perceptibles.

## Seguridad y áreas sensibles

- Nunca leas, publiques, registres ni confirmes secretos o datos personales.
  Usa valores ficticios y `.env.example`; los archivos `.env` son locales.
- Está prohibido modificar secretos, credenciales, tokens, configuración o
  archivos de producción y despliegue sin autorización explícita. Esto incluye
  `.env*`, `.github/workflows/`, `Dockerfile`, `railway.json`, configuración de
  Railway/Cloudinary/Brevo/Openpay y ajustes de producción en
  `core_erp/settings.py`.
- Son áreas especialmente sensibles: pagos (`comercial/services_openpay.py` y
  vistas asociadas), facturación, contabilidad y sus señales, nómina, datos
  fiscales, documentos y consentimientos legales, autenticación/autorización,
  middleware, rate limiting, migraciones y `core_erp/impuestos.py`.
- No desactives validaciones, controles de acceso, protección CSRF, rate
  limiting, auditoría, cifrado ni comprobaciones TLS para hacer pasar una tarea.
- No ejecutes operaciones destructivas ni comandos contra producción. No hagas
  `push`, merge, despliegues, seeds o migraciones sobre entornos remotos sin
  autorización explícita.
