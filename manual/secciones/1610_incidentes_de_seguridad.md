---
titulo: Incidentes de seguridad: qué hacer en los primeros minutos
capitulo: Seguridad
orden: 1610
roles: [DIRECCION]
---
Esta sección resume el procedimiento de incidentes de la Quinta. El detalle técnico está en `docs/security/RUNBOOK_INCIDENTES.md`.

### Señales de un incidente

- Un cliente reporta datos que no debería ver o un cargo que no reconoce.
- Un correo o aviso de Railway o GitHub sobre un cambio que nadie del equipo reconoce.
- Una avalancha de intentos de entrada, o respuestas 429 o 403 inusuales.
- Un cobro de Openpay o un mensaje de WhatsApp que no corresponde a nada real.

**Ante la duda, trátalo como incidente:** los primeros pasos son de bajo costo y casi todos reversibles.

### Primeros 15 minutos

1. **Anota la hora y qué viste.** No limpies ni borres nada.
2. **Cuenta de equipo comprometida**: en **Autenticación y Usuarios → Usuarios**, **desactiva** la cuenta (no la borres: se pierde la trazabilidad). Si es de un superusuario, borra también su dispositivo en **Autenticación (2FA)**.
3. **Sesiones abiertas**: pide a quien administra el servidor que cierre todas las sesiones; el efecto es que todos, tú incluido, vuelven a entrar.
4. **Secreto expuesto** (llave en un chat, en un log o en el repositorio): **se rota siempre**, sin evaluar si alguien lo vio. Se genera la nueva, se actualiza en Railway y al final se revoca la vieja.
5. **Pagos sospechosos en Openpay**: **no canceles cobros en curso a ciegas.** Revisa el panel de Openpay del periodo y decide con calma; el soporte de Openpay atiende en el teléfono que aparece en su panel.

### Evidencia

Antes de arreglar nada, guarda los registros del servidor del periodo (se conservan poco tiempo) y, si aplica, la copia de las tablas afectadas.

### Cuándo avisar a los clientes

Solo cuando el incidente involucre **datos fiscales o de pago** (RFC, razón social, datos de transacciones). Un incidente contenido sin evidencia de uso indebido no dispara aviso. El mensaje lo redacta y aprueba la Dirección.

### Al cerrar

Verifica que la causa quedó corregida (no solo contenida), que las credenciales rotadas funcionan en producción, y escribe el post-mortem: qué pasó, cuándo se detectó, cuánto tardó la contención y qué falló en el proceso.

[CONFIRMAR: ¿Quién tiene acceso a Railway, Cloudflare y los demás paneles aparte de la Dirección y a quién se le llama a cualquier hora si la Dirección no contesta? ¿Hay un contacto técnico de guardia?]
