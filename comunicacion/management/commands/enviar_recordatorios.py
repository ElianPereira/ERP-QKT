"""
Cron diario: envía recordatorios de parcialidades próximas a vencer (3 días antes)
y de parcialidades vencidas hace 1 día.

Uso:
    python manage.py enviar_recordatorios
"""
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from comercial.models import ParcialidadPago
from comunicacion.models import ComunicacionCliente
from comunicacion.services import enviar_email
from comunicacion.signals import _portal_url


class Command(BaseCommand):
    help = "Envía recordatorios automáticos de pagos pendientes."

    def handle(self, *args, **options):
        hoy = timezone.now().date()
        objetivos = [hoy + timedelta(days=3), hoy - timedelta(days=1)]

        parcialidades = ParcialidadPago.objects.filter(
            pagada=False,
            fecha_limite__in=objetivos,
            plan__activo=True,
        ).select_related('plan__cotizacion__cliente')

        enviadas = 0
        for parc in parcialidades:
            cot = parc.plan.cotizacion
            if not cot.cliente.email:
                continue

            # Idempotencia: no mandar dos recordatorios para la misma parcialidad el mismo día
            ya = ComunicacionCliente.objects.filter(
                cotizacion=cot,
                tipo='RECORDATORIO_PAGO',
                fecha_envio__date=hoy,
                cuerpo__contains=f"parcialidad #{parc.numero}",
            ).exists()
            if ya:
                continue

            saldo = cot.precio_final - cot.total_pagado()
            enviar_email(
                cotizacion=cot,
                tipo='RECORDATORIO_PAGO',
                destinatario=cot.cliente.email,
                asunto=f"Recordatorio de pago — {cot.nombre_evento}",
                template='comunicacion/email/recordatorio.html',
                context={
                    'cotizacion': cot,
                    'parcialidad': parc,
                    'dias_restantes': (parc.fecha_limite - hoy).days,
                    'saldo': saldo,
                    'portal_url': _portal_url(cot),
                    'parcialidad_marker': f"parcialidad #{parc.numero}",
                },
                trigger='CRON',
            )
            enviadas += 1

        self.stdout.write(self.style.SUCCESS(f"Recordatorios enviados: {enviadas}"))
