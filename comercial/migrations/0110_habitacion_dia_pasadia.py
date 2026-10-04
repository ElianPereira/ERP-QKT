"""
Habitación de uso de día como extra de Pasadía y FAQ de regaderas
(pedido del propietario, 2026-10-04).

Desde este cambio la Pasadía Básica ya no trae habitación y la Premium trae
una: la habitación se contrata como extra (una en Básico, una segunda en
Premium) a $500.00 con IVA. El precio se guarda como lo guarda el admin al
capturar un precio con IVA (`sin_iva_preciso`, 4 decimales). El nombre no
lleva «Pasadía»: la línea base de Básico tiene un respaldo por nombre
(`_producto_por_rol(..., 'Pasadia')`) que lo tomaría por la base.

La respuesta de «¿Hay baños y regaderas?» decía que las regaderas son solo
las de las habitaciones, sin aclarar quién tiene una en Pasadía.
"""
from decimal import Decimal

from django.db import migrations

from core_erp.impuestos import sin_iva_preciso

NOMBRE = 'Uso de día de una habitación'
PREGUNTA_REGADERAS = '¿Hay baños y regaderas?'
RESPUESTA_REGADERAS = (
    'Sí hay baños. Las regaderas y vestidores son únicamente los de las habitaciones: en '
    'Hospedaje y en la Pasadía Premium (que incluye una habitación de uso de día) los tienes; '
    'en la Pasadía Básica puedes agregar la habitación de uso de día como extra.'
)


def aplicar(apps, schema_editor):
    Producto = apps.get_model('comercial', 'Producto')
    PreguntaFrecuente = apps.get_model('comercial', 'PreguntaFrecuente')

    Producto.objects.get_or_create(
        nombre=NOMBRE,
        defaults={
            'precio_venta_fijo': sin_iva_preciso(Decimal('500.00')),
            'descripcion': ('Uso de día de una habitación, con regadera y vestidor, de 11:00 a.m. '
                            'a 7:00 p.m. No incluye quedarse a dormir.'),
            'descripcion_corta': 'Una habitación con regadera y vestidor, de 11:00 a.m. a 7:00 p.m.',
            'visible_cotizador': True,
            'cotizador_pasadia': True,
            'grupo_cotizador': 'EXTRAS',
        },
    )
    PreguntaFrecuente.objects.filter(pregunta=PREGUNTA_REGADERAS).update(
        respuesta=RESPUESTA_REGADERAS)


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0109_productocomponente_fijo_por_evento'),
    ]

    operations = [
        migrations.RunPython(aplicar, migrations.RunPython.noop),
    ]
