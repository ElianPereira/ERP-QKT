# Plantilla de WhatsApp del beneficio por recomendar (para someter a Meta)

Marketing, fase 7 (Issue #468). Idioma `es_MX`. Los parámetros no admiten saltos de línea ni más de 4 espacios seguidos.
Sin la plantilla y sin ventana de 24 h abierta, el aviso no sale por WhatsApp (queda un pendiente para avisar a mano).

## `beneficio_referido` → `META_WA_PLANTILLA_REFERIDO`
Categoría: **Utilidad** (aviso de un beneficio ya ganado). Solo a quien aceptó MARKETING y no pidió la BAJA.

- **Cuerpo** (en bloques, como el resto de plantillas de `docs/whatsapp_plantillas_formato.md`):

```
Hola {{1}} 🎉

La persona que nos recomendaste ya apartó su fecha en Quinta Ko'ox Tanil.

Ganaste un beneficio para tu próxima reservación:
• Válido hasta el {{2}}

Cuando vayas a cotizar, avísanos y lo aplicamos si es la mejor promoción disponible.
```

  - `{{1}}` nombre de pila · `{{2}}` fecha de vencimiento (dd/mm/aaaa).
  - Ejemplos para Meta: `{{1}}` = `Ana`, `{{2}}` = `06/11/2027`.
