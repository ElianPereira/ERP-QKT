"""
Management command: python manage.py verificar_plantilla_barra
Verifica el estado de PlantillaBarra y muestra qué categorías están vinculadas.

Ubicación: comercial/management/commands/verificar_plantilla_barra.py
(Crear las carpetas management/ y commands/ con sus __init__.py)
"""
from django.core.management.base import BaseCommand
from comercial.models import PlantillaBarra, Insumo


class Command(BaseCommand):
    help = 'Verifica el estado de la Plantilla de Barra y sugiere insumos para vincular'

    def handle(self, *args, **options):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("  📋 DIAGNÓSTICO DE PLANTILLA DE BARRA")
        self.stdout.write("=" * 70)

        total = PlantillaBarra.objects.count()
        activos = PlantillaBarra.objects.filter(activo=True).count()

        if total == 0:
            self.stdout.write(self.style.WARNING(
                "\n⚠️  La tabla PlantillaBarra está VACÍA."
                "\n   Por eso la lista de compras muestra nombres genéricos."
                "\n   Usa el Asistente en Admin > Plantilla de Barra > 'Configurar Plantilla'"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n✅ Registros totales: {total} ({activos} activos)"))

        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("  ESTADO POR CATEGORÍA:")
        self.stdout.write("-" * 70)

        for cat_key, cat_label in PlantillaBarra.CATEGORIAS_BARRA:
            plantilla = PlantillaBarra.objects.filter(categoria=cat_key, activo=True).first()
            if plantilla:
                self.stdout.write(self.style.SUCCESS(
                    f"  ✅ {cat_label:25s} → {plantilla.insumo.nombre} "
                    f"({plantilla.insumo.presentacion or 'sin presentación'}) "
                    f"[{plantilla.insumo.proveedor or 'sin proveedor'}]"
                ))
            else:
                # Buscar insumos candidatos
                palabras = cat_label.lower().split()
                candidatos = Insumo.objects.filter(categoria='CONSUMIBLE')
                for palabra in palabras:
                    if len(palabra) > 3:
                        found = candidatos.filter(nombre__icontains=palabra)
                        if found.exists():
                            candidatos = found
                            break

                if candidatos.exists() and candidatos.count() <= 5:
                    sugerencias = ", ".join([f"{c.nombre} (${c.costo_unitario})" for c in candidatos[:3]])
                    self.stdout.write(self.style.WARNING(
                        f"  ❌ {cat_label:25s} → SIN VINCULAR  💡 Sugerencias: {sugerencias}"
                    ))
                else:
                    self.stdout.write(self.style.ERROR(
                        f"  ❌ {cat_label:25s} → SIN VINCULAR  (No hay insumos similares)"
                    ))

        self.stdout.write("\n" + "-" * 70)
        total_insumos = Insumo.objects.count()
        self.stdout.write(f"  Total de Insumos en catálogo: {total_insumos}")
        self.stdout.write("=" * 70 + "\n")