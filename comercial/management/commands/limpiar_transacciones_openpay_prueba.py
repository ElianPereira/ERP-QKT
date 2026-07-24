"""
Borra las transacciones Openpay de sandbox y todo lo que generaron: el Pago,
la póliza del pago (origen=PAGO_CLIENTE) y la póliza de la comisión
(origen=COMISION_OPENPAY) con sus movimientos contables.

NO toca la Cotizacion ni el Cliente — solo revierte el pago como si nunca
se hubiera hecho (vuelve a quedar con saldo pendiente).

Por seguridad, se niega a correr si OPENPAY_MODE ya es 'production' — evita
que alguien lo vuelva a correr por error después de salir en vivo y borre
transacciones reales.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from comercial.models import OpenpayTransaccion
from comercial.services_openpay import borrar_transacciones_openpay_prueba


class Command(BaseCommand):
    help = "Borra las transacciones Openpay de prueba (sandbox) y sus Pago/Poliza asociados."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica el borrado. Sin esta flag, solo muestra qué haría (dry-run).',
        )

    def handle(self, *args, **options):
        if options['apply'] and settings.OPENPAY_MODE == 'production':
            raise CommandError(
                "OPENPAY_MODE es 'production' — este comando es solo para limpiar datos "
                "de sandbox y se niega a correr para no borrar transacciones reales."
            )

        apply = options['apply']
        registros = list(OpenpayTransaccion.objects.all().order_by('id'))

        if not registros:
            self.stdout.write(self.style.SUCCESS("No hay transacciones Openpay. Nada que hacer."))
            return

        self.stdout.write(self.style.WARNING(f"Encontradas {len(registros)} transacciones Openpay:\n"))
        for r in registros:
            pago_txt = f"Pago #{r.pago_id} (${r.pago.monto})" if r.pago_id else "sin Pago vinculado"
            self.stdout.write(
                f"  • {r.openpay_id} | {r.metodo or r.event_type} | ${r.monto} | {pago_txt} | "
                f"COT-{r.cotizacion_id if r.cotizacion_id else '?'}"
            )

        if not apply:
            self.stdout.write(self.style.NOTICE(
                "\n[DRY RUN] No se borró nada. Vuelve a correr con --apply para aplicar."
            ))
            return

        try:
            n_transacciones, n_pagos = borrar_transacciones_openpay_prueba(registros)
        except ValueError as e:
            raise CommandError(str(e))

        self.stdout.write(self.style.SUCCESS(
            f"\nBorrados: {n_transacciones} transacciones Openpay, {n_pagos} pagos, "
            f"pólizas/movimientos asociados. Las cotizaciones quedaron intactas con su saldo "
            f"pendiente restaurado."
        ))
