# Data migration: populate GrupoBarra, CategoriaBarra, and link existing PlantillaBarra records.
from decimal import Decimal
from django.db import migrations


GRUPOS_DATA = [
    # (clave, nombre, color, peso_calculadora, campo_cotizacion, orden, activo)
    ('CERVEZA', 'Cerveza', '#f39c12', 55, 'incluye_cerveza', 5, True),
    ('ALCOHOL_NACIONAL', 'Licores Nacionales', '#e67e22', 35, 'incluye_licor_nacional', 10, True),
    ('ALCOHOL_PREMIUM', 'Licores Premium', '#9b59b6', 25, 'incluye_licor_premium', 20, True),
    ('COCTELERIA_BASICA', 'Coctelería Básica', '#27ae60', 20, 'incluye_cocteleria_basica', 30, True),
    ('COCTELERIA_PREMIUM', 'Mixología Premium', '#8e44ad', 15, 'incluye_cocteleria_premium', 35, True),
    ('MEZCLADOR', 'Bebidas y Mezcladores', '#3498db', 15, 'incluye_refrescos', 40, True),
    ('HIELO', 'Hielo', '#1abc9c', 0, '', 50, True),
    ('CONSUMIBLE', 'Abarrotes y Consumibles', '#95a5a6', 0, '', 60, True),
]

CATEGORIAS_DATA = [
    # (clave, grupo_clave, nombre, proporcion, unidad_compra, unidad_contenido, orden, activo)
    # Cerveza
    ('CERVEZA', 'CERVEZA', 'Cerveza', '1.00', 'Cajas (12u)', '1.00', 1, True),
    # Licores Nacionales — oferta reducida: solo Tequila + Ron activos
    ('TEQUILA_NAC', 'ALCOHOL_NACIONAL', 'Tequila Nacional', '0.50', 'Botellas', '0.75', 10, True),
    ('WHISKY_NAC', 'ALCOHOL_NACIONAL', 'Whisky Nacional', '0.00', 'Botellas', '0.75', 11, False),
    ('RON_NAC', 'ALCOHOL_NACIONAL', 'Ron Nacional', '0.50', 'Botellas', '0.75', 12, True),
    ('VODKA_NAC', 'ALCOHOL_NACIONAL', 'Vodka Nacional', '0.00', 'Botellas', '0.75', 13, False),
    # Licores Premium — oferta reducida: Tequila + Whisky activos
    ('TEQUILA_PREM', 'ALCOHOL_PREMIUM', 'Tequila Premium', '0.50', 'Botellas', '0.75', 20, True),
    ('WHISKY_PREM', 'ALCOHOL_PREMIUM', 'Whisky Premium', '0.50', 'Botellas', '0.75', 21, True),
    ('GIN_PREM', 'ALCOHOL_PREMIUM', 'Ginebra / Ron Premium', '0.00', 'Botellas', '0.75', 22, False),
    # Mezcladores
    ('REFRESCO_COLA', 'MEZCLADOR', 'Refresco de Cola', '0.60', 'Botellas', '2.50', 30, True),
    ('REFRESCO_TORONJA', 'MEZCLADOR', 'Refresco de Toronja', '0.20', 'Botellas', '2.00', 31, True),
    ('AGUA_MINERAL', 'MEZCLADOR', 'Agua Mineral', '0.20', 'Botellas', '2.00', 32, True),
    ('AGUA_NATURAL', 'MEZCLADOR', 'Agua Natural', '1.00', 'Garrafones', '20.00', 33, True),
    # Hielo
    ('HIELO', 'HIELO', 'Hielo', '1.00', 'Bolsas', '20.00', 40, True),
    # Coctelería Básica — oferta reducida: solo Limón + Jarabe activos
    ('LIMON', 'COCTELERIA_BASICA', 'Limón', '1.00', 'Kg', '1.00', 50, True),
    ('HIERBABUENA', 'COCTELERIA_BASICA', 'Hierbabuena', '1.00', 'Manojos', '1.00', 51, False),
    ('JARABE', 'COCTELERIA_BASICA', 'Jarabe Natural', '1.00', 'Litros', '1.00', 52, True),
    # Coctelería Premium — oferta reducida: solo Frutos Rojos activo
    ('FRUTOS_ROJOS', 'COCTELERIA_PREMIUM', 'Frutos Rojos', '1.00', 'Bolsas', '1.00', 53, True),
    ('CAFE', 'COCTELERIA_PREMIUM', 'Café Espresso', '1.00', 'Kg', '1.00', 54, False),
    # Consumibles
    ('SERVILLETAS', 'CONSUMIBLE', 'Servilletas / Popotes', '1.00', 'Kit', '1.00', 60, True),
]

# Map old grupo CharField values to new GrupoBarra claves
OLD_GRUPO_TO_NEW = {
    'CERVEZA': 'CERVEZA',
    'ALCOHOL_NACIONAL': 'ALCOHOL_NACIONAL',
    'ALCOHOL_PREMIUM': 'ALCOHOL_PREMIUM',
    'MEZCLADOR': 'MEZCLADOR',
    'HIELO': 'HIELO',
    'COCTELERIA': 'COCTELERIA_BASICA',  # Old COCTELERIA splits into BASICA/PREMIUM
    'CONSUMIBLE': 'CONSUMIBLE',
}

# Categories that moved from COCTELERIA to COCTELERIA_PREMIUM
PREMIUM_COCTELERIA_CATS = {'FRUTOS_ROJOS', 'CAFE'}


def forwards(apps, schema_editor):
    GrupoBarra = apps.get_model('comercial', 'GrupoBarra')
    CategoriaBarra = apps.get_model('comercial', 'CategoriaBarra')
    PlantillaBarra = apps.get_model('comercial', 'PlantillaBarra')

    # 1. Create GrupoBarra entries
    grupo_map = {}
    for clave, nombre, color, peso, campo, orden, activo in GRUPOS_DATA:
        g, _ = GrupoBarra.objects.get_or_create(
            clave=clave,
            defaults={
                'nombre': nombre,
                'color': color,
                'peso_calculadora': peso,
                'campo_cotizacion': campo,
                'orden': orden,
                'activo': activo,
            }
        )
        grupo_map[clave] = g

    # 2. Create CategoriaBarra entries
    cat_map = {}
    for clave, grupo_clave, nombre, prop, unidad_compra, unidad_contenido, orden, activo in CATEGORIAS_DATA:
        c, _ = CategoriaBarra.objects.get_or_create(
            clave=clave,
            defaults={
                'grupo': grupo_map[grupo_clave],
                'nombre': nombre,
                'proporcion_default': Decimal(prop),
                'unidad_compra': unidad_compra,
                'unidad_contenido': Decimal(unidad_contenido),
                'orden': orden,
                'activo': activo,
            }
        )
        cat_map[clave] = c

    # 3. Link existing PlantillaBarra records to new CategoriaBarra
    for pb in PlantillaBarra.objects.all():
        old_cat = pb.categoria  # CharField value like 'TEQUILA_NAC'
        if old_cat in cat_map:
            pb.categoria_ref = cat_map[old_cat]
            pb.es_default = True  # Existing entries become defaults
            pb.save(update_fields=['categoria_ref', 'es_default'])


def backwards(apps, schema_editor):
    GrupoBarra = apps.get_model('comercial', 'GrupoBarra')
    CategoriaBarra = apps.get_model('comercial', 'CategoriaBarra')
    PlantillaBarra = apps.get_model('comercial', 'PlantillaBarra')

    # Unlink PlantillaBarra
    PlantillaBarra.objects.all().update(categoria_ref=None, es_default=False)
    # Delete seeded data
    CategoriaBarra.objects.all().delete()
    GrupoBarra.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0032_grupobarra_categoriabarra_refactor"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
