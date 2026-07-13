"""
Servicios del Módulo de Contabilidad
====================================
Lógica de negocio para reportes contables y regularización.
"""
from decimal import Decimal
from datetime import date
from typing import Dict, List, Optional, Tuple
from django.db import transaction
from django.db.models import Sum, Q
from django.contrib.contenttypes.models import ContentType


class BalanzaComprobacionService:
    """Genera la balanza de comprobación para un período."""
    
    @classmethod
    def generar(
        cls,
        fecha_inicio: date,
        fecha_fin: date,
        unidad_negocio=None,
        nivel_detalle: int = 3
    ) -> List[Dict]:
        from .models import CuentaContable, MovimientoContable
        
        cuentas = CuentaContable.objects.filter(
            activa=True,
            nivel__lte=nivel_detalle
        ).order_by('codigo_sat')
        
        filtros_mov = Q(poliza__estado='APLICADA')
        if unidad_negocio:
            filtros_mov &= Q(poliza__unidad_negocio=unidad_negocio)
        
        resultado = []
        
        for cuenta in cuentas:
            saldo_inicial_data = MovimientoContable.objects.filter(
                filtros_mov,
                cuenta=cuenta,
                poliza__fecha__lt=fecha_inicio
            ).aggregate(debe=Sum('debe'), haber=Sum('haber'))
            
            debe_inicial = saldo_inicial_data['debe'] or Decimal('0.00')
            haber_inicial = saldo_inicial_data['haber'] or Decimal('0.00')
            
            if cuenta.naturaleza == 'D':
                saldo_inicial = debe_inicial - haber_inicial
            else:
                saldo_inicial = haber_inicial - debe_inicial
            
            movimientos_periodo = MovimientoContable.objects.filter(
                filtros_mov,
                cuenta=cuenta,
                poliza__fecha__gte=fecha_inicio,
                poliza__fecha__lte=fecha_fin
            ).aggregate(debe=Sum('debe'), haber=Sum('haber'))
            
            cargos = movimientos_periodo['debe'] or Decimal('0.00')
            abonos = movimientos_periodo['haber'] or Decimal('0.00')
            
            if cuenta.naturaleza == 'D':
                saldo_final = saldo_inicial + cargos - abonos
            else:
                saldo_final = saldo_inicial - cargos + abonos
            
            if saldo_inicial != 0 or cargos != 0 or abonos != 0 or saldo_final != 0:
                resultado.append({
                    'codigo': cuenta.codigo_sat,
                    'nombre': cuenta.nombre,
                    'tipo': cuenta.tipo,
                    'naturaleza': cuenta.naturaleza,
                    'nivel': cuenta.nivel,
                    'saldo_inicial_debe': saldo_inicial if cuenta.naturaleza == 'D' and saldo_inicial > 0 else Decimal('0.00'),
                    'saldo_inicial_haber': abs(saldo_inicial) if cuenta.naturaleza == 'A' or saldo_inicial < 0 else Decimal('0.00'),
                    'cargos': cargos,
                    'abonos': abonos,
                    'saldo_final_debe': saldo_final if cuenta.naturaleza == 'D' and saldo_final > 0 else Decimal('0.00'),
                    'saldo_final_haber': abs(saldo_final) if cuenta.naturaleza == 'A' or saldo_final < 0 else Decimal('0.00'),
                })

        return resultado


# ==========================================
# REGULARIZACIÓN: SALDO DE APERTURA
# ==========================================

def aplicar_saldo_apertura(saldo_apertura, usuario=None):
    """
    Genera la póliza de apertura para una cuenta a su fecha de corte,
    comparando el saldo actual calculado en el sistema contra el saldo
    certificado por el contador. La diferencia se registra contra la
    cuenta de ajuste de apertura (AJUSTE_APERTURA), nunca se fuerza
    el saldo del sistema directamente.
    """
    from .models import Poliza, MovimientoContable
    from .signals import get_cuenta, get_unidad_negocio, get_usuario_sistema

    if saldo_apertura.aplicado:
        raise ValueError(f"El saldo de apertura #{saldo_apertura.pk} ya fue aplicado.")

    cuenta_bancaria = saldo_apertura.cuenta_bancaria
    if not cuenta_bancaria.cuenta_contable:
        raise ValueError(f"La cuenta {cuenta_bancaria} no tiene cuenta_contable ligada.")

    cuenta_ajuste = get_cuenta('AJUSTE_APERTURA')
    if not cuenta_ajuste:
        raise ValueError("Falta configurar AJUSTE_APERTURA en ConfiguracionContable.")

    unidad = get_unidad_negocio('QUINTA')
    usuario = usuario or get_usuario_sistema()

    saldo_sistema = cuenta_bancaria.saldo_actual
    diferencia = saldo_apertura.saldo_certificado - saldo_sistema

    with transaction.atomic():
        content_type = ContentType.objects.get_for_model(saldo_apertura)
        poliza = Poliza.objects.create(
            tipo='D',
            folio=Poliza.siguiente_folio('D', saldo_apertura.fecha_corte),
            fecha=saldo_apertura.fecha_corte,
            concepto=f"Apertura: {cuenta_bancaria.nombre} @ {saldo_apertura.fecha_corte}",
            unidad_negocio=unidad,
            estado='APLICADA',
            origen='APERTURA',
            content_type=content_type,
            object_id=saldo_apertura.pk,
            created_by=usuario,
        )

        if diferencia > 0:
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_bancaria.cuenta_contable,
                debe=diferencia, haber=Decimal('0.00'),
                concepto="Ajuste de apertura (saldo real mayor al del sistema)",
            )
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_ajuste,
                debe=Decimal('0.00'), haber=diferencia,
                concepto="Contrapartida ajuste de apertura",
            )
        elif diferencia < 0:
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_ajuste,
                debe=abs(diferencia), haber=Decimal('0.00'),
                concepto="Contrapartida ajuste de apertura",
            )
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta_bancaria.cuenta_contable,
                debe=Decimal('0.00'), haber=abs(diferencia),
                concepto="Ajuste de apertura (saldo real menor al del sistema)",
            )
        # Si diferencia == 0, no se generan movimientos, pero la póliza
        # queda como constancia de que la cuenta cuadró en el corte.

        saldo_apertura.aplicado = True
        saldo_apertura.poliza = poliza
        saldo_apertura.save(update_fields=['aplicado', 'poliza'])

    return poliza
