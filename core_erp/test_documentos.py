"""Sistema de documentos QKT (Issue #373): servicio, filtros y guardián de plantillas."""
import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber
from django.conf import settings
from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core_erp.documentos import cifra, moneda, nombre_archivo, render_pdf
from core_erp.test_utils import login_superuser_con_totp

# Plantillas ya migradas al sistema de documentos. Cada fase del Issue #373
# agrega aquí las suyas; al final de la migración, toda plantilla PDF del ERP.
PLANTILLAS_MIGRADAS = sorted(
    (Path(settings.BASE_DIR) / 'reportes' / 'templates' / 'reportes').glob('pdf_*.html')
)


class FormatoCifrasTest(SimpleTestCase):

    def test_moneda(self):
        self.assertEqual(moneda(Decimal('1234.5')), '$1,234.50')
        self.assertEqual(moneda(Decimal('-1234.5')), '-$1,234.50')
        self.assertEqual(moneda(0), '$0.00')

    def test_moneda_redondea_half_up(self):
        self.assertEqual(moneda(Decimal('0.125')), '$0.13')
        self.assertEqual(moneda('2.675'), '$2.68')

    def test_vacio_si_no_es_numero(self):
        self.assertEqual(moneda(None), '')
        self.assertEqual(moneda('abc'), '')
        self.assertEqual(cifra(''), '')

    def test_cifra_sin_signo_de_pesos(self):
        self.assertEqual(cifra(Decimal('98765.4')), '98,765.40')

    def test_filtros_y_badge_en_plantilla(self):
        html = Template(
            '{% load qkt_documentos %}{{ x|moneda }} {% badge_doc "Vencido" "error" %}'
            ' {% badge_doc "Otro" "morado" %} {{ n|tono_signo }}'
        ).render(Context({'x': Decimal('10'), 'n': Decimal('-1')}))
        self.assertIn('$10.00', html)
        self.assertIn('doc-badge--error', html)
        self.assertIn('doc-badge--neutro', html)
        self.assertTrue(html.strip().endswith('error'))


class NombreArchivoTest(SimpleTestCase):

    def test_convencion(self):
        self.assertEqual(
            nombre_archivo('Balanza', date(2026, 1, 1), date(2026, 9, 30)),
            'QKT_Balanza_20260101_20260930.pdf',
        )

    def test_quita_acentos_y_espacios_y_omite_vacios(self):
        self.assertEqual(
            nombre_archivo('Libro mayor', '102.01', None, 'Ka\'an Room'),
            'QKT_Libro-mayor_102-01_Ka-an-Room.pdf',
        )


class RenderPdfTest(SimpleTestCase):

    def test_pdf_con_fuente_incrustada_y_metadatos(self):
        pdf = render_pdf('reportes/pdf_facturas.html', {
            'titulo': 'Facturas emitidas',
            'fecha_inicio': date(2026, 1, 1), 'fecha_fin': date(2026, 1, 31),
            'facturas': [], 'count': 0, 'total_monto': Decimal('0'),
        })
        self.assertTrue(pdf.startswith(b'%PDF'))
        with pdfplumber.open(io.BytesIO(pdf)) as documento:
            self.assertEqual(documento.metadata.get('Title'), 'Facturas emitidas')
            self.assertEqual(documento.metadata.get('Author'), "Quinta Ko'ox Tanil")
            pagina = documento.pages[0]
            fuentes = {c['fontname'] for c in pagina.chars}
            self.assertTrue(fuentes and all('IBM-Plex-Sans' in f for f in fuentes), fuentes)
            texto = pagina.extract_text()
        self.assertIn('Generado:', texto)
        self.assertIn('Página 1 de 1', texto)


class PlantillasMigradasTest(SimpleTestCase):
    """Guardián: lo migrado no vuelve a los estilos sueltos."""

    def test_hay_plantillas_que_vigilar(self):
        self.assertGreaterEqual(len(PLANTILLAS_MIGRADAS), 8)

    def test_extienden_la_base_y_usan_los_componentes(self):
        for ruta in PLANTILLAS_MIGRADAS:
            with self.subTest(plantilla=ruta.name):
                texto = ruta.read_text(encoding='utf-8')
                self.assertTrue(texto.startswith('{% extends "documentos/_base.html" %}'))
                self.assertNotIn('style=', texto)
                self.assertNotIn('<style', texto)
                self.assertNotIn('intcomma', texto, 'Montos con |moneda o |cifra')
                self.assertIsNone(re.search(r'#[0-9A-Fa-f]{6}\b', texto), 'Colores con tokens')


class ReportesGeneranPdfTest(TestCase):
    """Cada reporte del centro de reportes sale como PDF con el sistema nuevo."""

    def setUp(self):
        self.usuario = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        login_superuser_con_totp(self.client, self.usuario)

    def _get(self, nombre, **params):
        return self.client.get(reverse(f'reportes:{nombre}'), params)

    def test_todos_responden_pdf(self):
        from contabilidad.models import CuentaContable
        cuenta = CuentaContable.objects.filter(permite_movimientos=True).first()
        padre = CuentaContable.objects.filter(permite_movimientos=False).first()
        casos = [
            ('balanza', {}), ('estado_resultados', {}), ('balance_general', {}),
            ('cxc', {}), ('cotizaciones', {}), ('facturas', {}),
            ('libro_mayor', {'cuenta_id': cuenta.pk}),
            ('auxiliar', {'cuenta_padre_id': padre.pk}),
        ]
        for nombre, params in casos:
            with self.subTest(reporte=nombre):
                respuesta = self._get(nombre, **params)
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta['Content-Type'], 'application/pdf')
                self.assertRegex(respuesta['Content-Disposition'], r'filename="QKT_[^"]+\.pdf"')
                self.assertTrue(respuesta.content.startswith(b'%PDF'))
