"""
Management command: migrar_proveedores
Convierte los valores de texto del campo proveedor_legacy de Insumo
en registros del modelo Proveedor y los vincula automáticamente.

Uso: python manage.py migrar_proveedores
"""
from django.core.management.base import BaseCommand
from comercial.models import Insumo, Proveedor


class Command(BaseCommand):
    help = 'Migra proveedores de texto libre a registros en la tabla Proveedor'

    def handle(self, *args, **options):
        insumos_con_proveedor = Insumo.objects.filter(
            proveedor_legacy__isnull=False
        ).exclude(proveedor_legacy='').exclude(proveedor_legacy__exact=' ')

        if not insumos_con_proveedor.exists():
            self.stdout.write(self.style.WARNING('No hay insumos con proveedor de texto para migrar.'))
            return

        creados = 0
        vinculados = 0
        ya_vinculados = 0

        for insumo in insumos_con_proveedor:
            # Saltar si ya tiene FK asignado
            if insumo.proveedor_id:
                ya_vinculados += 1
                continue

            nombre_limpio = insumo.proveedor_legacy.strip()
            if not nombre_limpio:
                continue

            # Buscar o crear el proveedor (case-insensitive)
            proveedor = Proveedor.objects.filter(nombre__iexact=nombre_limpio).first()
            if not proveedor:
                proveedor = Proveedor.objects.create(nombre=nombre_limpio)
                creados += 1
                self.stdout.write(f'  + Proveedor creado: "{nombre_limpio}"')

            # Vincular
            insumo.proveedor = proveedor
            # Usamos update para no disparar el save() completo del modelo
            Insumo.objects.filter(pk=insumo.pk).update(proveedor=proveedor)
            vinculados += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'=== MIGRACIÓN COMPLETADA ==='))
        self.stdout.write(f'  Proveedores creados:    {creados}')
        self.stdout.write(f'  Insumos vinculados:     {vinculados}')
        self.stdout.write(f'  Ya estaban vinculados:  {ya_vinculados}')
        self.stdout.write('')