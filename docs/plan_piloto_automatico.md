# Plan: QKT en piloto automático

**Objetivo:** el negocio opera solo. **Kooxi** atiende, el **ERP** ejecuta, el **encargado** resuelve lo operativo y al **dueño** le llega todo en copia, pero solo actúa cuando quiere o cuando le toca decidir.
**Meta:** dueño ≤ 2 h por semana al cerrar la Fase 3. Cada fase se despliega en cuanto está lista.
**Leyenda:** ✅ automático hoy · ◐ parcial · ✗ manual · 🆕 propuesta

---

## PARTE A · Cómo funciona (la estructura)

### A1. Tres capas
```
 CLIENTES ─┐      ENCARGADO (7178) ─┐      DUEÑO (9400) ─┐
           ▼                        ▼                     ▼
 ┌──────────────── KOOXI · WhatsApp 9191 (IA) ────────────────┐
 │ Entiende texto y responde. Nunca ejecuta por sí solo.       │
 └──────────────────────────────┬──────────────────────────────┘
                                ▼
 ┌──────────────── ERP · MOTOR (reglas fijas, ~$0) ───────────┐
 │ Cobra, confirma, emite contratos, recuerda, contabiliza,    │
 │ factura, avisa                                              │
 └──────────────────────────────┬──────────────────────────────┘
                                ▼  solo lo que una regla no resuelve
 ┌──────────────── BANDEJA DE PENDIENTES ─────────────────────┐
 │ Responsable + prioridad + plazo; si no se atiende, escala.  │
 └─────────────────────────────────────────────────────────────┘
```
**Regla:** la IA interpreta y el ERP ejecuta. Que Kooxi corriera todas las automatizaciones costaría ~US$100–500 al mes de más sin ganar nada. Con la IA solo donde hay que interpretar texto, el costo extra es ~US$5–15 al mes.

### A2. Qué te llega a ti (9400)
```
 Aviso para el ENCARGADO ─► te llega en [Copia] con botón [Intervengo]
                            (si intervienes, al encargado le llega «lo tomó Dirección»)
 Asunto para el DUEÑO    ─► URGENTE con botones [Aprobar] [Rechazar]
 Todo junto              ─► resumen diario 8:00 · resumen semanal los lunes
```
Copias ajustables por WhatsApp: «en vivo» (default), «cada hora» u «off».

### A3. Directorio de responsables (números fáciles de cambiar)
- Modelo `Responsable` en el admin con estas columnas: rol (Encargado / Dirección / Contador / Compras), persona, WhatsApp, correo, modo de copia, activo y suplente con fechas.
- Una sola función, `destinatarios(rol)`, sustituye las 7 lecturas de `WA_NUMERO_NEGOCIO` que hay hoy en el código.
- **Cambiar un número = editar un renglón.** No hace falta deploy. El número nuevo debe contestar «ACEPTO» antes de activarse y el anterior sigue activo mientras tanto.
- Kooxi reconoce al personal con este mismo directorio. Queda historial de cada cambio.
- **Números vigentes:** 9191 Kooxi (público, ya en los anuncios de Meta e Instagram) · 7178 encargado · 9400 dueño.

### A4. Dinero por WhatsApp: con tu orden sí, con candado
```
 Tú: «devuélvele el depósito a la boda del sábado»
   ─► Kooxi propone: «Devolver $3,000.00 · COT-145 · tarjeta» [Confirmar] [Cancelar]
   ─► botón tocado desde el 9400 en menos de 10 min ─► el ERP ejecuta y deja bitácora
```
- Solo un catálogo cerrado de acciones, con un tope por monto: arriba del tope te manda al admin, que pide 2FA.
- El encargado solo registra hechos; no autoriza dinero.
- Lo legal no se maneja por WhatsApp.

---

### A5. Infraestructura en Railway: de 11 crons a 1 programador

**Hoy** el proyecto tiene 13 servicios: `web`, `Postgres` y **11 crons**, cada uno con su comando, su horario y su copia de variables. Así es como han pasado los fallos silenciosos: crons que nunca se dieron de alta, variables faltantes y uno que se llama «Copy» pero corre otra cosa. Cada automatización nueva obliga a crear un servicio más.

**Propuesta:**
```
 ANTES                                   DESPUÉS
 web                                     web
 Postgres                                Postgres
 Cron Cierre Cotizaciones   ┐            Programador  (1 cron cada 5 min)
 Cron Recordatorio De Pagos │              └─ manage.py programador
 Cron De Mensajes           │                   lee core_erp/programador.py:
 Cron Solicitud Facturas    │                     cerrar_cotizaciones      diario 00:10
 Cron De Operaciones        ├─ 11                 enviar_recordatorios     diario 09:00
 Cron Seguimientos          │                     procesar_tareas_op.      cada 10 min
 Cron Preguntas Kooxi       │                     sincronizar_sat          cada hora
 Cron De Purga              │                     avisar_vencimientos      diario 07:05
 Cron Comisiones Openpay    │                     … (todos, en una sola tabla)
 Cron Web Services Sat      │                   y registra cada corrida (EjecucionCron)
 Cron Cumplimiento          ┘
```
**Inventario real de Railway** (revisado el 20/10, horarios en hora de Mérida):

| Servicio | Comando | Horario | Hallazgo |
|---|---|---|---|
| Cierre Cotizaciones | `cerrar_cotizaciones` | diario 00:10 | OK |
| Recordatorio De Pagos | `enviar_recordatorios` | diario 18:00 | OK |
| De Mensajes | `enviar_guias` | diario 09:00 | OK |
| Solicitud Facturas | `enviar_recordatorios_contador` | diario 18:00 | OK |
| De Operaciones | `procesar_tareas_operativas` | cada 15 min | OK |
| Cumplimiento | `avisar_vencimientos --aplicar` | diario 07:05 | OK (ya estaba dado de alta) |
| Web Services Sat | `sincronizar_sat` | cada hora | OK |
| Comisiones Openpay | `sincronizar_comisiones_openpay --aplicar` | día 2, 08:45 | Su nombre interno sigue siendo «cron-seguimientos-copy» |
| Purga | `purgar_conversaciones_whatsapp --aplicar` | diario 03:00 | OK |
| **Seguimientos** | `enviar_seguimientos` | diario 10:52 | **No tiene `WA_TEMPLATE_SEGUIMIENTO` ni las credenciales de WhatsApp: cada día termina en «no se manda nada». Los seguimientos a clientes nunca han salido.** |
| **Preguntas Kooxi** | `resumir_preguntas_agente --aplicar` | lunes 08:52 | **No tiene `ANTHROPIC_API_KEY` ni las variables de WhatsApp: no puede resumir ni avisar.** |

Las dos fallas en negritas son exactamente el tipo de error silencioso que elimina el programador. Mientras tanto se corrigen en la Fase 0 (punto C) con las referencias `${{web.…}}`.

- **Horarios en el código:** la tabla `core_erp/programador.py` queda versionada y revisada en el PR. Agregar una automatización es agregar un renglón; ya no hay que entrar a Railway.
- **Corrida segura:**
  - Un candado en la base de datos impide que dos corridas se encimen.
  - Cada tarea corre por separado, así que si una falla las demás siguen.
  - Una tarea atrasada (por ejemplo, Railway no corrió a las 00:10) corre en la siguiente vuelta.
- **El vigilante sale gratis:** el programador sabe qué debió correr y qué corrió. Si algo falla o no corre, se abre un pendiente urgente (B10).
- **Variables en un solo lugar:** se usan las *Shared Variables* del proyecto en Railway; `web` y el programador las referencian. Rotar un token es un cambio en un solo lugar y deja de haber variables por cron.
- **Migración sin riesgo, uno por uno:**
  1. Se pasa un comando a la tabla.
  2. Se verifica en el log del programador que corrió bien.
  3. Se **desactiva** el cron viejo en Railway (no se borra), sin dejar ambos activos a la vez.
  4. Después de 2 semanas estable, se borran los crons viejos.
  - Rollback: reactivar el cron viejo.
- **Opcional, recomendado más adelante:** un entorno `staging` en Railway para probar una rama con su propia base antes del merge. Hoy cada merge migra producción directo.
- **Costo:** baja o se queda igual: un cron de 5 min en lugar de 11 servicios. Hay que confirmarlo con el consumo real de Railway después de la migración.

## PARTE B · Automatización por módulo

### B1. Atención a clientes · PRE-VENTA (Kooxi + `comunicacion`)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Captar leads de Meta e Instagram | ✅ llegan directo al 9191 | 🆕 Kooxi registra de qué anuncio vino cada conversación (dato `referral` del webhook) | — | 2 |
| Dudas, precios y disponibilidad | ✅ Kooxi | Sin cambio | — | — |
| Cotización y consentimiento | ✅ Kooxi y cotizador web | Sin cambio | — | — |
| Visitas a la Quinta | ✗ pase a humano | 🆕 Kooxi agenda en los horarios del encargado y le avisa | Encargado recibe la visita | 2 |
| Contratar para el mismo día | ✗ pase a humano | 🆕 Va al encargado, no a ti | Encargado | 1 |
| Seguimiento a quien no compra | ◐ un mensaje a los 3 días | 🆕 Tres mensajes: día 1, día 3 y cuando su fecha esté por ocuparse (solo con consentimiento y respetando las bajas) | — | 2 |
| Preguntas que Kooxi no sabe responder | ◐ propuesta semanal de FAQ inactiva | 🆕 Te llega un resumen semanal: «aprueba estas 3 respuestas» | Dueño, 5 min por semana | 1 |

### B2. VENTA y cobro (`comercial`, Openpay, portal)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Pago con tarjeta, efectivo en tienda o SPEI | ✅ Openpay | Sin cambio | — | — |
| Confirmar y apartar la fecha | ✅ al llegar al mínimo | Sin cambio | — | — |
| Contrato y firma electrónica | ✅ | Sin cambio | — | — |
| Recordatorios de pago | ✅ | Sin cambio | — | — |
| Transferencia o depósito directo a BBVA | ✗ se captura a mano | 🆕 Identificación en capas (ver abajo) y el `Pago` se registra solo | Solo si ninguna capa lo identifica | 4 |
| Cobro con terminal en sitio | ✗ admin | 🆕 Capas propias de terminal (ver abajo) | Encargado registra el folio al cobrar | 1 |
| Choque de fecha con un pago ya hecho | ◐ correo | 🆕 Pendiente urgente a ti con opciones: reembolsar u ofrecer otra fecha | Dueño | 1 |
| Cancelación | ✗ a mano | 🆕 **Solicitud**, nunca automática: el cliente la pide, Kooxi analiza y tú decides (ver abajo) *(zona restringida: legal y Openpay)* | Dueño aprueba | 2 |
| Contracargos | ◐ | 🆕 Pendiente urgente con la evidencia ya armada | Dueño | 1 |

**Identificar transferencias y depósitos (capas en orden; se detiene en la primera que acierta):**
```
 Abono en el estado de cuenta
  1. ¿El concepto o la referencia trae «COT-145» (o «145», «cot 145»)?   ─► sí: Pago a esa cotización
  2. ¿El ordenante coincide con un cliente que tiene saldo y el monto con su saldo o su mínimo? ─► sí: Pago
  3. ¿Hay una sola cotización con ese monto exacto pendiente en ±5 días?  ─► sí: Pago
  4. Nada ─► Kooxi le pregunta al cliente probable: «¿Nos transferiste $X el 12/11?»
             y, si nadie responde, pendiente al encargado
```
- **Para que la capa 1 funcione, el folio se pide en todos lados:** portal, recordatorios, contrato y Kooxi («si pagas por transferencia, pon **COT-145** en el concepto»), junto con la CLABE.
- El registro automático de `Pago` toca el flujo de pagos (confirmación y factura), así que es la Fase 2 del #329 y requiere tu aprobación expresa.

**Terminal BBVA:** la terminal deposita **agrupado por día y con la comisión aparte**; el estado de cuenta no dice de qué cliente es cada cobro. Capas:
1. **Al cobrar**, el encargado escribe a Kooxi: «terminal $5,000 COT-145». El Pago queda registrado al momento con el método «terminal».
2. Si la terminal permite capturar una referencia, se le pone el folio. Hay que verificarlo con BBVA; si lo permite, sirve como evidencia.
3. Al llegar el estado de cuenta, el depósito del día se cuadra contra la suma de los cobros con terminal de ese día; si no cuadra, se abre un pendiente.
4. **Alternativa recomendada:** en sitio, cobrar con el **enlace de pago de Openpay** (un QR en el celular del encargado) en vez de la terminal. Así el cobro queda identificado y conciliado al 100 % sin que nadie capture nada.

**Qué es un «choque de fecha con un pago ya hecho»:** dos clientes pagan la misma fecha casi al mismo tiempo, o uno paga una fecha que ya se ocupó o se bloqueó. El ERP registra el dinero porque ya entró, pero no puede confirmar la segunda reservación. Alguien tiene que decidir si se reembolsa o se ofrece otra fecha; ya pasó en la simulación de septiembre.

**Cancelación: impacto en ventas y seguridad**
- **Ventas:** una política de cancelación visible y clara *ayuda* a cerrar, porque da confianza al pagar. El riesgo de un botón de «cancelar» con un clic es que facilita arrepentirse. Por eso **no hay botón de cancelar**: hay un «solicitar cambio o cancelación», y Kooxi **primero ofrece cambiar de fecha** (se retiene la venta) antes de tramitar la cancelación.
- **Flujo:**
```
 Cliente (portal o Kooxi desde SU número registrado)
   ─► código de verificación a su correo registrado (igual que la firma del contrato)
   ─► Kooxi ofrece cambio de fecha ── acepta ─► cambio (también lo apruebas tú)
                                  └── no ─► análisis para ti: días al evento, monto
                                            según la política, pagado, historial,
                                            probabilidad de revender la fecha
   ─► tú: [Aprobar reembolso $X] [Rechazar] [Contraoferta]
   ─► el ERP ejecuta y le avisa al cliente
```
- **Candados contra terceros:** se pide el token del portal o el número registrado **más** un código al correo registrado; máximo 3 intentos y bloqueo temporal. El reembolso **solo vuelve al mismo medio de pago** (tarjeta de origen por Openpay) o a una cuenta a nombre del titular verificada por ti; nunca a una cuenta nueva que el solicitante dicte. Todo queda en bitácora y nada se ejecuta sin tu botón.

### B3. POST-VENTA (`comunicacion`, depósitos)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Guía para el cliente 3 días antes | ✅ | Sin cambio | — | — |
| Inspección y depósito en garantía | ✗ | 🆕 El encargado responde «sin daños» o manda fotos y monto. Si en 48 h no reporta daños, el depósito se devuelve solo | Encargado; si la retención supera el tope, dueño | 3 |
| Encuesta y reseña | ✗ no existe | 🆕 Un día después: encuesta. Calificación buena → se pide reseña en Google; mala → pendiente a ti | Dueño solo si es mala | 3 |
| Quejas | ◐ pase a humano | 🆕 Pendiente al encargado con copia a ti | Encargado | 1 |
| Volver a vender | ✗ | 🆕 Reactivación anual (aniversario, cumpleaños) con consentimiento | — | 3 |

### B4. OPERACIONES (`operaciones`, inventario)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Preparación entre servicios | ✅ checklist al personal | 🆕 El personal contesta «listo» y la tarea se cierra; si no contesta, sube al encargado | Personal | 1 |
| Mantenimiento recurrente | ✅ | Igual que la anterior | Personal | 1 |
| Compras e insumos por evento | ✗ | 🆕 Lista de compras automática 3 días antes, con cantidades de la cotización, al rol **Compras** del directorio (puede ser el encargado, tú u otra persona) | Quien compra | 3 |
| Gastos sin factura | ◐ | 🆕 Identificados **solo con el estado de cuenta** (ver abajo) | Solo un comercio nuevo, una vez | 4 |
| Bloquear fechas | ✅ desde el calendario | 🆕 Por Kooxi, **solo desde el 9400**: «bloquea del 3 al 5 de diciembre por mantenimiento». Te confirma con botón | Dueño | 2 |

**Gastos sin factura desde el estado de cuenta:**
```
 Cargo en el estado de cuenta (p. ej. «OXXO KANASIN $480»)
  1. ¿Lo explica una Compra con CFDI (SAT)?                ─► liga
  2. ¿Calza una regla aprendida (mismo comercio o cuenta)? ─► asiento automático (ya existe, #329 fase 1)
  3. ¿Calza con una lista de compras del evento (fecha y monto ±10 %)? ─► se clasifica como insumo de ese evento
  4. Comercio nuevo ─► Kooxi le pregunta al rol Compras: «¿Qué fue el cargo de $480 en OXXO el 12/11?»
                      la respuesta lo clasifica y **se aprende**: el próximo mes ya no pregunta
```
Así, después de 2 o 3 meses casi todo se clasifica solo y nadie captura tickets.

### B5. CONTABILIDAD (`contabilidad`)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Pólizas de cobros, compras y nómina | ✅ | Sin cambio | — | — |
| Descarga de CFDI del SAT | ✅ | Pendiente si falla 24 h seguidas | — | 1 |
| Comisiones de Openpay | ✅ | Sin cambio | — | — |
| Clasificar gastos | ◐ aprende del proveedor | 🆕 Capas de B4 y Kooxi pregunta lo nuevo | Compras, una vez por comercio | 4 |
| Estado de cuenta BBVA | ✗ se sube el PDF cada mes | 🆕 **Llega solo por correo** (Issue #401, ajustado abajo) | — | **1** (en cuanto llegue el correo de octubre) |
| Conciliación y dudas contables | ◐ | 🆕 Kooxi consulta directo al contador y a ti solo te pide el visto bueno final (ver abajo) | Contador responde; tú das el visto bueno | 4 |

**Ajustes al Issue #401 (estado de cuenta por correo)**, que pasa a ser parte de la Fase 1:
1. El aviso de «cargado» o «no llegó» va por `destinatarios('DIRECCION')` con copia al contador, **no** por `WA_NUMERO_NEGOCIO` (A3).
2. «No llegó» o «error al procesar» abre un `Pendiente`, en vez de mandar solo un WhatsApp suelto.
3. Al terminar `procesar_estado_cuenta()` corren en cadena: reglas (#329 fase 1, ya existe) → capas de abonos (B2) → capas de cargos (B4) → preguntas al contador (abajo).
4. Se mantiene el bloqueo: el código se escribe cuando llegue el primer correo de BBVA y me pases los datos de la Fase 0 del Issue.
5. **Archivo protegido.** BBVA manda un **ZIP** con el PDF y/o el XML, protegido con contraseña.
   - **Dónde se guarda la contraseña:** en lugar de la variable `BBVA_ESTADO_CUENTA_PASSWORD` que planteaba el Issue, en un campo nuevo de `CuentaBancaria`, «Contraseña del estado de cuenta».
     - Se guarda **cifrado** (Fernet, el mismo esquema que la e.firma en `CredencialSAT`).
     - Solo un superusuario lo captura, y el campo es de escritura: nunca se vuelve a mostrar.
     - Si BBVA la cambia, se edita en el admin sin deploy.
     - **La contraseña nunca va en el código, en el Issue, en los tests ni en la Memoria.** Se captura a mano en el admin después del deploy.
   - **Cómo se abre el ZIP:** con `pyzipper`, porque el `zipfile` de Python no abre ZIP con cifrado AES, y es probable que BBVA lo use. Si el PDF viene cifrado aparte, se abre con la misma contraseña desde el parser. Todo se confirma con el primer correo real.
   - **Si la contraseña es incorrecta:** la bitácora queda en ERROR («contraseña incorrecta») y se abre un pendiente a Dirección, que puede corregirla en el admin y reprocesar.

**Kooxi ↔ contador:**
```
 Duda contable (diferencia de conciliación, cuenta incierta, «por clasificar»)
   ─► Kooxi le escribe al CONTADOR: la pregunta concreta + los datos mínimos + su propuesta
   ─► contador: «sí» / «no, va a la cuenta X» / pregunta de vuelta
   ─► Kooxi te resume: «El contador propone reclasificar 3 cargos a 602.03. [Aplicar] [Ver detalle]»
   ─► tu botón ─► el ERP aplica (póliza o reclasificación, con bitácora)
```
- El contador entra al directorio (A3) con su WhatsApp. Las dudas se agrupan en **un mensaje por día como máximo** para no saturarlo.
- Si el contador no responde en 3 días hábiles, Kooxi se lo recuerda una vez y después te avisa.

### B6. FACTURACIÓN (`facturacion`)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Solicitud al contador por cada pago | ✅ con recordatorios | Sin cambio | Contador timbra (externo) | — |
| Ligar la factura emitida y enviarla al cliente | ✅ **automático**: el ERP baja del SAT cada hora los CFDI emitidos, los liga a su solicitud, genera el PDF, la marca FACTURADA y le manda el correo al cliente | El contador **no sube nada** | — | — |
| Casos que no se pueden ligar solos (0 o varias solicitudes candidatas) | ◐ el contador lo asigna en el admin | 🆕 Kooxi le pregunta al contador: «¿El CFDI de $1,500 del 12/11 es de COT-145 o de COT-148?» y su respuesta lo liga | Contador responde | 4 |

Para que lo anterior funcione en producción hay que confirmar en la Fase 0 (punto C) que la e.firma esté cargada, que `SAT_FIEL_ENCRYPTION_KEY` esté definida y que el cron horario `sincronizar_sat` esté corriendo.

### B7. NÓMINA (`nomina`)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Asistencia con Jibble y recibos | ✅ | Sin cambio | — | — |
| Pago a empleados | ✗ banco | 🆕 Cada viernes te llega el total a pagar con [Aprobar]; la transferencia sigue siendo en el banco. **Sin póliza contable**: la nómina se registra aparte, como hoy | Dueño (5 min por semana) | 4 |

### B8. CUMPLIMIENTO y LEGAL (`cumplimiento`, `legal`)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Vigilar vencimientos | ✅ automático (verificar que el cron exista) | Sin cambio | — | 0 |
| Preparar la renovación | ✗ | 🆕 **Automático:** a 45 días el ERP abre la renovación prellenada, arma el expediente (documentos vigentes y requisitos del catálogo) y te lo manda listo | Tú revisas | 3 |
| Hacer el trámite ante la autoridad | ✗ | Sigue siendo humano: es presencial o requiere tu e.firma o tu firma | Tú o un gestor | — |
| Cerrar el trámite | ✗ | 🆕 Mandas foto o PDF del documento nuevo a Kooxi y se registra la vigencia con sus fechas | Tú (1 foto) | 3 |
| Solicitudes ARCO (datos personales) | ◐ | 🆕 **Automático:** Kooxi la recibe, pide la identificación, la registra con su plazo de 20 días hábiles y redacta la respuesta *(zona restringida: `legal/`)* | Tú apruebas el envío | 3 |

**En resumen:** todo lo que se puede preparar, vigilar y registrar es automático. Tú solo revisas y apruebas, y haces el trámite físico cuando no se puede delegar a un gestor.

### B9. DIRECCIÓN (`indicadores`)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Tablero y plan óptimo | ✅ hay que entrar a verlo | 🆕 Resumen de cada lunes por WhatsApp: venta contra meta, ocupación, caja, alertas | Dueño lee | 4 |
| Desviaciones del plan | ✗ | 🆕 Alerta si a mitad de mes la venta va por debajo del 70 % de la meta | Dueño | 4 |
| Mejoras y errores detectados | ◐ rutinas de Claude en GitHub | 🆕 Dentro del resumen semanal, una sección **«Detecté»** con hasta 3 hallazgos: errores de datos (precio con margen negativo, cotización sin seguimiento, pago sin factura), oportunidades (servicio por debajo de su «precio para entrar», días libres que se pueden promocionar) y fallas del sistema. Cada uno con [Crear tarea] o [Ignorar] | Tú decides | 4 |

### B10. SISTEMA (salud del ERP)
| Proceso | Hoy | Cómo queda | Humano | Fase |
|---|---|---|---|---|
| Automatizaciones que fallan sin aviso | ✗ | 🆕 Vigilante: cada cron deja registro de su corrida; si alguno no corre, pendiente urgente | — | 1 |
| Mejora continua del código | ✅ rutinas de Claude | Sin cambio | Dueño aprueba PRs | — |

---

## PARTE C · Fases

| Fase | Contenido | Cuándo | Qué dejas de hacer |
|---|---|---|---|
| **0** | Sin código: encargado en su ficha; `WA_NUMERO_NEGOCIO` → 9400 (puente); **corregir las variables de los crons Seguimientos y Preguntas Kooxi** (ver A5; lo hago yo con tu OK); definir topes; bitácora de 1 semana | Esta semana | — |
| **1** | Cimiento: tres capas, bandeja y avisos, directorio de responsables y **programador de Railway** (A1–A3, A5) + estado de cuenta por correo (#401 ajustado) + los renglones «Fase 1» de la Parte B | ~4 semanas | Revisar correos y avisos sueltos; dar de alta crons |
| **2** | Ventas (B1, B2) + dinero por WhatsApp (A4) | ~3 semanas | Atender pases a humano de ventas |
| **3** | Post-venta y operaciones (B3, B4) | ~3 semanas | Participar en la operación del evento |
| **4** | Finanzas y dirección (B5, B7, B9) | ~3 semanas | Capturar; solo revisas |

**Lo que siempre será tuyo:** aprobar reembolsos, retenciones grandes y la nómina; temas legales, PROFECO y contracargos; cambios de precio o de política; leer el resumen semanal.

## PARTE C.1 · Qué modelo implementa cada parte

**Flujo por fase:** Opus escribe el Issue con la especificación exacta (archivos, funciones, tests, criterios). Sonnet 5.5 implementa. Opus revisa el diff con `security-reviewer` y `code-reviewer` antes del PR.

| Sonnet 5.5 (especificación cerrada, riesgo bajo) | Opus (diseño o dinero/seguridad) |
|---|---|
| Directorio de responsables y migración de las 7 llamadas a `destinatarios()` | Registro automático de `Pago` desde el banco (capas de B2) |
| Programador de Railway y `EjecucionCron` | Cancelación por solicitud, candados y reembolsos |
| `Pendiente`, bandeja en el admin y resumen diario | Dinero por WhatsApp (A4) y modo «personal» de Kooxi (riesgo de que un mensaje manipule al agente) |
| #401 estado de cuenta por correo (ya viene especificado paso a paso) | Prompt y herramientas nuevas de Kooxi, con sus evals |
| Lista de compras, encuesta y reseña, resumen semanal, cumplimiento | Revisión final de cada PR |

- **Decisión (lo más económico):** **toda la implementación en sesiones con Sonnet 5.5** (`/model` al iniciar, sin subagentes, porque un subagente lanzado desde Opus paga además a Opus como orquestador).
  - El Issue de cada fase lleva la especificación cerrada, así que Sonnet no tiene que rediseñar nada.
  - **Opus solo entra a una revisión corta del diff** en los PR de dinero y seguridad (registro de Pago, cancelación, dinero por WhatsApp y modo «personal» de Kooxi), justo antes del merge.

## PARTE D · Ejecución y verificación
- Cada fase se documenta en un Issue (alcance y criterios), luego un PR que revisas, y el merge solo si lo pides. Las zonas restringidas (Openpay, `legal/`, `contabilidad/services.py`) y las migraciones se señalan aparte.
- **Se reutiliza:** `comunicacion/services.py`, `services_notificaciones`, `herramientas_agente`, `services_deposito.liquidar`, `reglas_contrato.TABLA_CANCELACION`, `indicadores/services`, `operaciones/services` y `core_erp/admin_ui.py` con `qkt_ui.css`.
- **Verificación por fase:** tests de la app, `ruff check .`, `manage.py check`, `makemigrations --check`, prueba en móvil (`core_erp/test_movil.py`) y casos nuevos de Kooxi en `evals/casos_agente.json`.
- **Métrica mensual:** pendientes por responsable, tiempo de resolución y horas tuyas contra la meta.

**Avance (20/10):** ✅ Las variables faltantes de los crons **Seguimientos** y **Preguntas Kooxi** ya se agregaron en Railway como `${{web.…}}`, y los dos servicios se redesplegaron.
⏳ Falta cambiar `WA_NUMERO_NEGOCIO` a 9400 en `web`: necesito confirmar el número completo (¿999 268 9400?).

**Siguientes pasos al aprobar:**
1. **Fase 0, con tu OK:** agregar en Railway las variables que faltan a Seguimientos (`WA_CLOUD_API_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_TEMPLATE_SEGUIMIENTO`, `WA_TEMPLATE_LANGUAGE`) y a Preguntas Kooxi (`ANTHROPIC_API_KEY`, `WA_CLOUD_API_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_NUMERO_NEGOCIO`, `WA_TEMPLATE_OPERACIONES`), todas como `${{web.…}}`. También cambiar `WA_NUMERO_NEGOCIO` a 9400 en `web`.
2. Guardar este plan en `docs/plan_piloto_automatico.md` (PR draft) y publicar la Fase 1 como tres Issues con especificación cerrada para Sonnet:
   - **1a** Directorio de responsables (`comunicacion`): `Responsable`, `destinatarios()`, confirmación «ACEPTO» y el cambio de las 7 lecturas de `WA_NUMERO_NEGOCIO`.
   - **1b** Bandeja de pendientes: `Pendiente`, `registrar_pendiente()` integrado dentro de `alertar_equipo_email` (así captura todas las alertas sin tocar archivos restringidos), resumen diario, copias y escalamiento.
   - **1c** Programador (`operaciones`): tabla `core_erp/programador.py`, comando `programador`, `EjecucionCron`, vigilante y migración de los 11 crons.
   - Además, un comentario en el #401 con sus ajustes (directorio, pendientes, campo cifrado de contraseña y `pyzipper`).
