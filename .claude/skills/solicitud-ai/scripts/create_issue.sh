#!/usr/bin/env bash
set -euo pipefail

mode="${1:-}"
title="${2:-}"
objective="${3:-}"
expected="${4:-}"
details="${5:-}"

case "$mode" in
  "Combinado"|"Solo Codex"|"Solo Claude") ;;
  *) echo "Modo inválido: usa Combinado, Solo Codex o Solo Claude." >&2; exit 2 ;;
esac

for value in "$title" "$objective" "$expected"; do
  test -n "$value" || { echo "Faltan el título, objetivo o resultado esperado." >&2; exit 2; }
done

login=$(gh api user --jq .login)
test "$login" = "ElianPereira" || {
  echo "GitHub está autenticado como $login; se requiere ElianPereira." >&2
  exit 3
}

repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
test "$repo" = "ElianPereira/ERP-QKT" || {
  echo "Este comando solo puede publicar en ElianPereira/ERP-QKT; repositorio actual: $repo." >&2
  exit 3
}

if [ -z "$details" ]; then
  details="Sin límites adicionales."
fi

body=$(printf '%s\n' \
  "### Modo" "" "$mode" "" \
  "### Objetivo" "" "$objective" "" \
  "### Resultado esperado" "" "$expected" "" \
  "### Detalles o límites" "" "$details" "" \
  "### Autorización" "" \
  "- [x] Autorizo ejecutar el modo seleccionado y consumir las APIs necesarias. Entiendo que solo el modo combinado puede fusionarse automáticamente.")

gh issue create --repo "$repo" --title "[AI]: $title" --body "$body"

