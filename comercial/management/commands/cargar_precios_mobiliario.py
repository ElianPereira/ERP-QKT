"""
Management command: cargar_precios_mobiliario
Asigna precio_venta_fijo a los productos de mobiliario que no tienen receta
de costeo. Uso: python manage.py cargar_precios_mobiliario

Nota: en Railway (remoto) este comando NO se ejecuta solo — el mismo cambio
ya se aplica automáticamente en el deploy vía la migración de datos
0048_cargar_precios_mobiliario. Este comando existe para desarrollo local
o para recargar precios manualmente sin generar una migración nueva.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from comercial.models import Producto

PRECIOS = {
    'Mobiliario Tiffany': Decimal('600.00'),
    'Mobiliario Crossback': Decimal('950.00'),
    'Mobiliario Vintage': Decimal('1150.00'),
}


class Command(BaseCommand):
    help = 'Carga precio_venta_fijo para productos de mobiliario sin receta'

    def handle(self, *args, **options):
        for nombre, precio in PRECIOS.items():
            producto = Producto.objects.filter(nombre=nombre).first()
            if not producto:
                self.stdout.write(self.style.WARNING(f'  No encontrado: "{nombre}"'))
                continue
            producto.precio_venta_fijo = precio
            producto.visible_cotizador = True
            producto.full_clean()
            producto.save()
            self.stdout.write(self.style.SUCCESS(f'  {nombre}: ${precio:,.2f}'))
        self.stdout.write(self.style.SUCCESS('Listo.'))
