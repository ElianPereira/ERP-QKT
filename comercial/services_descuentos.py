"""
Servicio de Descuentos — comercial/services_descuentos.py
=========================================================
Evalúa, aplica y revierte descuentos sobre cotizaciones.

Regla de negocio central (NO-ACUMULABLES):
- Entre descuentos con acumulable=False que compiten, se aplica SOLO UNO:
  el de mayor `prioridad`; si empatan, el que genere MAYOR monto en MXN
  sobre el subtotal actual (no el mayor porcentaje).
- Los descuentos con acumulable=True se suman aparte, independientes del
  ganador entre los no-acumulables.

Todo cálculo monetario con Decimal y quantize(Decimal('0.01'), ROUND_HALF_UP).
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F

from .models import Cotizacion, Descuento, DescuentoAplicado

CENT = Decimal('0.01')


def _q(valor):
    return Decimal(valor).quantize(CENT, rounding=ROUND_HALF_UP)


def _subtotal_items(cotizacion):
    """Subtotal bruto (suma de items, antes de descuentos)."""
    total = sum(
        (it.cantidad * it.precio_unitario for it in cotizacion.items.all()),
        Decimal('0.00'),
    )
    return _q(total)


def _monto_descuento(descuento, subtotal):
    """Monto en MXN que representaría este descuento sobre el subtotal dado."""
    if descuento.tipo_valor == 'PORCENTAJE':
        return _q(subtotal * (descuento.valor / Decimal('100')))
    return _q(descuento.valor)


class DescuentoService:

    # ── Evaluación ──────────────────────────────────────────────────────
    @staticmethod
    def evaluar_automaticos(cotizacion):
        """Retorna los descuentos AUTOMATICO + activos cuyas condiciones
        cumple la cotización. Usa select_related/prefetch_related (sin N+1)."""
        subtotal = _subtotal_items(cotizacion)
        candidatos = (
            Descuento.objects
            .filter(modo='AUTOMATICO', activo=True)
            .select_related('temporada')
            .prefetch_related('tipos_evento')
        )
        return [
            d for d in candidatos
            if DescuentoService._cumple_condiciones(d, cotizacion, subtotal)
        ]

    @staticmethod
    def _cumple_condiciones(descuento, cotizacion, subtotal):
        """Todas las condiciones presentes se evalúan con AND."""
        if not descuento.usos_disponibles():
            return False

        if descuento.monto_minimo is not None and subtotal < descuento.monto_minimo:
            return False

        fecha = cotizacion.fecha_evento
        if descuento.fecha_inicio and (not fecha or fecha < descuento.fecha_inicio):
            return False
        if descuento.fecha_fin and (not fecha or fecha > descuento.fecha_fin):
            return False

        if descuento.temporada_id:
            temp = descuento.temporada
            if not temp or not temp.activo or not temp.contiene(fecha):
                return False

        tipos_ev_ids = {t.id for t in descuento.tipos_evento.all()}
        if tipos_ev_ids:
            if not cotizacion.tipo_evento_id or cotizacion.tipo_evento_id not in tipos_ev_ids:
                return False

        if descuento.tipos_servicio:
            if cotizacion.tipo_servicio not in descuento.tipos_servicio:
                return False

        return True

    @staticmethod
    def mejor_descuento(candidatos, subtotal):
        """Entre no-acumulables en competencia: gana el de mayor prioridad;
        si empatan, el de mayor monto resultante en MXN sobre el subtotal."""
        if not candidatos:
            return None
        return max(
            candidatos,
            key=lambda d: (d.prioridad, _monto_descuento(d, subtotal)),
        )

    # ── Aplicación / reversión ──────────────────────────────────────────
    @staticmethod
    @transaction.atomic
    def aplicar(cotizacion, descuento, usuario=None, modo='MANUAL'):
        """Calcula el monto con Decimal, crea el DescuentoAplicado (auditoría),
        suma el monto a Cotizacion.descuento, recalcula totales e incrementa
        el contador de usos si el descuento tiene tope."""
        subtotal = _subtotal_items(cotizacion)

        # El descuento no puede exceder la base disponible (evita base negativa).
        base_disponible = subtotal - (cotizacion.descuento or Decimal('0.00'))
        if base_disponible < 0:
            base_disponible = Decimal('0.00')
        monto = _q(min(_monto_descuento(descuento, subtotal), base_disponible))

        porcentaje_equiv = (
            _q(monto / subtotal * Decimal('100')) if subtotal > 0 else Decimal('0.00')
        )

        aplicado = DescuentoAplicado.objects.create(
            cotizacion=cotizacion,
            descuento=descuento,
            monto_aplicado=monto,
            porcentaje_equivalente=porcentaje_equiv,
            modo_aplicacion=modo,
            aplicado_por=usuario if modo == 'MANUAL' else None,
            activo=True,
        )

        cotizacion.descuento = _q((cotizacion.descuento or Decimal('0.00')) + monto)
        DescuentoService._recalcular_y_guardar(cotizacion)

        if descuento.max_usos is not None:
            Descuento.objects.filter(pk=descuento.pk).update(usos=F('usos') + 1)
            descuento.usos = (descuento.usos or 0) + 1

        return aplicado

    @staticmethod
    @transaction.atomic
    def revertir(descuento_aplicado):
        """Marca activo=False, resta el monto de Cotizacion.descuento y
        recalcula totales. NO borra el registro de auditoría."""
        if not descuento_aplicado.activo:
            return

        cotizacion = descuento_aplicado.cotizacion
        monto = descuento_aplicado.monto_aplicado

        descuento_aplicado.activo = False
        descuento_aplicado.save(update_fields=['activo'])

        nuevo = (cotizacion.descuento or Decimal('0.00')) - monto
        cotizacion.descuento = _q(nuevo) if nuevo > 0 else Decimal('0.00')
        DescuentoService._recalcular_y_guardar(cotizacion)

        desc = descuento_aplicado.descuento
        if desc.max_usos is not None and desc.usos > 0:
            Descuento.objects.filter(pk=desc.pk).update(usos=F('usos') - 1)

    @staticmethod
    def _recalcular_y_guardar(cotizacion):
        """Recalcula con la lógica canónica de Cotizacion.calcular_totales()
        y persiste solo los campos fiscales + descuento (no dispara el
        recálculo de barra de save(), evitando duplicar lógica de IVA)."""
        cotizacion.calcular_totales()
        Cotizacion.objects.filter(pk=cotizacion.pk).update(
            descuento=cotizacion.descuento,
            subtotal=cotizacion.subtotal,
            iva=cotizacion.iva,
            retencion_isr=cotizacion.retencion_isr,
            retencion_iva=cotizacion.retencion_iva,
            precio_final=cotizacion.precio_final,
        )

    # ── Orquestador para el flujo automático ────────────────────────────
    @staticmethod
    @transaction.atomic
    def aplicar_automaticos(cotizacion, usuario=None):
        """Evalúa y aplica automáticamente: gana UN solo no-acumulable
        (mayor prioridad, desempate por mayor monto) y TODOS los acumulables.
        Idempotente: ignora descuentos ya aplicados activos. Devuelve la
        lista de DescuentoAplicado creados."""
        candidatos = DescuentoService.evaluar_automaticos(cotizacion)

        ya_aplicados = set(
            cotizacion.descuentos_aplicados
            .filter(activo=True)
            .values_list('descuento_id', flat=True)
        )
        candidatos = [d for d in candidatos if d.id not in ya_aplicados]
        if not candidatos:
            return []

        subtotal = _subtotal_items(cotizacion)
        acumulables = [d for d in candidatos if d.acumulable]
        no_acumulables = [d for d in candidatos if not d.acumulable]

        aplicados = []
        ganador = DescuentoService.mejor_descuento(no_acumulables, subtotal)
        if ganador:
            aplicados.append(
                DescuentoService.aplicar(cotizacion, ganador, usuario=usuario, modo='AUTOMATICO')
            )
        for d in acumulables:
            aplicados.append(
                DescuentoService.aplicar(cotizacion, d, usuario=usuario, modo='AUTOMATICO')
            )
        return aplicados
