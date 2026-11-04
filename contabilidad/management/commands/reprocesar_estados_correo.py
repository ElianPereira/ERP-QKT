"""Completa los estados de cuenta que llegaron por correo y no terminaron (Issue #401).

    manage.py reprocesar_estados_correo             # simula
    manage.py reprocesar_estados_correo --aplicar   # reprocesa y avisa si no llegó

Reprocesa las bitácoras en PENDIENTE o ERROR con menos de 8 intentos. Desde el
día 7 del mes, si no hay estado de cuenta del mes anterior, deja un solo
pendiente por mes. Lo agenda el programador (`core_erp/programador.py`).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from contabilidad import services_estados_correo as servicio


class Command(BaseCommand):
    help = "Reprocesa estados de cuenta recibidos por correo y avisa si el del mes no llegó."

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help="Sin esta bandera solo simula.")

    def handle(self, *args, **o):
        candidatas, ok = servicio.reprocesar_pendientes(aplicar=o['aplicar'])
        if not o['aplicar']:
            self.stdout.write(f"Simulación: {candidatas} correo(s) por reprocesar. Usa --aplicar.")
            return
        avisos = servicio.avisar_si_no_llego(timezone.localdate())
        self.stdout.write(f"{candidatas} correo(s) reprocesado(s), {ok} completado(s); "
                          f"{avisos} aviso(s) de «no llegó».")
