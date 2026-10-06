"""
Emisión automática del contrato al confirmarse la cotización.

Ejecutar: python manage.py test comercial.test_contrato_automatico
"""
import datetime
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, ContratoServicio, Cotizacion
from comunicacion.models import ComunicacionCliente

ALMACEN = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'privado': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(
    STORAGES=ALMACEN,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    COMUNICACION_SIGNALS_ENABLED=False,
)
class ContratoAutomaticoTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Ana Ruiz', email='ana@ejemplo.com')
        self.manana = timezone.localdate() + datetime.timedelta(days=1)

    def _cot(self, **kwargs):
        datos = {
            'cliente': self.cliente, 'nombre_evento': 'Pasadía — Ana Ruiz',
            'tipo_servicio': 'PASADIA', 'fecha_evento': self.manana,
            'hora_inicio': datetime.time(11), 'hora_fin': datetime.time(19), 'num_personas': 20,
        }
        datos.update(kwargs)
        return Cotizacion.objects.create(**datos)

    def _confirmar(self, cot):
        with patch('weasyprint.HTML') as html, \
             patch('comercial.services_deposito.asegurar_deposito'), \
             self.captureOnCommitCallbacks(execute=True):
            html.return_value.write_pdf.return_value = b'%PDF-1.4 simulado'
            cot.estado = 'CONFIRMADA'
            cot.save()

    def test_al_confirmarse_emite_el_contrato_y_avisa_al_cliente(self):
        cot = self._cot()
        self._confirmar(cot)

        contrato = ContratoServicio.objects.get(cotizacion=cot)
        self.assertEqual(contrato.tipo_servicio, 'PASADIA')
        self.assertIsNone(contrato.generado_por)
        cot.refresh_from_db()
        self.assertTrue(cot.archivo_contrato.name)
        aviso = ComunicacionCliente.objects.get(tipo='CONTRATO', cotizacion=cot)
        self.assertIn('listo para firmar', aviso.asunto)
        self.assertEqual([m.to for m in mail.outbox], [['ana@ejemplo.com']])

    def test_no_duplica_si_ya_tiene_contrato(self):
        cot = self._cot()
        self._confirmar(cot)
        self._confirmar(cot)
        self.assertEqual(ContratoServicio.objects.filter(cotizacion=cot).count(), 1)

    def test_no_emite_para_una_reservacion_con_fecha_pasada(self):
        cot = self._cot(fecha_evento=timezone.localdate() - datetime.timedelta(days=1))
        self._confirmar(cot)
        self.assertFalse(ContratoServicio.objects.filter(cotizacion=cot).exists())

    def test_no_emite_si_no_esta_confirmada(self):
        cot = self._cot()
        with self.captureOnCommitCallbacks(execute=True):
            cot.estado = 'COTIZADA'
            cot.save()
        self.assertFalse(ContratoServicio.objects.exists())

    @override_settings(CONTRATO_AUTOMATICO=False)
    def test_se_puede_apagar_por_variable(self):
        cot = self._cot()
        self._confirmar(cot)
        self.assertFalse(ContratoServicio.objects.exists())

    def test_si_falla_la_generacion_avisa_al_equipo_y_no_rompe_la_confirmacion(self):
        cot = self._cot()
        with patch('comercial.services.emitir_contrato', side_effect=RuntimeError('WeasyPrint caído')), \
             self.assertLogs('comercial.signals', level='ERROR'), \
             self.captureOnCommitCallbacks(execute=True):
            cot.estado = 'CONFIRMADA'
            cot.save()

        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CONFIRMADA')
        self.assertFalse(ContratoServicio.objects.exists())
        alerta = ComunicacionCliente.objects.get(tipo='OTRO', cotizacion=cot)
        self.assertIn('No se pudo generar el contrato', alerta.asunto)

