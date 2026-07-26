# CLAUDE.md

Operational quick-reference for working on ERP-QKT in Claude Code. For the
full architecture/domain tour (apps, models, routes, business conventions),
read `PROJECT_CONTEXT.md` first — this file only covers what that one
doesn't: exact commands and Claude-Code-specific gotchas.

## Commands

- Run one app's tests: `python manage.py test <app>` (`comercial`,
  `contabilidad`, `airbnb`, `facturacion`, `nomina` — the same set CI runs)
- Run everything CI runs: `python manage.py test comercial contabilidad airbnb facturacion nomina`
- Lint: `ruff check .` / autofix: `ruff check --fix .` (config in
  `pyproject.toml`, migrations excluded)
- Django system check: `python manage.py check`
- Check for missing migrations after a `models.py` change:
  `python manage.py makemigrations --check --dry-run`
- Local server needs `.env` populated from `.env.example` — `SECRET_KEY` has
  no default, Django refuses to start without it.

## Hooks already configured (`.claude/settings.json`)

Ruff autofix, a per-app test run, and a makemigrations-check warning fire
automatically on file edits — see that file for the exact matchers. They
only do real work when the venv/deps are active and `.env` is set up; they
no-op safely otherwise.

## Non-obvious conventions (see `PROJECT_CONTEXT.md` for the rest)

- **Never mutate `contabilidad` state from `comercial` directly.** Pólizas
  are generated automatically via signals (`contabilidad/signals.py`) when a
  `Pago` is saved or an Openpay commission is confirmed.
- **Openpay error messages are always translated.** `services_openpay.py`'s
  `_mensaje_error_openpay` maps `error_code` to Spanish; never surface
  Openpay's raw English `description` to a client.
- **Payment idempotency**: `OpenpayTransaccion.openpay_id` is unique, and
  both checkout and the webhook use `update_or_create`. A short cache lock
  in `portal_procesar_pago_openpay` also guards near-simultaneous
  double-submits.
- **Migrations**: many are hand-written data migrations using `RunPython`
  with an explicit forward function *and* a `revertir()`/reverse function
  (see `contabilidad/migrations/0044_recalcular_totales_iva.py` or
  `0012_renombrar_unidad_negocio_eventos_a_quinta.py`). Follow that pattern
  for anything that transforms existing data, not just schema changes — see
  the `create-migration` skill.
- **Jazzmin sidebar grouping is hand-rolled.** `comercial/templatetags/qkt_sidebar.py`
  + `templates/admin/base.html` add a second nesting level Jazzmin doesn't
  support natively (`MODEL_SUBGROUPS`). Standard Jazzmin config won't
  control the "Ventas"/"Pagos" grouping — edit those two files instead.
