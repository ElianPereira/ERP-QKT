from django.core.management.base import BaseCommand
from comercial.models import Cotizacion


class Command(BaseCommand):
    help = (
        'Recalcula y persiste los totales fiscales (subtotal, IVA, retenciones, '
        'precio_final) de todas las cotizaciones activas. Útil para reparar '
        'cotizaciones creadas antes del fix de persistencia de IVA.'
    )

    def handle(self, *args, **options):
        qs = Cotizacion.objects.exclude(estado='CANCELADA').select_related('cliente')
        total = qs.count()
        self.stdout.write(f"Recalculando {total} cotizaciones...")
        for cot in qs:
            cot.calcular_totales()
            Cotizacion.objects.filter(pk=cot.pk).update(
                subtotal=cot.subtotal,
                iva=cot.iva,
                retencion_isr=cot.retencion_isr,
                retencion_iva=cot.retencion_iva,
                precio_final=cot.precio_final,
            )
        self.stdout.write(self.style.SUCCESS(f"Listo. {total} cotizaciones actualizadas."))
