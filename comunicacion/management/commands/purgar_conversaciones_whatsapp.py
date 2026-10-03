from django.core.management.base import BaseCommand

from comunicacion.services_retencion import purgar_conversaciones


class Command(BaseCommand):
    help = ('Borra las conversaciones de WhatsApp del agente cuyo plazo de conservación venció '
            '(Aviso de Privacidad §9). Simula por defecto; --aplicar borra.')

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Borrar de verdad (sin esto solo cuenta).')

    def handle(self, *args, **opciones):
        aplicar = opciones['aplicar']
        r = purgar_conversaciones(aplicar=aplicar)
        accion = 'Borradas' if aplicar else 'Por borrar (simulación)'
        self.stdout.write(
            f"{accion}: {r['CONSULTA']} de consulta (1 año), {r['ATENCION']} de atención (2 años), "
            f"{r['CONTRATACION']} de contratación (5 años); {r['copias_bitacora']} copias en la bitácora."
        )
