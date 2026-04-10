# Management command: python manage.py cargar_plantilla_barra
# Crea/actualiza GrupoBarra, CategoriaBarra, y vincula PlantillaBarra a insumos existentes.
from django.core.management.base import BaseCommand
from comercial.models import Insumo, PlantillaBarra, GrupoBarra, CategoriaBarra


# Datos de grupos — fuente canónica para recrear estructura
GRUPOS_DEFAULT = [
    # (clave, nombre, color, peso_calculadora, campo_cotizacion, orden)
    ('CERVEZA', 'Cerveza', '#f39c12', 55, 'incluye_cerveza', 5),
    ('ALCOHOL_NACIONAL', 'Licores Nacionales', '#e67e22', 35, 'incluye_licor_nacional', 10),
    ('ALCOHOL_PREMIUM', 'Licores Premium', '#9b59b6', 25, 'incluye_licor_premium', 20),
    ('COCTELERIA_BASICA', 'Coctelería Básica', '#27ae60', 20, 'incluye_cocteleria_basica', 30),
    ('COCTELERIA_PREMIUM', 'Mixología Premium', '#8e44ad', 15, 'incluye_cocteleria_premium', 35),
    ('MEZCLADOR', 'Bebidas y Mezcladores', '#3498db', 15, 'incluye_refrescos', 40),
    ('HIELO', 'Hielo', '#1abc9c', 0, '', 50),
    ('CONSUMIBLE', 'Abarrotes y Consumibles', '#95a5a6', 0, '', 60),
]

# Datos de categorías con keywords de búsqueda para vincular insumos
CATEGORIAS_DEFAULT = [
    # (clave, grupo_clave, nombre, proporcion, unidad_compra, keywords, orden, activo_default)
    ('CERVEZA', 'CERVEZA', 'Cerveza', 1.00, 'Cajas (12u)', ['cerveza', 'caguama'], 1, True),
    ('TEQUILA_NAC', 'ALCOHOL_NACIONAL', 'Tequila Nacional', 0.50, 'Botellas', ['tequila', 'tradicional', 'cuervo'], 10, True),
    ('WHISKY_NAC', 'ALCOHOL_NACIONAL', 'Whisky Nacional', 0.00, 'Botellas', ['whisky', 'red label'], 11, False),
    ('RON_NAC', 'ALCOHOL_NACIONAL', 'Ron Nacional', 0.50, 'Botellas', ['ron', 'bacardi', 'carta blanca'], 12, True),
    ('VODKA_NAC', 'ALCOHOL_NACIONAL', 'Vodka Nacional', 0.00, 'Botellas', ['vodka', 'smirnoff'], 13, False),
    ('TEQUILA_PREM', 'ALCOHOL_PREMIUM', 'Tequila Premium', 0.50, 'Botellas', ['don julio', 'tequila premium', 'herradura'], 20, True),
    ('WHISKY_PREM', 'ALCOHOL_PREMIUM', 'Whisky Premium', 0.50, 'Botellas', ['black label', 'whisky premium', 'buchanan'], 21, True),
    ('GIN_PREM', 'ALCOHOL_PREMIUM', 'Ginebra / Ron Premium', 0.00, 'Botellas', ['gin', 'ginebra', 'hendrick', 'bombay'], 22, False),
    ('REFRESCO_COLA', 'MEZCLADOR', 'Refresco de Cola', 0.60, 'Botellas', ['coca', 'cola', 'refresco cola'], 30, True),
    ('REFRESCO_TORONJA', 'MEZCLADOR', 'Refresco de Toronja', 0.20, 'Botellas', ['toronja', 'squirt', 'fresca'], 31, True),
    ('AGUA_MINERAL', 'MEZCLADOR', 'Agua Mineral', 0.20, 'Botellas', ['mineral', 'topo chico'], 32, True),
    ('AGUA_NATURAL', 'MEZCLADOR', 'Agua Natural', 1.00, 'Garrafones', ['agua natural', 'garrafon'], 33, True),
    ('HIELO', 'HIELO', 'Hielo', 1.00, 'Bolsas', ['hielo'], 40, True),
    ('LIMON', 'COCTELERIA_BASICA', 'Limón', 1.00, 'Kg', ['limon', 'limón'], 50, True),
    ('HIERBABUENA', 'COCTELERIA_BASICA', 'Hierbabuena', 1.00, 'Manojos', ['hierbabuena', 'menta'], 51, False),
    ('JARABE', 'COCTELERIA_BASICA', 'Jarabe Natural', 1.00, 'Litros', ['jarabe'], 52, True),
    ('FRUTOS_ROJOS', 'COCTELERIA_PREMIUM', 'Frutos Rojos', 1.00, 'Bolsas', ['frutos rojos', 'berries', 'frambuesa'], 53, True),
    ('CAFE', 'COCTELERIA_PREMIUM', 'Café Espresso', 1.00, 'Kg', ['café', 'cafe', 'espresso'], 54, False),
    ('SERVILLETAS', 'CONSUMIBLE', 'Servilletas / Popotes', 1.00, 'Kit', ['servilleta', 'popote', 'desechable'], 60, True),
]


class Command(BaseCommand):
    help = 'Carga/actualiza grupos, categorías y plantilla de barra vinculando insumos existentes'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
            help='Recrea toda la plantilla (borra PlantillaBarra y vuelve a crear)')
        parser.add_argument('--dry-run', action='store_true',
            help='Solo muestra qué haría sin crear nada')

    def handle(self, *args, **options):
        force = options['force']
        dry_run = options['dry_run']

        # 1. Crear/actualizar GrupoBarra
        self.stdout.write('\n--- Grupos de Barra ---')
        grupo_map = {}
        for clave, nombre, color, peso, campo, orden in GRUPOS_DEFAULT:
            if dry_run:
                self.stdout.write(f'  Grupo: {clave} ({nombre})')
                grupo_map[clave] = None
            else:
                g, created = GrupoBarra.objects.update_or_create(
                    clave=clave,
                    defaults={'nombre': nombre, 'color': color, 'peso_calculadora': peso,
                              'campo_cotizacion': campo, 'orden': orden, 'activo': True}
                )
                grupo_map[clave] = g
                status = 'CREADO' if created else 'actualizado'
                self.stdout.write(self.style.SUCCESS(f'  {clave}: {status}'))

        # 2. Crear/actualizar CategoriaBarra
        self.stdout.write('\n--- Categorías de Barra ---')
        cat_map = {}
        for clave, grupo_clave, nombre, prop, unidad, keywords, orden, activo in CATEGORIAS_DEFAULT:
            if dry_run:
                self.stdout.write(f'  Categoría: {clave} ({nombre}) -> {grupo_clave}')
                cat_map[clave] = (keywords, activo)
            else:
                cat, created = CategoriaBarra.objects.update_or_create(
                    clave=clave,
                    defaults={'grupo': grupo_map[grupo_clave], 'nombre': nombre,
                              'proporcion_default': prop, 'unidad_compra': unidad,
                              'orden': orden, 'activo': activo}
                )
                cat_map[clave] = (keywords, cat)
                status = 'CREADO' if created else 'actualizado'
                tag = '' if activo else ' [INACTIVA]'
                self.stdout.write(self.style.SUCCESS(f'  {clave}: {status}{tag}'))

        # 3. Vincular PlantillaBarra a insumos existentes
        self.stdout.write('\n--- Vinculación de Insumos ---')
        if force and not dry_run:
            borrados = PlantillaBarra.objects.all().delete()[0]
            self.stdout.write(self.style.WARNING(f'  Se borraron {borrados} registros de PlantillaBarra.'))

        creados = 0
        no_encontrados = []
        ya_existen = 0

        for clave, grupo_clave, nombre, prop, unidad, keywords, orden, activo in CATEGORIAS_DEFAULT:
            if dry_run:
                cat_obj = None
            else:
                cat_obj = CategoriaBarra.objects.get(clave=clave)

            # Verificar si ya existe
            if not force and not dry_run:
                if PlantillaBarra.objects.filter(categoria_ref=cat_obj).exists():
                    ya_existen += 1
                    continue

            # Buscar insumo por palabras clave
            insumo = None
            for kw in keywords:
                insumo = Insumo.objects.filter(nombre__icontains=kw).first()
                if insumo:
                    break

            if insumo:
                if dry_run:
                    self.stdout.write(f'   {clave} -> {insumo.nombre} ({insumo.presentacion or "sin presentación"})')
                else:
                    PlantillaBarra.objects.create(
                        categoria_ref=cat_obj,
                        categoria=clave,
                        grupo=grupo_clave,
                        insumo=insumo,
                        proporcion=prop,
                        orden=orden,
                        activo=activo,
                        es_default=True
                    )
                    self.stdout.write(self.style.SUCCESS(f'   {clave} -> {insumo.nombre}'))
                creados += 1
            else:
                no_encontrados.append((clave, nombre, keywords))
                self.stdout.write(self.style.WARNING(f'    {clave}: No se encontró insumo con: {", ".join(keywords)}'))

        self.stdout.write(f'\n Resumen:')
        self.stdout.write(f'   Creados: {creados}')
        self.stdout.write(f'   Ya existían: {ya_existen}')
        self.stdout.write(f'   Sin insumo encontrado: {len(no_encontrados)}')

        if no_encontrados:
            self.stdout.write(self.style.WARNING('\n  Sin vincular — crear como Insumo y asignar en Admin:'))
            for cat, nombre, kws in no_encontrados:
                self.stdout.write(f'   - {nombre} (buscó: {", ".join(kws)})')

        if dry_run:
            self.stdout.write(self.style.NOTICE('\n  Modo DRY-RUN: No se creó nada.'))
