"""
Envía a la Conversions API de Meta los eventos pendientes de la bandeja (Issue #468, fase 6).
Corre cada 15 minutos en el programador. Sin META_CAPI_TOKEN no hace nada.

Uso:
    python manage.py enviar_eventos_conversion
"""
from django.core.management.base import BaseCommand

from marketing.services import conversiones


class Command(BaseCommand):
    help = 'Envía a Meta los eventos de conversión pendientes (con reintentos y solo con consentimiento).'

    def handle(self, *args, **options):
        r = conversiones.enviar_pendientes()
        if r['apagado']:
            self.stdout.write('Conversions API sin credenciales: envío apagado.')
            return
        self.stdout.write(self.style.SUCCESS(
            f"Conversiones: {r['enviados']} enviados, {r['reintentos']} por reintentar, {r['fallidos']} fallidos, "
            f"{r['omitidos']} omitidos sin consentimiento, {r['cancelados']} cancelados."))
