"""Documentos al cliente con el sistema de documentos (Issue #373, fase 2).

Se generan por sus vistas reales (admin y portal) y se lee el texto del PDF:
no basta con que salga un PDF, tienen que decir lo correcto. En particular,
el plan de pagos debe prometer la tabla de cancelación vigente
(`reglas_contrato.TABLA_CANCELACION`), no la que traía escrita a mano.
"""
import io
from datetime import timedelta
from decimal import Decimal

import pdfplumber
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago, PortalCliente
from core_erp.test_utils import login_superuser_con_totp


def _texto(respuesta):
    with pdfplumber.open(io.BytesIO(respuesta.content)) as pdf:
        return '\n'.join(p.extract_text() or '' for p in pdf.pages)


class DocumentosClienteTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        cls.cliente = Cliente.objects.create(
            nombre='Ana Pech Canul', telefono='5555550001', email='ana@example.com')
        cls.cot = cls._cotizacion('EVENTO', 'Boda Pech', dias=90)
        Pago.objects.create(cotizacion=cls.cot, monto=Decimal('5800.00'), metodo='TRANSFERENCIA')

    @classmethod
    def _cotizacion(cls, tipo, nombre, dias):
        fecha = timezone.localdate() + timedelta(days=dias)
        cot = Cotizacion.objects.create(
            cliente=cls.cliente, nombre_evento=nombre, tipo_servicio=tipo,
            fecha_evento=fecha, incluye_refrescos=False,
            fecha_salida=fecha + timedelta(days=2) if tipo == 'HOSPEDAJE' else None,
        )
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=Decimal('10000.00'))
        cot.refresh_from_db()
        return cot

    def setUp(self):
        login_superuser_con_totp(self.client, self.usuario)

    def _plan(self, cot):
        from comercial.services import PlanPagosService
        return PlanPagosService(cot).generar(usuario=self.usuario, num_parcialidades=2)

    # ----- Cotización -----

    def test_cotizacion_desde_el_admin(self):
        r = self.client.get(reverse('cotizacion_pdf', args=[self.cot.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'QKT_Cotizacion_COT-{self.cot.pk:03d}.pdf', r['Content-Disposition'])
        texto = _texto(r)
        self.assertIn('Cotización de Evento', texto)
        self.assertIn('Ana Pech Canul', texto)
        self.assertIn('Precio total $11,600.00', texto)
        self.assertIn('Abonado -$5,800.00', texto)
        self.assertIn('Pendiente $5,800.00', texto)
        self.assertIn('999 169 9191', texto)
        self.assertNotIn('direccion', texto)  # el usuario interno no se le muestra al cliente

    def test_cotizacion_desde_el_portal(self):
        portal = PortalCliente.objects.get(cotizacion=self.cot)
        r = self.client.get(reverse('portal_descargar_cotizacion', args=[portal.token]), secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('Precio total $11,600.00', _texto(r))

    # ----- Plan de pagos -----

    def test_plan_de_pagos_usa_la_tabla_de_cancelacion_vigente(self):
        self._plan(self.cot)
        r = self.client.get(reverse('plan_pagos_pdf', args=[self.cot.pk]))
        self.assertEqual(r.status_code, 200)
        texto = _texto(r)
        self.assertIn('Plan de pagos', texto)
        self.assertIn('Más de 60 días naturales antes 10% 90%', texto)
        self.assertIn('15 días naturales o menos 100% 0%', texto)
        self.assertNotIn('+90', texto)                # la tabla vieja, contraria a la Política
        self.assertNotIn('menos de 30 días', texto)
        self.assertNotIn('Mérida', texto)             # jurisdicción contraria a los TyC
        self.assertIn('15 días naturales antes de la fecha del servicio', texto)
        fecha_total = (self.cot.fecha_evento - timedelta(days=15)).strftime('%d/%m/%Y')
        self.assertIn(fecha_total, texto)

    def test_plan_de_pagos_de_hospedaje_usa_su_tabla_y_sus_dias(self):
        cot = self._cotizacion('HOSPEDAJE', 'Estancia Ka\'an', dias=40)
        self._plan(cot)
        texto = _texto(self.client.get(reverse('plan_pagos_pdf', args=[cot.pk])))
        self.assertIn('Menos de 7 días naturales o no presentación 100% 0%', texto)
        self.assertIn('7 días naturales antes de la fecha del servicio', texto)
        self.assertNotIn('Cambio de fecha', texto)

    # ----- Solicitud de factura -----

    def test_solicitud_de_factura(self):
        from facturacion.models import SolicitudFactura
        from facturacion.services import generar_pdf_solicitud
        solicitud = SolicitudFactura.objects.get(cotizacion=self.cot)
        with pdfplumber.open(io.BytesIO(generar_pdf_solicitud(solicitud))) as pdf:
            texto = pdf.pages[0].extract_text()
        self.assertIn('Solicitud de factura', texto)
        self.assertIn('PECE010202IA0', texto)       # RFC emisor de la Quinta
        self.assertIn('XAXX010101000', texto)       # público en general
        self.assertIn('Total factura $5,800.00', texto)
