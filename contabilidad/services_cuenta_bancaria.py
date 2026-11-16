"""
Cambio manual de la cuenta bancaria de un Pago o de un MovimientoDeposito (Issue #499, PR 3).

Reapunta la línea de bancos de su póliza (y la de su comisión de terminal o de Openpay) a la cuenta contable de la
cuenta nueva: no cancela ni toca importes, IVA ni folio. Se rechaza si alguna línea ya está ligada a un renglón del
estado de cuenta (hay que desligarla primero allí) o si la póliza cae en un periodo cerrado con `SaldoApertura`.
Para una Compra se usa `services_compras.reapuntar_banco_de_compra`.
"""
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import ConfiguracionContable, CuentaBancaria, MovimientoContable, Poliza, SaldoApertura


class CambioCuentaRechazado(Exception):
    """El cambio de cuenta bancaria no es seguro; el mensaje dice por qué y qué hacer."""


# Pólizas cuyo renglón de bancos sigue a la cuenta del cobro/pago.
ORIGENES = {'Pago': ('PAGO_CLIENTE', 'COMISION_TPV'), 'MovimientoDeposito': ('DEPOSITO_GARANTIA',)}
METODOS_SIN_BANCO = {'Pago': ('EFECTIVO', 'CONDONACION'), 'MovimientoDeposito': ('EFECTIVO', 'NO_APLICA')}


def _cuenta_contable_actual(obj):
    """Cuenta contable de bancos con la que se asentó hasta hoy (la suya, o BANCO_PRINCIPAL si no tiene)."""
    if obj.cuenta_bancaria_id:
        return obj.cuenta_bancaria.cuenta_contable
    return ConfiguracionContable.obtener_cuenta('BANCO_PRINCIPAL')


def _polizas_del_objeto(obj):
    nombre = type(obj).__name__
    polizas = Poliza.objects.filter(
        content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk,
        origen__in=ORIGENES[nombre], estado='APLICADA')
    ids = list(polizas.values_list('pk', flat=True))
    if nombre == 'Pago':  # la comisión de Openpay cuelga de la transacción del cobro
        from comercial.models import OpenpayTransaccion
        for transaccion in OpenpayTransaccion.objects.filter(pago=obj):
            ids += list(Poliza.objects.filter(
                content_type=ContentType.objects.get_for_model(transaccion), object_id=transaccion.pk,
                origen='COMISION_OPENPAY', estado='APLICADA').values_list('pk', flat=True))
    return Poliza.objects.filter(pk__in=ids).select_for_update()


def cambiar_cuenta_bancaria(obj, nueva, usuario):
    """
    Cambia la cuenta bancaria de un Pago o MovimientoDeposito y reapunta su póliza. Devuelve el número de líneas
    de bancos reapuntadas. Lanza `CambioCuentaRechazado` sin escribir nada si no es seguro.
    """
    nombre = type(obj).__name__
    if nombre not in ORIGENES:
        raise TypeError(f'Tipo no soportado: {nombre}')
    if obj.metodo in METODOS_SIN_BANCO[nombre]:
        raise CambioCuentaRechazado('Este método no pasa por el banco: no tiene cuenta bancaria.')
    if not nueva.activa or not nueva.cuenta_contable_id:
        raise CambioCuentaRechazado('La cuenta destino debe estar activa y ligada a una cuenta contable de bancos.')

    with transaction.atomic():
        obj = type(obj).objects.select_for_update().get(pk=obj.pk)
        actual = _cuenta_contable_actual(obj)
        if actual is not None and actual.pk == nueva.cuenta_contable_id:
            raise CambioCuentaRechazado('Ya está en esa cuenta.')

        lineas = []
        for poliza in _polizas_del_objeto(obj):
            if actual is not None:
                lineas += list(MovimientoContable.objects.filter(poliza=poliza, cuenta_id=actual.pk))
            cerrada = SaldoApertura.objects.filter(
                cuenta_bancaria__cuenta_contable_id__in=[pk for pk in (getattr(actual, 'pk', None), nueva.cuenta_contable_id) if pk],
                aplicado=True, fecha_corte__gte=poliza.fecha).exists()
            if cerrada:
                raise CambioCuentaRechazado(f'La póliza {poliza.folio} es de un periodo ya cerrado.')
        for linea in lineas:
            if linea.movimientos_banco_emparejados.exists():
                raise CambioCuentaRechazado(
                    f'La línea de la póliza {linea.poliza.folio} ya está ligada a un renglón del estado de cuenta: '
                    'desliga primero en el estado de cuenta.')

        for linea in lineas:
            linea.cuenta_id = nueva.cuenta_contable_id
            linea.save(update_fields=['cuenta'])
        anterior = CuentaBancaria.objects.filter(cuenta_contable=actual).first() if actual else None
        nota = (f"Cuenta bancaria cambiada de «{anterior or 'Banco principal'}» a «{nueva}» por "
                f"{getattr(usuario, 'username', 'sistema')} el {_hoy():%d/%m/%Y}.")
        cambios = {'cuenta_bancaria': nueva}
        if nombre == 'Pago':
            cambios['notas'] = f"{obj.notas} {nota}".strip()[:255]
        else:
            cambios['desglose'] = f"{obj.desglose}\n{nota}".strip()
        type(obj).objects.filter(pk=obj.pk).update(**cambios)  # sin save(): ni full_clean ni señales de alta
        return len(lineas)


def _hoy():
    from django.utils import timezone
    return timezone.localdate()
