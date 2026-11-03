"""Ejecución de decisiones de marketing en Meta (Issue #468, fase 5). **Mueve dinero**: el diseño es conservador.

Reglas (todas verificadas aquí, no en la vista):

* Solo una `Decision` APROBADA, vigente (`expira_en`) y de acción ejecutable (PAUSAR, REANUDAR,
  AJUSTAR_PRESUPUESTO, REASIGNAR), y solo un superusuario activo. INFORMATIVA nunca ejecuta.
* Interruptor `ERP_MARKETING_ACCIONES_ACTIVAS` (apagado por defecto): apagado, todo es SIMULACION: se calcula con
  los datos locales y se registra lo que se haría, sin tocar la red. En modo REAL además hace falta el token de
  escritura (`META_ADS_TOKEN_ESCRITURA`); sin él no se crea nada ni se envía nada.
* Idempotencia (patrón de `comercial/services_cancelacion`): bajo `select_for_update` de la decisión se crean las
  filas de `AccionAnuncio` en EJECUTANDO y se confirma ANTES de hablar con Meta. Un segundo clic ve la ejecución en
  curso y no hace nada; si la fila lleva más de `MINUTOS_COLGADA` minutos sin resultado, no se repite (Meta pudo
  aplicarla): se marca FALLIDA y se pide verificar en Meta.
* Revalidación: antes de cambiar se lee el estado ACTUAL en Meta; si el presupuesto o el estado ya no son los de la
  propuesta, no se ejecuta con la cifra vieja (la decisión pasa a FALLIDA, no se envió nada).
* Límite duro de ±20 % por acción sobre el valor actual: se recalcula y se recorta (nunca excede, el recorte
  redondea hacia adentro). En una reasignación el monto se limita al 20 % de ambas campañas.
* Tope diario total (`ERP_MARKETING_PRESUPUESTO_MAX_DIARIO`): si el cambio SUBE la suma de presupuestos diarios de
  las campañas activas, la suma posterior no puede excederlo; vacío = no se permite subir. Bajar o mover (neto
  cero) siempre se permite. La suma solo ve presupuestos de campaña (CBO): los de conjuntos de anuncios no entran.
* REASIGNAR = dos ajustes (primero baja, luego sube). Si el segundo falla se revierte el primero; si la reversión
  también falla queda FALLIDA con un pendiente URGENTE a Dirección para verificar en Meta.
* Los importes son `Decimal` (ROUND_HALF_UP; los topes redondean hacia adentro). Nada de tokens en la bitácora.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import List, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from comunicacion.services_direccion import dinero
from comunicacion.services_pendientes import registrar_pendiente

from ..models import AccionAnuncio, Anuncio, Campana, Decision
from .proveedores import EstadoObjeto, ProveedorError, proveedor_meta

logger = logging.getLogger(__name__)

CENTAVOS = Decimal('0.01')
LIMITE_PCT = Decimal('20')
MINUTOS_COLGADA = 10
EJECUTABLES = (Decision.PAUSAR, Decision.REANUDAR, Decision.AJUSTAR, Decision.REASIGNAR)
MAX_RESPUESTA = 500


class Rechazo(Exception):
    """La acción no se puede (o no se debe) ejecutar. `terminal` = la decisión ya no sirve (pasa a FALLIDA);
    si no, queda APROBADA para reintentar. Nada se envió a Meta."""

    def __init__(self, mensaje, terminal=False):
        super().__init__(mensaje)
        self.terminal = terminal


@dataclass
class Paso:
    tipo: str
    objeto: object  # Campana | Anuncio
    anterior: Optional[Decimal] = None  # valor de la propuesta (presupuesto)
    pedido: Optional[Decimal] = None
    # calculados al revalidar
    actual: Optional[Decimal] = None
    nuevo: Optional[Decimal] = None
    sin_cambio: bool = False
    fila: Optional[AccionAnuncio] = None

    @property
    def objeto_tipo(self):
        return AccionAnuncio.CAMPANA if isinstance(self.objeto, Campana) else AccionAnuncio.ANUNCIO


# ───────────────────────────── Utilidades ─────────────────────────────────────

def _q(valor) -> Decimal:
    return Decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def _limpio(texto) -> str:
    texto = str(texto)
    for secreto in (settings.META_ADS_TOKEN, settings.META_ADS_TOKEN_ESCRITURA):
        if secreto:
            texto = texto.replace(secreto, '***')
    return texto[:MAX_RESPUESTA]


def modo_actual() -> str:
    return AccionAnuncio.REAL if settings.ERP_MARKETING_ACCIONES_ACTIVAS else AccionAnuncio.SIMULACION


def tope_diario() -> Optional[Decimal]:
    """Tope diario total en pesos; None si no está definido o es inválido (= no se permite subir)."""
    crudo = str(settings.ERP_MARKETING_PRESUPUESTO_MAX_DIARIO or '').strip()
    if not crudo:
        return None
    try:
        valor = Decimal(crudo)
    except Exception:
        return None
    return _q(valor) if valor > 0 else None


def recortar(actual: Decimal, pedido: Decimal) -> Decimal:
    """Lleva `pedido` al rango ±20 % de `actual`; el rango redondea hacia adentro para no excederlo nunca."""
    actual = Decimal(actual)
    piso = (actual * (100 - LIMITE_PCT) / 100).quantize(CENTAVOS, rounding=ROUND_UP)
    techo = (actual * (100 + LIMITE_PCT) / 100).quantize(CENTAVOS, rounding=ROUND_DOWN)
    return _q(min(max(Decimal(pedido), piso), techo))


def _dec(valor) -> Decimal:
    try:
        return Decimal(str(valor))
    except Exception:
        raise Rechazo('La propuesta trae un importe inválido.', terminal=True)


def ejecutable(decision) -> bool:
    return decision.tipo_accion in EJECUTABLES


# ───────────────────────────── Plan a partir de la propuesta ──────────────────

def _campana(pk, nombre_campo='campaña') -> Campana:
    obj = Campana.objects.filter(pk=pk, plataforma=Campana.META).first() if pk else None
    if obj is None:
        raise Rechazo(f'La {nombre_campo} de la propuesta ya no existe.', terminal=True)
    return obj


def planear(decision) -> List[Paso]:
    """Pasos que propone la decisión, resueltos contra los registros locales. No toca la red."""
    a = decision.accion_propuesta or {}
    tipo = decision.tipo_accion
    if tipo in (Decision.PAUSAR, Decision.REANUDAR):
        if a.get('nivel', 'anuncio') == 'campana':
            objeto = _campana(a.get('campana_id'))
        else:
            objeto = Anuncio.objects.filter(pk=a.get('anuncio_id')).select_related('campana').first()
            if objeto is None or (a.get('anuncio_externo') and objeto.id_externo != str(a['anuncio_externo'])):
                raise Rechazo('El anuncio de la propuesta ya no existe.', terminal=True)
        return [Paso(tipo, objeto)]
    if tipo == Decision.AJUSTAR:
        return [Paso(Decision.AJUSTAR, _campana(a.get('campana_id')),
                     anterior=_dec(a.get('presupuesto_actual')), pedido=_dec(a.get('presupuesto_nuevo')))]
    if tipo == Decision.REASIGNAR:
        monto = _dec(a.get('monto_diario'))
        desde, hacia = _campana(a.get('desde_campana_id')), _campana(a.get('hacia_campana_id'))
        if desde.pk == hacia.pk or monto <= 0:
            raise Rechazo('La reasignación propuesta no es válida.', terminal=True)
        return [Paso(Decision.AJUSTAR, desde, anterior=_dec(a.get('desde_nuevo')) + monto,
                     pedido=_dec(a.get('desde_nuevo'))),
                Paso(Decision.AJUSTAR, hacia, anterior=_dec(a.get('hacia_nuevo')) - monto,
                     pedido=_dec(a.get('hacia_nuevo')))]
    raise Rechazo('Esta decisión es informativa: no se ejecuta.')


def vista_previa(decision) -> List[str]:
    """Líneas «antes → después» para confirmar en pantalla, sacadas de la propuesta (los valores definitivos se
    recalculan contra Meta al ejecutar)."""
    try:
        pasos = planear(decision)
    except Rechazo:
        return []
    lineas = []
    for p in pasos:
        nombre = p.objeto.nombre or p.objeto.id_externo
        if p.tipo == Decision.AJUSTAR:
            lineas.append(f'«{nombre}»: {dinero(p.anterior)} → {dinero(p.pedido)} diarios')
        elif p.tipo == Decision.PAUSAR:
            lineas.append(f'«{nombre}»: activo → pausado')
        else:
            lineas.append(f'«{nombre}»: pausado → activo')
    return lineas


# ───────────────────────────── Lectura del estado ─────────────────────────────

def _estado_local(objeto) -> EstadoObjeto:
    if isinstance(objeto, Campana):
        return EstadoObjeto(objeto.id_externo, objeto.estado, objeto.presupuesto_diario,
                            'CAMPANA' if objeto.presupuesto_diario else '')
    return EstadoObjeto(objeto.id_externo, objeto.estado)


def _estado(modo, proveedor, objeto) -> EstadoObjeto:
    """REAL lee de Meta; SIMULACION usa lo ya sincronizado (sin red)."""
    if modo == AccionAnuncio.SIMULACION:
        return _estado_local(objeto)
    try:
        return proveedor.leer_estado(objeto.id_externo, con_presupuesto=isinstance(objeto, Campana))
    except ProveedorError as exc:
        raise Rechazo(f'No se pudo leer el estado en Meta: {_limpio(exc)}. No se envió nada.')


def _total_activo(modo, proveedor) -> Decimal:
    """Suma de presupuestos diarios de campañas activas (en REAL, leída de Meta en ese momento)."""
    if modo == AccionAnuncio.SIMULACION:
        filas = Campana.objects.filter(plataforma=Campana.META, estado__iexact='ACTIVE', presupuesto_diario__gt=0)
        return sum((Decimal(c.presupuesto_diario) for c in filas), Decimal('0.00'))
    try:
        return sum((Decimal(c.presupuesto_diario) for c in proveedor.listar_campanas()
                    if c.estado.upper() == 'ACTIVE' and c.presupuesto_diario), Decimal('0.00'))
    except ProveedorError as exc:
        raise Rechazo(f'No se pudo leer el presupuesto total en Meta: {_limpio(exc)}. No se envió nada.')


# ───────────────────────────── Revalidación y cálculo ─────────────────────────

ESTADOS_DETENIDOS = frozenset({'PAUSED', 'CAMPAIGN_PAUSED', 'ADSET_PAUSED', 'ARCHIVED', 'DELETED'})


def revalidar(pasos: List[Paso], modo, proveedor, decision) -> Decimal:
    """Calcula `actual`/`nuevo` de cada paso contra el estado vivo, aplica el ±20 % y el tope diario. Devuelve el
    cambio neto del presupuesto diario total. Lanza `Rechazo` sin haber enviado nada."""
    delta_neto = Decimal('0.00')
    if decision.tipo_accion == Decision.REASIGNAR:
        vivos = [_estado(modo, proveedor, p.objeto) for p in pasos]
        for p, vivo in zip(pasos, vivos):
            _exigir_presupuesto_campana(p, vivo)
            # Las dos deben estar activas: quitarle a una pausada no libera gasto real y el neto cero
            # escondería una subida del total activo sin pasar por el tope diario.
            if vivo.estado.upper() != 'ACTIVE':
                raise Rechazo(f'La campaña «{p.objeto.nombre}» no está activa ({vivo.estado or "sin estado"}); '
                              'no se reasigna presupuesto.', terminal=True)
            if abs(vivo.presupuesto_diario - p.anterior) > CENTAVOS:
                raise Rechazo(f'El presupuesto de «{p.objeto.nombre}» cambió desde la propuesta '
                              f'({dinero(p.anterior)} → {dinero(vivo.presupuesto_diario)}). No se ejecuta con la cifra vieja.',
                              terminal=True)
            p.actual = vivo.presupuesto_diario
        desde, hacia = pasos
        monto = min(p_monto(decision), (desde.actual * LIMITE_PCT / 100).quantize(CENTAVOS, rounding=ROUND_DOWN),
                    (hacia.actual * LIMITE_PCT / 100).quantize(CENTAVOS, rounding=ROUND_DOWN))
        if monto <= 0:
            raise Rechazo('El monto a mover quedó en cero tras aplicar el límite de 20 %.', terminal=True)
        desde.nuevo, hacia.nuevo = _q(desde.actual - monto), _q(hacia.actual + monto)
        delta_neto = _q(desde.nuevo - desde.actual) + _q(hacia.nuevo - hacia.actual)
    else:
        p = pasos[0]
        vivo = _estado(modo, proveedor, p.objeto)
        if p.tipo == Decision.AJUSTAR:
            _exigir_presupuesto_campana(p, vivo)
            if vivo.estado.upper() != 'ACTIVE':
                raise Rechazo(f'La campaña «{p.objeto.nombre}» ya no está activa ({vivo.estado or "sin estado"}).',
                              terminal=True)
            if abs(vivo.presupuesto_diario - p.anterior) > CENTAVOS:
                raise Rechazo(f'El presupuesto de «{p.objeto.nombre}» cambió desde la propuesta '
                              f'({dinero(p.anterior)} → {dinero(vivo.presupuesto_diario)}). No se ejecuta con la cifra vieja.',
                              terminal=True)
            p.actual = vivo.presupuesto_diario
            p.nuevo = recortar(p.actual, p.pedido)
            delta_neto = _q(p.nuevo - p.actual)
            p.sin_cambio = delta_neto == 0
        elif p.tipo == Decision.PAUSAR:
            estado = vivo.estado.upper()
            if not estado:
                raise Rechazo(f'Meta no informó el estado de «{p.objeto.nombre}»; no se puede confirmar la pausa.',
                              terminal=True)
            # Solo un estado que de verdad no gasta cuenta como «ya detenido»; WITH_ISSUES, IN_PROCESS o
            # PENDING_REVIEW pueden seguir gastando, así que se envía la pausa.
            p.sin_cambio = estado in ESTADOS_DETENIDOS
        else:  # REANUDAR
            if vivo.estado.upper() != 'PAUSED':
                raise Rechazo(f'«{p.objeto.nombre}» no está pausado ({vivo.estado or "sin estado"}); no se reanuda.',
                              terminal=True)
            if isinstance(p.objeto, Campana):
                if not vivo.presupuesto_diario:
                    _exigir_presupuesto_campana(p, vivo)
                delta_neto = _q(vivo.presupuesto_diario or 0)
    if delta_neto > 0:
        tope = tope_diario()
        if tope is None:
            raise Rechazo('No hay tope diario total definido (ERP_MARKETING_PRESUPUESTO_MAX_DIARIO): '
                          'no se permite subir presupuesto.')
        despues = _q(_total_activo(modo, proveedor) + delta_neto)
        if despues > tope:
            raise Rechazo(f'El presupuesto diario total quedaría en {dinero(despues)}, por encima del tope de '
                          f'{dinero(tope)}. No se ejecuta.')
    return delta_neto


def p_monto(decision) -> Decimal:
    return _dec((decision.accion_propuesta or {}).get('monto_diario'))


def _exigir_presupuesto_campana(paso, vivo):
    if vivo.nivel_presupuesto == 'CONJUNTO':
        raise Rechazo(f'El presupuesto de «{paso.objeto.nombre}» vive en sus conjuntos de anuncios, no en la '
                      'campaña: ajústalo directamente en Meta.', terminal=True)
    if vivo.presupuesto_diario is None:
        raise Rechazo(f'«{paso.objeto.nombre}» no tiene presupuesto diario de campaña en Meta.', terminal=True)


# ───────────────────────────── Escritura ──────────────────────────────────────

def _enviar(proveedor, paso: Paso, valor=None):
    if paso.tipo == Decision.PAUSAR:
        proveedor.pausar(paso.objeto.id_externo)
    elif paso.tipo == Decision.REANUDAR:
        proveedor.reanudar(paso.objeto.id_externo)
    else:
        proveedor.ajustar_presupuesto(paso.objeto.id_externo, valor if valor is not None else paso.nuevo)


def _actualizar_local(paso: Paso, usuario, valor=None):
    ahora = timezone.now()
    ficha = {'updated_at': ahora, 'updated_by': usuario if getattr(usuario, 'pk', None) else None}
    modelo = type(paso.objeto)
    if paso.tipo == Decision.AJUSTAR:
        ficha['presupuesto_diario'] = valor if valor is not None else paso.nuevo
    elif paso.tipo == Decision.PAUSAR:
        ficha['estado'] = 'PAUSED'
    else:
        ficha['estado'] = 'ACTIVE'
    modelo.objects.filter(pk=paso.objeto.pk).update(**ficha)


def _fila(decision, paso, numero, modo, estado, usuario, reversion=False) -> AccionAnuncio:
    return AccionAnuncio.objects.create(
        decision=decision, paso=numero, reversion=reversion, tipo=paso.tipo, objeto_tipo=paso.objeto_tipo,
        objeto_id_externo=paso.objeto.id_externo, objeto_nombre=(paso.objeto.nombre or '')[:300],
        valor_anterior=paso.anterior if paso.tipo == Decision.AJUSTAR else None,
        valor_nuevo=paso.pedido if paso.tipo == Decision.AJUSTAR else None,
        modo=modo, estado=estado, created_by=usuario if getattr(usuario, 'pk', None) else None)


def _terminar(fila: AccionAnuncio, estado, respuesta, anterior=None, nuevo=None):
    fila.estado, fila.respuesta = estado, _limpio(respuesta)
    if anterior is not None:
        fila.valor_anterior = anterior
    if nuevo is not None:
        fila.valor_nuevo = nuevo
    fila.save()


def _cerrar_decision(decision_id, estado, resultado, usuario, ejecutada=False):
    ahora = timezone.now()
    with transaction.atomic():
        d = Decision.objects.select_for_update().get(pk=decision_id)
        d.resultado = _limpio(resultado)
        d.updated_by = usuario if getattr(usuario, 'pk', None) else None
        campos = ['resultado', 'updated_by', 'updated_at']
        if estado:
            d.estado = estado
            campos.append('estado')
        if ejecutada:
            d.ejecutado_en = ahora
            campos.append('ejecutado_en')
        d.save(update_fields=campos)


def _avisar_fallo(decision, detalle):
    registrar_pendiente(
        titulo='Acción en Meta fallida: verifica la cuenta publicitaria', rol='DIRECCION', tipo='ALERTA',
        severidad='URGENTE', detalle=_limpio(detalle), objeto=decision,
        clave_idempotencia=f'mkt_accion_fallida:{decision.pk}')


# ───────────────────────────── Punto de entrada ───────────────────────────────

def ejecutar(decision_id: int, usuario) -> tuple:
    """Ejecuta (o simula) una decisión aprobada. Devuelve (hecho, mensaje)."""
    if not (getattr(usuario, 'is_active', False) and getattr(usuario, 'is_superuser', False)):
        return False, 'Solo Dirección puede ejecutar acciones en Meta.'
    modo = modo_actual()
    proveedor = proveedor_meta()
    if modo == AccionAnuncio.REAL and not proveedor.puede_escribir():
        return False, ('Falta la credencial de escritura de Meta (META_ADS_TOKEN_ESCRITURA, permiso ads_management). '
                       'No se envió nada.')

    # Fase 1: validar, tomar el candado lógico y confirmar las filas EJECUTANDO antes de hablar con Meta.
    ahora = timezone.now()
    colgada = decision_colgada = None
    return_colgada = False
    with transaction.atomic():
        decision = Decision.objects.select_for_update().filter(pk=decision_id).first()
        if decision is None:
            return False, 'No existe esa decisión.'
        if decision.estado != Decision.APROBADA:
            return False, f'Solo se ejecuta una decisión aprobada; esta está {decision.get_estado_display().lower()}.'
        if decision.expira_en <= ahora:
            return False, 'Esa decisión ya caducó.'
        if not ejecutable(decision):
            return False, 'Esta decisión es informativa: no se ejecuta.'
        if modo == AccionAnuncio.REAL:
            previas = decision.acciones.filter(modo=AccionAnuncio.REAL)
            vivas = previas.filter(estado__in=(AccionAnuncio.EJECUTANDO, AccionAnuncio.PENDIENTE))
            if vivas.filter(updated_at__gte=ahora - timedelta(minutes=MINUTOS_COLGADA)).exists():
                return False, 'Esa acción ya se está ejecutando.'
            if vivas.exists() or previas.filter(estado=AccionAnuncio.OK).exists():
                for fila in vivas:
                    _terminar(fila, AccionAnuncio.FALLIDA, 'Sin resultado: pudo aplicarse en Meta. Verifica antes de repetir.')
                detalle = ('Una ejecución anterior quedó sin resultado. No se repite: verifica en Meta el estado de '
                           'la campaña o el anuncio antes de crear otra propuesta.')
                Decision.objects.filter(pk=decision.pk).update(
                    estado=Decision.FALLIDA, resultado=detalle, updated_at=timezone.now())
                colgada = detalle
                decision_colgada = decision
                return_colgada = True
        if return_colgada:
            pasos = []
        else:
            try:
                pasos = planear(decision)
            except Rechazo as exc:
                return False, str(exc)
        if modo == AccionAnuncio.REAL and not return_colgada:
            for numero, paso in enumerate(pasos, start=1):
                paso.fila = _fila(decision, paso, numero, modo, AccionAnuncio.EJECUTANDO, usuario)

    if return_colgada:
        _avisar_fallo(decision_colgada, colgada)
        return False, colgada

    if modo == AccionAnuncio.REAL:
        # Candado global: dos decisiones distintas que suben presupuesto leerían el mismo total y juntas
        # rebasarían el tope diario. Bloquear las campañas de Meta serializa las ejecuciones reales.
        with transaction.atomic():
            list(Campana.objects.select_for_update().filter(plataforma=Campana.META).values_list('pk', flat=True))
            return _revalidar_y_ejecutar(decision, pasos, modo, proveedor, usuario)
    return _revalidar_y_ejecutar(decision, pasos, modo, proveedor, usuario)


def _revalidar_y_ejecutar(decision, pasos, modo, proveedor, usuario) -> tuple:
    # Fase 2: revalidar contra el estado vivo (sin tocar nada todavía).
    try:
        revalidar(pasos, modo, proveedor, decision)
    except Exception as exc:
        rechazo = exc if isinstance(exc, Rechazo) else Rechazo(
            'Error inesperado al revalidar; no se envió nada.')
        if not isinstance(exc, Rechazo):
            logger.exception('Marketing: error al revalidar la decisión %s', decision.pk)
        mensaje = str(rechazo)
        if modo == AccionAnuncio.REAL:
            for paso in pasos:
                _terminar(paso.fila, AccionAnuncio.FALLIDA, mensaje)
            if rechazo.terminal:
                _cerrar_decision(decision.pk, Decision.FALLIDA, mensaje, usuario)
        else:
            _registrar_simulacion(decision, pasos, usuario, estado=AccionAnuncio.FALLIDA, texto=mensaje)
        return False, mensaje

    # Fase 3: simulación (no sale a la red) o escritura real.
    if modo == AccionAnuncio.SIMULACION:
        _registrar_simulacion(decision, pasos, usuario, estado=AccionAnuncio.OK)
        resumen = '; '.join(_describir(p) for p in pasos)
        _cerrar_decision(decision.pk, None, f'Simulación: {resumen}', usuario)
        return True, f'Simulación registrada (no se envió nada a Meta): {resumen}.'
    return _escribir(decision, pasos, proveedor, usuario)


def _describir(p: Paso) -> str:
    nombre = p.objeto.nombre or p.objeto.id_externo
    if p.tipo == Decision.AJUSTAR:
        return f'«{nombre}» {dinero(p.actual)} → {dinero(p.nuevo)} diarios'
    return f'{"pausar" if p.tipo == Decision.PAUSAR else "reanudar"} «{nombre}»'


def _registrar_simulacion(decision, pasos, usuario, estado, texto=''):
    for numero, paso in enumerate(pasos, start=1):
        fila = _fila(decision, paso, numero, AccionAnuncio.SIMULACION, AccionAnuncio.PENDIENTE, usuario)
        respuesta = texto or ('Simulación: no se llamó a Meta. ' + _describir(paso))
        _terminar(fila, estado, respuesta, anterior=paso.actual, nuevo=paso.nuevo)


def _escribir(decision, pasos, proveedor, usuario) -> tuple:
    hechos = []
    for paso in pasos:
        paso.fila.valor_anterior = paso.actual if paso.tipo == Decision.AJUSTAR else None
        paso.fila.valor_nuevo = paso.nuevo if paso.tipo == Decision.AJUSTAR else None
        paso.fila.save()
        if paso.sin_cambio:
            _terminar(paso.fila, AccionAnuncio.OK, 'Sin cambio: ya estaba en el estado pedido. No se envió nada.')
            continue
        try:
            _enviar(proveedor, paso)
        except Exception as exc:  # incluye cortes de red: Meta pudo aplicar el cambio
            return _fallo_en_escritura(decision, pasos, paso, hechos, proveedor, usuario, exc)
        _terminar(paso.fila, AccionAnuncio.OK, 'Meta confirmó el cambio.')
        hechos.append(paso)
        try:
            _actualizar_local(paso, usuario)
        except Exception:  # Meta ya cambió; el próximo sync corrige lo local
            logger.exception('Marketing: no se pudo actualizar lo local tras la acción %s', decision.pk)
    resumen = '; '.join(_describir(p) for p in pasos)
    _cerrar_decision(decision.pk, Decision.EJECUTADA, f'Ejecutada en Meta: {resumen}', usuario, ejecutada=True)
    return True, f'Ejecutada en Meta: {resumen}.'


def _fallo_en_escritura(decision, pasos, paso, hechos, proveedor, usuario, exc) -> tuple:
    motivo = _limpio(exc)
    logger.warning('Marketing: fallo al escribir en Meta (decisión %s): %s', decision.pk, motivo)
    _terminar(paso.fila, AccionAnuncio.FALLIDA, f'Error al enviar: {motivo}')
    for restante in pasos[pasos.index(paso) + 1:]:
        _terminar(restante.fila, AccionAnuncio.FALLIDA, 'No se envió: falló un paso anterior.')
    notas = [f'Falló «{paso.objeto.nombre or paso.objeto.id_externo}»: {motivo}.']
    # Reversión de lo ya aplicado (solo ajustes; una pausa no se deshace sola).
    for hecho in reversed(hechos):
        if hecho.tipo != Decision.AJUSTAR:
            notas.append(f'«{hecho.objeto.nombre}» ya se había {"pausado" if hecho.tipo == Decision.PAUSAR else "reanudado"}.')
            continue
        reversa = Paso(Decision.AJUSTAR, hecho.objeto, anterior=hecho.nuevo, pedido=hecho.actual,
                       actual=hecho.nuevo, nuevo=hecho.actual)
        fila = _fila(decision, reversa, len(pasos) + 1, AccionAnuncio.REAL, AccionAnuncio.EJECUTANDO, usuario,
                     reversion=True)
        try:
            _enviar(proveedor, reversa, valor=hecho.actual)
        except Exception as exc2:
            _terminar(fila, AccionAnuncio.FALLIDA, f'La reversión falló: {_limpio(exc2)}')
            notas.append(f'La reversión de «{hecho.objeto.nombre}» también falló: puede haber quedado en '
                         f'{dinero(hecho.nuevo)} en vez de {dinero(hecho.actual)}.')
        else:
            _terminar(fila, AccionAnuncio.OK, 'Reversión confirmada por Meta.')
            _actualizar_local(reversa, usuario, valor=hecho.actual)
            notas.append(f'«{hecho.objeto.nombre}» se devolvió a {dinero(hecho.actual)}.')
    detalle = ' '.join(notas) + ' Verifica la cuenta publicitaria antes de repetir.'
    _cerrar_decision(decision.pk, Decision.FALLIDA, detalle, usuario)
    _avisar_fallo(decision, detalle)
    return False, detalle
