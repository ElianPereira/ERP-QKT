"""Test end-to-end del webhook de ManyChat con validación de fecha apartada."""
import json
from datetime import date, timedelta
from unittest.mock import patch

from django.core.files.storage import InMemoryStorage
from django.test import TestCase, override_settings, Client

from comercial.models import Cliente, Cotizacion


@override_settings(
    MANYCHAT_WEBHOOK_TOKEN='test-token',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='test@qkt.mx',
    DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage',
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class WebhookManyChatDisponibilidadTest(TestCase):
    def setUp(self):
        # Reemplaza el storage de Cloudinary por uno en memoria
        self._storage_patcher = patch.object(
            Cotizacion._meta.get_field('archivo_pdf'),
            'storage',
            InMemoryStorage(),
        )
        self._storage_patcher.start()
        self.addCleanup(self._storage_patcher.stop)

        self.client = Client()
        self.fecha_ocupada = date.today() + timedelta(days=120)
        self.fecha_libre = date.today() + timedelta(days=200)

        # Crear cotización ya apartada (CONFIRMADA) en la fecha conflictiva
        cliente_otro = Cliente.objects.create(
            nombre='OTRO CLIENTE', telefono='9990000001', tipo_persona='FISICA'
        )
        cot = Cotizacion.objects.create(
            cliente=cliente_otro,
            nombre_evento='Boda Existente',
            fecha_evento=self.fecha_ocupada,
            num_personas=100,
            incluye_refrescos=False,
            incluye_cerveza=False,
            incluye_licor_nacional=False,
            incluye_licor_premium=False,
            incluye_cocteleria_basica=False,
            incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')

    def _post_webhook(self, fecha):
        payload = {
            'telefono_cliente': '9999999999',
            'nombre_cliente': 'PRUEBA WEBHOOK',
            'tipo_servicio': 'Evento',
            'tipo_evento': 'Cumpleaños',
            'fecha_tentativa': fecha.strftime('%d/%m/%Y'),
            'num_personas': '120',
            'hora_inicio': '18:00',
            'hora_fin': '23:00',
        }
        return self.client.post(
            '/api/webhook-manychat/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_WEBHOOK_TOKEN='test-token',
        )

    def test_webhook_fecha_ocupada_responde_aviso(self):
        resp = self._post_webhook(self.fecha_ocupada)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        print("\n=== RESPUESTA WEBHOOK (fecha OCUPADA) ===")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        self.assertEqual(data['status'], 'success')
        self.assertIn('fecha_disponible', data)
        self.assertIn('aviso_fecha', data)
        self.assertFalse(data['fecha_disponible'])
        self.assertIsNotNone(data['aviso_fecha'])
        self.assertIn('apartado', data['aviso_fecha'].lower())

    def test_webhook_fecha_libre_responde_disponible(self):
        resp = self._post_webhook(self.fecha_libre)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        print("\n=== RESPUESTA WEBHOOK (fecha LIBRE) ===")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        self.assertEqual(data['status'], 'success')
        self.assertTrue(data['fecha_disponible'])
        self.assertIsNone(data['aviso_fecha'])
