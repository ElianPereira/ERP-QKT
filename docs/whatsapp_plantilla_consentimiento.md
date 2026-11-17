# Plantilla de consentimiento de privacidad (Issue #506)

Se manda **una sola vez por cliente y versión del Aviso** a quien ya reservó o pagó sin haber
aceptado el Aviso de Privacidad (cotizaciones capturadas en el admin) y no está dentro de la
ventana de 24 h. Dentro de la ventana sale como mensaje con botones y el texto ya aprobado de
Kooxi (`MENSAJE_CONSENTIMIENTO`); esta plantilla solo cubre el caso fuera de la ventana.

Variable: `META_WA_PLANTILLA_CONSENTIMIENTO` (en `web` y en el Programador). Sin ella, fuera de
la ventana no se manda nada y se reintenta al día siguiente.

## `autorizacion_privacidad` (Utilidad, es_MX)

Cuerpo (1 variable: `{{1}}` = primer nombre del cliente):

```
Hola {{1}}, para atender tu reservación en Quinta Ko'ox Tanil necesitamos tu autorización para tratar tus datos conforme a nuestro Aviso de Privacidad: https://quintakooxtanil.com/aviso-de-privacidad

También puedes consultar los Términos y Condiciones (https://quintakooxtanil.com/terminos-y-condiciones) y la Política de Cancelación (https://quintakooxtanil.com/politica-de-cancelacion).

Si además quieres recibir promociones y fechas disponibles, y aceptas que midamos nuestra publicidad en Meta y Google, elige «Acepto + promociones».
```

Botones de respuesta rápida, en este orden (el ERP les pone el identificador; no se configura en Meta):

1. `Acepto`
2. `Acepto + promociones`
3. `No acepto`

## Qué guarda el ERP

- Al tocar «Acepto» o «Acepto + promociones»: una `AceptacionLegal` con origen `WHATSAPP`, la versión
  vigente de los documentos y el `wamid` del mensaje del botón como evidencia (MARKETING solo con la segunda).
- «No acepto» no guarda nada y no se vuelve a pedir en esa versión del Aviso (el cliente lo puede
  aceptar en cualquier momento desde el portal).
- Solo vale el botón tocado desde el teléfono del cliente.
