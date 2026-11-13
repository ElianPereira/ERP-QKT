"""
Registra la pauta de Meta que se cobró a tarjetas que no son de la cuenta bancaria del ERP.

Contexto: el informe de facturación de Meta (jul–nov 2026) muestra cobros con otras tarjetas
(Mastercard ····5035 y ····3777, atribuidas por el propietario a Revolut) que nunca aparecen en el
estado de cuenta de BBVA y por eso la conciliación de pauta no cuadraba. Cada cobro es una póliza
de DIARIO, APLICADA: DEBE Publicidad (`GASTO_PUBLICIDAD`) / HABER Aportaciones del dueño
(`APORTACIONES_DUENO`), con el concepto de `conciliacion.PREFIJOS_EXTERNOS['META']`, que es lo que
la conciliación suma como «pagado con otra tarjeta». Meta factura con IVA 0 %, así que no hay IVA.

Simula por defecto; `--aplicar` escribe. Idempotente: un cobro cuya póliza vigente ya existe se omite.

Uso:
    python manage.py registrar_pauta_externa            # solo muestra lo que haría
    python manage.py registrar_pauta_externa --aplicar
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from contabilidad.models import ConfiguracionContable, MovimientoContable, Poliza, UnidadNegocio
from contabilidad.services_reglas_banco import usuario_sistema
from marketing.services import conciliacion

# (fecha del cobro, últimos 4 dígitos de la tarjeta, importe MXN): informe de facturación de Meta.
COBROS = [
    (date(2026, 8, 20), '5035', Decimal('405.44')),
    (date(2026, 9, 1), '5035', Decimal('406.48')),
    (date(2026, 9, 17), '3777', Decimal('401.80')),
    (date(2026, 9, 24), '3777', Decimal('49.24')),
    (date(2026, 9, 24), '3777', Decimal('196.76')),
    (date(2026, 9, 24), '3777', Decimal('98.38')),
    (date(2026, 9, 24), '3777', Decimal('49.19')),
]


def concepto_de(fecha, tarjeta, importe) -> str:
    return f"{conciliacion.PREFIJOS_EXTERNOS['META']} (tarjeta ····{tarjeta}) {fecha:%d/%m/%Y} ${importe}"


class Command(BaseCommand):
    help = "Registra como pólizas la pauta de Meta cobrada a tarjetas ajenas a BBVA. Simula por defecto."

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribe las pólizas (sin esto solo simula).')

    def handle(self, *args, **opciones):
        gasto = ConfiguracionContable.obtener_cuenta('GASTO_PUBLICIDAD')
        aportacion = ConfiguracionContable.obtener_cuenta('APORTACIONES_DUENO')
        faltan = [n for n, c in (('GASTO_PUBLICIDAD', gasto), ('APORTACIONES_DUENO', aportacion)) if c is None]
        if faltan:
            raise CommandError(f"Falta configurar la cuenta de: {', '.join(faltan)} (Contabilidad → Configuración contable).")
        unidad = UnidadNegocio.objects.filter(clave='QUINTA').first()
        if unidad is None:
            raise CommandError('No existe la unidad de negocio QUINTA.')
        aplicar = opciones['aplicar']
        usuario = usuario_sistema()
        self.stdout.write(f'Debe {gasto.codigo_sat} {gasto.nombre} / Haber {aportacion.codigo_sat} {aportacion.nombre}.')

        creadas = omitidas = 0
        total = Decimal('0.00')
        for fecha, tarjeta, importe in COBROS:
            concepto = concepto_de(fecha, tarjeta, importe)
            if Poliza.objects.filter(concepto=concepto).exclude(estado='CANCELADA').exists():
                omitidas += 1
                self.stdout.write(f'  ya existe  {concepto}')
                continue
            creadas += 1
            total += importe
            self.stdout.write(f'  {"crea" if aplicar else "crearía"}  {concepto}')
            if not aplicar:
                continue
            with transaction.atomic():
                poliza = Poliza.objects.create(
                    tipo='D', folio=Poliza.siguiente_folio('D', fecha), fecha=fecha, concepto=concepto,
                    unidad_negocio=unidad, estado='BORRADOR', origen='MANUAL', created_by=usuario)
                MovimientoContable.objects.create(poliza=poliza, cuenta=gasto, debe=importe, haber=Decimal('0.00'),
                                                  concepto=concepto[:300])
                MovimientoContable.objects.create(poliza=poliza, cuenta=aportacion, debe=Decimal('0.00'), haber=importe,
                                                  concepto=concepto[:300])
                poliza.aplicar(usuario)
        verbo = 'Creadas' if aplicar else 'Por crear'
        self.stdout.write(f'{verbo}: {creadas} por ${total}. Omitidas (ya existían): {omitidas}.')
        if not aplicar:
            self.stdout.write('Simulación: no se escribió nada. Usa --aplicar para registrarlas.')
