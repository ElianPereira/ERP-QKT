# Rellena `autorizacion` para transacciones ya existentes a partir del
# `payload_crudo` que ya se guardaba desde antes de agregar el campo.

from django.db import migrations


def backfill_autorizacion(apps, schema_editor):
    OpenpayTransaccion = apps.get_model('comercial', 'OpenpayTransaccion')
    for registro in OpenpayTransaccion.objects.exclude(payload_crudo=None):
        if registro.autorizacion:
            continue
        payload = registro.payload_crudo or {}
        data = payload.get('transaction', payload) if isinstance(payload, dict) else {}
        autorizacion = data.get('authorization') if isinstance(data, dict) else None
        if autorizacion:
            registro.autorizacion = autorizacion
            registro.save(update_fields=['autorizacion'])


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0063_openpaytransaccion_autorizacion'),
    ]

    operations = [
        migrations.RunPython(backfill_autorizacion, revertir),
    ]
