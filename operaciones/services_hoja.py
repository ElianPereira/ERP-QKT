"""
Hoja de operación del evento: PDF operativo (PRE / DURANTE / POST) que se imprime y
se lleva en la Quinta. Es solo lectura sobre datos que ya existen; no persiste nada.

Qué NO lleva, a propósito: precios, costos, proveedores, RFC, correo ni datos fiscales o de
identificación del cliente (el PDF circula entre personal operativo).
"""
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

from django.db.models import Q

from comercial.models import Cotizacion, Producto
from core_erp.documentos import render_pdf
from core_erp.horarios import formato_hora_ampm

from .constantes import (
    AFORO_MAXIMO,
    OFFSET_DESMONTAJE,
    OFFSET_LLEGADA_EQUIPO,
    OFFSET_LLEGADA_PROVEEDORES,
    OFFSET_REVISION_ANTES_DE_EVENTO,
    TOLERANCIA_SALDO,
    WHATSAPP_ATENCION_QUINTA,
)
from .models import PlantillaChecklist
from .services import _fecha_larga

PLANTILLA = 'operaciones/pdf_hoja_operacion.html'
MAX_HOJAS = 15
ESTADOS_OPERABLES = ('CONFIRMADA', 'EJECUTADA')
SERVICIOS_OPERABLES = ('EVENTO', 'PASADIA', 'ARRENDAMIENTO', 'HOSPEDAJE')
HORA_EN_BLANCO = '____'
CENTAVOS = Decimal('0.01')

_FLAGS_BARRA = (
    'incluye_refrescos', 'incluye_cerveza', 'incluye_licor_nacional',
    'incluye_licor_premium', 'incluye_cocteleria_basica', 'incluye_cocteleria_premium',
)

CHECKLIST_CIERRE = (
    'Apagar el equipo de sonido al concluir el horario contratado',
    'Cerrar la barra y registrar lo que regresó',
    'Revisar baños, áreas comunes y alberca',
    'Recoger objetos olvidados y entregarlos al encargado',
    'Retirar a proveedores y cerrar el montaje',
    'Revisar daños y faltantes de mobiliario y áreas',
    'Apagar luces y equipos',
    'Cerrar portón y áreas de acceso',
)


def cotizaciones_operables(fechas=None):
    """Ventas confirmadas o ejecutadas de eventos, pasadías, arrendamientos y hospedajes, con todo precargado."""
    qs = Cotizacion.objects.filter(estado__in=ESTADOS_OPERABLES, tipo_servicio__in=SERVICIOS_OPERABLES)
    if fechas is not None:
        fechas = list(fechas)
        qs = qs.filter(Q(fecha_evento__in=fechas) | Q(fecha_actividad__in=fechas))
    return (
        qs.select_related('cliente', 'tipo_evento', 'insumo_barman', 'insumo_auxiliar', 'deposito_garantia')
        .prefetch_related('items__producto', 'items__insumo', 'pagos', 'contratos__firma', 'tareas_operativas')
        .order_by('fecha_evento', 'hora_inicio', 'pk')
    )


def _hora(hora):
    return formato_hora_ampm(hora) or HORA_EN_BLANCO


def _con_offset(hora, offset):
    if not hora:
        return HORA_EN_BLANCO
    return formato_hora_ampm((datetime.combine(datetime(2000, 1, 1), hora) + offset).time())


def _cantidad(valor):
    return format(Decimal(valor).normalize(), 'f')


def _servicios(cotizacion):
    orden = [clave for clave, _ in Producto.GRUPO_COTIZADOR_CHOICES]
    grupos = {}
    for item in cotizacion.items.all():
        producto = item.producto
        etiqueta = producto.get_grupo_cotizador_display() if producto and producto.grupo_cotizador else 'Otros'
        clave = producto.grupo_cotizador if producto and producto.grupo_cotizador else 'zzz'
        descripcion = item.descripcion or (producto.nombre if producto else '') \
            or (item.insumo.nombre if item.insumo else '') or '—'
        grupos.setdefault((orden.index(clave) if clave in orden else len(orden), etiqueta), []).append(
            {'descripcion': descripcion, 'cantidad': _cantidad(item.cantidad)})
    return [{'grupo': etiqueta, 'lineas': lineas} for (_, etiqueta), lineas in sorted(grupos.items())]


def _insumos_barra(cotizacion):
    """Nombre, cantidad y unidad de la lista de barra; nunca proveedor ni costos."""
    from comercial.views import generar_lista_compras_barra

    try:
        lista = generar_lista_compras_barra(cotizacion)
    except Exception:  # la hoja sale aunque falle el cálculo de barra
        return []
    return [{'nombre': e.get('item', ''), 'cantidad': e.get('cantidad', ''), 'unidad': e.get('unidad', '')}
            for items in lista.values() for e in items]


def _barra(cotizacion):
    modelo = type(cotizacion)
    incluye = [str(modelo._meta.get_field(f).verbose_name) for f in _FLAGS_BARRA if getattr(cotizacion, f)]
    if not incluye:
        return None
    return {
        'incluye': incluye,
        'horas': cotizacion.horas_servicio,
        'barman': cotizacion.insumo_barman.nombre if cotizacion.insumo_barman else '',
        'auxiliar': cotizacion.insumo_auxiliar.nombre if cotizacion.insumo_auxiliar else '',
        'insumos': _insumos_barra(cotizacion),
    }


def _saldo(cotizacion):
    """Misma regla de `saldo_pendiente()` (VENTA neta de reembolsos), sobre los pagos precargados."""
    pagos = list(cotizacion.pagos.all())
    ingresos = sum((p.monto for p in pagos if p.tipo == 'INGRESO' and p.concepto == 'VENTA'), Decimal('0.00'))
    reembolsos = sum((p.monto for p in pagos if p.tipo == 'REEMBOLSO'), Decimal('0.00'))
    return (cotizacion.precio_final - (ingresos - reembolsos)).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def _deposito(cotizacion):
    deposito = getattr(cotizacion, 'deposito_garantia', None)
    if deposito is None:
        return None
    return {
        'monto': deposito.monto,
        'estado': deposito.get_estado_display(),
        'en_custodia': deposito.en_custodia,
    }


def _contrato_firmado(cotizacion):
    for contrato in cotizacion.contratos.all():
        firma = getattr(contrato, 'firma', None)
        if firma is not None and firma.firmado:
            return True
    return False


def _checklist(cotizacion, plantillas):
    tipo = PlantillaChecklist.TIPO_POR_SERVICIO.get(cotizacion.tipo_servicio)
    if not tipo:
        return None
    if tipo not in plantillas:
        plantillas[tipo] = (PlantillaChecklist.objects.filter(tipo=tipo, activa=True)
                            .prefetch_related('items').first())
    plantilla = plantillas[tipo]
    if plantilla is None:
        return None
    tareas = [t for t in cotizacion.tareas_operativas.all() if t.plantilla_id == plantilla.pk]
    tarea = tareas[0] if tareas else None
    return {
        'titulo': plantilla.titulo_mensaje(),
        'items': [i.texto for i in sorted(plantilla.items.all(), key=lambda i: i.orden)],
        'hora_entrada': formato_hora_ampm(tarea.hora_entrada) if tarea else '',
    }


def _a_cargo(fecha) -> dict:
    """Encargado (o su suplente) y equipo de apoyo vigentes el día del servicio, en una sola consulta.
    Mismo criterio que `comunicacion.services_responsables.responsable` y `equipo_apoyo`."""
    from comunicacion.models import Responsable
    from comunicacion.services_responsables import TEMAS_APOYO
    from comunicacion.services_visitas import telefono_legible

    def vigente(r):
        return (r.vigente_desde is None or r.vigente_desde <= fecha) and (r.vigente_hasta is None or r.vigente_hasta >= fecha)

    filas = [r for r in Responsable.objects.filter(rol__in=('ENCARGADO', 'APOYO'), activo=True)
             .exclude(telefono_wa='').order_by('-id') if vigente(r)]
    encargados = [r for r in filas if r.rol == 'ENCARGADO']
    suplentes = [r for r in encargados if r.vigente_desde or r.vigente_hasta]
    encargado = (suplentes or encargados or [None])[0]
    apoyo = sorted((r for r in filas if r.rol == 'APOYO' and r.vigente_desde and r.vigente_hasta),
                   key=lambda r: (r.nombre, r.id))
    return {
        'encargado': ({'nombre': encargado.nombre, 'telefono': telefono_legible(encargado.telefono_wa)}
                      if encargado else None),
        'apoyo': [{
            'nombre': r.nombre, 'telefono': telefono_legible(r.telefono_wa),
            'temas': ', '.join(str(Responsable._meta.get_field(c).verbose_name)
                               for c in TEMAS_APOYO.values() if getattr(r, c)),
        } for r in apoyo],
    }


def _es_estancia(cotizacion) -> bool:
    return cotizacion.tipo_servicio == 'HOSPEDAJE' and bool(cotizacion.fecha_salida)


def _fecha_larga_hoja(cotizacion) -> str:
    inicio = _fecha_larga(cotizacion.fecha_evento, con_anio=True)
    if not _es_estancia(cotizacion):
        return inicio
    return f"{inicio} al {_fecha_larga(cotizacion.fecha_salida, con_anio=True)}"


def _horario(cotizacion) -> str:
    if _es_estancia(cotizacion):
        return f"Entrada {_hora(cotizacion.hora_inicio)} · Salida {_hora(cotizacion.hora_fin)}"
    return f"{_hora(cotizacion.hora_inicio)} a {_hora(cotizacion.hora_fin)}"


def _actividad(cotizacion):
    """(inicio, fin) del evento dentro de una estancia, o None. Un fin menor al inicio cruza la medianoche."""
    datos = (cotizacion.fecha_actividad, cotizacion.hora_actividad_inicio, cotizacion.hora_actividad_fin)
    if not _es_estancia(cotizacion) or any(d is None for d in datos):
        return None
    fecha, hora_inicio, hora_fin = datos
    inicio = datetime.combine(fecha, hora_inicio)
    fin = datetime.combine(fecha, hora_fin)
    return inicio, fin if fin > inicio else fin + timedelta(days=1)


def _horario_actividad(actividad) -> str:
    if not actividad:
        return ''
    inicio, fin = actividad
    return f"{_fecha_larga(inicio.date())}, {_hora(inicio.time())} a {_hora(fin.time())}"


def _cronograma_estancia(cotizacion) -> list:
    """Momentos de la estancia y, si lo hay, de su evento, en orden cronológico."""
    def momento(etiqueta, fecha, hora, offset=timedelta()):
        cuando = datetime.combine(fecha, hora or time.min) + offset
        return cuando, etiqueta, _hora(cuando.time()) if hora else HORA_EN_BLANCO

    entrada, salida = cotizacion.fecha_evento, cotizacion.fecha_salida
    actividad = _actividad(cotizacion)
    revision = OFFSET_DESMONTAJE
    if actividad and cotizacion.hora_fin:
        checkout = datetime.combine(salida, cotizacion.hora_fin)
        # Si el evento empieza antes de la revisión habitual, las habitaciones se revisan antes.
        if checkout <= actividad[0] <= checkout + OFFSET_DESMONTAJE:
            revision = min(OFFSET_REVISION_ANTES_DE_EVENTO, actividad[0] - checkout)
    momentos = [
        momento('Habitaciones listas', entrada, cotizacion.hora_inicio, OFFSET_LLEGADA_PROVEEDORES),
        momento(f"Entrada ({entrada:%d/%m})", entrada, cotizacion.hora_inicio),
        momento(f"Salida ({salida:%d/%m})", salida, cotizacion.hora_fin),
        momento('Revisión de habitaciones', salida, cotizacion.hora_fin, revision),
    ]
    if actividad:
        inicio, fin = actividad
        for etiqueta, base, offset in (
            ('Llegada del equipo', inicio, OFFSET_LLEGADA_EQUIPO),
            ('Llegada de proveedores', inicio, OFFSET_LLEGADA_PROVEEDORES),
            ('Inicio del evento', inicio, timedelta()),
            ('Cierre del evento', fin, timedelta()),
            ('Desmontaje', fin, OFFSET_DESMONTAJE),
        ):
            cuando = base + offset
            momentos.append(momento(f"{etiqueta} ({cuando:%d/%m})", cuando.date(), cuando.time()))
    # `sorted` es estable: los empates conservan el orden de captura.
    return [(etiqueta, hora) for _, etiqueta, hora in sorted(momentos, key=lambda m: m[0])]


def _cronograma(cotizacion) -> list:
    if _es_estancia(cotizacion):
        return _cronograma_estancia(cotizacion)
    return [
        ('Llegada del equipo', _con_offset(cotizacion.hora_inicio, OFFSET_LLEGADA_EQUIPO)),
        ('Llegada de proveedores', _con_offset(cotizacion.hora_inicio, OFFSET_LLEGADA_PROVEEDORES)),
        ('Inicio del servicio', _hora(cotizacion.hora_inicio)),
        ('Cierre del servicio', _hora(cotizacion.hora_fin)),
        ('Desmontaje', _con_offset(cotizacion.hora_fin, OFFSET_DESMONTAJE)),
    ]


def armar_contexto_hoja(cotizacion, plantillas=None, a_cargo=None) -> dict:
    """Datos de una hoja, listos para la plantilla (sin precios, costos ni datos fiscales).
    `a_cargo` es un caché por fecha para no repetir consultas en un PDF de varias hojas."""
    plantillas = {} if plantillas is None else plantillas
    a_cargo = {} if a_cargo is None else a_cargo
    if cotizacion.fecha_evento not in a_cargo:
        a_cargo[cotizacion.fecha_evento] = _a_cargo(cotizacion.fecha_evento)
    saldo = _saldo(cotizacion)
    actividad = _actividad(cotizacion)
    aforo = AFORO_MAXIMO.get('EVENTO' if actividad else cotizacion.tipo_servicio)
    return {
        'folio': f"COT-{cotizacion.pk:03d}",
        'nombre_evento': cotizacion.nombre_evento,
        'tipo_servicio': cotizacion.get_tipo_servicio_display(),
        'tipo_evento': str(cotizacion.tipo_evento) if cotizacion.tipo_evento else '',
        'fecha': cotizacion.fecha_evento,
        'fecha_larga': _fecha_larga_hoja(cotizacion),
        'hora_inicio': _hora(cotizacion.hora_inicio),
        'horario': _horario(cotizacion),
        'num_personas': cotizacion.num_personas,
        'horario_actividad': _horario_actividad(actividad),
        'actividad': ({'fecha': actividad[0].date(), 'hora': actividad[0].time()} if actividad else None),
        'hora_orden': cotizacion.hora_inicio or time.min,
        'cliente_nombre': cotizacion.cliente.nombre,
        'cliente_telefono': cotizacion.cliente.telefono,
        'servicios': _servicios(cotizacion),
        'barra': _barra(cotizacion),
        'notas_cliente': cotizacion.notas_cliente,
        'saldo': saldo,
        'por_cobrar': saldo > Decimal(str(TOLERANCIA_SALDO)),
        'deposito': _deposito(cotizacion),
        'contrato_firmado': _contrato_firmado(cotizacion),
        'identificacion_revisada': cotizacion.identificacion_revisada,
        'documentos_no_requeridos': cotizacion.documentos_no_requeridos,
        'checklist': _checklist(cotizacion, plantillas),
        'cronograma': _cronograma(cotizacion),
        'aforo_maximo': aforo,
        'whatsapp_atencion': WHATSAPP_ATENCION_QUINTA,
        'a_cargo': a_cargo[cotizacion.fecha_evento],
        'checklist_cierre': CHECKLIST_CIERRE,
        'incidentes': range(6),
        'renglones_barra': range(8),
        'renglones_danos': range(4),
    }


def _indice(hojas):
    """Portada de varias hojas: agrupadas por día (fecha · evento · hora). El evento dentro de una
    estancia lleva su propio renglón en su día."""
    renglones = []
    for hoja in hojas:
        renglones.append(hoja)
        actividad = hoja['actividad']
        if actividad:
            renglones.append({
                **hoja, 'fecha': actividad['fecha'], 'hora_orden': actividad['hora'],
                'fecha_larga': _fecha_larga(actividad['fecha'], con_anio=True),
                'hora_inicio': _hora(actividad['hora']), 'tipo_servicio': 'Evento (en hospedaje)',
            })
    dias = []
    for renglon in sorted(renglones, key=lambda r: (r['fecha'], r['hora_orden'])):
        if not dias or dias[-1]['fecha'] != renglon['fecha']:
            dias.append({'fecha': renglon['fecha'], 'fecha_larga': renglon['fecha_larga'], 'hojas': []})
        dias[-1]['hojas'].append(renglon)
    return dias


def contexto_documento_hoja(cotizaciones) -> dict:
    plantillas, a_cargo = {}, {}
    hojas = [armar_contexto_hoja(c, plantillas, a_cargo) for c in cotizaciones]
    return {
        'titulo': 'Hoja de operación' if len(hojas) == 1 else 'Agenda de operación',
        'hojas': hojas,
        'indice': _indice(hojas) if len(hojas) > 1 else [],
    }


def generar_hoja_pdf(cotizaciones, usuario=None, request=None) -> bytes:
    """Un solo PDF (un solo render) con la hoja de cada cotización; con varias, lleva portada-índice."""
    if request is None and usuario is not None:
        request = SimpleNamespace(user=usuario)
    return render_pdf(PLANTILLA, contexto_documento_hoja(cotizaciones), request=request)
