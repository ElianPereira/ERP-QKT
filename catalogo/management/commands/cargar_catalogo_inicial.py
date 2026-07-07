"""
Management command: cargar_catalogo_inicial
Carga el contenido inicial del catálogo a los modelos dinámicos.
Correr UNA sola vez. Es seguro re-correr (usa update_or_create).
Uso: python manage.py cargar_catalogo_inicial

Nota: los nombres de producto usados aquí ("Mobiliario Tiffany", etc.)
son los nombres REALES ya sembrados en comercial (migración 0043 + 0048),
no los nombres genéricos de un borrador anterior.
"""
from comercial.models import Producto
from catalogo.models import (
    ConfiguracionCatalogo, BadgeServicio, SeccionCatalogo,
    CaracteristicaSeccion, TarjetaCatalogo, PaqueteCatalogo,
)
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Carga el contenido inicial del catálogo (secciones, tarjetas, paquetes)'

    def handle(self, *args, **options):
        # --- Configuración general ---
        config = ConfiguracionCatalogo.cargar()
        config.nombre_empresa = "Quinta Ko'ox Tanil"
        config.ubicacion = "Umán, Yucatán · México"
        config.subtitulo_portada = "Donde los momentos se vuelven recuerdos"
        config.telefono = "(999) 445 71 78"
        config.sitio_web = "quintakooxtanil.com"
        config.url_cotizador = "quintakooxtanil.com/cotizar"
        config.url_portal_clientes = "quintakooxtanil.com/mi-evento"
        config.direccion = "Carretera Tanil — Ticimul Km 1.92, Umán, Yucatán"
        config.instagram = "@quintakooxtanil"
        config.save()
        self.stdout.write(self.style.SUCCESS('Configuración cargada.'))

        # --- Badges de portada ---
        for i, texto in enumerate([
            'EVENTOS Y BODAS', 'PASADÍA', 'HOSPEDAJE AIRBNB',
            'MOBILIARIO Y MONTAJE', 'BARRA Y CATERING',
        ]):
            BadgeServicio.objects.update_or_create(texto=texto, defaults={'orden': i})
        self.stdout.write(self.style.SUCCESS('Badges de portada cargados.'))

        # --- Sección Eventos ---
        eventos, _ = SeccionCatalogo.objects.update_or_create(
            slug='eventos',
            defaults=dict(
                numero='01', titulo='El espacio para', titulo_enfasis='tu celebración',
                descripcion=(
                    'Jardín bajo los árboles, pista de baile y salón con arcos coloniales. '
                    'Capacidad para hasta 200 invitados, con la atención personalizada de '
                    'una familia que vive para que tu evento sea perfecto.'
                ),
                nota_pie=(
                    'La carpa tiene costo adicional y está sujeta a disponibilidad. Es '
                    'opcional: brinda protección climática y realza el montaje de tu '
                    'evento — consulta disponibilidad.'
                ),
                orden=1,
            ),
        )
        for i, texto in enumerate([
            'Jardín con capacidad para hasta 200 personas',
            'Pista de baile bajo los árboles y las estrellas',
            'Salón cubierto con arcos coloniales',
            'Área de bar integrada al espacio',
            'Estacionamiento dentro de la Quinta',
            'Uso exclusivo durante tu evento',
        ]):
            CaracteristicaSeccion.objects.update_or_create(
                seccion=eventos, texto=texto, defaults={'orden': i}
            )

        producto_evento = Producto.objects.filter(nombre__iexact='Renta de Inmueble').first()
        TarjetaCatalogo.objects.update_or_create(
            seccion=eventos, orden=0,
            defaults=dict(
                producto=producto_evento,
                titulo='Renta del inmueble',
                badge_texto='6 horas · acceso a todas las áreas',
                mostrar_precio=True,
            ),
        )
        if not producto_evento:
            self.stdout.write(self.style.WARNING(
                '  Producto "Renta de Inmueble" no encontrado — vincula manualmente '
                'la tarjeta de Eventos a un Producto real en el admin.'
            ))

        # --- Sección Pasadía ---
        pasadia, _ = SeccionCatalogo.objects.update_or_create(
            slug='pasadia',
            defaults=dict(
                numero='02', titulo='Un día para', titulo_enfasis='descansar',
                descripcion=(
                    'La Quinta abre sus puertas para que tú y tu familia disfruten un '
                    'día completo de descanso y naturaleza.'
                ),
                nota_pie='Disponible todos los días del año · Barra y catering opcionales.',
                orden=2,
            ),
        )
        for i, texto in enumerate([
            'Alberca privada de uso exclusivo', 'Áreas verdes y jardines con palmeras',
            'Horario 10:00 a.m. – 7:00 p.m.', 'Capacidad máxima: 20 personas',
            'Mesas y sillas para 20 personas', 'Brincolín y carrito de bolis (sabores surtidos)',
            'Uso de 1 habitación incluido', 'Estacionamiento dentro de la Quinta',
        ]):
            CaracteristicaSeccion.objects.update_or_create(
                seccion=pasadia, texto=texto, defaults={'orden': i}
            )

        # --- Secciones restantes: Hospedaje, Mobiliario ---
        SeccionCatalogo.objects.update_or_create(
            slug='hospedaje',
            defaults=dict(numero='03', titulo='Habitaciones en', titulo_enfasis='la Quinta', orden=3),
        )
        mobiliario, _ = SeccionCatalogo.objects.update_or_create(
            slug='mobiliario',
            defaults=dict(numero='04', titulo='Líneas de', titulo_enfasis='mobiliario', orden=4),
        )

        # Nombres REALES en comercial (con prefijo "Mobiliario ", ver migración 0043/0048).
        for i, nombre in enumerate(['Mobiliario Tiffany', 'Mobiliario Crossback', 'Mobiliario Vintage']):
            producto = Producto.objects.filter(nombre=nombre).first()
            if not producto:
                self.stdout.write(self.style.WARNING(
                    f'  Producto "{nombre}" no encontrado — crea la tarjeta manualmente.'
                ))
                continue
            TarjetaCatalogo.objects.update_or_create(
                seccion=mobiliario, producto=producto,
                defaults=dict(orden=i, badge_texto='SET PARA 10 INVITADOS'),
            )

        # --- Paquetes completos (David Vera) ---
        for i, nombre in enumerate(['Básico', 'Tiffany', 'Crossback']):
            PaqueteCatalogo.objects.update_or_create(
                nombre=nombre,
                defaults={'proveedor': 'David Vera Banquetes', 'orden': i},
            )
        self.stdout.write(self.style.SUCCESS(
            'Paquetes creados sin items — cárgalos manualmente desde el admin '
            '(mobiliario + cristalería por paquete) y sin precio hasta que lo definas.'
        ))

        self.stdout.write(self.style.SUCCESS('=== CARGA INICIAL COMPLETADA ==='))
        self.stdout.write(self.style.WARNING(
            'Revisa en el admin: imágenes de cada sección/tarjeta se suben a mano '
            '(no se migran automáticamente).'
        ))
