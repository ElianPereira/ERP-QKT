# Management command: python manage.py verificar_plantilla_barra
# Verifica el estado de GrupoBarra, CategoriaBarra y PlantillaBarra.
from django.core.management.base import BaseCommand
from comercial.models import PlantillaBarra, GrupoBarra, CategoriaBarra, Insumo


class Command(BaseCommand):
    help = 'Verifica el estado del catálogo de barra (grupos, categorías, insumos vinculados)'

    def handle(self, *args, **options):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("   DIAGNÓSTICO DEL CATÁLOGO DE BARRA")
        self.stdout.write("=" * 70)

        # Grupos
        grupos = GrupoBarra.objects.all().order_by('orden')
        self.stdout.write(f"\n  Grupos de Barra: {grupos.count()} ({grupos.filter(activo=True).count()} activos)")

        for grupo in grupos:
            estado = self.style.SUCCESS('ACTIVO') if grupo.activo else self.style.ERROR('INACTIVO')
            cats_count = grupo.categorias.filter(activo=True).count()
            self.stdout.write(f"   {grupo.clave:25s} | {estado} | Peso: {grupo.peso_calculadora:3d} | "
                              f"Campo: {grupo.campo_cotizacion or '-':30s} | Categorías activas: {cats_count}")

        # Categorías
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("  ESTADO POR CATEGORÍA:")
        self.stdout.write("-" * 70)

        total_vinculados = 0
        total_sin_vincular = 0

        for grupo in grupos.filter(activo=True):
            self.stdout.write(f"\n  [{grupo.nombre}]")
            for cat in CategoriaBarra.objects.filter(grupo=grupo).order_by('orden'):
                plantillas = PlantillaBarra.objects.filter(
                    categoria_ref=cat, activo=True
                ).select_related('insumo', 'insumo__proveedor')

                estado_cat = self.style.SUCCESS('') if cat.activo else self.style.WARNING(' INACTIVA')

                if plantillas.exists():
                    total_vinculados += 1
                    for p in plantillas:
                        prov = p.insumo.proveedor.nombre if p.insumo.proveedor else 'sin proveedor'
                        default_tag = ' [DEFAULT]' if p.es_default else ''
                        self.stdout.write(
                            f"   {estado_cat} {cat.nombre:25s} -> {p.insumo.nombre} "
                            f"({p.insumo.presentacion or '-'}) [{prov}] "
                            f"prop={p.proporcion}{default_tag}"
                        )
                else:
                    total_sin_vincular += 1
                    self.stdout.write(self.style.WARNING(
                        f"   {estado_cat} {cat.nombre:25s} -> SIN VINCULAR"
                    ))

        # Resumen
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write(f"  Categorías vinculadas: {total_vinculados}")
        self.stdout.write(f"  Categorías sin vincular: {total_sin_vincular}")
        self.stdout.write(f"  Total de Insumos en catálogo: {Insumo.objects.count()}")
        self.stdout.write("=" * 70 + "\n")
