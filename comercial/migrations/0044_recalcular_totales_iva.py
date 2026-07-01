from decimal import Decimal, ROUND_HALF_UP
from django.db import migrations

CENT = Decimal('0.01')


def _q(valor):
    return Decimal(valor).quantize(CENT, rounding=ROUND_HALF_UP)


def recalcular_totales(apps, schema_editor):
    """Repara cotizaciones creadas antes del fix de persistencia de IVA.

    Replica Cotizacion.calcular_totales() con modelos históricos (que no
    tienen los métodos custom). Recalcula subtotal, IVA, retenciones y
    precio_final a partir de los items reales.
    """
    Cotizacion = apps.get_model('comercial', 'Cotizacion')
    qs = Cotizacion.objects.exclude(estado='CANCELADA').select_related('cliente')
    for cot in qs.iterator():
        suma_items = sum(
            (it.cantidad * it.precio_unitario for it in cot.items.all()),
            Decimal('0.00'),
        )
        subtotal = _q(suma_items)
        base = subtotal - (cot.descuento or Decimal('0.00'))
        if base < 0:
            base = Decimal('0.00')
        iva = _q(base * Decimal('0.16'))
        if cot.cliente and cot.cliente.tipo_persona == 'MORAL':
            retencion_isr = _q(base * Decimal('0.0125'))
        else:
            retencion_isr = Decimal('0.00')
        retencion_iva = Decimal('0.00')
        precio_final = _q(base + iva - retencion_isr - retencion_iva)

        Cotizacion.objects.filter(pk=cot.pk).update(
            subtotal=subtotal,
            iva=iva,
            retencion_isr=retencion_isr,
            retencion_iva=retencion_iva,
            precio_final=precio_final,
        )


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0043_seed_mobiliario_cotizador"),
    ]

    operations = [
        migrations.RunPython(recalcular_totales, revertir),
    ]
