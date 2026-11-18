from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Manda a quien tiene el rol VENTAS su resumen de la semana. Idempotente por semana.'

    def handle(self, *args, **opciones):
        from ventas.services_avisos import enviar_resumen_semanal_ventas

        self.stdout.write(self.style.SUCCESS(f'Resumen de ventas enviado: {enviar_resumen_semanal_ventas()}'))
