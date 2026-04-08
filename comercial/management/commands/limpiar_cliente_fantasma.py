"""
Aísla clientes fantasma (PUBLICO EN GENERAL, PROSPECTO sin teléfono real)
para que el lookup por teléfono nunca más los matchee.

NO borra cotizaciones ni clientes. Solo:
- Lista cuántas cotizaciones cuelgan de cada cliente fantasma
- Renombra el cliente a `_FANTASMA_<id>_NO_USAR`
- Pone el teléfono a `_invalid_<id>` (cadena no numérica) para impedir matches futuros
"""
from django.core.management.base import BaseCommand

from comercial.models import Cliente
from comercial.services import _es_nombre_generico


class Command(BaseCommand):
    help = "Aísla clientes fantasma (PUBLICO EN GENERAL/PROSPECTO sin teléfono válido)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica los cambios. Sin esta flag, solo muestra qué haría (dry-run).',
        )

    def handle(self, *args, **options):
        apply = options['apply']

        # Candidatos: nombre genérico Y teléfono inválido (<10 dígitos numéricos)
        candidatos = []
        for c in Cliente.objects.all().order_by('id'):
            tel_digits = ''.join(filter(str.isdigit, c.telefono or ''))
            if _es_nombre_generico(c.nombre) and len(tel_digits) < 10:
                candidatos.append(c)

        if not candidatos:
            self.stdout.write(self.style.SUCCESS("No hay clientes fantasma. Nada que hacer."))
            return

        self.stdout.write(self.style.WARNING(
            f"Encontrados {len(candidatos)} clientes fantasma:\n"
        ))
        for c in candidatos:
            n_cot = c.cotizacion_set.count() if hasattr(c, 'cotizacion_set') else \
                    c.cotizacion_set.count() if hasattr(c, 'cotizaciones') else 0
            try:
                from comercial.models import Cotizacion
                n_cot = Cotizacion.objects.filter(cliente=c).count()
            except Exception:
                n_cot = '?'
            self.stdout.write(
                f"  • #{c.id:4d} | tel='{c.telefono}' | nombre='{c.nombre}' "
                f"| cotizaciones={n_cot}"
            )

        if not apply:
            self.stdout.write(self.style.NOTICE(
                "\n[DRY RUN] No se aplicaron cambios. "
                "Vuelve a correr con --apply para aplicarlos."
            ))
            return

        actualizados = 0
        for c in candidatos:
            nuevo_nombre = f"_FANTASMA_{c.id}_NO_USAR"
            nuevo_tel = f"_invalid_{c.id}"
            c.nombre = nuevo_nombre
            c.telefono = nuevo_tel
            c.save(update_fields=['nombre', 'telefono'])
            actualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{actualizados} clientes aislados. "
            f"Sus cotizaciones quedaron asignadas pero el cliente ya no será matcheado por canal."
        ))
