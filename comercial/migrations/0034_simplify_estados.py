from django.db import migrations
from django.utils import timezone


def migrar_estados(apps, schema_editor):
    Cotizacion = apps.get_model('comercial', 'Cotizacion')
    hoy = timezone.now().date()

    # ANTICIPO → CONFIRMADA (seguía siendo una venta confirmada con anticipo)
    Cotizacion.objects.filter(estado='ANTICIPO').update(estado='CONFIRMADA')

    # EN_PREPARACION con fecha pasada → EJECUTADA (el evento ya ocurrió)
    Cotizacion.objects.filter(estado='EN_PREPARACION', fecha_evento__lt=hoy).update(estado='EJECUTADA')

    # EN_PREPARACION con fecha futura → CONFIRMADA
    Cotizacion.objects.filter(estado='EN_PREPARACION', fecha_evento__gte=hoy).update(estado='CONFIRMADA')


def revertir_estados(apps, schema_editor):
    # No hay forma precisa de saber cuáles eran ANTICIPO vs CONFIRMADA originalmente.
    # La reversión deja todo como CONFIRMADA (sin pérdida funcional).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0033_cotizacion_tipo_arrendamiento'),
    ]

    operations = [
        migrations.RunPython(migrar_estados, revertir_estados),
    ]
