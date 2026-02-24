# Guardar en: comercial/management/commands/cargar_plantilla_barra.py
# Crear las carpetas: comercial/management/__init__.py y comercial/management/commands/__init__.py
# Ejecutar: python manage.py cargar_plantilla_barra

from django.core.management.base import BaseCommand
from comercial.models import Insumo, PlantillaBarra


# Mapeo de categorías de barra con sus datos por defecto.
# El sistema buscará insumos existentes que contengan estas palabras clave.
PLANTILLA_DEFAULT = [
    # (categoria_barra, grupo, palabras_clave_busqueda, proporcion, orden, fallback_nombre)
    
    # CERVEZA
    ('CERVEZA', 'CERVEZA', ['cerveza', 'caguama'], 1.00, 1, 'Cerveza Nacional Caguama'),
    
    # LICORES NACIONALES
    ('TEQUILA_NAC', 'ALCOHOL_NACIONAL', ['tequila', 'tradicional', 'cuervo'], 0.40, 10, 'Tequila Nacional'),
    ('WHISKY_NAC', 'ALCOHOL_NACIONAL', ['whisky', 'red label', 'etiqueta roja'], 0.30, 11, 'Whisky Nacional'),
    ('RON_NAC', 'ALCOHOL_NACIONAL', ['ron', 'bacardi', 'carta blanca'], 0.20, 12, 'Ron Nacional'),
    ('VODKA_NAC', 'ALCOHOL_NACIONAL', ['vodka', 'smirnoff'], 0.10, 13, 'Vodka Nacional'),
    
    # LICORES PREMIUM
    ('TEQUILA_PREM', 'ALCOHOL_PREMIUM', ['don julio', 'tequila premium', 'herradura'], 0.40, 20, 'Tequila Premium'),
    ('WHISKY_PREM', 'ALCOHOL_PREMIUM', ['black label', 'whisky premium', 'buchanan'], 0.30, 21, 'Whisky Premium'),
    ('GIN_PREM', 'ALCOHOL_PREMIUM', ['gin', 'ginebra', 'hendrick', 'bombay'], 0.30, 22, 'Ginebra Premium'),
    
    # MEZCLADORES
    ('REFRESCO_COLA', 'MEZCLADOR', ['coca', 'cola', 'refresco cola'], 0.60, 30, 'Refresco de Cola'),
    ('REFRESCO_TORONJA', 'MEZCLADOR', ['toronja', 'squirt', 'fresca'], 0.20, 31, 'Refresco de Toronja'),
    ('AGUA_MINERAL', 'MEZCLADOR', ['mineral', 'topo chico'], 0.20, 32, 'Agua Mineral'),
    ('AGUA_NATURAL', 'MEZCLADOR', ['agua natural', 'garrafon', 'garrafón'], 1.00, 33, 'Agua Natural'),
    
    # HIELO
    ('HIELO', 'HIELO', ['hielo'], 1.00, 40, 'Hielo'),
    
    # COCTELERÍA
    ('LIMON', 'COCTELERIA', ['limon', 'limón'], 1.00, 50, 'Limón'),
    ('HIERBABUENA', 'COCTELERIA', ['hierbabuena', 'menta'], 1.00, 51, 'Hierbabuena'),
    ('JARABE', 'COCTELERIA', ['jarabe'], 1.00, 52, 'Jarabe Natural'),
    ('FRUTOS_ROJOS', 'COCTELERIA', ['frutos rojos', 'berries', 'frambuesa'], 1.00, 53, 'Frutos Rojos'),
    ('CAFE', 'COCTELERIA', ['café', 'cafe', 'espresso'], 1.00, 54, 'Café Espresso'),
    
    # CONSUMIBLES
    ('SERVILLETAS', 'CONSUMIBLE', ['servilleta', 'popote', 'desechable'], 1.00, 60, 'Servilletas y Popotes'),
]


class Command(BaseCommand):
    help = 'Carga la plantilla inicial de barra buscando insumos existentes por nombre'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Recrea toda la plantilla (borra y vuelve a crear)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo muestra qué haría sin crear nada',
        )

    def handle(self, *args, **options):
        force = options['force']
        dry_run = options['dry_run']
        
        if force and not dry_run:
            borrados = PlantillaBarra.objects.all().delete()[0]
            self.stdout.write(self.style.WARNING(f'🗑️  Se borraron {borrados} registros existentes.'))
        
        creados = 0
        no_encontrados = []
        ya_existen = 0
        
        for cat, grupo, keywords, proporcion, orden, fallback in PLANTILLA_DEFAULT:
            # Verificar si ya existe
            if not force and PlantillaBarra.objects.filter(categoria=cat).exists():
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
                    self.stdout.write(f'  ✅ {cat} → {insumo.nombre} ({insumo.presentacion or "sin presentación"}) [{insumo.proveedor or "sin proveedor"}]')
                else:
                    PlantillaBarra.objects.create(
                        categoria=cat,
                        grupo=grupo,
                        insumo=insumo,
                        proporcion=proporcion,
                        orden=orden,
                        activo=True
                    )
                    self.stdout.write(self.style.SUCCESS(f'  ✅ {cat} → {insumo.nombre}'))
                creados += 1
            else:
                no_encontrados.append((cat, fallback, keywords))
                self.stdout.write(self.style.WARNING(f'  ⚠️  {cat}: No se encontró insumo con: {", ".join(keywords)}'))
        
        self.stdout.write('')
        self.stdout.write(f'📊 Resumen:')
        self.stdout.write(f'   Creados: {creados}')
        self.stdout.write(f'   Ya existían: {ya_existen}')
        self.stdout.write(f'   Sin insumo encontrado: {len(no_encontrados)}')
        
        if no_encontrados:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('⚠️  Los siguientes conceptos NO se pudieron vincular:'))
            self.stdout.write('   Debes crearlos como Insumos y luego asignarlos manualmente en Admin > Plantilla de Barra:')
            for cat, nombre, kws in no_encontrados:
                self.stdout.write(f'   - {nombre} (buscó: {", ".join(kws)})')
        
        if dry_run:
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE('🔍 Modo DRY-RUN: No se creó nada. Ejecuta sin --dry-run para aplicar.'))
