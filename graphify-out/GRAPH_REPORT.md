# Graph Report - .  (2026-07-22)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1585 nodes · 4246 edges · 206 communities (101 shown, 105 thin omitted)
- Extraction: 57% EXTRACTED · 43% INFERRED · 0% AMBIGUOUS · INFERRED: 1805 edges (avg confidence: 0.53)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `31a0da9b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14
- Community 15
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 23
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 30
- Community 31
- Community 32
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 47
- Community 48
- Community 49
- Community 50
- Community 51
- Community 52
- Community 53
- Community 55
- Community 56
- Community 57
- Community 58
- Community 59
- Community 60
- Community 61
- Community 62
- Community 63
- Community 64
- Community 65
- Community 66
- Community 67
- Community 68
- Community 69
- Community 70
- Community 71
- Community 72
- Community 73
- Community 74
- Community 75
- Community 76
- Community 77
- Community 78
- Community 79
- Community 80
- Community 81
- Community 82
- Community 83
- Community 84
- Community 85
- Community 86
- Community 87
- Community 88
- Community 89
- Community 90
- Community 91
- Community 92
- Community 93
- Community 94
- Community 95
- Community 96
- Community 97
- Community 98
- Community 99
- Community 100
- Community 101
- Community 102
- Community 103
- Community 104
- Community 105
- Community 106
- Community 107
- Community 108
- Community 109
- Community 110
- Community 111
- Community 112
- Community 113
- Community 114
- Community 115
- Community 116
- Community 117
- Community 118
- Community 119
- Community 120
- Community 121
- Community 122
- Community 123
- Community 124
- Community 125
- Community 126
- Community 127
- Community 128
- Community 129
- Community 130
- Community 131
- Community 132
- Community 133
- Community 134
- Community 135
- Community 136
- Community 137
- Community 138
- Community 139
- Community 140
- Community 141
- Community 142
- Community 143
- Community 144
- Community 145
- Community 146
- Community 147
- Community 148
- Community 149
- Community 150
- Community 151
- Community 152
- Community 153
- Community 154
- Community 155
- Community 156
- Community 157
- Community 158
- Community 159
- Community 160
- Community 161
- Community 162
- Community 163
- Community 164
- Community 165
- Community 166
- Community 167
- Community 168
- Community 169
- Community 170
- Community 171
- Community 172
- Community 173
- Community 174

## God Nodes (most connected - your core abstractions)
1. `Cotizacion` - 94 edges
2. `PortalCliente` - 87 edges
3. `Cliente` - 64 edges
4. `Pago` - 63 edges
5. `DescuentoService` - 60 edges
6. `PlanPago` - 55 edges
7. `CalculadoraBarraService` - 55 edges
8. `ItemCotizacion` - 54 edges
9. `Compra` - 54 edges
10. `CotizacionAdmin` - 53 edges

## Surprising Connections (you probably didn't know these)
- `OcupacionService` --uses--> `AnuncioAirbnb`  [INFERRED]
  reportes/services/airbnb.py → airbnb/models.py
- `OcupacionService` --uses--> `ReservaAirbnb`  [INFERRED]
  reportes/services/airbnb.py → airbnb/models.py
- `AnalizarXmlCompraTest` --uses--> `PagoAirbnb`  [INFERRED]
  comercial/tests.py → airbnb/models.py
- `AsignacionEspacioTest` --uses--> `PagoAirbnb`  [INFERRED]
  comercial/tests.py → airbnb/models.py
- `AsignacionPersonalTest` --uses--> `PagoAirbnb`  [INFERRED]
  comercial/tests.py → airbnb/models.py

## Import Cycles
- None detected.

## Communities (206 total, 105 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.08
Nodes (91): AsignacionEspacioAdmin, AsignacionEspacioInline, AsignacionPersonalAdmin, AsignacionPersonalInline, ClienteAdmin, ComponenteInline, CompraAdmin, ConstanteSistemaAdmin (+83 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (34): ConciliacionBancariaAdmin, ConfiguracionContableAdmin, CuentaBancariaAdmin, CuentaContableAdmin, EstadoCuentaBancarioAdmin, MovimientoContableInline, MovimientoEstadoCuentaInline, Admin del Módulo de Contabilidad ================================ Sistema de Dis (+26 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (27): Página intermedia: muestra descuentos aplicables y ya aplicados,         y permi, _monto_descuento(), _q(), Servicio de Descuentos — comercial/services_descuentos.py ======================, Calcula el monto con Decimal, crea el DescuentoAplicado (auditoría),         sum, Marca activo=False, resta el monto de Cotizacion.descuento y         recalcula t, Recalcula con la lógica canónica de Cotizacion.calcular_totales()         y pers, Evalúa y aplica automáticamente: gana UN solo no-acumulable         (mayor prior (+19 more)

### Community 3 - "Community 3"
Cohesion: 0.05
Nodes (18): Migration, Migration, revertir(), Migration, Ingresos ligados a la cotización que NO forman parte del precio de la         ve, Valida que el pago no exceda el saldo pendiente (no aplica a reembolsos, calcular_metricas(), TestCase (+10 more)

### Community 4 - "Community 4"
Cohesion: 0.11
Nodes (24): AnuncioAirbnbAdmin, ConflictoCalendarioAdmin, Media, PagoAirbnbAdmin, Admin del módulo Airbnb ======================= Panel de administración para ges, ReservaAirbnbAdmin, AnuncioAirbnb, ConflictoCalendario (+16 more)

### Community 5 - "Community 5"
Cohesion: 0.09
Nodes (35): OcupacionService, date, Calcula tasa de ocupación por listing y mes.     Ocupación = noches reservadas /, URLs del Módulo de Reportes ============================ ERP Quinta Ko'ox Tanil, _logo_url(), _parse_fecha(), Vistas del Módulo de Reportes ============================== Selector centraliza, Genera Estado de Resultados en PDF. (+27 more)

### Community 6 - "Community 6"
Cohesion: 0.06
Nodes (12): _detectar_unidad_negocio_por_rfc(), Incrementa contador de visitas., Guarda el movimiento y actualiza el stock del insumo atómicamente., Precio de venta: precio_venta_fijo (si > 0) o costo × (1 + margen).         Siem, Cambia el estado de la cotización validando transiciones permitidas.         Ret, Obtiene el porcentaje mínimo de anticipo desde ConstanteSistema., Retorna el porcentaje de pago como número., Si la cotización está apartando una fecha (anticipo o superior),         valida (+4 more)

### Community 7 - "Community 7"
Cohesion: 0.09
Nodes (31): bloquear_en_airbnb(), calendario_unificado(), Calendario unificado que muestra eventos de la quinta + reservas de Airbnb., Redirige a Airbnb para bloquear manualmente las fechas de un evento.     Abre el, Reporte fiscal mensual de ingresos Airbnb.     Incluye: detalle por reserva, res, reporte_fiscal_airbnb(), configurar_plantilla_barra(), descargar_ficha_producto() (+23 more)

### Community 8 - "Community 8"
Cohesion: 0.13
Nodes (30): calcular_desglose_proporcional(), Calcula el desglose fiscal proporcional de un pago basado en la cotización., Obtiene el siguiente folio disponible para el tipo y mes., aplicar_saldo_apertura(), Servicios del Módulo de Contabilidad ==================================== Lógica, Genera la póliza de apertura para una cuenta a su fecha de corte,     comparando, crear_poliza_compra(), crear_poliza_ingreso_extra() (+22 more)

### Community 9 - "Community 9"
Cohesion: 0.10
Nodes (22): _agrupar_por_fila(), _clasificar_monto(), _emparejar_automaticamente(), _es_pie_de_pagina(), _localizar_columnas(), _parsear_pdf_bbva(), _parsear_xml_bbva(), procesar_estado_cuenta() (+14 more)

### Community 10 - "Community 10"
Cohesion: 0.10
Nodes (22): ComunicacionClienteAdmin, ComunicacionConfig, AppConfig, Command, BaseCommand, Cron diario: envía recordatorios de parcialidades próximas a vencer (3 días ante, ComunicacionCliente, Meta (+14 more)

### Community 11 - "Community 11"
Cohesion: 0.10
Nodes (13): Command, BaseCommand, Aísla clientes fantasma (PUBLICO EN GENERAL, PROSPECTO sin teléfono real) para q, _email_valido(), _es_nombre_generico(), get_or_create_cliente_desde_canal(), Validación liviana de formato de email (sin tocar BD)., Obtiene o crea un Cliente a partir de los datos crudos de un canal externo     ( (+5 more)

### Community 12 - "Community 12"
Cohesion: 0.13
Nodes (8): PlanPagosService, Genera planes de pago calendarizados según la anticipación del evento., DashboardGraficaFinanzasSoloAnioActualTest, PlanPagosTest, Verifica la generación de planes de pago., Generar un nuevo plan elimina el anterior (OneToOne)., Regresión: la gráfica 'Finanzas (ventas vs gastos)' debe mostrar solo     los me, Regresión: un mes que solo tuvo gastos (sin ventas) debe aparecer         en su

### Community 13 - "Community 13"
Cohesion: 0.08
Nodes (11): ClaveUnidadNegocioTest, CompraSinCFDINoDeducibleTest, CompraSinDatosCompletosTest, NominaNuncaGeneraPolizaTest, TestCase, Regresión del bug: 'EVENTOS' no debe usarse en ningún lado; 'QUINTA' sí debe exi, Una Compra sin cuenta_pago y/o unidad_negocio debe generar póliza en BORRADOR., Un gasto sin factura timbrada (uuid) se registra igual, pero como no     deducib (+3 more)

### Community 14 - "Community 14"
Cohesion: 0.09
Nodes (10): MovimientoContableAdmin, Poliza, Encabezado de póliza contable.     Agrupa movimientos que deben cuadrar (suma de, Aplica la póliza (la hace definitiva)., Cancela la póliza con motivo y usuario., Calcula la diferencia entre saldo banco y saldo libros ajustado.         Si es 0, BalanzaComprobacionService, Genera la balanza de comprobación para un período. (+2 more)

### Community 15 - "Community 15"
Cohesion: 0.14
Nodes (10): Command, BaseCommand, Management command: sincronizar_airbnb Sincroniza todos los calendarios de Airbn, DetectorConflictosService, Sincroniza reservas desde calendarios iCal de Airbnb.          FIX de duplicados, Procesa un evento del iCal y crea/actualiza la reserva.         Usa uid_ical com, Detecta el estado y origen de una reserva basado en el título del iCal., Detecta conflictos entre reservas de Airbnb y eventos de la quinta. (+2 more)

### Community 16 - "Community 16"
Cohesion: 0.12
Nodes (20): _agregar_item(), api_disponibilidad_fecha(), api_fechas_ocupadas(), api_paquetes_cotizador(), api_productos_cotizador(), _buscar_producto_por_nombre(), cotizador_enviar(), cotizador_gracias() (+12 more)

### Community 17 - "Community 17"
Cohesion: 0.12
Nodes (11): ICalParserService, date, Sincroniza todos los anuncios activos., Sincroniza un anuncio específico., Parsea archivos iCal de Airbnb., Parsea contenido iCal y retorna lista de eventos.         Maneja líneas multi-lí, Agrupa las filas del CSV por código de confirmación.                  Estructura, Parsea fecha desde string. (+3 more)

### Community 18 - "Community 18"
Cohesion: 0.18
Nodes (9): DisponibilidadFechaTest, TestCase, Tests para verificar_disponibilidad_fecha considerando cotizaciones apartadas., obtener_fechas_bloqueadas(), date, Servicio de validación de fechas bloqueadas ====================================, Verifica si una fecha está disponible para eventos.          Args:         fecha, Obtiene todas las fechas bloqueadas en un rango.          Returns:         Lista (+1 more)

### Community 19 - "Community 19"
Cohesion: 0.12
Nodes (15): landing_publico(), portal_acceso(), portal_descargar_contrato(), portal_descargar_cotizacion(), portal_descargar_plan(), portal_evento(), Vista principal del portal — muestra toda la info del evento., Descarga PDF de cotización desde el portal. (+7 more)

### Community 20 - "Community 20"
Cohesion: 0.20
Nodes (8): Exception, JibbleAPIError, JibbleService, Servicio de integración con Jibble API ======================================= E, Obtiene horas por dia por empleado usando el endpoint Timesheets.         Incluy, Parsea ISO 8601 duration a segundos: PT6H6M52.753408S -> 22012, Formatea hora de Jibble a HH:MM., GET /v1/People -> {person_id: nombre_upper}. Excluye owners/admins.

### Community 21 - "Community 21"
Cohesion: 0.21
Nodes (8): analizar_xml_compra(), Analiza un XML de CFDI para decidir si es elegible como Compra del     negocio e, AnalizarXmlCompraTest, _construir_cfdi(), Tests del módulo Comercial ========================== Ejecutar: python manage.py, CFDI 4.0 mínimo (solo lo que analizar_xml_compra/Compra.save() necesitan leer)., La carga masiva de XML debe distinguir qué CFDIs son compras válidas     del neg, Si el RFC receptor no es el del negocio (ej. es una factura que         ELLOS em

### Community 22 - "Community 22"
Cohesion: 0.13
Nodes (7): _crear_cotizacion(), PolizaPagoClienteTest, Crea catálogo mínimo y configuración para signals., Un Pago concepto=EXTRA se registra completo contra 'Otros ingresos',         sin, ReembolsoClienteTest, ReversionCancelacionTest, setup_contabilidad_minima()

### Community 23 - "Community 23"
Cohesion: 0.18
Nodes (8): EmpleadoAdmin, ReciboNominaAdmin, Management command: sync_jibble ================================ Sincroniza time, Empleado, Meta, ReciboNomina, marcar_recibo_como_pagado(), Marca un recibo de nómina como pagado en efectivo. Puramente administrativo:

### Community 24 - "Community 24"
Cohesion: 0.14
Nodes (8): Media, Admin del Módulo de Reportes ============================ Panel centralizado de, Vista principal: selector de reportes., ReporteGeneradoAdmin, Meta, Modelos del Módulo de Reportes ============================== Historial de repor, Registro de auditoría de cada reporte generado.     No almacena el PDF, solo el, ReporteGenerado

### Community 25 - "Community 25"
Cohesion: 0.13
Nodes (6): AsignacionPersonalTest, MovimientoInventarioTest, TestCase, Verifica movimientos de inventario., Regresión: un evento CERRADA y 100% pagado debe contar en Ventas Mes., VentasMesIncluyeEventosCerradosTest

### Community 26 - "Community 26"
Cohesion: 0.24
Nodes (14): calcular_hora_salida(), cargar_nomina(), _generar_recibos_desde_datos(), jibble_diagnostico_view(), parsear_hms(), parsear_horario_trabajo(), parsear_horas_complejas(), Genera recibos PDF.     fecha_emision_por_empleado: {nombre: 'YYYY-MM-DD HH:MM'} (+6 more)

### Community 27 - "Community 27"
Cohesion: 0.15
Nodes (10): AuxiliarCuentasService, BalanceGeneralService, EstadoResultadosService, LibroMayorService, date, Genera el Balance General (Estado de Situación Financiera).     Estructura:, Genera el Estado de Resultados (P&L) para un período.     Estructura:         In, Calcula resultado del ejercicio: Ingresos - Costos - Gastos. (+2 more)

### Community 28 - "Community 28"
Cohesion: 0.19
Nodes (11): Command, _construir_mensaje(), _enviar_whatsapp(), _get_constante_texto(), _limpiar_telefono(), BaseCommand, Management command: enviar_recordatorios_pagos Uso: python manage.py enviar_reco, Limpia y formatea teléfono para WhatsApp Cloud API.     Retorna formato internac (+3 more)

### Community 29 - "Community 29"
Cohesion: 0.15
Nodes (9): ConfiguracionContadorAdmin, _enviar_pdf_whatsapp(), _generar_pdf_solicitud(), Admin del Módulo de Facturación =============================== Sistema de Diseñ, Descarga el PDF de la solicitud., Genera el PDF de la solicitud y retorna los bytes., Genera el PDF y lo envía al contador via WhatsApp Cloud API., Envía email al contador con PDF adjunto. (+1 more)

### Community 30 - "Community 30"
Cohesion: 0.16
Nodes (7): Verifica si ya se subió la factura (PDF o ZIP)., Marca la solicitud como enviada., Marca la solicitud como facturada (cuando se sube el archivo)., Solicitud de factura enviada al contador.     Se puede generar automáticamente d, SolicitudFactura, Signals del Módulo de Facturación ================================= Genera solic, Tests del módulo Facturación ============================

### Community 31 - "Community 31"
Cohesion: 0.29
Nodes (3): ContratoService, Genera el contrato como PDF usando WeasyPrint + template HTML.     Mismo patrón, Genera el PDF del contrato y retorna (pdf_bytes, numero_contrato).

### Community 32 - "Community 32"
Cohesion: 0.26
Nodes (4): CotizacionTotalesTest, Verifica que los totales se calculen correctamente., Crea cotización sin disparar auto-cálculo de barra., Todo ingreso aplica IVA 16% sin excepción.

### Community 33 - "Community 33"
Cohesion: 0.17
Nodes (3): PolizaAdmin, generar_compra_retroactiva(), Genera el registro Compra que debió existir detrás de una póliza de     egreso c

### Community 34 - "Community 34"
Cohesion: 0.26
Nodes (8): Media, FormaPago, MetodoPago, ConfiguracionContador, Meta, Modelos del Módulo de Facturación ================================= Gestión de s, Datos del contador para envío de solicitudes de factura.     Solo debe existir u, crear_solicitud()

### Community 36 - "Community 36"
Cohesion: 0.24
Nodes (6): Command, _nombre_evento(), BaseCommand, Devuelve el primer superusuario disponible para asignar como creador., importar_historico_view(), Página de administración para importar el historial del sistema anterior.     GE

### Community 37 - "Community 37"
Cohesion: 0.18
Nodes (3): Verifica la máquina de estados de cotización., Reproduce el caso real: la suma de pagos VENTA cuadra exacto con el         tota, TransicionEstadosTest

### Community 38 - "Community 38"
Cohesion: 0.38
Nodes (4): ConflictoFechasTest, TestCase, Evento 9am-5am del día siguiente debe detectar reserva del día sig., Reserva que termina el día del evento (checkout AM) no conflicta.

### Community 39 - "Community 39"
Cohesion: 0.29
Nodes (3): CompraDeteccionAutomaticaTest, Regresión: subir un XML directo en 'Compras > Añadir' (sin pasar por     la carg, Aunque no haya XML, si se captura proveedor_nombre a mano, debe         buscar/c

### Community 40 - "Community 40"
Cohesion: 0.20
Nodes (3): PagoValidacionTest, Verifica la validación de sobrepago., Un pago concepto=EXTRA no debe validarse contra el saldo de la venta.

### Community 41 - "Community 41"
Cohesion: 0.28
Nodes (6): balanza_comprobacion(), estado_resultados(), _parse_periodo(), Vistas de reportes contables., Balanza de comprobación por período y unidad de negocio., Estado de resultados: Ingresos − Costos − Gastos = Utilidad.

### Community 42 - "Community 42"
Cohesion: 0.25
Nodes (5): _crear_cotizacion(), TestCase, Regresión: un pago de un cliente SIN datos fiscales debe generar una     solicit, No debe romperse el caso normal: cliente con datos fiscales reales., SolicitudFacturaClienteNoFiscalTest

### Community 43 - "Community 43"
Cohesion: 0.25
Nodes (6): CotizacionesPeriodoService, CxCCarteraService, date, Servicios de Reportes Comerciales ================================== CxC (Antigü, Genera reporte de antigüedad de saldos (CxC) para PDF.     Reutiliza la lógica d, Genera reporte de cotizaciones filtrado por período y estado.

### Community 44 - "Community 44"
Cohesion: 0.25
Nodes (3): LimpiarDatosFiscalesGenericosTest, TestCase, Tests para el comando limpiar_datos_fiscales_genericos.

### Community 45 - "Community 45"
Cohesion: 0.25
Nodes (8): _agregar_a_lista(), _buscar_insumo_palabra_completa(), _fallback_item(), generar_lista_compras_barra(), _obtener_item_plantilla(), Devuelve un item con datos genéricos cuando no hay plantilla configurada., Helper para agregar un ítem a la lista de compras con formato consistente., Busca un insumo donde el keyword sea una PALABRA COMPLETA,     no parte de otra

### Community 46 - "Community 46"
Cohesion: 0.29
Nodes (4): generar_conciliacion_preliminar(), Crea o actualiza la ConciliacionBancaria del periodo con los datos ya     proces, ConciliacionPreliminarTest, generar_conciliacion_preliminar usa saldo_a_fecha, no saldo_actual corrido a hoy

### Community 47 - "Community 47"
Cohesion: 0.39
Nodes (5): _AppsFalso, Regresión: producción arrastraba una UnidadNegocio con clave='EVENTOS'     (crea, Sustituto mínimo del `apps` histórico que recibe un RunPython:         el modelo, Instalación fresca (como los tests locales): 0002 ya sembró         'QUINTA' dir, RenombrarUnidadNegocioEventosAQuintaMigrationTest

### Community 48 - "Community 48"
Cohesion: 0.39
Nodes (3): GenerarCompraRetroactivaTest, Backfill: una póliza de egreso capturada a mano (sin Compra detrás,     ej. 'FAC, El punto entero del backfill: la Compra creada NO debe disparar         una segu

### Community 51 - "Community 51"
Cohesion: 0.29
Nodes (3): DashboardSeparacionElianRubyTest, Regresión: el dashboard debe separar ingresos/gastos/utilidad de     Elián (Quin, Regresión: la gráfica combinada debe alinear las 4 series (ventas y         gast

### Community 53 - "Community 53"
Cohesion: 0.33
Nodes (3): Importa pagos desde contenido CSV.                  Returns:             Tuple (, Procesa una reserva agrupada y crea el pago.                  Returns:, Busca anuncio por nombre parcial.

### Community 55 - "Community 55"
Cohesion: 0.33
Nodes (3): Command, BaseCommand, Vacía los campos fiscales de Clientes que fueron llenados manualmente con los da

### Community 56 - "Community 56"
Cohesion: 0.40
Nodes (4): Migration, _q(), Repara cotizaciones creadas antes del fix de persistencia de IVA.      Replica C, recalcular_totales()

### Community 57 - "Community 57"
Cohesion: 0.33
Nodes (5): cargar_catalogo_sat(), Migration, Revierte la carga del catálogo (vacía las tablas)., Carga el catálogo de cuentas SAT 2024 y unidades de negocio., reverse_catalogo()

### Community 58 - "Community 58"
Cohesion: 0.33
Nodes (5): convertir_regimenes(), Migration, Revierte a los valores anteriores (para rollback)., Convierte los valores anteriores a claves SAT., revertir_regimenes()

### Community 59 - "Community 59"
Cohesion: 0.33
Nodes (5): cargar_configuracion_contable(), Migration, Elimina la configuración precargada., Precarga la configuración de cuentas por tipo de operación., revertir_configuracion()

### Community 60 - "Community 60"
Cohesion: 0.33
Nodes (3): Genera el texto con datos para enviar al contador., Genera URL de WhatsApp con los datos para el contador., Retorna el contador activo o None.

### Community 61 - "Community 61"
Cohesion: 0.33
Nodes (4): FacturasEmitidasService, date, Servicios de Reportes de Facturación ===================================== Factu, Genera reporte de solicitudes de factura emitidas en un período.

### Community 62 - "Community 62"
Cohesion: 0.40
Nodes (3): Command, BaseCommand, Management command: setup_airbnb Configura los anuncios iniciales de Airbnb con

### Community 63 - "Community 63"
Cohesion: 0.40
Nodes (3): Command, BaseCommand, Management command: cargar_precios_mobiliario Asigna precio_venta_fijo a los pro

### Community 64 - "Community 64"
Cohesion: 0.40
Nodes (3): Command, BaseCommand, Cierra automáticamente cotizaciones cuyo evento ya ocurrió.  Lógica:   1. CONFIR

### Community 65 - "Community 65"
Cohesion: 0.40
Nodes (3): Migration, Backfill: las Compras que ya existían (con proveedor_nombre/rfc_emisor     de te, vincular_proveedores_existentes()

### Community 68 - "Community 68"
Cohesion: 0.40
Nodes (3): agregar_cuenta_ajuste_apertura(), Migration, Agrega 304 (Resultado de ejercicios anteriores) y su subcuenta 304.01     bajo e

### Community 69 - "Community 69"
Cohesion: 0.40
Nodes (3): cargar_configuracion_otros_ingresos(), Migration, Mapea OTROS_INGRESOS_CLIENTE a la cuenta 402.02 'Otros ingresos', que     ya exi

### Community 70 - "Community 70"
Cohesion: 0.40
Nodes (3): Migration, Producción arrastra una UnidadNegocio con clave 'EVENTOS' (creada a mano,     co, renombrar_eventos_a_quinta()

### Community 72 - "Community 72"
Cohesion: 0.50
Nodes (4): exportar_reporte_excel(), Reporte de pagos de Airbnb para el contador., Genera archivo Excel con el reporte de pagos., reporte_pagos_airbnb()

### Community 73 - "Community 73"
Cohesion: 0.50
Nodes (3): AuthConfig, QktAuthConfig, Renombra el grupo 'Authentication and Authorization' del admin.

### Community 75 - "Community 75"
Cohesion: 0.50
Nodes (3): migrar_proveedores_texto_a_fk(), Migration, Paso de datos: toma el texto de proveedor_legacy, crea registros     en la tabla

### Community 80 - "Community 80"
Cohesion: 0.50
Nodes (3): marcar_existentes_como_visibles_en_galeria(), Migration, Los registros creados antes de este cambio se quedaron en False     (default ant

### Community 83 - "Community 83"
Cohesion: 0.67
Nodes (3): _extract_model_subgroups(), get_side_menu_grouped(), Agrupa apps y modelos del sidebar de Jazzmin como submenú de otra app.  Jazzmin

## Knowledge Gaps
- **84 isolated node(s):** `Migration`, `Migration`, `Migration`, `Migration`, `Migration` (+79 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **105 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Cotizacion` connect `Community 0` to `Community 2`, `Community 3`, `Community 4`, `Community 6`, `Community 7`, `Community 9`, `Community 10`, `Community 11`, `Community 16`, `Community 18`, `Community 19`, `Community 21`, `Community 153`, `Community 154`, `Community 30`, `Community 34`, `Community 36`, `Community 43`, `Community 49`, `Community 64`, `Community 74`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Why does `PagoAirbnb` connect `Community 4` to `Community 32`, `Community 3`, `Community 37`, `Community 5`, `Community 71`, `Community 39`, `Community 40`, `Community 7`, `Community 12`, `Community 15`, `Community 17`, `Community 50`, `Community 51`, `Community 21`, `Community 25`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `UnidadNegocio` connect `Community 1` to `Community 0`, `Community 33`, `Community 5`, `Community 8`, `Community 9`, `Community 41`, `Community 14`, `Community 21`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Are the 163 inferred relationships involving `Decimal` (e.g. with `.calcular_retenciones()` and `.tarifa_por_noche()`) actually correct?**
  _`Decimal` has 163 INFERRED edges - model-reasoned connections that need verification._
- **Are the 52 inferred relationships involving `Cotizacion` (e.g. with `bloquear_en_airbnb()` and `AsignacionEspacioAdmin`) actually correct?**
  _`Cotizacion` has 52 INFERRED edges - model-reasoned connections that need verification._
- **Are the 74 inferred relationships involving `PortalCliente` (e.g. with `AsignacionEspacioAdmin` and `AsignacionEspacioInline`) actually correct?**
  _`PortalCliente` has 74 INFERRED edges - model-reasoned connections that need verification._
- **Are the 44 inferred relationships involving `Cliente` (e.g. with `AsignacionEspacioAdmin` and `AsignacionEspacioInline`) actually correct?**
  _`Cliente` has 44 INFERRED edges - model-reasoned connections that need verification._