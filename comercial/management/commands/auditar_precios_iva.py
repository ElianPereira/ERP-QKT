"""
Verificaciones que solo pueden correrse contra la base de datos real.

Uso en Railway:

    python manage.py auditar_precios_iva

Reporta dos cosas:

1. Cotizaciones de persona MORAL cuya retención de ISR cae en un empate
   exacto de .005. Es el único caso donde el redondeo explícito con
   ROUND_HALF_UP puede diferir del ROUND_HALF_EVEN que Django aplicaba antes
   al guardar el DecimalField. Para el IVA el empate es inalcanzable con una
   base de dos decimales, así que no hay riesgo por ese lado.

2. La lista de precios a reetiquetar en la landing, con el precio con IVA ya
   calculado y listo para copiar.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from core_erp import impuestos
from comercial.models import Cotizacion, Producto


class Command(BaseCommand):
    help = "Audita el impacto del refactor de IVA y genera la lista de precios con IVA."

    def handle(self, *args, **opciones):
        self._empates_retencion()
        self.stdout.write('')
        self._lista_precios()

    def _empates_retencion(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '1. Cotizaciones MORAL con empate .005 en la retención de ISR'
        ))
        afectadas = []
        qs = Cotizacion.objects.filter(cliente__tipo_persona='MORAL') \
                               .select_related('cliente')
        for cot in qs.iterator():
            base = Decimal(cot.subtotal or 0) - Decimal(cot.descuento or 0)
            if base < 0:
                base = Decimal('0.00')
            centavos_base = int((base * 100).to_integral_value())
            if centavos_base % 800 == 200:
                afectadas.append((cot.id, base, cot.retencion_isr))

        total_moral = qs.count()
        self.stdout.write(f'   Cotizaciones de persona moral revisadas: {total_moral}')
        if not afectadas:
            self.stdout.write(self.style.SUCCESS(
                '   Sin empates: el refactor no mueve un solo centavo del histórico.'
            ))
            return
        self.stdout.write(self.style.WARNING(
            f'   {len(afectadas)} cotización(es) donde la retención podría variar 1 centavo:'
        ))
        for cot_id, base, ret in afectadas:
            nueva = impuestos.ret_isr_de(base)
            self.stdout.write(
                f'     COT-{cot_id:03d}  base={base}  guardada={ret}  con ROUND_HALF_UP={nueva}'
            )

    def _lista_precios(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '2. Precios para reetiquetar en la landing (IVA incluido)'
        ))
        productos = Producto.objects.filter(visible_cotizador=True) \
                                    .order_by('-es_paquete', 'grupo_cotizador',
                                              'orden_cotizador', 'nombre')
        if not productos:
            self.stdout.write('   (sin productos visibles en el cotizador)')
            return

        self.stdout.write(f'   {"PRODUCTO":<44}{"BASE":>13}{"CON IVA":>13}')
        self.stdout.write('   ' + '-' * 70)
        for p in productos:
            base = Decimal(str(p.sugerencia_precio()))
            etiqueta = ('[PAQ] ' if p.es_paquete else '') + p.nombre
            self.stdout.write(
                f'   {etiqueta[:44]:<44}{base:>13,.2f}{impuestos.con_iva(base):>13,.2f}'
            )
        self.stdout.write('   ' + '-' * 70)
        self.stdout.write('   Leyenda obligatoria en la landing: "Precios en MXN, IVA incluido"')
