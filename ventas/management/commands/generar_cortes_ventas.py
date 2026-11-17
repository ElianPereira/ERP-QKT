from django.core.management.base import BaseCommand

from ventas.services_cortes import generar_cortes


class Command(BaseCommand):
    help = ('Genera los cortes en borrador de visitas (semanal) o comisiones (quincenal) del personal de '
            'ventas y avisa a Dirección. Idempotente: un periodo ya cortado no se vuelve a crear.')

    def add_arguments(self, parser):
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument('--visitas', action='store_true')
        grupo.add_argument('--comisiones', action='store_true')

    def handle(self, *args, **opciones):
        tipo = 'VISITAS' if opciones['visitas'] else 'COMISIONES'
        cortes = generar_cortes(tipo)
        self.stdout.write(f'Cortes de {tipo.lower()}: {len(cortes)}')
        for c in cortes:
            self.stdout.write(f'  {c} → ${c.total} ({c.get_estado_display()})')
