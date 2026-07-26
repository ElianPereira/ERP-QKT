---
name: create-migration
description: Create a new Django migration for ERP-QKT (schema or data migration) following this repo's conventions — explicit forward+reverse RunPython functions for data migrations, correct app targeting, and a pre-flight check for drift. Use when the user asks to add/generate a migration, backfill data, or change a model in a way that needs a migration.
tools: Read, Bash, Grep, Glob, Write, Edit
disable-model-invocation: true
---

# Create Migration (ERP-QKT)

This repo has 95+ migrations across 5 apps, several of which are hand-written
data migrations (not just `makemigrations` schema diffs). Follow the
existing pattern rather than inventing a new one.

## Workflow

1. **Identify the app and whether this is schema-only or data-touching.**
   - Schema-only (add/remove/alter a field, index, model): a plain
     `makemigrations` diff is enough.
   - Data-touching (backfill, rename a value across existing rows, seed a
     catalog, repair previously-wrong data): needs a `RunPython` migration
     with an explicit reverse function. Never leave `migrations.RunPython.noop`
     as the reverse for anything that actually mutates data — write a real
     `revertir()`/reverse function, matching
     `contabilidad/migrations/0044_recalcular_totales_iva.py` and
     `comercial/migrations/0012_renombrar_unidad_negocio_eventos_a_quinta.py`.

2. **Run the pre-flight check** before writing anything by hand:
   ```bash
   python manage.py makemigrations --check --dry-run <app_label>
   ```
   or use `check_migrations.sh` in this skill's directory, which runs it
   for every app that has migrations and reports drift. If this passes with
   no changes needed and you expected a schema change, the model edit didn't
   actually change anything Django tracks (e.g. a `verbose_name` change on
   its own may not trigger a migration depending on other settings).

3. **For schema changes**, generate normally:
   ```bash
   python manage.py makemigrations <app_label> -n <descriptive_snake_case_name>
   ```
   Read the generated file before considering it done — Django's diff is not
   always what you want (e.g. it may propose a default for a new NOT NULL
   column that doesn't match this repo's data reality).

4. **For data migrations**, write the file by hand (or edit the
   `makemigrations`-generated skeleton) with:
   - A forward function with a clear docstring-style comment explaining
     *why* (not what — the code already shows what).
   - A reverse function that actually undoes the change, not `noop`, unless
     the change is genuinely irreversible (rare — if so, say so explicitly
     in a comment).
   - Use `apps.get_model(app_label, ModelName)` (the historical/frozen
     model), never import the real model — this repo's existing data
     migrations all do this correctly; keep doing it.
   - Batch/paginate if touching a table that could be large in production
     (`comercial.Cotizacion`, `comercial.Pago`, `contabilidad.MovimientoContable`)
     rather than loading the whole queryset into memory.

5. **If the migration is a backfill/repair of a known-bad state**, add a
   regression test for it under the owning app's `tests.py`, following the
   existing pattern (see `RenombrarUnidadNegocioEventosAQuintaMigrationTest`
   in `comercial/tests.py` for a template — it builds a fake historical
   `apps` via a real migration executor rather than importing the current
   models).

6. **Never edit a migration that's already been applied elsewhere** (i.e.
   anything already merged to `main` and likely deployed). Once merged, a
   migration is immutable — write a new migration to correct it instead,
   the way `0044_recalcular_totales_iva.py` corrects earlier IVA persistence
   bugs rather than rewriting the original migration.

7. **Sanity-check before finishing**:
   ```bash
   python manage.py makemigrations --check --dry-run
   python manage.py migrate <app_label> --plan
   ```
   The `--plan` output should show your new migration in the right order
   with no unexpected dependents.
