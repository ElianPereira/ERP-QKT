"""
Retiro de los datos de Airbnb (Issue #311, fase 2)
===================================================
La línea de negocio Airbnb salió del portafolio y el propietario decidió
borrar todos sus datos del ERP, pólizas incluidas: la contabilidad oficial la
lleva el contador fuera del ERP, así que aquí solo se replicaba.

`ejecutar()` lo llama la migración `contabilidad.0020_retiro_airbnb_datos`
con el registro histórico de apps. Producción ya la corrió (23/09/2026, tras
revisar un diagnóstico de solo lectura que se retiró en la fase 3); sigue
aquí porque en una BD nueva `contabilidad.0002`/`0005` siembran la unidad
AIRBNB y sus cuentas, y esta migración las limpia. Si hay conflictos no
borra nada y lanza un error con el detalle.

Conflicto = un dato de la Quinta que usa algo de Airbnb (una póliza de la
Quinta con movimientos en la cuenta Libretón o en una cuenta de Airbnb, una
compra de la Quinta pagada desde la Libretón, o una operación contable de la
Quinta —p. ej. BANCO_PRINCIPAL— configurada sobre una de esas cuentas). Borrarlo alteraría la Quinta;
dejarlo impediría borrar la cuenta. Se resuelve a mano en el admin antes del
borrado, nunca a discreción del código.
"""
from django.db.models import Q

CLAVE_UNIDAD = 'AIRBNB'
ORIGEN_POLIZA = 'PAGO_AIRBNB'
# Cuentas contables exclusivas de Airbnb (con sus subcuentas: 401.02 trae
# 401.02.01-03 de la migración 0002).
CODIGOS_CUENTAS = ('401.02', '109.03', '109.04', '601.04.02')
# BANCO_SECUNDARIO se siembra sobre 102.02.02 y ningún código lo lee; es el
# único mapeo que puede apuntar a la Libretón sin ser un conflicto.
OPERACIONES_CONFIG = (
    'INGRESO_AIRBNB', 'RETENCION_ISR_AIRBNB', 'RETENCION_IVA_AIRBNB', 'COMISION_AIRBNB',
    'BANCO_SECUNDARIO',
)
LINEA_FACTURACION = 'AIRBNB'
TIPOS_REPORTE = ('OCUPACION', 'COMPARATIVO')


class ConflictosRetiroAirbnb(RuntimeError):
    """Hay datos de la Quinta que dependen de algo de Airbnb."""


def _q_codigos():
    q = Q()
    for codigo in CODIGOS_CUENTAS:
        q |= Q(codigo_sat=codigo) | Q(codigo_sat__startswith=f'{codigo}.')
    return q


def _alcance(apps):
    """Querysets de todo lo que se borra y de los conflictos."""
    UnidadNegocio = apps.get_model('contabilidad', 'UnidadNegocio')
    CuentaBancaria = apps.get_model('contabilidad', 'CuentaBancaria')
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    Poliza = apps.get_model('contabilidad', 'Poliza')
    Compra = apps.get_model('comercial', 'Compra')

    unidades = UnidadNegocio.objects.filter(clave=CLAVE_UNIDAD)
    cuentas_bancarias = CuentaBancaria.objects.filter(unidad_negocio__in=unidades)
    cuentas_contables = CuentaContable.objects.filter(
        _q_codigos() | Q(pk__in=cuentas_bancarias.values('cuenta_contable'))
    )
    compras = Compra.objects.filter(unidad_negocio__in=unidades)
    polizas = Poliza.objects.filter(
        Q(origen=ORIGEN_POLIZA)
        | Q(unidad_negocio__in=unidades)
        | Q(origen='COMPRA', object_id__in=compras.values('pk'))
    )

    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    configuracion = ConfiguracionContable.objects.filter(operacion__in=OPERACIONES_CONFIG)

    alcance = {
        'Pólizas': polizas,
        'Compras': compras,
        'Saldos de apertura': apps.get_model('contabilidad', 'SaldoApertura').objects.filter(
            cuenta_bancaria__in=cuentas_bancarias),
        'Estados de cuenta': apps.get_model('contabilidad', 'EstadoCuentaBancario').objects.filter(
            cuenta_bancaria__in=cuentas_bancarias),
        'Conciliaciones bancarias': apps.get_model('contabilidad', 'ConciliacionBancaria').objects.filter(
            cuenta_bancaria__in=cuentas_bancarias),
        'Configuración contable': configuracion,
        'Cuentas bancarias': cuentas_bancarias,
        'Cuentas contables': cuentas_contables,
        'Unidades de negocio': unidades,
        'Solicitudes de factura': apps.get_model('facturacion', 'SolicitudFactura').objects.filter(
            linea_negocio=LINEA_FACTURACION),
        'Reportes generados': apps.get_model('reportes', 'ReporteGenerado').objects.filter(
            tipo__in=TIPOS_REPORTE),
    }

    conflictos = {
        'Pólizas de la Quinta con movimientos en cuentas de Airbnb o de la Libretón':
            Poliza.objects.exclude(pk__in=polizas.values('pk')).filter(
                movimientos__cuenta__in=cuentas_contables).distinct(),
        'Compras de la Quinta pagadas desde la Libretón':
            Compra.objects.exclude(pk__in=compras.values('pk')).filter(
                cuenta_pago__in=cuentas_bancarias),
        'Operaciones contables de la Quinta configuradas sobre cuentas de Airbnb o de la Libretón':
            ConfiguracionContable.objects.exclude(pk__in=configuracion.values('pk')).filter(
                cuenta__in=cuentas_contables),
    }
    return alcance, conflictos


def ejecutar(apps):
    """Borra todo lo de Airbnb. Sin conflictos o no borra nada."""
    alcance, conflictos = _alcance(apps)
    pendientes = {etiqueta: list(qs.values_list('pk', flat=True)[:20])
                  for etiqueta, qs in conflictos.items() if qs.exists()}
    if pendientes:
        raise ConflictosRetiroAirbnb(
            "No se borró nada: hay datos de la Quinta que usan algo de Airbnb. "
            f"Resuélvelos en el admin y vuelve a desplegar. Detalle (ids): {pendientes}"
        )
    # Los querysets son perezosos y se filtran unos por otros (p. ej. las
    # pólizas por las compras): se congelan los pk antes de empezar a borrar.
    ids = {etiqueta: list(qs.values_list('pk', flat=True)) for etiqueta, qs in alcance.items()}
    # Orden: primero lo que protege (PROTECT) a lo siguiente de la lista.
    for etiqueta, qs in alcance.items():
        qs.model.objects.filter(pk__in=ids[etiqueta]).delete()
