"""Mantenimiento diario de referidos (Issue #468, fase 7): gana los que ya quedaron confirmados y vence los de 12 meses."""
from django.core.management.base import BaseCommand

from marketing.models import Referido
from marketing.services import referidos


class Command(BaseCommand):
    help = 'Gana los referidos cuya venta ya está confirmada (aunque la señal no corrió), avisa y vence los de 12 meses.'

    def handle(self, *args, **options):
        ganados = 0
        for ref in Referido.objects.filter(estado=Referido.REGISTRADO).select_related(
                'recomendado_cotizacion', 'recomendado_lead__cotizacion'):
            cot = ref.recomendado_cotizacion or (ref.recomendado_lead.cotizacion if ref.recomendado_lead else None)
            if cot is None:
                continue
            for ganado in referidos.ganar_por_cotizacion(cot):
                referidos.avisar_ganado(ganado)
                ganados += 1
        vencidos = referidos.vencer()
        self.stdout.write(self.style.SUCCESS(f'Referidos ganados: {ganados}; vencidos: {vencidos}.'))
