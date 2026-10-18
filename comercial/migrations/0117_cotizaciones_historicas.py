"""Repara las ventas importadas del sistema anterior ([HIST-NNNN]).

`importar_historico` las creaba sin conceptos y fijaba el total con un
update(); el primer save() completo les agregaba «Servicio de Barra» cotizada
para 1 persona y recalculaba, así que todas terminaron con el mismo precio.
Aquí se quita esa barra, se pone un concepto con el total original y se
marcan como históricas para que nunca vuelvan a recalcularse.

Decisiones del propietario (2026-10-18):
- Llevan IVA las ventas cuyo total sale de una base redonda (÷1.16 exacto);
  las demás se cobraron sin IVA y su total es la base.
- Todas se cobraron completas. El saldo que el sistema anterior dejó sin
  registrar se asienta como un pago «cobrado fuera del registro», igual que el
  resto del histórico: `bulk_create`, sin póliza ni solicitud de factura. Todas
  quedan CERRADAS (el precio corrupto ya había hecho que el cron cerrara
  algunas EJECUTADAS con saldo).
"""

import datetime
import re
from decimal import Decimal

from django.db import migrations, models
from django.db.models import Sum

from core_erp import impuestos

# Folio del sistema anterior -> total contratado (copia de
# comercial/management/commands/importar_historico.py: la migración no debe
# depender de un comando que puede cambiar).
TOTALES = {
    72: '6100.00', 76: '1250.00', 80: '450.00', 82: '1100.00',
    83: '10950.00', 85: '4500.00', 86: '1700.00', 88: '725.00',
    99: '10650.00', 100: '450.00', 103: '13578.99', 104: '11200.00',
    109: '150.00', 111: '400.00', 116: '750.00', 118: '600.00',
    119: '700.00', 120: '700.00', 121: '1450.00', 125: '6350.00',
    134: '17166.84', 136: '700.00', 137: '500.00', 138: '700.00',
    145: '800.00', 146: '200.00', 147: '700.00', 148: '986.00',
    149: '18328.00',
}

# Total = base redonda + IVA: 725 = 625, 1450 = 1250, 17166.84 = 14799,
# 986 = 850 y 18328 = 15800.
CON_IVA = {88, 121, 134, 148, 149}

NOTA_SALDO = 'Saldo cobrado fuera del registro del sistema anterior'

PATRON = re.compile(r'^\[HIST-(\d{4})\]')
CERO = Decimal('0.00')


def _desglose(folio, total):
    if folio in CON_IVA:
        d = impuestos.desglosar(total)
        return d['base'], d['iva']
    return total, CERO


def reparar(apps, schema_editor):
    Cotizacion = apps.get_model('comercial', 'Cotizacion')
    ItemCotizacion = apps.get_model('comercial', 'ItemCotizacion')
    Pago = apps.get_model('comercial', 'Pago')

    reparadas = 0
    for cot in Cotizacion.objects.filter(nombre_evento__startswith='[HIST-'):
        coincide = PATRON.match(cot.nombre_evento)
        if not coincide or int(coincide.group(1)) not in TOTALES:
            continue
        folio = int(coincide.group(1))
        total = Decimal(TOTALES[folio])
        base, iva = _desglose(folio, total)
        concepto = f'Venta registrada en el sistema anterior (folio {folio})'

        # Solo la barra que agregó el recálculo automático (sin producto);
        # cualquier otro concepto capturado a mano se conserva.
        ItemCotizacion.objects.filter(
            cotizacion=cot, producto__isnull=True,
            descripcion__startswith='Servicio de Barra',
        ).delete()
        item, _ = ItemCotizacion.objects.get_or_create(
            cotizacion=cot, descripcion=concepto,
            defaults={'cantidad': Decimal('1'), 'precio_unitario': base},
        )
        if item.precio_unitario != base or item.cantidad != 1:
            ItemCotizacion.objects.filter(pk=item.pk).update(cantidad=Decimal('1'), precio_unitario=base)
        otros = ItemCotizacion.objects.filter(cotizacion=cot).exclude(descripcion=concepto)
        if otros.exists():
            print(f'  [HIST-{folio:04d}] conserva {otros.count()} concepto(s) capturado(s) a mano; '
                  f'el total queda en el original (${total}).')

        pagos = Pago.objects.filter(cotizacion=cot)
        cobrado = (
            (pagos.filter(tipo='INGRESO', concepto='VENTA').aggregate(s=Sum('monto'))['s'] or CERO)
            - (pagos.filter(tipo='REEMBOLSO').aggregate(s=Sum('monto'))['s'] or CERO)
        )
        saldo = total - cobrado
        if saldo > 0:
            Pago.objects.bulk_create([Pago(
                cotizacion=cot, tipo='INGRESO', concepto='VENTA',
                fecha_pago=cot.fecha_evento or datetime.date.today(),
                monto=saldo, metodo='OTRO', solicitar_factura=False, notas=NOTA_SALDO,
            )])
            print(f'  [HIST-{folio:04d}] saldo de ${saldo} registrado como cobrado.')

        Cotizacion.objects.filter(pk=cot.pk).update(
            es_historica=True, subtotal=base, descuento=CERO, iva=iva,
            impuesto_hospedaje=CERO, retencion_isr=CERO, retencion_iva=CERO,
            precio_final=total, estado='CERRADA',
        )
        reparadas += 1

    if reparadas:
        print(f'  Ventas históricas reparadas: {reparadas}')


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0116_faq_mismo_dia_y_visitas"),
    ]

    operations = [
        migrations.AddField(
            model_name="cotizacion",
            name="es_historica",
            field=models.BooleanField(
                default=False, editable=False, verbose_name="Venta del sistema anterior"
            ),
        ),
        migrations.RunPython(reparar, migrations.RunPython.noop),
    ]
