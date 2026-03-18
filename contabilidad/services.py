"""
Servicios del Módulo de Contabilidad
====================================
Lógica de negocio para reportes contables.
"""
from decimal import Decimal
from datetime import date
from typing import Dict, List, Optional, Tuple
from django.db.models import Sum, Q


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
