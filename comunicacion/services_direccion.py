"""
Dirección por WhatsApp (Issue #431, fase 4 del plan de piloto automático).

Resumen de cada lunes con «Detecté» (hasta 3 hallazgos con [Crear tarea] e
[Ignorar]), alerta de desviación de la venta del mes y aprobación semanal de la
nómina. Las cifras salen de `indicadores`, `reportes` y `contabilidad`: aquí no
se recalcula nada. Todo monto es `Decimal`; los botones solo valen desde el
teléfono activo del responsable de DIRECCION. Nada lanza: un fallo de
WhatsApp o de un detector no debe tumbar el cron.
"""
import logging
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import ComunicacionCliente, Hallazgo, Pendiente
from .services import normalizar_telefono_wa
from .services_pendientes import _base_url, _enviar_aviso, registrar_pendiente, url_bandeja, ventana_abierta
from .services_responsables import responsable, telefono_de

logger = logging.getLogger(__name__)

MAX_HALLAZGOS = 3
UMBRAL_DESVIACION = Decimal('70')
DIA_DESVIACION = 15
DIAS_SIN_SEGUIMIENTO = 7
DIAS_SIN_FACTURA = 60
UMBRAL_DIAS_LIBRES = 15
HORIZONTE_DIAS_LIBRES = 30
CENTAVOS = Decimal('0.01')

RE_BOTON = re.compile(r'^(hal|nom):(\d+):(tarea|ign|ok)$')
ACCIONES_VALIDAS = {'hal': ('tarea', 'ign'), 'nom': ('ok',)}


def id_boton_hallazgo(hallazgo_id: int, accion: str) -> str:
    return f'hal:{hallazgo_id}:{accion}'


def id_boton_nomina(aprobacion_id: int) -> str:
    return f'nom:{aprobacion_id}:ok'


def es_boton(texto: str) -> bool:
    coincide = RE_BOTON.match((texto or '').strip())
    return bool(coincide) and coincide.group(3) in ACCIONES_VALIDAS[coincide.group(1)]


def dinero(valor) -> str:
    return f'${Decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP):,.2f}'


def _es_direccion(telefono: str) -> bool:
    """Solo el teléfono activo de un responsable de DIRECCION de verdad (no el
    número de respaldo, ni uno aún pendiente de «ACEPTO»)."""
    resp = responsable('DIRECCION')
    tel = normalizar_telefono_wa(telefono)
    return bool(resp and tel and normalizar_telefono_wa(resp.telefono_wa) == tel)


def _enviado(comm) -> bool:
    return comm is not None and comm.estado != 'FALLIDO'


# ───────────────────────────── Hallazgos ──────────────────────────────────────

def _hallazgos_margen(hoy):
    from comercial.models import Producto

    for producto in Producto.objects.order_by('id'):
        try:
            costo = Decimal(producto.calcular_costo() or 0)
            if costo > 0 and producto.sugerencia_precio() < costo:
                yield {'clave': f'margen:{producto.pk}', 'tipo': 'MARGEN',
                       'titulo': f'«{producto.nombre}» se vende con margen negativo',
                       'detalle': (f'Precio base {dinero(producto.sugerencia_precio())} contra costo {dinero(costo)} '
                                   f'(sin IVA). Corrige el precio o la receta en Productos.')}
        except Exception:
            logger.exception("Hallazgos: no se pudo revisar el producto %s", producto.pk)


def _hallazgos_sin_factura(hoy):
    from comercial.models import Pago

    desde = hoy - timedelta(days=DIAS_SIN_FACTURA)
    pagos = (Pago.objects.filter(tipo='INGRESO', fecha_pago__gte=desde, solicitudes_factura__isnull=True,
                                 cotizacion__es_historica=False)
             .exclude(metodo='CONDONACION').exclude(notas__startswith='Reactivación automática')
             .select_related('cotizacion').order_by('id'))
    for pago in pagos:
        yield {'clave': f'sin_factura:{pago.pk}', 'tipo': 'SIN_FACTURA',
               'titulo': f'Pago de {dinero(pago.monto)} de COT-{pago.cotizacion_id:03d} sin solicitud de factura',
               'detalle': f'Pago #{pago.pk} del {pago.fecha_pago:%d/%m/%Y}. El contador no recibió solicitud para timbrarlo.'}


def _hallazgos_sin_seguimiento(hoy):
    from comercial.models import Cotizacion

    limite = hoy - timedelta(days=DIAS_SIN_SEGUIMIENTO)
    cotizaciones = (Cotizacion.objects.filter(estado='COTIZADA', fecha_evento__gt=hoy, created_at__date__lte=limite,
                                              pagos__isnull=True).select_related('cliente').order_by('id'))
    for cot in cotizaciones:
        con_seguimiento = ComunicacionCliente.objects.filter(cotizacion=cot, tipo='SEGUIMIENTO').exclude(
            estado='FALLIDO').exists()
        if not con_seguimiento:
            yield {'clave': f'seguimiento:{cot.pk}', 'tipo': 'SIN_SEGUIMIENTO',
                   'titulo': f'COT-{cot.pk:03d} lleva más de {DIAS_SIN_SEGUIMIENTO} días cotizada sin seguimiento',
                   'detalle': (f'{cot.cliente} · evento del {cot.fecha_evento:%d/%m/%Y} · {dinero(cot.precio_final)}. '
                               f'Nadie le ha dado seguimiento y no ha pagado.')}


def _hallazgos_precio_para_entrar(hoy, parametros=None):
    from core_erp.impuestos import con_iva
    from indicadores.models import ParametrosNegocio
    from indicadores.services.plan_anual import PlanAnualService

    p = parametros or ParametrosNegocio.vigente()
    plan = PlanAnualService.generar(hoy.year, p, hoy=hoy, con_umbrales=True)
    for tipo, s in plan['servicios'].items():
        if s['entra'] or not s.get('umbral'):
            continue
        yield {'clave': f'umbral:{tipo}:{hoy.year}', 'tipo': 'PRECIO_ENTRAR',
               'titulo': f'{s["nombre"]} no entra al plan {hoy.year}',
               'detalle': (f'Hoy se cotiza en {dinero(con_iva(s["neto"]))} con IVA; entraría al plan desde '
                           f'{dinero(s["umbral"]["precio"])}. Revisa su precio o su costo en Parámetros.')}


def _hallazgos_dias_libres(hoy):
    from reportes.services.operacion import OcupacionService

    inicio, fin = hoy + timedelta(days=1), hoy + timedelta(days=HORIZONTE_DIAS_LIBRES)
    datos = OcupacionService.generar(inicio, fin)
    if datos['dias_libres'] >= UMBRAL_DIAS_LIBRES:
        yield {'clave': f'libres:{hoy:%Y-%m}', 'tipo': 'DIAS_LIBRES',
               'titulo': f'{datos["dias_libres"]} días libres en los próximos {HORIZONTE_DIAS_LIBRES}',
               'detalle': (f'Ocupación de {inicio:%d/%m} a {fin:%d/%m}: {datos["ocupacion"]} %. '
                           f'Buen momento para promocionar esas fechas.')}


DETECTORES = (_hallazgos_margen, _hallazgos_sin_factura, _hallazgos_sin_seguimiento,
              _hallazgos_precio_para_entrar, _hallazgos_dias_libres)


def detectar(hoy=None, detectores=DETECTORES) -> List[Hallazgo]:
    """Hasta `MAX_HALLAZGOS` hallazgos abiertos, a lo más uno por tipo y en orden de
    prioridad. Excluye lo ya ignorado o convertido en tarea; lo propuesto sin
    responder se vuelve a mostrar. Cada detector falla por su cuenta."""
    hoy = hoy or timezone.localdate()
    resueltos = set(Hallazgo.objects.exclude(estado='PROPUESTO').values_list('clave', flat=True))
    elegidos = []
    for detector in detectores:
        if len(elegidos) >= MAX_HALLAZGOS:
            break
        try:
            candidato = next((c for c in detector(hoy) if c['clave'] not in resueltos), None)
        except Exception:
            logger.exception("Hallazgos: falló %s", getattr(detector, '__name__', detector))
            continue
        if candidato is None:
            continue
        hallazgo, creado = Hallazgo.objects.get_or_create(
            clave=candidato['clave'],
            defaults={'tipo': candidato['tipo'], 'titulo': candidato['titulo'][:200], 'detalle': candidato['detalle']})
        if not creado and hallazgo.estado == 'PROPUESTO':
            Hallazgo.objects.filter(pk=hallazgo.pk).update(
                titulo=candidato['titulo'][:200], detalle=candidato['detalle'], updated_at=timezone.now())
            hallazgo.refresh_from_db()
        elegidos.append(hallazgo)
    return elegidos


def crear_tarea(hallazgo, usuario=None) -> Optional[Pendiente]:
    """[Crear tarea]: el hallazgo pasa a un `Pendiente` NORMAL de Dirección. Un solo uso."""
    usuario = usuario if getattr(usuario, 'pk', None) else None
    ahora = timezone.now()
    with transaction.atomic():
        filas = Hallazgo.objects.filter(pk=hallazgo.pk, estado='PROPUESTO').update(
            estado='TAREA', resuelto_en=ahora, resuelto_por=usuario, updated_at=ahora)
    if not filas:
        return None
    hallazgo.refresh_from_db()
    pendiente = registrar_pendiente(
        titulo=hallazgo.titulo, rol='DIRECCION', tipo='HALLAZGO', severidad='NORMAL', detalle=hallazgo.detalle,
        clave_idempotencia=f'hallazgo:{hallazgo.pk}', usuario=usuario)
    if pendiente is not None:
        Hallazgo.objects.filter(pk=hallazgo.pk).update(pendiente=pendiente)
    return pendiente


def ignorar(hallazgo, usuario=None) -> bool:
    """[Ignorar]: no se vuelve a proponer."""
    usuario = usuario if getattr(usuario, 'pk', None) else None
    ahora = timezone.now()
    return bool(Hallazgo.objects.filter(pk=hallazgo.pk, estado='PROPUESTO').update(
        estado='IGNORADO', resuelto_en=ahora, resuelto_por=usuario, updated_at=ahora))


# ───────────────────────────── Resumen semanal ────────────────────────────────

def caja_segun_libros() -> Decimal:
    from contabilidad.models import CuentaBancaria

    total = Decimal('0.00')
    for cuenta in CuentaBancaria.objects.filter(activa=True):
        total += Decimal(cuenta.saldo_actual)
    return total.quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def _url_hallazgos() -> str:
    return _base_url() + reverse('admin:comunicacion_hallazgo_changelist') + '?estado__exact=PROPUESTO'


def texto_resumen(hoy, mes: dict, caja: Decimal, abiertos: int, hallazgos) -> str:
    if mes.get('meta_venta'):
        venta = f'{dinero(mes["venta"])} de {dinero(mes["meta_venta"])} ({mes["pct_meta"]} %)'
    else:
        venta = f'{dinero(mes["venta"])} (sin meta)'
    partes = [f'Resumen semanal del {hoy:%d/%m}. Venta de {mes["etiqueta"]}: {venta}.']
    ocupacion = f'Ocupación: {mes["ocupacion"]} %'
    if mes.get('ocupacion_plan') is not None:
        ocupacion += f' (plan {mes["ocupacion_plan"]} %)'
    partes.append(ocupacion + '.')
    partes.append(f'Caja según libros: {dinero(caja)}.')
    partes.append(f'Pendientes abiertos: {abiertos}.')
    if hallazgos:
        partes.append('Detecté: ' + ' '.join(f'{n}) {h.titulo}.' for n, h in enumerate(hallazgos, 1))
                      + f' Revísalos: {_url_hallazgos()}')
    partes.append(f'Bandeja: {url_bandeja()}')
    return ' '.join(partes)


def enviar_resumen_semanal(hoy=None) -> int:
    """Resumen de venta contra meta, ocupación, caja, pendientes y «Detecté» a DIRECCION.
    Idempotente por semana. Devuelve 1 si salió, 0 si no."""
    from indicadores.services.tablero import TableroService

    hoy = hoy or timezone.localdate()
    telefono = telefono_de('DIRECCION', hoy)
    if not telefono:
        logger.error("Resumen semanal: no hay responsable de DIRECCION")
        return 0
    mes = TableroService.generar(hoy.year, hoy.month, hoy=hoy)['mes']
    abiertos = Pendiente.objects.filter(estado='ABIERTO', rol='DIRECCION').count()
    hallazgos = detectar(hoy)
    lunes = hoy - timedelta(days=hoy.weekday())
    comm = _enviar_aviso(telefono, texto_resumen(hoy, mes, caja_segun_libros(), abiertos, hallazgos),
                         clave=f'dir:resumen:{lunes}:{telefono}')
    if not _enviado(comm):
        return 0
    if ventana_abierta(telefono):
        for hallazgo in hallazgos:
            texto = f'Detecté: {hallazgo.titulo}. {hallazgo.detalle}'
            botones = [(id_boton_hallazgo(hallazgo.pk, 'tarea'), 'Crear tarea'),
                       (id_boton_hallazgo(hallazgo.pk, 'ign'), 'Ignorar')]
            if _enviado(_enviar_aviso(telefono, texto, botones=botones)):
                Hallazgo.objects.filter(pk=hallazgo.pk).update(propuesto_en=timezone.now())
    return 1


# ───────────────────────────── Desviación ─────────────────────────────────────

def revisar_desviacion(hoy=None) -> Optional[Pendiente]:
    """Desde el día 15: si la venta neta del mes va por debajo del 70 % de su meta,
    pendiente URGENTE para Dirección, una sola vez por mes. Sin meta, no alerta."""
    from indicadores.services.tablero import TableroService

    hoy = hoy or timezone.localdate()
    if hoy.day < DIA_DESVIACION:
        return None
    mes = TableroService.generar(hoy.year, hoy.month, hoy=hoy)['mes']
    meta, pct = mes.get('meta_venta'), mes.get('pct_meta')
    if not meta or pct is None or Decimal(pct) >= UMBRAL_DESVIACION:
        return None
    return registrar_pendiente(
        titulo=f'La venta de {mes["etiqueta"]} va en {pct} % de la meta',
        detalle=(f'Venta neta {dinero(mes["venta"])} contra meta {dinero(meta)} al {hoy:%d/%m}. '
                 f'Umbral de alerta: {UMBRAL_DESVIACION} %.'),
        rol='DIRECCION', tipo='DESVIACION', severidad='URGENTE', clave_idempotencia=f'desviacion:{hoy:%Y-%m}')


# ───────────────────────────── Nómina ─────────────────────────────────────────

def _url_aprobacion(aprobacion) -> str:
    return _base_url() + reverse('admin:nomina_aprobacionnomina_change', args=[aprobacion.pk])


def enviar_nomina_semanal(hoy=None) -> int:
    """El viernes: total a pagar de la semana a DIRECCION con [Aprobar]. Una sola vez por semana."""
    from nomina.models import AprobacionNomina
    from nomina.services_aprobacion import registrar_semana

    hoy = hoy or timezone.localdate()
    telefono = telefono_de('DIRECCION', hoy)
    if not telefono:
        logger.error("Nómina semanal: no hay responsable de DIRECCION")
        return 0
    aprobacion = registrar_semana(hoy)
    if aprobacion is None or aprobacion.notificada_en or aprobacion.aprobada_en:
        return 0
    texto = (f'Nómina de la semana del {aprobacion.semana_inicio:%d/%m} al {aprobacion.semana_fin:%d/%m}: '
             f'{aprobacion.recibos} recibos, total a pagar {dinero(aprobacion.total)}. Aprobar solo deja constancia; '
             f'la transferencia se hace en el banco. Detalle: {_url_aprobacion(aprobacion)}')
    comm = _enviar_aviso(telefono, texto, botones=[(id_boton_nomina(aprobacion.pk), 'Aprobar')],
                         clave=f'dir:nomina:{aprobacion.semana_inicio}:{telefono}')
    if not _enviado(comm):
        return 0
    AprobacionNomina.objects.filter(pk=aprobacion.pk).update(notificada_en=timezone.now())
    return 1


def aprobar_nomina(aprobacion, usuario=None, telefono: str = '') -> bool:
    from nomina.services_aprobacion import aprobar

    resp = responsable('DIRECCION')
    nombre = (usuario.get_full_name() or usuario.get_username()) if getattr(usuario, 'pk', None) else (
        resp.nombre if resp else '')
    return aprobar(aprobacion, nombre=nombre, telefono=telefono, usuario=usuario)


# ───────────────────────────── Botones ────────────────────────────────────────

def atender_boton(telefono: str, texto: str) -> Optional[str]:
    """`hal:<id>:tarea|ign` y `nom:<id>:ok`: solo valen desde el teléfono de DIRECCION.
    Devuelve el texto con que contestarle, o None si no hizo nada."""
    from nomina.models import AprobacionNomina

    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide or not es_boton(texto) or not _es_direccion(telefono):
        return None
    tipo, pk, accion = coincide.group(1), int(coincide.group(2)), coincide.group(3)
    if tipo == 'nom':
        aprobacion = AprobacionNomina.objects.filter(pk=pk).first()
        if aprobacion is None:
            return None
        if aprobar_nomina(aprobacion, telefono=normalizar_telefono_wa(telefono)):
            return (f'Listo, quedó aprobada la nómina del {aprobacion.semana_inicio:%d/%m} al '
                    f'{aprobacion.semana_fin:%d/%m} ({dinero(aprobacion.total)}). Solo es constancia: '
                    f'la transferencia se hace en el banco.')
        return 'Esa nómina ya estaba aprobada.'
    hallazgo = Hallazgo.objects.filter(pk=pk).first()
    if hallazgo is None:
        return None
    if accion == 'tarea':
        if crear_tarea(hallazgo) is not None:
            return f'Listo, abrí la tarea en la bandeja: {hallazgo.titulo}.'
        return 'Ese hallazgo ya estaba atendido.'
    if ignorar(hallazgo):
        return f'Listo, lo ignoro: {hallazgo.titulo}.'
    return 'Ese hallazgo ya estaba atendido.'
