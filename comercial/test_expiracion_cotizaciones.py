"""
Tests de la expiración automática de cotizaciones que nunca prosperaron.

Cubre las dos mitades del contrato: qué expira (`motivo_expiracion()`, la
fuente única de la regla) y qué hace el cron con eso — incluida la razón por la
que el paso de expiración va ANTES del de "evento ejecutado": sin ese orden,
una cotización que nadie pagó terminaba contada como venta real.

Ejecutar: python manage.py test comercial.test_expiracion_cotizaciones --verbosity=2
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago


def _cotizacion(*, estado='COTIZADA', dias_para_evento=120, dias_de_antiguedad=0,
                precio=Decimal('10000.00')):
    cliente = Cliente.objects.create(
        nombre='Cliente Expiración', tipo_persona='FISICA', telefono='9991234567')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento sin pagar',
        tipo_servicio='EVENTO',
        fecha_evento=timezone.localdate() + timedelta(days=dias_para_evento),
        incluye_refrescos=False,
    )
    ItemCotizacion.objects.create(
        cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=precio)
    # `created_at` es auto_now_add: no se puede fijar en el create(), hay que
    # reescribirlo con un update() que se salta save(). Igual que `estado`, que
    # tiene su propia máquina de transiciones.
    Cotizacion.objects.filter(pk=cot.pk).update(
        estado=estado,
        created_at=timezone.now() - timedelta(days=dias_de_antiguedad),
    )
    return Cotizacion.objects.get(pk=cot.pk)


def _pagar(cotizacion, monto=Decimal('100.00')):
    Pago.objects.create(
        cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
        monto=monto, metodo='EFECTIVO',
    )
    return Cotizacion.objects.get(pk=cotizacion.pk)


class MotivoExpiracionTest(TestCase):
    """La regla en sí, sin pasar por el comando."""

    def test_una_cotizacion_reciente_sin_pago_no_expira(self):
        cot = _cotizacion(dias_de_antiguedad=3)
        self.assertIsNone(cot.motivo_expiracion())

    def test_expira_al_cumplir_el_umbral_de_dias(self):
        cot = _cotizacion(dias_de_antiguedad=Cotizacion.DIAS_EXPIRACION_SIN_PAGO)
        self.assertIn('sin ningún pago', cot.motivo_expiracion())

    def test_el_dia_anterior_al_umbral_todavia_no_expira(self):
        # El límite es inclusivo: se prueba el borde, no solo un valor cómodo.
        cot = _cotizacion(dias_de_antiguedad=Cotizacion.DIAS_EXPIRACION_SIN_PAGO - 1)
        self.assertIsNone(cot.motivo_expiracion())

    def test_expira_si_la_fecha_del_evento_ya_paso_aunque_sea_reciente(self):
        cot = _cotizacion(dias_para_evento=-1, dias_de_antiguedad=1)
        self.assertEqual(cot.motivo_expiracion(), 'fecha del evento ya pasada sin ningún pago')

    def test_un_abono_parcial_la_salva_de_expirar(self):
        # El corazón de la regla: donde hubo dinero, decide una persona.
        cot = _pagar(_cotizacion(dias_de_antiguedad=90), Decimal('1.00'))
        self.assertIsNone(cot.motivo_expiracion())

    def test_una_confirmada_nunca_expira(self):
        cot = _cotizacion(estado='CONFIRMADA', dias_de_antiguedad=365)
        self.assertIsNone(cot.motivo_expiracion())

    def test_una_cancelada_no_se_toca(self):
        cot = _cotizacion(estado='CANCELADA', dias_de_antiguedad=365)
        self.assertIsNone(cot.motivo_expiracion())

    def test_un_borrador_viejo_sin_pago_tambien_expira(self):
        cot = _cotizacion(estado='BORRADOR', dias_de_antiguedad=60)
        self.assertIsNotNone(cot.motivo_expiracion())


class CronExpiraCotizacionesTest(TestCase):
    """El comando `cerrar_cotizaciones` completo."""

    def _correr(self, *args):
        salida = StringIO()
        call_command('cerrar_cotizaciones', *args, stdout=salida)
        return salida.getvalue()

    def test_el_cron_marca_expirada_la_vieja_sin_pago(self):
        cot = _cotizacion(dias_de_antiguedad=30)
        self._correr()
        self.assertEqual(Cotizacion.objects.get(pk=cot.pk).estado, 'EXPIRADA')

    def test_una_fecha_pasada_sin_pago_ya_no_se_cuenta_como_evento_ejecutado(self):
        # La regresión que motivó todo esto: antes terminaba en EJECUTADA y
        # entraba en views.ESTADOS_VENTA_REAL, inflando el reporte de ventas.
        cot = _cotizacion(dias_para_evento=-5, dias_de_antiguedad=40)
        self._correr()
        self.assertEqual(Cotizacion.objects.get(pk=cot.pk).estado, 'EXPIRADA')

    def test_una_fecha_pasada_CON_pago_sigue_yendo_a_ejecutada(self):
        # No se cambia el comportamiento donde sí hubo dinero de por medio.
        cot = _pagar(_cotizacion(dias_para_evento=-5, dias_de_antiguedad=40))
        self._correr()
        self.assertEqual(Cotizacion.objects.get(pk=cot.pk).estado, 'EJECUTADA')

    def test_una_confirmada_con_fecha_pasada_sigue_su_curso_normal(self):
        cot = _pagar(_cotizacion(estado='CONFIRMADA', dias_para_evento=-3))
        self._correr()
        self.assertEqual(Cotizacion.objects.get(pk=cot.pk).estado, 'EJECUTADA')

    def test_dry_run_no_escribe_nada(self):
        cot = _cotizacion(dias_de_antiguedad=30)
        salida = self._correr('--dry-run')
        self.assertIn('EXPIRADA', salida)
        self.assertEqual(Cotizacion.objects.get(pk=cot.pk).estado, 'COTIZADA')

    def test_correr_dos_veces_no_cambia_nada_la_segunda(self):
        # Idempotencia: EXPIRADA ya no está entre los estados candidatos.
        cot = _cotizacion(dias_de_antiguedad=30)
        self._correr()
        salida = self._correr()
        self.assertEqual(Cotizacion.objects.get(pk=cot.pk).estado, 'EXPIRADA')
        self.assertIn('0 → EXPIRADA', salida)


class TransicionesExpiradaTest(TestCase):
    """EXPIRADA dentro de la máquina de estados."""

    def test_se_puede_revivir_a_borrador_si_el_cliente_reaparece(self):
        cot = _cotizacion(dias_de_antiguedad=30)
        self._expirar(cot)
        ok, _ = Cotizacion.objects.get(pk=cot.pk).cambiar_estado('BORRADOR')
        self.assertTrue(ok)

    def test_no_se_puede_saltar_de_expirada_a_confirmada(self):
        # Revivir obliga a pasar por BORRADOR y recotizar: los precios de hace
        # meses no deberían confirmarse tal cual sin que nadie los revise.
        cot = _cotizacion(dias_de_antiguedad=30)
        self._expirar(cot)
        ok, mensaje = Cotizacion.objects.get(pk=cot.pk).cambiar_estado('CONFIRMADA')
        self.assertFalse(ok)
        self.assertIn('No se puede cambiar', mensaje)

    def _expirar(self, cot):
        call_command('cerrar_cotizaciones', stdout=StringIO())
        self.assertEqual(Cotizacion.objects.get(pk=cot.pk).estado, 'EXPIRADA')
