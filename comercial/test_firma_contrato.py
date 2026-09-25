"""
Firma electrónica del contrato desde el portal (Issue #318, fase 2).

Ejecutar: python manage.py test comercial.test_firma_contrato
"""
import base64
import io
import re
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import patch

import pypdfium2
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.test import TestCase
from django.utils import timezone
from PIL import Image, ImageDraw
from weasyprint import HTML

from comercial.models import Cliente, ContratoServicio, Cotizacion, FirmaContrato, PortalCliente
from comercial.services_firma import (
    MAX_INTENTOS,
    FirmaError,
    firmar,
    sha256,
    solicitar_codigo,
)


def _png(trazo=True):
    img = Image.new('RGBA', (300, 100), (0, 0, 0, 0))
    if trazo:
        ImageDraw.Draw(img).line((20, 60, 280, 40), fill=(0, 0, 0, 255), width=4)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _pdf(texto='Contrato de prueba'):
    return HTML(string=f'<p>{texto}</p>').write_pdf()


class FirmaBase(TestCase):
    def setUp(self):
        memoria = InMemoryStorage()
        self._pilas = ExitStack()
        for modelo, campo in (
            (ContratoServicio, 'archivo'),
            (FirmaContrato, 'imagen_firma'),
            (FirmaContrato, 'archivo_firmado'),
        ):
            self._pilas.enter_context(patch.object(modelo._meta.get_field(campo), 'storage', memoria))
        self.addCleanup(self._pilas.close)

        self.cliente = Cliente.objects.create(nombre='Ana Ruiz', tipo_persona='FISICA', email='ana@example.com')
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='Boda', tipo_servicio='EVENTO',
            fecha_evento=date.today() + timedelta(days=60), num_personas=80,
        )
        self.contrato = ContratoServicio(cotizacion=self.cot, numero='CONT-2026-0001', tipo_servicio='EVENTO')
        self.contrato.archivo.save('contrato.pdf', ContentFile(_pdf()), save=False)
        self.contrato.save()

    def _codigo(self):
        solicitar_codigo(self.contrato)
        return re.search(r'>(\d{6})<', mail.outbox[-1].alternatives[0][0]).group(1)

    def _firmar(self, codigo, **kwargs):
        datos = {
            'codigo': codigo, 'firma_data_url': _png(), 'nombre': 'Ana Ruiz',
            'ip': '200.1.2.3', 'user_agent': 'Pruebas/1.0',
        }
        datos.update(kwargs)
        return firmar(self.contrato, **datos)


class SolicitarCodigoTest(FirmaBase):
    def test_envia_codigo_y_no_lo_guarda_en_claro(self):
        codigo = self._codigo()
        firma = FirmaContrato.objects.get(contrato=self.contrato)
        self.assertNotEqual(firma.codigo_hash, codigo)
        self.assertEqual(firma.codigo_destino, 'ana@example.com')
        self.assertEqual(firma.hash_documento, sha256(self.contrato.archivo.open('rb').read()))
        # La bitácora de comunicaciones no guarda el código.
        from comunicacion.models import ComunicacionCliente
        comm = ComunicacionCliente.objects.get(cotizacion=self.cot, tipo='CONTRATO')
        self.assertNotIn(codigo, comm.cuerpo)

    def test_no_reenvia_antes_de_un_minuto(self):
        self._codigo()
        with self.assertRaisesMessage(FirmaError, 'Espera un minuto'):
            solicitar_codigo(self.contrato)

    def test_sin_correo_no_envia(self):
        Cliente.objects.filter(pk=self.cliente.pk).update(email='')
        self.contrato.cotizacion.cliente.refresh_from_db()
        with self.assertRaisesMessage(FirmaError, 'correo'):
            solicitar_codigo(self.contrato)


class FirmarTest(FirmaBase):
    def test_firma_valida_genera_pdf_con_constancia(self):
        codigo = self._codigo()
        with self.captureOnCommitCallbacks(execute=True):
            firma = self._firmar(codigo, acepta_publicidad=True)

        firma.refresh_from_db()
        self.assertTrue(firma.firmado)
        self.assertEqual(firma.codigo_hash, '')  # un solo uso
        self.assertTrue(firma.acepta_publicidad)
        pdf = firma.archivo_firmado.open('rb').read()
        self.assertEqual(firma.hash_firmado, sha256(pdf))
        # Original sin tocar + una hoja de constancia.
        self.assertEqual(len(pypdfium2.PdfDocument(pdf)), 2)
        # Copia al cliente con el PDF firmado.
        copia = mail.outbox[-1]
        self.assertIn('firmado', copia.subject)
        self.assertEqual(copia.attachments[0][2], 'application/pdf')

    def test_codigo_incorrecto_cuenta_intentos_y_bloquea(self):
        self._codigo()
        for _ in range(MAX_INTENTOS):
            with self.assertRaisesMessage(FirmaError, 'no es correcto'):
                self._firmar('000000')
        self.assertEqual(FirmaContrato.objects.get().intentos, MAX_INTENTOS)
        with self.assertRaisesMessage(FirmaError, 'Demasiados intentos'):
            self._firmar('000000')

    def test_codigo_vencido(self):
        codigo = self._codigo()
        with self.assertRaisesMessage(FirmaError, 'venció'):
            self._firmar(codigo, ahora=timezone.now() + timedelta(minutes=11))

    def test_contrato_regenerado_invalida_la_firma(self):
        codigo = self._codigo()
        self.contrato.archivo.save('contrato2.pdf', ContentFile(_pdf('Otro texto')), save=True)
        with self.assertRaisesMessage(FirmaError, 'cambió'):
            self._firmar(codigo)

    def test_rechaza_lienzo_vacio_y_no_png(self):
        codigo = self._codigo()
        with self.assertRaisesMessage(FirmaError, 'Traza tu firma'):
            self._firmar(codigo, firma_data_url=_png(trazo=False))
        with self.assertRaisesMessage(FirmaError, 'no es válida'):
            self._firmar(codigo, firma_data_url='data:image/png;base64,' + base64.b64encode(b'no soy png').decode())

    def test_no_se_firma_dos_veces(self):
        codigo = self._codigo()
        self._firmar(codigo)
        with self.assertRaisesMessage(FirmaError, 'ya está firmado'):
            self._firmar(codigo)


class PortalFirmaTest(FirmaBase):
    def setUp(self):
        super().setUp()
        self.portal = PortalCliente.objects.get_or_create(cotizacion=self.cot)[0]
        self.url = f'/mi-evento/{self.portal.token}/contrato/firmar/'

    def test_flujo_completo_desde_el_portal(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.post(self.url, {'accion': 'codigo'})
        codigo = re.search(r'>(\d{6})<', mail.outbox[-1].alternatives[0][0]).group(1)

        respuesta = self.client.post(self.url, {
            'accion': 'firmar', 'codigo': codigo, 'firma': _png(),
            'nombre': 'Ana Ruiz', 'acepto': 'si',
        })
        self.assertEqual(respuesta.status_code, 302)
        firma = FirmaContrato.objects.get()
        self.assertTrue(firma.firmado)

        # La descarga del portal ya entrega la versión firmada.
        pdf = b''.join(self.client.get(f'/mi-evento/{self.portal.token}/contrato.pdf').streaming_content)
        self.assertEqual(sha256(pdf), firma.hash_firmado)
        self.assertContains(self.client.get(self.url), 'Contrato firmado')

    def test_sin_aceptar_no_firma(self):
        self.client.post(self.url, {'accion': 'codigo'})
        respuesta = self.client.post(self.url, {'accion': 'firmar', 'codigo': '123456', 'firma': _png(), 'nombre': 'Ana'})
        self.assertContains(respuesta, 'Confirma que leíste')
        self.assertFalse(FirmaContrato.objects.get().firmado)

    def test_token_invalido_da_404(self):
        self.assertEqual(self.client.get('/mi-evento/no-existe/contrato/firmar/').status_code, 404)
