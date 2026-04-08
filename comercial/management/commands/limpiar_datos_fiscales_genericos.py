"""
Vacía los campos fiscales de Clientes que fueron llenados manualmente con los
datos genéricos de PUBLICO EN GENERAL (RFC XAXX010101000, razón social
PUBLICO EN GENERAL, etc).

El sistema YA aplica el fallback PUBLICO EN GENERAL automáticamente en
facturacion/signals.py cuando rfc/razon_social están vacíos, así que
guardar esos datos manualmente solo ensucia el catálogo.

Uso:
    python manage.py limpiar_datos_fiscales_genericos          # dry-run
    python manage.py limpiar_datos_fiscales_genericos --apply  # aplica
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from comercial.models import Cliente


RFC_GENERICO = 'XAXX010101000'
RAZON_SOCIAL_GENERICA_KEYS = ('PUBLICO EN GENERAL', 'PÚBLICO EN GENERAL')


class Command(BaseCommand):
    help = (
        "Vacía los campos fiscales de Clientes que tienen datos genéricos "
        "PUBLICO EN GENERAL (el sistema ya aplica ese fallback automático)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica los cambios. Sin esta flag, solo muestra qué haría (dry-run).',
        )

    def handle(self, *args, **options):
        apply = options['apply']

        # Filtro: cualquier cliente cuyo RFC sea XAXX010101000 o cuya razón
        # social empiece con PUBLICO EN GENERAL.
        qs = Cliente.objects.filter(
            Q(rfc__iexact=RFC_GENERICO)
            | Q(razon_social__iexact=RAZON_SOCIAL_GENERICA_KEYS[0])
            | Q(razon_social__iexact=RAZON_SOCIAL_GENERICA_KEYS[1])
        ).order_by('id')

        if not qs.exists():
            self.stdout.write(self.style.SUCCESS(
                "No hay clientes con datos fiscales genéricos. Nada que hacer."
            ))
            return

        self.stdout.write(self.style.WARNING(
            f"Encontrados {qs.count()} clientes con datos fiscales genéricos:\n"
        ))
        for c in qs:
            self.stdout.write(
                f"  • #{c.id:4d} | nombre='{c.nombre}' | rfc='{c.rfc}' "
                f"| razon_social='{c.razon_social}' | cp='{c.codigo_postal_fiscal}' "
                f"| regimen='{c.regimen_fiscal}' | uso_cfdi='{c.uso_cfdi}'"
            )

        if not apply:
            self.stdout.write(self.style.NOTICE(
                "\n[DRY RUN] No se aplicaron cambios. "
                "Vuelve a correr con --apply para vaciar los campos fiscales."
            ))
            return

        actualizados = 0
        for c in qs:
            c.rfc = None
            c.razon_social = None
            c.codigo_postal_fiscal = None
            c.regimen_fiscal = None
            c.uso_cfdi = None
            c.es_cliente_fiscal = False
            c.save(update_fields=[
                'rfc', 'razon_social', 'codigo_postal_fiscal',
                'regimen_fiscal', 'uso_cfdi', 'es_cliente_fiscal',
            ])
            actualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{actualizados} clientes limpiados. "
            f"Cuando se les facture, el sistema aplicará automáticamente "
            f"PUBLICO EN GENERAL como fallback."
        ))
