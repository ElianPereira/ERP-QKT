"""
Cron: envío automático de última instancia de evidencia de contracargos
(Issue #305).

Un `Contracargo` en `EN_DISPUTA` debería tener su evidencia enviada a mano
desde el admin (`ContracargoAdmin.enviar_evidencia_openpay`) mucho antes del
plazo — este comando es la red de seguridad: si nadie lo hizo, manda la
evidencia ya armada por el propio ERP cuando faltan `HORAS_ANTICIPACION`
horas para `fecha_limite_evidencia`, para no perder la disputa solo por no
haber reaccionado a tiempo.

El umbral es en HORAS, no en días — a diferencia de los demás cron del
repo (`enviar_recordatorios_contador`, `enviar_recordatorios_pagos`), este
debe darse de alta en Railway con periodicidad horaria, no diaria.

Uso:
    python manage.py enviar_evidencia_contracargos_pendientes
    python manage.py enviar_evidencia_contracargos_pendientes --dry-run
"""
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from comercial.models import Contracargo
from comercial.services_evidencia_contracargo import enviar_evidencia_a_openpay
from comunicacion.services_notificaciones import alertar_equipo_evidencia_automatica

# Hora de corte del día límite (fin de jornada laboral) — `fecha_limite_evidencia`
# es un DateField sin hora; el plazo real se cuenta desde este punto de ese día,
# no desde medianoche. Mismo criterio ya usado en el repo para "el día antes"
# (ver operaciones/constantes.py).
HORA_CORTE_PLAZO = time(18, 0)
HORAS_ANTICIPACION = 24


class Command(BaseCommand):
    help = (
        "Envío automático de última instancia de evidencia de contracargos en "
        f"disputa que nadie mandó a mano, cuando faltan {HORAS_ANTICIPACION}h "
        "para el plazo límite ante Openpay."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra qué se enviaría sin mandar nada ni marcar nada.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        ahora = timezone.now()

        pendientes = Contracargo.objects.filter(
            estado='EN_DISPUTA',
            evidencia_enviada=False,
            fecha_limite_evidencia__isnull=False,
        )
        enviados = 0
        for contracargo in pendientes:
            limite_dt = timezone.make_aware(
                datetime.combine(contracargo.fecha_limite_evidencia, HORA_CORTE_PLAZO)
            )
            disparo = limite_dt - timedelta(hours=HORAS_ANTICIPACION)
            if ahora < disparo:
                continue  # todavía no se llega al umbral de última instancia

            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] Contracargo {contracargo.openpay_id} — se enviaría "
                    f"(vence {timezone.localtime(limite_dt):%d/%m/%Y %H:%M})"
                )
                continue

            # usuario=None: es la marca de un envío automático, no manual
            # (ver Contracargo.evidencia_enviada_por).
            ok, mensaje = enviar_evidencia_a_openpay(contracargo)
            if ok:
                enviados += 1
                alertar_equipo_evidencia_automatica(contracargo)
                self.stdout.write(self.style.SUCCESS(
                    f"Contracargo {contracargo.openpay_id}: evidencia enviada automáticamente."
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    f"Contracargo {contracargo.openpay_id}: no se pudo enviar ({mensaje})."
                ))

        if not dry_run:
            self.stdout.write(f"{enviados} envío(s) automático(s) de evidencia realizados.")
