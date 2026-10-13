"""Descarga masiva del SAT: crea las solicitudes del tramo pendiente y da un
paso a todas las que siguen abiertas (el SAT tarda minutos u horas en preparar
los paquetes). Pensado como Cron Job de Railway cada hora; es idempotente.

    manage.py sincronizar_sat                      # tramo nuevo + avanzar
    manage.py sincronizar_sat --solo-avanzar       # no crea solicitudes
    manage.py sincronizar_sat --historial-desde 2026-01-01   # trae más historial

El historial también se pide desde el admin (campo «Traer historial desde» de la
e.firma); se descarga por tramos de un mes y lo que falle se reintenta solo.
"""
from datetime import date

from django.core.management.base import BaseCommand

from contabilidad import services_sat as sat
from contabilidad.models import CredencialSAT


class Command(BaseCommand):
    help = "Sincroniza CFDI emitidos y recibidos con el Servicio de Descarga Masiva del SAT."

    def add_arguments(self, parser):
        parser.add_argument('--historial-desde', type=date.fromisoformat,
                            help="AAAA-MM-DD: fecha desde la que se quiere tener todo.")
        parser.add_argument('--solo-avanzar', action='store_true')

    def handle(self, *args, **o):
        for cred in CredencialSAT.objects.filter(activa=True):
            if cred.vencida:
                self.stderr.write(f"{cred.rfc}: e.firma vencida, se omite.")
                continue
            if o['historial_desde'] and (not cred.historial_desde or o['historial_desde'] < cred.historial_desde):
                cred.historial_desde = o['historial_desde']
                cred.save(update_fields=['historial_desde', 'updated_at'])
            if o['solo_avanzar']:
                continue
            creadas = sat.planear_sincronizacion(cred)
            self.stdout.write(f"{cred.rfc}: {len(creadas)} solicitud(es) nueva(s).")
        pasos = sat.procesar_pendientes()
        resueltos = sat.reintentar_comprobantes()
        self.stdout.write(f"{pasos} solicitud(es) avanzada(s); {resueltos} comprobante(s) resuelto(s) tras reintento.")
