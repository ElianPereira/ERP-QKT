---
name: solicitud-ai
description: Crea una solicitud de IA controlada en el repositorio actual
disable-model-invocation: true
argument-hint: "[descripción opcional]"
allowed-tools: Bash(gh auth status:*) Bash(gh api user:*) Bash(gh repo view:*) Bash(gh issue create:*) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/create_issue.sh *)
---

Crea un GitHub Issue que active la automatización de IA del repositorio actual.

1. Si `$ARGUMENTS` contiene una descripción, úsala como punto de partida.
2. Pregunta únicamente lo que falte:
   - modo: Combinado, Solo Codex o Solo Claude;
   - objetivo;
   - resultado esperado;
   - detalles o límites, opcional.
3. Resume el título en una frase breve, sin el prefijo `[AI]:`.
4. Muestra un resumen y pide una confirmación explícita antes de publicar.
5. Después de confirmar, ejecuta:
   `bash ${CLAUDE_SKILL_DIR}/scripts/create_issue.sh "<modo>" "<título>" "<objetivo>" "<resultado>" "<detalles>"`
6. Devuelve el enlace del Issue creado.

No publiques si la cuenta autenticada no es `ElianPereira`, si el repositorio no coincide con el proyecto actual o si falta la confirmación. Nunca incluyas secretos, credenciales ni datos personales.
