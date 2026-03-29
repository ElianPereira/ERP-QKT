import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core_erp.settings')
django.setup()

from comercial.models import Cotizacion

qs = Cotizacion.objects.exclude(estado='CANCELADA').select_related('cliente')
print(f"Recalculando {qs.count()} cotizaciones...")
for cot in qs:
    cot.requiere_factura = True
    cot.calcular_totales()
    Cotizacion.objects.filter(pk=cot.pk).update(
        requiere_factura=True,
        subtotal=cot.subtotal,
        iva=cot.iva,
        retencion_isr=cot.retencion_isr,
        retencion_iva=cot.retencion_iva,
        precio_final=cot.precio_final,
    )
print("Listo.")