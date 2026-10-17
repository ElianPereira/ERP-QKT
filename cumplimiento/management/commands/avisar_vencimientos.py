"""
Avisa por WhatsApp y correo los trámites de cumplimiento por vencer, vencidos
y los contratos con preaviso de cancelación (Issue #403).

Simula por defecto; `--aplicar` envía. Pensado como Cron diario en Railway
(`5 13 * * *` UTC, ~07:05 Mérida). Es idempotente: correrlo dos veces el
mismo día no repite mensajes, y un canal fallido se reintenta en la corrida
siguiente.

Uso:
    python manage.py avisar_vencimientos
    python manage.py avisar_vencimientos --aplicar
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from cumplimiento.services_avisos import _linea, procesar_avisos


class Command(BaseCommand):
    help = "Avisa los vencimientos de trámites de cumplimiento (simula por defecto)."

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Envía los avisos (por defecto solo simula).')

    def handle(self, *args, **options):
        aplicar = options['aplicar']
        resultado = procesar_avisos(timezone.localdate(), aplicar=aplicar)
        avisos = resultado['avisos']
        if not avisos:
            self.stdout.write('Sin vencimientos que avisar hoy.')
            return
        for aviso in avisos:
            self.stdout.write(f"  {_linea(aviso)}")
        if not aplicar:
            self.stdout.write(self.style.WARNING(f'[SIMULACIÓN] {len(avisos)} aviso(s); usa --aplicar para enviar.'))
            return
        for canal, stats in resultado['canales'].items():
            self.stdout.write(self.style.SUCCESS(
                f"{canal}: {stats['enviados']} enviado(s), {stats['fallidos']} fallido(s)"))
        if not resultado['canales']:
            self.stdout.write('Nada nuevo que enviar (ya avisado).')
