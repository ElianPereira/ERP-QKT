"""
Cron diario: envía recordatorios de parcialidades por email y WhatsApp.

Uso:
    python manage.py enviar_recordatorios
    python manage.py enviar_recordatorios --dry-run

Es el único comando de recordatorios con lógica real. `comercial.enviar_recordatorios_pagos`
quedó como shim deprecado que delega aquí, porque el Cron de Railway lo invoca
por ese nombre y vive fuera del repositorio.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from comercial.models import ParcialidadPago
from comunicacion.services_notificaciones import notificar_recordatorio

# Días respecto a la fecha límite en los que se avisa. Es la unión de los dos
# calendarios que existían antes de consolidar los comandos: +3 y −1 venían de
# `comunicacion`, el día 0 (vencimiento) venía de `comercial`. Quitar un valor
# de esta lista es todo lo que hace falta para cambiar el calendario.
DIAS_AVISO = (3, 0, -1)


class Command(BaseCommand):
    help = "Envía recordatorios automáticos de pagos pendientes (email + WhatsApp)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra a quién se enviaría sin llamar a Brevo/Meta ni registrar nada.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # localdate() y no now().date(): el segundo devuelve la fecha UTC, así que
        # entre las 18:00 y la medianoche de Mérida el cron comparaba contra el
        # día siguiente y mandaba los recordatorios corridos un día.
        hoy = timezone.localdate()
        objetivos = [hoy + timedelta(days=d) for d in DIAS_AVISO]

        parcialidades = ParcialidadPago.objects.filter(
            pagada=False,
            fecha_limite__in=objetivos,
            plan__activo=True,
        ).select_related('plan__cotizacion__cliente')

        enviadas = 0
        for parc in parcialidades:
            cot = parc.plan.cotizacion
            cliente = cot.cliente
            if not cliente or (not cliente.email and not cliente.telefono):
                continue

            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] COT-{cot.id:03d} parcialidad #{parc.numero} "
                    f"vence {parc.fecha_limite} → {cliente.nombre}"
                )
                enviadas += 1
                continue

            # La idempotencia es responsabilidad del servicio (clave por
            # parcialidad + fecha + canal), así que correr el cron dos veces el
            # mismo día no duplica.
            notificar_recordatorio(
                parc,
                fecha=hoy,
                dias_restantes=(parc.fecha_limite - hoy).days,
            )
            enviadas += 1

        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f"{etiqueta}Recordatorios procesados: {enviadas}"))
