# Plantilla de WhatsApp — Avisos al equipo en bloques

Los avisos internos (pendientes urgentes, visitas, llegadas a la Quinta) se
arman en bloques: encabezado, título en negritas, datos en viñetas y el
enlace en su propio renglón. Así llegan cuando el encargado o Dirección
escribieron al número en las últimas 24 h.

Fuera de esa ventana Meta solo entrega plantillas, y un parámetro de plantilla
no admite saltos de línea. Hoy esos avisos viajan dentro de
`aviso_operaciones` («Tienes un aviso nuevo: {{1}}») con todo en un solo
renglón. Esta plantilla separa las partes en 4 parámetros para que el aviso
se lea igual de claro aunque la ventana esté cerrada.

## Qué someter en Meta Business Manager

1. **WhatsApp Manager → Plantillas de mensajes → Crear plantilla**.
2. Categoría: **Utilidad** (`UTILITY`).
3. Nombre: **`aviso_equipo`** (si usas otro, ponlo en la variable).
4. Idioma: **Español (MX)**.
5. Encabezado: ninguno. Pie: `Quinta Ko'ox Tanil`. Botones: ninguno.
6. Cuerpo, exactamente así (Meta no acepta un cuerpo que empiece o termine
   con variable, por eso lleva texto fijo antes y después):

   ```
   Aviso del equipo · {{1}}

   *{{2}}*
   {{3}}

   Ábrelo aquí: {{4}}
   Responde por este chat si necesitas algo.
   ```

   Ejemplos que pide Meta:
   - `{{1}}` → `🔴 Urgente · Llegada`
   - `{{2}}` → `Ana Ruiz llegó a la Quinta`
   - `{{3}}` → `Visita confirmada: martes 27 de octubre, 4:00 p.m. · 3 personas · Tel. 999 123 4567`
   - `{{4}}` → `https://erp.quintakooxtanil.com/admin/comunicacion/pendiente/1/change/`

## Cuando Meta la apruebe

En Railway, servicio `web` (el Programador la lee como `${{web.…}}`):

```
META_WA_PLANTILLA_AVISO_EQUIPO=aviso_equipo
```

Sin la variable todo sigue funcionando con `aviso_operaciones`, solo que
fuera de la ventana el aviso llega en un renglón (separado por « · »).
