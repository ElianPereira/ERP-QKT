# Estado de cuenta BBVA por correo (Issue #401)

Cada mes el estado de cuenta de la BBVA Maestra PYME llega a `quintakooxtanil@gmail.com`
(el corte es el último día del mes; el correo llega el 1 o 2). Este flujo lo manda al ERP y lo
procesa **igual que si lo subieras a mano** en Contabilidad → Estados.

```
Gmail (filtro) → estados@quintakooxtanil.com → Cloudflare Email Routing → Worker
  → POST firmado a /api/contabilidad/estados-cuenta/correo/ → bitácora «Correos»
  → EstadoCuentaBancario (origen «Correo del banco») → procesar_estado_cuenta()
```

## Qué hace el ERP

- Guarda el `.eml` en el bucket privado antes de procesar (si algo falla, no se pierde).
- Acepta PDF, XML o un ZIP con contraseña (AES). Si vienen PDF y XML gana el XML cuando su parser exista;
  hoy solo el PDF tiene parser, así que se usa el PDF. Un PDF cifrado aparte se abre con la misma contraseña.
  El PDF original queda también dentro del `.eml` guardado.
- El periodo es el mes de la **fecha de corte** del estado de cuenta (la Maestra PYME corta a fin de mes).
- Si ese periodo ya estaba cargado a mano → resultado «Ignorado» y no cambia nada. Si el correo solo trae una
  liga → «Ignorado» y un pendiente URGENTE para Dirección (hay que descargarlo de BBVA y subirlo a mano).
- Si el saldo inicial no encadena con el saldo final del mes anterior → se procesa y queda una advertencia.
- Avisos por WhatsApp a Dirección con copia al contador: «cargado: N movimientos, M sin conciliar» (nunca
  montos ni nombres). Errores y «no llegó» abren un pendiente URGENTE para Dirección.
- `reprocesar_estados_correo --aplicar` corre **diario a las 09:15** en el programador: reintenta (hasta 8 veces)
  lo que quedó pendiente o con error, y desde el **día 7** de cada mes avisa, una sola vez, si no llegó el estado
  de cuenta del mes anterior.
- Tras `procesar_estado_cuenta()` queda un punto de enganche (`CADENA_POST_PROCESO` en
  `contabilidad/services_estados_correo.py`) para las capas futuras (identificar abonos, gastos sin factura,
  preguntas al contador). Hoy no hay ninguna más allá de las reglas que ya corre `procesar_estado_cuenta()`.

## Pasos para activarlo (en este orden)

1. **Deploy** de este PR (la migración `contabilidad.0041` es solo aditiva) y, en Railway, servicio `web` y
   Programador: define `ERP_ESTADOS_CORREO_SECRET` con `openssl rand -hex 32`. Sin ella el webhook rechaza todo.
2. **Contraseña del ZIP/PDF**: Admin → Contabilidad → Bancos → BBVA Maestra → «Contraseña del estado de cuenta»
   (solo la ve y la escribe un superusuario; se guarda cifrada y no se vuelve a mostrar). Verifica de paso que el
   «Número de cuenta» sea el que imprime el estado (`0489570314` en el ejemplo).
3. **Cloudflare → Email Routing**: crea la dirección `estados@quintakooxtanil.com` con acción
   «Send to an email» → `quintakooxtanil@gmail.com` (temporal, solo para confirmar el reenvío de Gmail).
4. **Gmail → Configuración → Reenvío y correo POP/IMAP**: agrega `estados@quintakooxtanil.com`; el código de
   confirmación llega al mismo Gmail (por el paso 3) y se confirma.
5. **Gmail → filtro**: De = remitente de BBVA (y asunto típico) → «Reenviar a estados@quintakooxtanil.com».
   Anota el remitente exacto del primer correo real.
6. **Worker**: crea el Worker con `docs/cloudflare/email_worker_estados.js`, carga sus secretos
   (`ERP_ESTADOS_CORREO_SECRET` = el mismo de Railway, `ERP_URL`, `BBVA_REMITENTES` = el remitente del paso 5) y
   cambia la acción de `estados@` a **«Send to a Worker»**.
7. **Prueba**: reenvía a mano el último correo de BBVA a `estados@…` y revisa Contabilidad → Correos. Con el
   `.eml` real también puedes usar la acción «Reprocesar». **Nunca subas el `.eml` al repo** (trae datos reales).

## Seguridad

- El Worker solo reenvía si el `From` está en `BBVA_REMITENTES` **y** el correo trae DKIM o DMARC aprobado y alineado con
  el dominio de ese remitente (cabecera `Authentication-Results` que agrega Gmail). Sin eso cualquiera podría mandar un
  PDF falso a `estados@…` con el `From` de BBVA. Si el primer correo real no pasa, pon `BBVA_AUTENTICACION = no` en el
  Worker **solo** para depurar y quítalo después.
- El ERP vuelve a validar la firma HMAC, el número de cuenta y el formato; el cuadre de saldos con el mes anterior queda
  como advertencia. Un mes ya cargado manda: si se colara un estado falso primero, el verdadero saldría «Ignorado».
- La contraseña del estado se cifra con la misma llave Fernet que la e.firma (`ERP_SAT_FIEL_CIFRADO_SECRET`): si la
  rotas, hay que volver a capturar la contraseña de la cuenta bancaria.
- El PDF descifrado (o el que viene dentro del ZIP) se guarda en el bucket privado, igual que una carga manual.

## Si algo falla

| Síntoma | Qué mirar |
|---|---|
| El correo no aparece en «Correos» | Filtro de Gmail, ruta de Email Routing, registro del Worker (¿`BBVA_REMITENTES` correcto?), 403 del webhook (secreto distinto en Railway y Worker). |
| Resultado «Error: contraseña incorrecta» | Recaptura la contraseña en la cuenta bancaria; luego «Reprocesar». |
| «Error: número de cuenta…» | El «Número de cuenta» de la cuenta bancaria no coincide con el del estado. |
| «Ignorado: ya estaba cargado» | Normal si lo subiste a mano; el cargado a mano manda. |

Riesgo conocido: un PDF cifrado aparte se descifra con PDFium; no se ha probado con un PDF real de BBVA
cifrado (solo con uno sintético). Si el primer estado real llega así y falla, el error queda en la bitácora.
