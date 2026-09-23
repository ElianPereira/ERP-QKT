"""
Confirmación automática al pagar el anticipo y candado de fecha ocupada.

Origen: la simulación del flujo completo (cotizador → portal → evento) mostró
que una cotización pagada desde el portal se quedaba en BORRADOR para siempre:
no apartaba la fecha —otro cliente podía pagar el mismo día—, no había contrato
ni guía y el cron nunca la ejecutaba ni la cerraba.

Ejecutar: python manage.py test comercial.test_confirmacion_por_pago --verbosity=2
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, ConstanteSistema, Cotizacion, ItemCotizacion, Pago


def _cotizacion(dias=60, fecha=None):
    cliente = Cliente.objects.create(nombre='Cliente Confirmación', telefono='5555550001')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento',
        fecha_evento=fecha or (timezone.localdate() + timedelta(days=dias)),
        incluye_refrescos=False,
    )
    ItemCotizacion.objects.create(
        cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=Decimal('10000.00'),
    )
    cot.refresh_from_db()
    return cot  # precio_final = 11,600.00


def _pagar(cot, monto, **extra):
    return Pago.objects.create(cotizacion=cot, monto=Decimal(monto), metodo='TRANSFERENCIA', **extra)


class ConfirmacionPorPagoTest(TestCase):

    def test_anticipo_del_50_confirma_la_cotizacion(self):
        cot = _cotizacion()
        _pagar(cot, '5800.00')
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CONFIRMADA')

    def test_abono_menor_al_anticipo_no_confirma(self):
        cot = _cotizacion()
        _pagar(cot, '1000.00')
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'BORRADOR')

    def test_abonos_que_suman_el_anticipo_confirman_al_llegar(self):
        cot = _cotizacion()
        _pagar(cot, '3000.00')
        _pagar(cot, '2800.00')
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CONFIRMADA')

    def test_porcentaje_configurado_manda_sobre_el_50(self):
        ConstanteSistema.objects.create(clave='PORCENTAJE_ANTICIPO_MINIMO', valor=Decimal('30'))
        cot = _cotizacion()
        _pagar(cot, '3480.00')  # 30%
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CONFIRMADA')

    def test_ingreso_extra_no_confirma(self):
        cot = _cotizacion()
        _pagar(cot, '9000.00', concepto='EXTRA')
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'BORRADOR')

    def test_desde_cotizada_tambien_confirma(self):
        cot = _cotizacion()
        Cotizacion.objects.filter(pk=cot.pk).update(estado='COTIZADA')
        cot.refresh_from_db()
        _pagar(cot, '11600.00')
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CONFIRMADA')

    def test_expirada_no_se_revive_con_un_pago_tardio(self):
        # Una ficha pagada tarde se registra (el dinero entró), pero revivir la
        # venta es decisión de alguien, no de la llegada del dinero.
        cot = _cotizacion()
        Cotizacion.objects.filter(pk=cot.pk).update(estado='EXPIRADA')
        cot.refresh_from_db()
        _pagar(cot, '5800.00')
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'EXPIRADA')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_fecha_ya_apartada_registra_el_pago_sin_confirmar_y_avisa(self):
        a = _cotizacion()
        _pagar(a, '5800.00')
        b = _cotizacion(fecha=a.fecha_evento)
        with self.captureOnCommitCallbacks(execute=True):
            pago = _pagar(b, '5800.00')
        b.refresh_from_db()
        self.assertEqual(b.estado, 'BORRADOR')
        self.assertTrue(Pago.objects.filter(pk=pago.pk).exists())
        self.assertTrue(any('fecha ocupada' in m.subject for m in mail.outbox))


class PortalFechaOcupadaTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_portal_no_ofrece_pago_si_otra_reserva_aparto_la_fecha(self):
        a = _cotizacion()
        _pagar(a, '5800.00')
        b = _cotizacion(fecha=a.fecha_evento)
        admite, motivo = b.admite_pago_detalle()
        self.assertFalse(admite)
        self.assertIn('ya no está disponible', motivo)
        # El motivo que ve el cliente no revela nada de la otra reservación.
        self.assertNotIn(str(a.pk), motivo)

    def test_la_cotizacion_confirmada_sigue_admitiendo_su_propio_saldo(self):
        a = _cotizacion()
        _pagar(a, '5800.00')
        a.refresh_from_db()
        self.assertTrue(a.admite_pago())

    @override_settings(OPENPAY_MERCHANT_ID='m', OPENPAY_PUBLIC_KEY='pk', OPENPAY_PRIVATE_KEY='sk')
    def test_checkout_rechaza_antes_de_cobrar(self):
        a = _cotizacion()
        _pagar(a, '5800.00')
        b = _cotizacion(fecha=a.fecha_evento)
        b.identificacion_oficial.name = 'cotizaciones/identificaciones/ine.jpg'
        b.save()
        with patch('comercial.services_openpay.requests.post') as post:
            r = self.client.post(f'/mi-evento/{b.portal.token}/pagar-openpay/', {
                'metodo': 'card', 'monto': '5800.00', 'token_id': 't',
                'device_session_id': 'd', 'acepta_legales': '1',
            })
        self.assertFalse(r.json()['ok'])
        post.assert_not_called()


class CronConfirmaPagadasTest(TestCase):

    def _pagada_sin_confirmar(self, **kw):
        cot = _cotizacion(**kw)
        _pagar(cot, '11600.00')
        # Estado heredado: pagada antes de que existiera la confirmación automática.
        Cotizacion.objects.filter(pk=cot.pk).update(estado='BORRADOR')
        return cot

    def test_confirma_las_pagadas_que_quedaron_en_borrador(self):
        cot = self._pagada_sin_confirmar()
        call_command('cerrar_cotizaciones', stdout=StringIO())
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CONFIRMADA')

    def test_evento_pasado_pagado_termina_cerrado(self):
        cot = self._pagada_sin_confirmar(dias=-2)
        call_command('cerrar_cotizaciones', stdout=StringIO())
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CERRADA')

    def test_simulacion_no_escribe(self):
        cot = self._pagada_sin_confirmar()
        out = StringIO()
        call_command('cerrar_cotizaciones', '--dry-run', stdout=out)
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'BORRADOR')
        self.assertIn('1 → CONFIRMADA', out.getvalue())

    def test_con_fecha_ocupada_avisa_y_no_confirma(self):
        a = _cotizacion()
        _pagar(a, '5800.00')
        b = self._pagada_sin_confirmar(fecha=a.fecha_evento)
        out = StringIO()
        call_command('cerrar_cotizaciones', stdout=out)
        b.refresh_from_db()
        self.assertEqual(b.estado, 'BORRADOR')
        self.assertIn('SIN APARTAR', out.getvalue())



class ConfirmarNoRecotizaTest(TestCase):
    """Confirmar por pago no debe recotizar la barra con los costos de hoy."""

    def test_guardar_solo_el_estado_no_recalcula_precios(self):
        cot = _cotizacion()
        with patch('comercial.services.actualizar_item_cotizacion') as recotizar:
            _pagar(cot, '5800.00')
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CONFIRMADA')
        recotizar.assert_not_called()

    def test_un_guardado_completo_si_recalcula(self):
        cot = _cotizacion()
        with patch('comercial.services.actualizar_item_cotizacion') as recotizar:
            cot.save()
        recotizar.assert_called_once()
