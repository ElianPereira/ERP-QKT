from django.db import migrations


def renombrar_eventos_a_quinta(apps, schema_editor):
    """
    Producción arrastra una UnidadNegocio con clave 'EVENTOS' (creada a mano,
    con su régimen fiscal y demás datos reales ya capturados) que predata al
    código que usa clave='QUINTA' en todo el proyecto (comercial/views.py,
    comercial/admin.py, contabilidad/signals.py). La migración 0002 ya corrió
    hace tiempo en producción y por eso nunca creó/fusionó una fila 'QUINTA'
    — solo renombramos la clave de la fila existente, preservando su PK
    (y con ella, todos los Compra/Poliza que ya la referencian).
    """
    UnidadNegocio = apps.get_model('contabilidad', 'UnidadNegocio')
    eventos = UnidadNegocio.objects.filter(clave='EVENTOS').first()
    if eventos and not UnidadNegocio.objects.filter(clave='QUINTA').exists():
        eventos.clave = 'QUINTA'
        eventos.save(update_fields=['clave'])


def revertir(apps, schema_editor):
    UnidadNegocio = apps.get_model('contabilidad', 'UnidadNegocio')
    quinta = UnidadNegocio.objects.filter(clave='QUINTA').first()
    if quinta:
        quinta.clave = 'EVENTOS'
        quinta.save(update_fields=['clave'])


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0011_rename_contabilida_codigo__a1b2c3_idx_contabilida_codigo__b58027_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(renombrar_eventos_a_quinta, revertir),
    ]
