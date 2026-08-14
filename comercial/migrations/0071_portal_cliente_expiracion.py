from datetime import datetime, timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill_expira_en(apps, schema_editor):
    """90 días después del evento para los portales que ya existían.

    Mismo cálculo que PortalCliente._expiracion_default(), pero no se puede
    reusar directamente: los modelos históricos de una migración no tienen
    métodos, solo los campos que declara esta migración hacia atrás.
    """
    PortalCliente = apps.get_model('comercial', 'PortalCliente')
    for portal in PortalCliente.objects.select_related('cotizacion').iterator():
        fecha_evento = portal.cotizacion.fecha_evento
        inicio = timezone.make_aware(datetime.combine(fecha_evento, datetime.min.time()))
        portal.expira_en = inicio + timedelta(days=90)
        portal.save(update_fields=['expira_en'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0070_alter_contratoservicio_archivo"),
    ]

    operations = [
        migrations.AddField(
            model_name="portalcliente",
            name="expira_en",
            field=models.DateTimeField(null=True, verbose_name="Expira el"),
        ),
        migrations.RunPython(backfill_expira_en, noop),
        migrations.AlterField(
            model_name="portalcliente",
            name="expira_en",
            field=models.DateTimeField(
                verbose_name="Expira el",
                help_text="Se calcula automáticamente. No editar directamente: usa la "
                          "acción «Regenerar token y extender acceso 90 días».",
            ),
        ),
    ]
