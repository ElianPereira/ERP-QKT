"""Hojas de Excel del tablero de Indicadores (Issue #402, fase 4); los montos salen como `Decimal`."""
from .services.costos_fijos import GRUPOS


def hojas(d: dict):
    eq, pl, em, mt = d['equilibrio'], d['plan']['plan'], d['embudo'], d['metas']
    b, o = eq['base'], eq['objetivo']
    yield ('Resumen', ['Indicador', 'Valor', 'Detalle'],
           [(k['etiqueta'], k['valor'], k['sub']) for k in d['resumen']])
    yield ('Capacidad', ['Servicio', 'Días vendibles', 'Capacidad', 'Ingreso máximo', 'Contribución máxima',
                         'Costo variable'],
           [(s['nombre'], s['dias_vendibles'], s['unidades'], s['ingreso_max'], s['mc_max'], s['fuente_costo'])
            for s in d['capacidad']['servicios'].values()])
    yield ('Equilibrio', ['Concepto', 'Equilibrio', 'Con utilidad objetivo'], [
        ('Costos fijos mensuales', b['objetivo'], o['objetivo']),
        ('Venta neta necesaria', b['pesos'], o['pesos']),
        ('Días ocupados necesarios', b['dias'], o['dias']),
        ('Ocupación de equilibrio (%)', b['ocupacion_pct'], o['ocupacion_pct']),
    ])
    yield ('Costos fijos', ['Mes', *GRUPOS.values(), 'Total', 'Fuente de nómina'],
           [(m['etiqueta'], *(m['por_grupo'][g] for g in GRUPOS), m['total'], m['fuente_nomina'])
            for m in d['costos']['meses']])
    yield ('Plan anual', ['Mes', 'Eventos', 'Pasadías', 'Noches-habitación', 'Días ocupados', 'Venta neta',
                          'Contribución', 'Costos fijos', 'Utilidad'],
           [(m['etiqueta'], m['eventos'], m['pasadias'], m['noches'], m['dias_ocupados'], m['venta'],
             m['contribucion'], m['cf'], m['utilidad']) for m in pl['meses']]
           + [('Año', pl['unidades']['EVENTO'], pl['unidades']['PASADIA'], pl['unidades']['HOSPEDAJE'],
               pl['dias_ocupados'], pl['venta'], pl['contribucion'], pl['cf'], pl['utilidad'])])
    yield ('Comercial', ['Canal', 'Solicitudes', 'Vendidas', 'Conversión (%)', 'Expiración (%)',
                         'Cancelación (%)', 'Venta neta'],
           [(f['nombre'], f['solicitudes'], f['ventas'], f['tasa_conversion'], f['tasa_expiracion'],
             f['tasa_cancelacion'], f['venta_neta']) for f in em['por_canal']])
    yield ('Metas', ['Paso', 'Valor'], [
        ('Venta neta necesaria', mt['venta_necesaria']), ('Ventas necesarias', mt['ventas_necesarias']),
        ('Solicitudes necesarias', mt['solicitudes_necesarias']),
        ('Presupuesto de marketing sugerido', mt['presupuesto_marketing']),
    ])
