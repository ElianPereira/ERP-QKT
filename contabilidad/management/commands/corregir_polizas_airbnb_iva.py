"""
Corrige las pólizas de Airbnb emitidas sin la línea de IVA trasladado.

Hasta que el modelo distinguió el IVA trasladado de las retenciones, el asiento
de un pago de Airbnb cargaba a bancos el depósito completo —que incluye el IVA
que la plataforma cobra al huésped y transfiere al anfitrión— pero solo abonaba
el ingreso. El asiento nunca cuadró: quedó descuadrado exactamente por el IVA
trasladado, y ese IVA no está registrado en ninguna cuenta de pasivo pese a que
es el anfitrión quien lo entera.

La corrección no edita ni borra nada. Por cada póliza defectuosa:

  1. se cancela, dejando asentados el motivo, el usuario y la fecha; sus
     movimientos quedan intactos para la auditoría, y fuera de los saldos,
     que en todo el ERP solo suman pólizas APLICADAS,
  2. se emite en su lugar la póliza correcta, con el IVA trasladado al HABER.

Es cancelación y reexpedición, no reversión: la operación con Airbnb nunca
cambió —el depósito fue el que fue—, lo que estaba mal era la captura. Una
póliza de ajuste no serviría aquí: el abono que falta a IVA trasladado no
tiene contrapartida propia, porque la contrapartida ya está registrada en
bancos desde el asiento original.

La corrección se asienta en el período de la póliza original a propósito. El
descuadre nació ahí, y las cifras que se declararon —ingreso, retenciones,
depósito— no cambian: lo único que aparece es el IVA trasladado que faltaba
registrar. Reclasificar contra el mes corriente movería un pasivo fiscal al mes
equivocado.

Uso:
    python manage.py corregir_polizas_airbnb_iva            # solo reporta
    python manage.py corregir_polizas_airbnb_iva --aplicar  # escribe
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from airbnb.models import PagoAirbnb
from contabilidad.models import Poliza
from contabilidad.signals import (
    get_cuenta,
    get_usuario_sistema,
    sincronizar_poliza_pago_airbnb,
)


class Command(BaseCommand):
    help = ("Reemite las pólizas de Airbnb que se generaron sin la línea de "
            "IVA trasladado: las cancela y las reexpide.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar', action='store_true',
            help="Escribe los cambios. Sin esta bandera solo reporta.",
        )

    def handle(self, *args, **opciones):
        cuenta_iva = get_cuenta('IVA_TRASLADADO')
        if cuenta_iva is None:
            self.stderr.write(self.style.ERROR(
                "No hay cuenta configurada para IVA_TRASLADADO: no se puede "
                "corregir nada sin saber dónde registrar el pasivo."))
            return

        pendientes = list(self._pendientes(cuenta_iva))
        if not pendientes:
            self.stdout.write(self.style.SUCCESS(
                "No hay pólizas de Airbnb sin IVA trasladado."))
            return

        total = sum((p.iva_trasladado for p, _ in pendientes), Decimal('0.00'))
        for pago, poliza in pendientes:
            self.stdout.write(
                f"  {poliza.fecha}  póliza {poliza.tipo}-{poliza.folio}  "
                f"{pago.codigo_confirmacion or f'pago #{pago.pk}'}  "
                f"IVA trasladado ${pago.iva_trasladado}")
        self.stdout.write(
            f"{len(pendientes)} pólizas · ${total} de IVA trasladado sin registrar.")

        if not opciones['aplicar']:
            self.stdout.write(self.style.WARNING(
                "Simulación: no se escribió nada. Repite con --aplicar."))
            return

        with transaction.atomic():
            for pago, poliza in pendientes:
                self._corregir(pago, poliza)

        self.stdout.write(self.style.SUCCESS(
            f"{len(pendientes)} pólizas reemitidas con el IVA trasladado."))

    @staticmethod
    def _pendientes(cuenta_iva):
        """
        Pagos cuya póliza vigente no registra el IVA trasladado que sí cobraron.

        Los ya corregidos quedan fuera solos: su póliza vigente es la nueva,
        que sí registra el IVA. El comando se puede correr dos veces.
        """
        pagos = (PagoAirbnb.objects
                 .filter(estado='PAGADO', iva_trasladado__gt=0)
                 .order_by('fecha_pago', 'pk'))

        for pago in pagos:
            poliza = (Poliza.objects
                      .filter(origen='PAGO_AIRBNB', object_id=pago.pk,
                              content_type__app_label='airbnb',
                              content_type__model='pagoairbnb')
                      .exclude(estado='CANCELADA')
                      .order_by('-pk')
                      .first())
            if poliza is None:
                continue
            if poliza.movimientos.filter(cuenta=cuenta_iva).exists():
                continue
            yield pago, poliza

    @staticmethod
    def _corregir(pago, poliza):
        poliza.cancelar(
            get_usuario_sistema(),
            "Cancelada y reexpedida: el asiento no registraba el IVA "
            "trasladado que Airbnb cobra al huésped y transfiere al "
            "anfitrión, y por eso no cuadraba.",
        )
        # La póliza nueva la arma el mismo signal que genera las de hoy, así
        # que el asiento corregido es exactamente el que emitiría el sistema.
        sincronizar_poliza_pago_airbnb(
            sender=PagoAirbnb, instance=pago, created=False)
