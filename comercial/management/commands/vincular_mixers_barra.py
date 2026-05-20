"""
Management command: python manage.py vincular_mixers_barra [--dry-run]

Agrega el SubProducto "Refrescos Y Mezcladores Para 10 Pax" como
ComponenteProducto de cada producto de barra, con la cantidad
proporcional al consumo de mixers de cada tipo de servicio:

  - Cerveza Nacional:   0.50  (solo refrescos de acompañamiento + hielo)
  - Licores Nacionales: 1.00  (mixer completo para tragos preparados)
  - Licores Premium:    1.00  (mixer completo para tragos preparados)
"""
from django.core.management.base import BaseCommand
from comercial.models import Producto, SubProducto, ComponenteProducto


PRODUCTOS_CANTIDADES = [
    ('Cerveza Nacional Para 10', 0.50),
    ('Licores Nacionales 10', 1.00),
    ('Licores Premium Para 10', 1.00),
]

SUBPRODUCTO_NOMBRE = 'Refrescos Y Mezcladores Para 10'


class Command(BaseCommand):
    help = 'Vincula el SubProducto de mixers/refrescos a los 3 productos de barra'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
            help='Solo muestra qué haría sin crear nada')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        sub = SubProducto.objects.filter(nombre__icontains=SUBPRODUCTO_NOMBRE).first()
        if not sub:
            self.stderr.write(self.style.ERROR(
                f'No se encontro SubProducto con nombre que contenga "{SUBPRODUCTO_NOMBRE}"'))
            self.stderr.write('SubProductos disponibles:')
            for sp in SubProducto.objects.all().order_by('nombre'):
                self.stderr.write(f'  - {sp.nombre} (ID: {sp.id})')
            return

        self.stdout.write(f'SubProducto encontrado: {sub.nombre} (ID: {sub.id})')
        self.stdout.write(f'  Costo insumos: ${sub.costo_insumos()}')
        for r in sub.receta.select_related('insumo').all():
            self.stdout.write(f'  - {r.insumo.nombre}: {r.cantidad} {r.insumo.unidad_medida} @ ${r.insumo.costo_unitario}')

        self.stdout.write('')

        for nombre_parcial, cantidad in PRODUCTOS_CANTIDADES:
            prod = Producto.objects.filter(nombre__icontains=nombre_parcial).first()
            if not prod:
                self.stderr.write(self.style.WARNING(
                    f'  Producto no encontrado: "{nombre_parcial}"'))
                continue

            existente = ComponenteProducto.objects.filter(
                producto=prod, subproducto=sub).first()

            if existente:
                if abs(float(existente.cantidad) - cantidad) < 0.01:
                    self.stdout.write(f'  {prod.nombre}: ya vinculado con cantidad={existente.cantidad} (sin cambios)')
                else:
                    if dry_run:
                        self.stdout.write(self.style.WARNING(
                            f'  {prod.nombre}: actualizaria cantidad {existente.cantidad} -> {cantidad}'))
                    else:
                        existente.cantidad = cantidad
                        existente.save()
                        self.stdout.write(self.style.SUCCESS(
                            f'  {prod.nombre}: cantidad actualizada {existente.cantidad} -> {cantidad}'))
            else:
                if dry_run:
                    self.stdout.write(self.style.WARNING(
                        f'  {prod.nombre}: agregaria SubProducto con cantidad={cantidad}'))
                else:
                    ComponenteProducto.objects.create(
                        producto=prod, subproducto=sub, cantidad=cantidad)
                    self.stdout.write(self.style.SUCCESS(
                        f'  {prod.nombre}: SubProducto agregado con cantidad={cantidad}'))

            costo = prod.calcular_costo()
            precio = prod.sugerencia_precio()
            tag = ' (simulado)' if dry_run else ''
            self.stdout.write(f'    Costo nuevo: ${costo}  Precio sugerido: ${precio}{tag}')

        if dry_run:
            self.stdout.write(self.style.NOTICE('\nModo DRY-RUN: No se modifico nada.'))
        else:
            self.stdout.write(self.style.SUCCESS('\nListo. Verifica precios en el admin.'))
