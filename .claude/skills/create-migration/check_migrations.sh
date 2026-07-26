#!/usr/bin/env bash
# Pre-flight drift check for ERP-QKT's Django apps. Run before writing a
# migration by hand, and again before finishing, to catch model changes
# that don't yet have a matching migration.
set -uo pipefail

apps=(comercial contabilidad airbnb facturacion nomina comunicacion reportes core_erp)
status=0

for app in "${apps[@]}"; do
  echo "== $app =="
  if ! python manage.py makemigrations --check --dry-run "$app" 2>&1; then
    status=1
  fi
  echo
done

if [ "$status" -ne 0 ]; then
  echo "Drift detected in at least one app above — run makemigrations for it." >&2
fi

exit "$status"
