"""Descarga masiva del SAT: crea las solicitudes del tramo pendiente y da un
paso a todas las que siguen abiertas (el SAT tarda minutos u horas en preparar
los paquetes). Pensado como Cron Job de Railway cada hora; es idempotente.

    manage.py sincronizar_sat                      # tramo nuevo + avanzar
    manage.py sincronizar_sat --solo-avanzar       # no crea solicitudes
    manage.py sincronizar_sat --desde 2026-07-01 --hasta 2026-09-30
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from contabilidad import services_sat as sat
from contabilidad.models import CredencialSAT


class Command(BaseCommand):
    help = "Sincroniza CFDI emitidos y recibidos con el Servicio de Descarga Masiva del SAT."

    def add_arguments(self, parser):
        parser.add_argument('--desde', type=date.fromisoformat)
        parser.add_argument('--hasta', type=date.fromisoformat)
        parser.add_argument('--solo-avanzar', action='store_true')

    def handle(self, *args, **o):
        if bool(o['desde']) != bool(o['hasta']):
            raise CommandError("--desde y --hasta van juntos.")
        for cred in CredencialSAT.objects.filter(activa=True):
            if cred.vencida:
                self.stderr.write(f"{cred.rfc}: e.firma vencida, se omite.")
                continue
            if o['solo_avanzar']:
                continue
            if o['desde']:
                creadas = sat.crear_solicitudes(cred, o['desde'], o['hasta'])
            else:
                creadas = sat.planear_sincronizacion(cred)
            self.stdout.write(f"{cred.rfc}: {len(creadas)} solicitud(es) nueva(s).")
        pasos = sat.procesar_pendientes()
        resueltos = sat.reintentar_comprobantes()
        self.stdout.write(f"{pasos} solicitud(es) avanzada(s); {resueltos} comprobante(s) resuelto(s) tras reintento.")
