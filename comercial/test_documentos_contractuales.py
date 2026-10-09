"""Contratos con el sistema de documentos (Issue #373, fase 4).

Solo cambian los estilos: se generan los PDF reales y se revisa la identidad
(IBM Plex y Cormorant incrustadas, título en metadatos, pie con el número de
contrato) y que el texto contractual siga ahí.
"""
import io
from datetime import date, time
from decimal import Decimal

import pdfplumber
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, Cotizacion, ItemCotizacion
from comercial.services import ContratoService
from core_erp.test_utils import login_superuser_con_totp


def _leer(pdf):
    with pdfplumber.open(io.BytesIO(pdf)) as documento:
        # ☑/☐ (casillas del contrato PROFECO) no existen en IBM Plex: salen de la fuente del sistema.
        fuentes = {c['fontname'] for p in documento.pages for c in p.chars if c['text'] not in '☑☐'}
        texto = '\n'.join(p.extract_text() or '' for p in documento.pages)
        return documento.metadata, fuentes, texto


class ContratosDocumentoTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        cliente = Cliente.objects.create(nombre='Ana Ruiz Pech', tipo_persona='FISICA')
        cls.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Boda Ruiz', tipo_servicio='EVENTO',
            fecha_evento=date(2026, 12, 5), hora_inicio=time(11), hora_fin=time(19), num_personas=80,
        )
        ItemCotizacion.objects.create(
            cotizacion=cls.cot, descripcion='Servicio', cantidad=1, precio_unitario=Decimal('10000.00'))

    def _sin_fuentes_ajenas(self, fuentes):
        self.assertTrue(fuentes)
        ajenas = {f for f in fuentes if 'IBM-Plex-Sans' not in f and 'Cormorant' not in f}
        self.assertFalse(ajenas, ajenas)

    def test_contrato_propio(self):
        pdf, numero = ContratoService(self.cot).generar()
        metadatos, fuentes, texto = _leer(pdf)
        self.assertEqual(metadatos.get('Title'), f'Contrato {numero}')
        self._sin_fuentes_ajenas(fuentes)
        self.assertIn('Contrato de Prestación de Servicios', texto)
        self.assertIn('Décima cuarta. Jurisdicción', texto)
        self.assertIn(f"Quinta Ko'ox Tanil · Contrato {numero}", texto)
        self.assertNotIn('BORRADOR', texto)

    @override_settings(CONTRATO_PROPIO_ACTIVO=False)
    def test_contrato_profeco_toma_la_misma_identidad(self):
        pdf, numero = ContratoService(self.cot).generar()
        _, fuentes, texto = _leer(pdf)
        self._sin_fuentes_ajenas(fuentes)  # antes, Arial (Liberation Sans en el servidor)
        self.assertIn('9341-2023', texto)
        self.assertIn('Reglamento Interno', texto)
        self.assertIn(f"Quinta Ko'ox Tanil · Contrato {numero}", texto)

    def test_vista_previa_desde_el_admin(self):
        login_superuser_con_totp(self.client, self.usuario)
        r = self.client.get(reverse('cotizacion_contrato_vista_previa', args=[self.cot.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'QKT_Contrato_VP-COT-{self.cot.pk:03d}.pdf', r['Content-Disposition'])
        _, _, texto = _leer(r.content)
        self.assertIn('Vista previa para revisión.', texto)
