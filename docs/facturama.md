# Integración Facturama — Emisión de CFDI 4.0

Esta guía describe cómo está integrado el ERP con **Facturama** (PAC) para
timbrar CFDI 4.0 desde el admin de Django, con soporte para múltiples
emisores (Eventos + Airbnb) desde una sola cuenta API.

## Arquitectura

```
facturacion/
├── models.py
│   ├── EmisorFiscal          (RFC, razón social, régimen, serie)
│   └── SolicitudFactura      (FK → EmisorFiscal)
├── services/
│   ├── facturama_client.py   HTTP wrapper (Basic Auth, JSON, PDF/XML)
│   ├── facturama_mapper.py   SolicitudFactura → payload Facturama
│   ├── facturama_service.py  Orquestador: validar → emitir → descargar → guardar
│   └── sat_errors.py         Traducción de códigos CFDIxxxxx a sugerencias
└── admin.py                  Acción masiva "Emitir CFDI ante el SAT (Facturama)"
```

El flujo es **semi-automático**: el contador revisa la solicitud en el admin
y pulsa la acción para timbrarla. El UUID y los archivos quedan guardados
en la misma `SolicitudFactura`.

## Configuración inicial

### 1. Variables de entorno

Agrega a tu `.env`:

```bash
# Cuenta API Anualidad de Facturama (incluye Multiemisor + 100 folios)
FACTURAMA_USER=tu_usuario_api
FACTURAMA_PASS=tu_password_api

# True → apisandbox.facturama.mx · False → api.facturama.mx
FACTURAMA_SANDBOX=True
```

Las mismas credenciales manejan todos los emisores: el RFC emisor se elige
per-CFDI a partir del campo `Issuer.Rfc` del payload (no por credenciales).

### 2. Subir los CSD (una sola vez por emisor)

Los certificados de sello digital (`.cer`, `.key` y contraseña) se suben
**una vez por cada RFC** desde el panel web de Facturama:

- Panel sandbox: <https://apisandbox.facturama.mx>
- Panel producción: <https://api.facturama.mx>

Una vez cargados, Facturama los guarda asociados al RFC y los usa
automáticamente cuando el payload indica ese `Issuer.Rfc`.

### 3. Emisores seed

La migración `facturacion/0005_seed_emisores_fiscales.py` crea dos emisores
al aplicar migraciones:

| Emisor         | RFC             | Régimen | Serie |
|----------------|-----------------|---------|-------|
| Eventos Elian  | PECE010202IA0   | 626 RESICO PF | A |
| Airbnb Ruby    | CERU580518QZ5   | 625 Plataformas Tec. | H |

Si necesitas agregar otro emisor (ej. otra LLC), créalo desde el admin
(`Facturación → Emisores Fiscales`) y sube su CSD en el panel de Facturama.

## Cómo emitir una factura

1. **Registra el pago** del cliente (se crea la `SolicitudFactura`
   automáticamente vía señal).
2. Ve a `Facturación → Solicitudes de Factura` en el admin.
3. Verifica:
   - Emisor correcto (por default se asigna según la unidad de negocio).
   - RFC y razón social del receptor (deben coincidir con la Constancia
     de Situación Fiscal del cliente).
   - Concepto, monto y desglose fiscal.
4. Marca la casilla de la solicitud y elige la acción **"Emitir CFDI
   ante el SAT (Facturama)"**.
5. Si todo está correcto:
   - Aparece un mensaje verde: `SOL-0042 timbrada: UUID <xxx>`
   - Los campos `archivo_pdf`, `archivo_xml`, `uuid_factura` y
     `fecha_factura` quedan guardados en la solicitud.
   - El estado cambia a `FACTURADA`.
6. Si hay un error del SAT aparece un mensaje rojo con el código
   (`CFDIxxxxx`) y una sugerencia accionable (ver `sat_errors.py`).

## Reglas de negocio

- **Retención ISR 1.25%**: se agrega automáticamente cuando el emisor es
  RESICO Persona Física (régimen 626) y el receptor es Persona Moral
  (RFC de 12 caracteres). Ver `_aplica_retencion_isr_resico()` en
  `facturama_mapper.py`.
- **Público en General** (RFC `XAXX010101000`): nunca lleva retención y
  usa Uso CFDI `S01`.
- **ProductCode SAT**: se asigna por unidad de negocio del emisor:
  - Eventos → `90101501` (Servicios de banquetes)
  - Airbnb  → `90111800` (Servicios de hospedaje)
  - Otros   → `80000000` (Servicios de gestión)
- **UnitCode**: siempre `E48` (Unidad de servicio).
- **Idempotencia**: si la solicitud ya tiene `uuid_factura`, el servicio
  lanza `SolicitudNoFacturableError` y no re-emite.

## Pruebas

```bash
# Tests unitarios (no golpean red)
python manage.py test facturacion.test_facturama_mapper
python manage.py test facturacion.test_facturama_client
python manage.py test facturacion.test_sat_errors
python manage.py test facturacion.test_facturama_service
```

Los tests mockean `requests.request` y el `FacturamaClient`, así que no
necesitan credenciales ni conexión a internet.

## Prueba end-to-end en sandbox

1. `FACTURAMA_SANDBOX=True` y credenciales sandbox en `.env`.
2. Sube los CSD de prueba en el panel sandbox.
3. Crea un pago de prueba y factúralo desde el admin.
4. Verifica que Facturama te devuelva un UUID de prueba (empieza con
   `xxxxxxxx`) y que los archivos queden guardados en la solicitud.
5. Cuando todo funcione, cambia `FACTURAMA_SANDBOX=False` y repite en
   producción con los CSD reales.

## Errores comunes

| Código SAT | Significado | Corrección |
|------------|-------------|------------|
| CFDI40147  | RFC no registrado en el SAT | Verificar RFC en Constancia del cliente |
| CFDI40149  | Razón social no coincide con padrón | Copiar exacto de la Constancia |
| CFDI40150  | CP del receptor inválido | Actualizar CP fiscal del cliente |
| CFDI40157  | Régimen fiscal no corresponde al RFC | Pedir régimen actualizado |
| CFDI40158  | Uso CFDI incompatible con régimen | Probar G03 - Gastos en general |
| CFDI33118  | Descuadre en totales | Revisar desglose (subtotal+IVA-ret=monto) |
| CFDI40161  | Lugar de expedición inválido | Corregir CP en el Emisor Fiscal |

Lista completa en `facturacion/services/sat_errors.py::SAT_ERRORS`.

## Extensiones pendientes

- [ ] Mapper alternativo para Airbnb que aplique las reglas de retención
      de régimen 625 (Plataformas Tecnológicas) cuando corresponda.
- [ ] Acción de cancelación desde el admin (usando
      `FacturamaClient.cancelar_cfdi`).
- [ ] Envío automático de PDF/XML al cliente por email al timbrar.
- [ ] Generación de CFDI de tipo Pago (CRP) para facturas PPD.
