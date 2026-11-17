"""PDF del expediente de ventas (Issue #505, PR 1A): propuesta y acuerdo de confidencialidad.

La propuesta toma todas sus cifras de las CondicionesPago del registro; el acuerdo
conserva el texto aprobado por el abogado y solo llena los datos del empleado.
"""
from decimal import Decimal

from core_erp import impuestos
from core_erp.documentos import TITULAR_LEGAL, moneda, nombre_archivo, render_pdf

RESPONSABILIDADES_VENTAS = [
    'Recibir a los visitantes con cita y mostrarles la Quinta.',
    'Elaborar la cotización en el sistema de la Quinta.',
    'Dar seguimiento al cliente hasta que aparte su fecha.',
    'Registrar los cobros que recibas del cliente.',
    'Ofrecer servicios extras que mejoren su evento.',
    'Marcar en el sistema cada visita como atendida.',
]
HORARIO_VENTAS = [
    {'bloque': 'Mañana', 'horas': '10:00 a.m. a 1:00 p.m.'},
    {'bloque': 'Tarde', 'horas': '4:00 p.m. a 8:00 p.m.'},
]
# Ejemplo ilustrativo de la propuesta (precio final y extras con IVA); las cifras de
# pago salen de las condiciones, estos dos datos solo arman la situación de ejemplo.
EJEMPLO_PRECIO = Decimal('20000.00')
EJEMPLO_EXTRAS_CON_IVA = Decimal('3480.00')


class FaltanDatos(Exception):
    """El documento no se genera porque falta información del expediente."""

    def __init__(self, faltantes):
        self.faltantes = list(faltantes)
        super().__init__('Faltan datos en el expediente: ' + ', '.join(self.faltantes))


def _pct(fraccion) -> str:
    """0.0500 → '5'; 0.075 → '7.5'."""
    valor = (Decimal(fraccion) * 100).normalize()
    return f'{valor:f}'


def _lineas(texto) -> list:
    return [r.strip() for r in (texto or '').splitlines() if r.strip()]


def _cifras(c, escalones):
    cifras = []
    if c.pago_visita:
        cifras.append({'valor': moneda(c.pago_visita),
                       'texto': f'por cada visita agendada que atiendas (hasta {c.tope_visitas_semana} por semana)'})
    for e in escalones:
        cifras.append({'valor': moneda(e['monto']), 'texto': f'por cada contratación, {e["rango_corto"]}'})
    if c.porcentaje_extras:
        cifras.append({'valor': f'{_pct(c.porcentaje_extras)} %',
                       'texto': 'adicional sobre los servicios extras que contrate el cliente'})
    return cifras


def _escalones(c):
    filas = c.escalones_ordenados()
    salida = []
    for i, e in enumerate(filas):
        siguiente = filas[i + 1] if i + 1 < len(filas) else None
        if siguiente:
            fin = siguiente.desde_contratacion - 1
            rango = (f'La {e.desde_contratacion}' if fin == e.desde_contratacion
                     else f'De la {e.desde_contratacion} a la {fin}')
            corto = f'de la {e.desde_contratacion} a la {fin} del año'
        else:
            rango = f'De la {e.desde_contratacion} en adelante'
            corto = f'de la {e.desde_contratacion} en adelante'
        salida.append({'monto': impuestos.centavos(e.monto), 'rango': rango, 'rango_corto': corto,
                       'desde': e.desde_contratacion, 'ejemplo': e.desde_contratacion + 11})
    return salida


def _ejemplo(c, escalones):
    if not escalones:
        return None
    primera = escalones[0]
    base_extras = Decimal('0.00')
    extras = Decimal('0.00')
    if c.porcentaje_extras:
        base_extras = impuestos.sin_iva(EJEMPLO_EXTRAS_CON_IVA)
        extras = impuestos.centavos(base_extras * c.porcentaje_extras)
    total = primera['monto'] + extras
    mitad_cliente = impuestos.centavos(EJEMPLO_PRECIO / 2)
    mitad_comision = impuestos.centavos(total / 2)
    segundo = escalones[1] if len(escalones) > 1 else None
    return {
        'precio': EJEMPLO_PRECIO, 'extras_con_iva': EJEMPLO_EXTRAS_CON_IVA, 'base_extras': base_extras,
        'primeras': (segundo['desde'] - 1) if segundo else None,
        'fija': primera['monto'], 'extras': extras, 'total': total,
        'mitad_cliente': mitad_cliente, 'mitad_comision': mitad_comision,
        'resto_comision': total - mitad_comision,
    }


def _calendario(c, escalones):
    filas = []
    if c.pago_visita:
        filas.append({'concepto': 'Visitas', 'corte': 'Semanal, de lunes a domingo', 'pago': 'Domingo'})
    if escalones or c.porcentaje_extras:
        if c.periodicidad_comisiones == 'QUINCENAL':
            filas.append({'concepto': 'Comisiones', 'corte': 'Quincenal, el día 15 y el último día del mes',
                          'pago': 'El domingo siguiente al corte'})
        else:
            filas.append({'concepto': 'Comisiones', 'corte': c.get_periodicidad_comisiones_display(),
                          'pago': 'El domingo siguiente al corte'})
    return filas


def contexto_propuesta(c):
    escalones = _escalones(c)
    responsabilidades = _lineas(c.responsabilidades) or (
        RESPONSABILIDADES_VENTAS if c.empleado.puesto == 'VENTAS' else [])
    horario = []
    for linea in _lineas(c.horario):
        bloque, _, horas = linea.partition('|')
        horario.append({'bloque': bloque.strip(), 'horas': horas.strip()})
    horario = horario or HORARIO_VENTAS
    return {
        'titulo': 'Propuesta de colaboración en ventas',
        'condiciones': c,
        'empleado_nombre': c.empleado.nombre_para_documentos,
        'cifras': _cifras(c, escalones),
        'responsabilidades': responsabilidades,
        'horario': horario,
        'escalones': escalones,
        'ultimo_escalon': escalones[-1] if escalones else None,
        'porcentaje_extras': _pct(c.porcentaje_extras) if c.porcentaje_extras else '',
        'tope_semanal': impuestos.centavos((c.pago_visita or 0) * c.tope_visitas_semana),
        'tope_compensacion': _pct(c.tope_compensacion),
        'calendario': _calendario(c, escalones),
        'ejemplo': _ejemplo(c, escalones),
    }


def pdf_propuesta(condiciones, request=None):
    """(bytes, nombre) de la propuesta. Se genera aunque falte el nombre legal."""
    contexto = contexto_propuesta(condiciones)
    nombre = nombre_archivo('Propuesta', 'Ventas', condiciones.empleado.nombre_para_documentos,
                            condiciones.vigente_desde)
    return render_pdf('nomina/pdf_propuesta.html', contexto, request=request), nombre


def pdf_acuerdo(empleado, request=None):
    """(bytes, nombre) del acuerdo de confidencialidad. Si falta nombre legal,
    domicilio o identificación no se genera: `FaltanDatos` dice cuáles."""
    faltan = []
    if not (empleado.nombre_legal or '').strip():
        faltan.append('Nombre legal')
    if not empleado.domicilio_completo:
        faltan.append('Domicilio completo (calle, colonia, C.P. y municipio)')
    if not empleado.identificacion_texto:
        faltan.append('Identificación (tipo y número)')
    if faltan:
        raise FaltanDatos(faltan)
    contexto = {
        'titulo': 'Acuerdo de confidencialidad y uso de sistemas',
        'titular': TITULAR_LEGAL,
        'nombre': empleado.nombre_legal.strip(),
        'domicilio': empleado.domicilio_completo,
        'identificacion': empleado.identificacion_texto,
    }
    nombre = nombre_archivo('Acuerdo', 'Confidencialidad', empleado.nombre_legal)
    return render_pdf('nomina/pdf_acuerdo.html', contexto, request=request), nombre
