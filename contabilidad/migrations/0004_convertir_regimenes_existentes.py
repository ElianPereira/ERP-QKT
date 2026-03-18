# Migration to convert existing regimen values to SAT codes

from django.db import migrations


def convertir_regimenes(apps, schema_editor):
    """Convierte los valores anteriores a claves SAT."""
    UnidadNegocio = apps.get_model('contabilidad', 'UnidadNegocio')
    
    # Mapeo de valores anteriores a claves SAT
    mapeo = {
        'EMPRESARIAL': '612',   # Personas Físicas con Actividades Empresariales y Profesionales
        'PLATAFORMAS': '625',   # Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas
        'MIXTO': '612',         # Default a Actividades Empresariales
    }
    
    for unidad in UnidadNegocio.objects.all():
        nuevo_regimen = mapeo.get(unidad.regimen_fiscal, '612')
        unidad.regimen_fiscal = nuevo_regimen
        unidad.save()


def revertir_regimenes(apps, schema_editor):
    """Revierte a los valores anteriores."""
    UnidadNegocio = apps.get_model('contabilidad', 'UnidadNegocio')
    
    mapeo_reverso = {
        '612': 'EMPRESARIAL',
        '625': 'PLATAFORMAS',
    }
    
    for unidad in UnidadNegocio.objects.all():
        valor_anterior = mapeo_reverso.get(unidad.regimen_fiscal, 'EMPRESARIAL')
        unidad.regimen_fiscal = valor_anterior
        unidad.save()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0003_actualizar_regimenes_sat'),
    ]

    operations = [
        migrations.RunPython(convertir_regimenes, revertir_regimenes),
    ]