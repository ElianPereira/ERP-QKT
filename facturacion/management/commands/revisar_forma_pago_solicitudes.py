"""
Revisión de SOLO LECTURA: lista las solicitudes de factura aún no timbradas
(PENDIENTE/ENVIADA) cuya forma de pago no coincide con la que hoy saldría de
su Pago (p. ej. un depósito en efectivo que se mandó como 03 Transferencia).
No modifica nada: el contador decide qué corregir.

Uso:
    python manage.py revisar_forma_pago_solicitudes [--desde AAAA-MM-DD]
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from facturacion.models import SolicitudFactura
from facturacion.signals import forma_pago_de_pago


class Command(BaseCommand):
    help = "Lista solicitudes sin timbrar cuya forma de pago difiere de la que hoy saldría de su pago (solo lectura)."

    def add_arguments(self, parser):
        parser.add_argument('--desde', help='Fecha de pago mínima (AAAA-MM-DD).')

    def handle(self, *args, **opts):
        qs = SolicitudFactura.objects.filter(
            estado__in=['PENDIENTE', 'ENVIADA'], pago__isnull=False,
        ).select_related('pago', 'cotizacion').order_by('fecha_pago', 'id')
        if opts['desde']:
            try:
                qs = qs.filter(fecha_pago__gte=date.fromisoformat(opts['desde']))
            except ValueError as e:
                raise CommandError("--desde debe ser AAAA-MM-DD.") from e

        distintas = 0
        for sol in qs:
            esperada = forma_pago_de_pago(sol.pago)
            if sol.forma_pago == esperada:
                continue
            distintas += 1
            self.stdout.write(
                f"Solicitud #{sol.pk} (COT-{sol.cotizacion_id}, pago #{sol.pago_id} {sol.pago.metodo}, "
                f"{sol.fecha_pago}, {sol.estado}): tiene {sol.forma_pago}, hoy saldría {esperada}."
            )
        self.stdout.write(f"{distintas} solicitud(es) con forma de pago distinta. No se modificó nada.")
