"""Herramienta `mi_reservacion` del agente de WhatsApp (Issue #366, fase A)."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion import herramientas_agente

from .utils import TEL_CLIENTE

OTRO_TEL = '525555550009'


class MiReservacionTest(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.cliente = Cliente.objects.create(nombre='Ana', telefono=TEL_CLIENTE[-10:])

    def _cot(self, cliente=None, estado='COTIZADA', dias=30, precio='10000.00'):
        cot = Cotizacion.objects.create(
            cliente=cliente or self.cliente, tipo_servicio='EVENTO', nombre_evento='Boda',
            fecha_evento=self.hoy + timedelta(days=dias), num_personas=50)
        Cotizacion.objects.filter(pk=cot.pk).update(estado=estado, precio_final=Decimal(precio))
        cot.refresh_from_db()
        return cot

    def _ejecutar(self, telefono, entrada=None):
        conv = SimpleNamespace(telefono=telefono)
        salida, error = herramientas_agente.ejecutar('mi_reservacion', entrada or {}, conv=conv)
        return json.loads(salida), error

    def test_el_saldo_es_el_mismo_del_portal(self):
        cot = self._cot()
        r, error = self._ejecutar(TEL_CLIENTE)
        self.assertFalse(error)
        reserva = r['reservaciones'][0]
        self.assertEqual(reserva['folio'], f'COT-{cot.id:03d}')
        self.assertEqual(reserva['saldo'], f'${cot.saldo_pendiente():,.2f}')
        self.assertEqual(reserva['minimo_a_pagar_hoy'], f'${cot.monto_minimo_pago():,.2f}')
        self.assertEqual(reserva['fecha_limite_para_liquidar'],
                         (cot.fecha_evento - timedelta(days=Cotizacion.DIAS_PAGO_TOTAL['EVENTO'])).isoformat())
        self.assertFalse(reserva['fecha_apartada'])
        self.assertIn(herramientas_agente.URL_PORTAL_ACCESO, r['para_pagar_o_ver_contrato'])

    def test_un_numero_sin_cliente_no_recibe_nada(self):
        self._cot()
        r, _ = self._ejecutar(OTRO_TEL)
        self.assertEqual(r['reservaciones'], [])

    def test_el_modelo_no_puede_pedir_las_de_otro_numero(self):
        self._cot()
        r, error = self._ejecutar(OTRO_TEL, {'telefono': TEL_CLIENTE})
        self.assertTrue(error)
        self.assertNotIn('reservaciones', r)

    def test_sin_telefono_no_busca(self):
        self._cot()
        self.assertEqual(self._ejecutar('')[0]['reservaciones'], [])

    def test_solo_las_vigentes_y_de_ese_numero(self):
        vigente = self._cot()
        self._cot(estado='CANCELADA')
        self._cot(estado='EXPIRADA')
        self._cot(dias=-10)
        otro = Cliente.objects.create(nombre='Luis', telefono=OTRO_TEL[-10:])
        self._cot(cliente=otro)
        folios = [x['folio'] for x in self._ejecutar(TEL_CLIENTE)[0]['reservaciones']]
        self.assertEqual(folios, [f'COT-{vigente.id:03d}'])

    def test_dos_clientes_con_el_mismo_numero_ven_las_de_ambos(self):
        self._cot()
        duplicado = Cliente.objects.create(nombre='Ana R', telefono=TEL_CLIENTE)
        self._cot(cliente=duplicado, dias=40)
        self.assertEqual(len(self._ejecutar(TEL_CLIENTE)[0]['reservaciones']), 2)

    def test_pagada_no_lleva_minimo(self):
        self._cot(estado='CONFIRMADA', precio='0.00')
        reserva = self._ejecutar(TEL_CLIENTE)[0]['reservaciones'][0]
        self.assertTrue(reserva['fecha_apartada'])
        self.assertNotIn('minimo_a_pagar_hoy', reserva)
