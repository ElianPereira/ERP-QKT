"""Reseñas de Google Business Profile (Issue #468, fase 7). Apagado sin credenciales."""
from django.core.management.base import BaseCommand, CommandError

from marketing.services import resenas


class Command(BaseCommand):
    help = 'Trae las reseñas de Google y abre un pendiente URGENTE por cada una de 3 estrellas o menos.'

    def handle(self, *args, **options):
        if not resenas.configurado():
            self.stdout.write('Reseñas de Google sin credenciales: sincronización apagada.')
            return
        try:
            r = resenas.sincronizar()
        except resenas.GoogleError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Reseñas nuevas: {r['nuevas']}; avisos a Dirección: {r['avisos']}."))
