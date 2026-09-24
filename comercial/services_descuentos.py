"""
Servicio de Descuentos — comercial/services_descuentos.py
=========================================================
Evalúa, aplica, recalcula y revierte descuentos sobre cotizaciones.

Regla de negocio central (NO-ACUMULABLES):
- Entre descuentos con acumulable=False que compiten, se aplica SOLO UNO:
  el de mayor `prioridad`; si empatan, el que genere MAYOR monto en MXN
  sobre su base (no el mayor porcentaje).
- Los descuentos con acumulable=True se suman aparte, independientes del
  ganador entre los no-acumulables.

Base de cada descuento: el subtotal de la cotización o, si la regla está
acotada a `productos`, solo el subtotal de esos conceptos.

La evaluación trabaja sobre un `ContextoDescuento` (líneas + fecha + tipo),
así el cotizador público simula exactamente lo que se aplicará al crear la
cotización, sin guardar nada.

Todo cálculo monetario con Decimal y quantize(Decimal('0.01'), ROUND_HALF_UP).
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Cotizacion, Descuento, DescuentoAplicado

CENT = Decimal('0.01')
CERO = Decimal('0.00')


def _q(valor):
    return Decimal(valor).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class ContextoDescuento:
    """Lo que un descuento necesita saber de una cotización (real o simulada).

    `lineas` son pares (producto_id | None, base sin IVA).
    """
    lineas: list = field(default_factory=list)
    fecha: Optional[date] = None
    tipo_evento_id: Optional[int] = None
    tipo_servicio: str = ''

    @property
    def subtotal(self):
        return _q(sum((base for _, base in self.lineas), CERO))

    @classmethod
    def de_cotizacion(cls, cotizacion):
        return cls(
            lineas=[(it.producto_id, it.cantidad * it.precio_unitario)
                    for it in cotizacion.items.all()],
            fecha=cotizacion.fecha_evento,
            tipo_evento_id=cotizacion.tipo_evento_id,
            tipo_servicio=cotizacion.tipo_servicio,
        )


def _base_descuento(descuento, ctx):
    """Subtotal sobre el que opera el descuento: toda la cotización, o solo
    los conceptos de los productos/paquetes a los que está acotado."""
    ids = {p.id for p in descuento.productos.all()}
    if not ids:
        return ctx.subtotal
    return _q(sum((base for pid, base in ctx.lineas if pid in ids), CERO))


def _monto_descuento(descuento, base):
    """Monto en MXN que representaría este descuento sobre la base dada."""
    if base <= 0:
        return CERO
    if descuento.tipo_valor == 'PORCENTAJE':
        return _q(base * (descuento.valor / Decimal('100')))
    return _q(min(descuento.valor, base))


def _porcentaje(monto, subtotal):
    return _q(monto / subtotal * Decimal('100')) if subtotal > 0 else CERO


class DescuentoService:

    # ── Evaluación ──────────────────────────────────────────────────────
    @staticmethod
    def evaluar_automaticos(cotizacion):
        """Descuentos AUTOMATICO + activos cuyas condiciones cumple la cotización."""
        return DescuentoService._evaluar(ContextoDescuento.de_cotizacion(cotizacion))

    @staticmethod
    def _evaluar(ctx):
        candidatos = (
            Descuento.objects
            .filter(modo='AUTOMATICO', activo=True)
            .select_related('temporada')
            .prefetch_related('tipos_evento', 'productos')
        )
        return [d for d in candidatos if DescuentoService._cumple_condiciones(d, ctx)]

    @staticmethod
    def _cumple_condiciones(descuento, ctx):
        """Todas las condiciones presentes se evalúan con AND."""
        if not descuento.usos_disponibles():
            return False

        if descuento.monto_minimo is not None and ctx.subtotal < descuento.monto_minimo:
            return False

        fecha = ctx.fecha
        if descuento.fecha_inicio and (not fecha or fecha < descuento.fecha_inicio):
            return False
        if descuento.fecha_fin and (not fecha or fecha > descuento.fecha_fin):
            return False

        if descuento.temporada_id:
            temp = descuento.temporada
            if not temp or not temp.activo or not temp.contiene(fecha):
                return False

        tipos_ev_ids = {t.id for t in descuento.tipos_evento.all()}
        if tipos_ev_ids and ctx.tipo_evento_id not in tipos_ev_ids:
            return False

        if descuento.tipos_servicio and ctx.tipo_servicio not in descuento.tipos_servicio:
            return False

        # Acotado a productos: la cotización debe incluir al menos uno.
        if _base_descuento(descuento, ctx) <= 0:
            return False

        return True

    @staticmethod
    def mejor_descuento(candidatos, ctx):
        """Entre no-acumulables en competencia: gana el de mayor prioridad;
        si empatan, el de mayor monto resultante en MXN sobre su base."""
        if not candidatos:
            return None
        return max(
            candidatos,
            key=lambda d: (d.prioridad, _monto_descuento(d, _base_descuento(d, ctx))),
        )

    @staticmethod
    def _seleccionar(candidatos, ctx):
        """Orden de aplicación: el no-acumulable ganador y luego los acumulables."""
        ganador = DescuentoService.mejor_descuento(
            [d for d in candidatos if not d.acumulable], ctx)
        return ([ganador] if ganador else []) + [d for d in candidatos if d.acumulable]

    @staticmethod
    def simular_automaticos(ctx):
        """Lo que `aplicar_automaticos` le aplicaría a una cotización con este
        contexto, sin escribir nada: lista de (descuento, monto). Lo usa el
        cotizador público para exhibir la promoción antes de enviar."""
        disponible = ctx.subtotal
        resultado = []
        for d in DescuentoService._seleccionar(DescuentoService._evaluar(ctx), ctx):
            monto = min(_monto_descuento(d, _base_descuento(d, ctx)), disponible)
            disponible -= monto
            resultado.append((d, monto))
        return resultado

    # ── Aplicación / recálculo / reversión ──────────────────────────────
    @staticmethod
    @transaction.atomic
    def aplicar(cotizacion, descuento, usuario=None, modo='MANUAL'):
        """Crea el DescuentoAplicado (auditoría), suma el monto a
        Cotizacion.descuento y recalcula totales.

        Si un descuento MANUAL deja la cotización en $0 (cortesía total), la
        confirma: no habrá pago que lo haga (ver `cubierta_por_cortesia`)."""
        ctx = ContextoDescuento.de_cotizacion(cotizacion)
        subtotal = ctx.subtotal

        # El descuento no puede exceder la base disponible (evita base negativa).
        actual = Cotizacion.objects.filter(pk=cotizacion.pk).values_list('descuento', flat=True).get()
        disponible = max(subtotal - (actual or CERO), CERO)
        monto = min(_monto_descuento(descuento, _base_descuento(descuento, ctx)), disponible)

        aplicado = DescuentoAplicado.objects.create(
            cotizacion=cotizacion,
            descuento=descuento,
            monto_aplicado=monto,
            porcentaje_equivalente=_porcentaje(monto, subtotal),
            modo_aplicacion=modo,
            aplicado_por=usuario if modo == 'MANUAL' else None,
            activo=True,
        )
        Cotizacion.objects.filter(pk=cotizacion.pk).update(descuento=F('descuento') + monto)
        cotizacion.persistir_totales()

        if cotizacion.precio_final <= 0:
            cotizacion.confirmar_por_pago()
        return aplicado

    @staticmethod
    def recalcular(cotizacion):
        """Reajusta los descuentos activos al subtotal ACTUAL de la cotización.

        Un 10% aplicado sobre $10,000 se guarda como $1,000; si después se
        agregan conceptos, sin esto seguiría en $1,000 (un 5% real). Se
        recalcula en el orden en que se aplicaron, con el mismo tope de base
        disponible que `aplicar`. Lo que haya en `Cotizacion.descuento` que no
        venga de un DescuentoAplicado (captura directa en el admin) se respeta.

        Solo recalcula montos: no vuelve a evaluar condiciones. Cada ajuste
        queda anotado en `notas` del registro de auditoría.

        Escribe DescuentoAplicado y deja `cotizacion.descuento` en memoria; el
        llamador (`Cotizacion.persistir_totales`) persiste los totales.
        """
        activos = list(
            cotizacion.descuentos_aplicados.filter(activo=True)
            .select_related('descuento').prefetch_related('descuento__productos')
            .order_by('fecha_aplicacion', 'id')
        )
        if not activos:
            return
        ctx = ContextoDescuento.de_cotizacion(cotizacion)
        subtotal = ctx.subtotal
        suma_anterior = sum((a.monto_aplicado for a in activos), CERO)
        manual = max((cotizacion.descuento or CERO) - suma_anterior, CERO)
        disponible = max(subtotal - manual, CERO)

        suma_nueva = CERO
        hoy = timezone.localdate().strftime('%d/%m/%Y')
        for a in activos:
            nuevo = min(_monto_descuento(a.descuento, _base_descuento(a.descuento, ctx)), disponible)
            disponible -= nuevo
            suma_nueva += nuevo
            porcentaje = _porcentaje(nuevo, subtotal)
            if nuevo != a.monto_aplicado or porcentaje != a.porcentaje_equivalente:
                if nuevo != a.monto_aplicado:
                    nota = f"{hoy}: recalculado ${a.monto_aplicado:,.2f} → ${nuevo:,.2f} por cambio de conceptos."
                    a.notas = f"{a.notas}\n{nota}".strip()
                a.monto_aplicado = nuevo
                a.porcentaje_equivalente = porcentaje
                a.save(update_fields=['monto_aplicado', 'porcentaje_equivalente', 'notas'])
        cotizacion.descuento = _q(manual + suma_nueva)

    @staticmethod
    @transaction.atomic
    def revertir(descuento_aplicado):
        """Marca activo=False, resta el monto de Cotizacion.descuento y
        recalcula totales. NO borra el registro de auditoría."""
        if not descuento_aplicado.activo:
            return

        cotizacion = descuento_aplicado.cotizacion
        descuento_aplicado.activo = False
        descuento_aplicado.save(update_fields=['activo'])

        actual = Cotizacion.objects.filter(pk=cotizacion.pk).values_list('descuento', flat=True).get()
        nuevo = (actual or CERO) - descuento_aplicado.monto_aplicado
        Cotizacion.objects.filter(pk=cotizacion.pk).update(descuento=_q(nuevo) if nuevo > 0 else CERO)
        cotizacion.persistir_totales()

    # ── Orquestador para el flujo automático ────────────────────────────
    @staticmethod
    @transaction.atomic
    def aplicar_automaticos(cotizacion, usuario=None):
        """Evalúa y aplica automáticamente: gana UN solo no-acumulable
        (mayor prioridad, desempate por mayor monto) y TODOS los acumulables.
        Idempotente: ignora descuentos ya aplicados activos. Devuelve la
        lista de DescuentoAplicado creados."""
        ya_aplicados = set(
            cotizacion.descuentos_aplicados
            .filter(activo=True)
            .values_list('descuento_id', flat=True)
        )
        ctx = ContextoDescuento.de_cotizacion(cotizacion)
        candidatos = [d for d in DescuentoService._evaluar(ctx) if d.id not in ya_aplicados]
        return [
            DescuentoService.aplicar(cotizacion, d, usuario=usuario, modo='AUTOMATICO')
            for d in DescuentoService._seleccionar(candidatos, ctx)
        ]
