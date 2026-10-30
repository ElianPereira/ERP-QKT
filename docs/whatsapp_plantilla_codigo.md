# Plantilla de WhatsApp — Código de verificación

Se usa cuando un cliente **sin correo registrado** pide un código (firma del
contrato o solicitud de cambio/cancelación) y su ventana de 24 h está cerrada.
Dentro de la ventana el código sale como texto normal y no hace falta plantilla.

## Qué someter en Meta Business Manager

1. **WhatsApp Manager → Plantillas de mensajes → Crear plantilla**.
2. Categoría: **Autenticación** (`AUTHENTICATION`).
3. Nombre: **`codigo_verificacion`** (si usas otro, ponlo en la variable).
4. Idioma: **Español (MX)**.
5. Tipo de botón: **Copiar código** (*copy code*). Marca «Agregar recomendación
   de seguridad» y deja la caducidad en 10 minutos (igual que el código).
6. El cuerpo lo fija Meta («*{{1}} es tu código de verificación…*»): no se edita.

## Variable

`META_WA_PLANTILLA_CODIGO=codigo_verificacion` en el servicio `web`.

El código viaja en el parámetro del cuerpo y en el del botón (así lo exige Meta
para esta categoría) y **nunca** se guarda en la bitácora de comunicaciones.
