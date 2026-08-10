"""Tests del comando consolidado de recordatorios y de su shim deprecado."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ParcialidadPago, PlanPago
from comunicacion.models import ComunicacionCliente

from .utils import TEL_CLIENTE, TEL_EMISOR, RespuestaFalsa, limpiar_cache_emisor, wa_settings


@wa_settings()
class EnviarRecordatoriosTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.hoy = timezone.localdate()
        self.cliente = Cliente.objects.create(
            nombre='Ana Ruiz', email='ana@example.com', telefono=TEL_CLIENTE,
        )
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Boda Test',
            fecha_evento=self.hoy + timedelta(days=90),
            num_personas=100,
            precio_final=Decimal('40000.00'),
        )
        Cotizacion.objects.filter(pk=self.cot.pk).update(precio_final=Decimal('40000.00'))
        self.cot.refresh_from_db()
        self.plan = PlanPago.objects.create(cotizacion=self.cot, activo=True)

    def _parcialidad(self, dias, numero=1, pagada=False):
        return ParcialidadPago.objects.create(
            plan=self.plan, numero=numero, concepto=f'Parcialidad {numero}',
            monto=Decimal('10000.00'), porcentaje=Decimal('25.00'),
            fecha_limite=self.hoy + timedelta(days=dias), pagada=pagada,
        )

    def _correr(self, **kwargs):
        salida = StringIO()
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            call_command('enviar_recordatorios', stdout=salida, stderr=StringIO(), **kwargs)
        return salida.getvalue(), post

    def test_avisa_a_los_tres_dias_antes(self):
        self._parcialidad(3)
        self._correr()
        comms = ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO')
        self.assertEqual(comms.filter(canal='EMAIL').count(), 1)
        self.assertEqual(comms.filter(canal='WHATSAPP').count(), 1)

    def test_avisa_el_dia_del_vencimiento(self):
        self._parcialidad(0)
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO').count(), 2)

    def test_avisa_un_dia_despues(self):
        self._parcialidad(-1)
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO').count(), 2)

    def test_no_avisa_en_dias_fuera_del_calendario(self):
        self._parcialidad(7)
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO').count(), 0)

    def test_parcialidad_pagada_no_recibe_recordatorio(self):
        self._parcialidad(3, pagada=True)
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO').count(), 0)

    def test_plan_inactivo_no_recibe_recordatorio(self):
        self._parcialidad(3)
        self.plan.activo = False
        self.plan.save()
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO').count(), 0)

    def test_correr_dos_veces_el_mismo_dia_no_duplica(self):
        self._parcialidad(3)
        self._correr()
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO').count(), 2)

    def test_el_mensaje_lleva_monto_fecha_limite_saldo_y_portal(self):
        parc = self._parcialidad(3)
        _, post = self._correr()
        parametros = [
            p['text']
            for p in post.call_args.kwargs['json']['template']['components'][0]['parameters']
        ]
        self.assertEqual(parametros[1], '10,000.00')
        self.assertEqual(parametros[2], parc.fecha_limite.strftime('%d/%m/%Y'))
        self.assertEqual(parametros[3], '40,000.00')
        self.assertTrue(parametros[4].startswith('https://portal.test/mi-evento/'))

    def test_dry_run_no_envia_ni_registra(self):
        self._parcialidad(3)
        mail.outbox = []
        salida, post = self._correr(dry_run=True)
        post.assert_not_called()
        self.assertEqual(ComunicacionCliente.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('DRY RUN', salida)

    def test_un_canal_puede_fallar_sin_bloquear_al_otro(self):
        self._parcialidad(3)
        mail.outbox = []
        with patch('comunicacion.services.requests.post', side_effect=RuntimeError('Meta caído')), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            call_command('enviar_recordatorios', stdout=StringIO())
        comms = ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO')
        self.assertEqual(comms.get(canal='EMAIL').estado, 'ENVIADO')
        self.assertEqual(comms.get(canal='WHATSAPP').estado, 'FALLIDO')
        self.assertEqual(len(mail.outbox), 1)


@wa_settings()
class ShimRecordatoriosPagosTest(TestCase):
    """`comercial.enviar_recordatorios_pagos` debe delegar, no duplicar."""

    def setUp(self):
        limpiar_cache_emisor()
        hoy = timezone.localdate()
        cliente = Cliente.objects.create(
            nombre='Ana', email='ana@example.com', telefono=TEL_CLIENTE,
        )
        cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Boda',
            fecha_evento=hoy + timedelta(days=90),
            num_personas=50, precio_final=Decimal('10000.00'),
        )
        plan = PlanPago.objects.create(cotizacion=cot, activo=True)
        ParcialidadPago.objects.create(
            plan=plan, numero=1, concepto='Anticipo',
            monto=Decimal('5000.00'), porcentaje=Decimal('50.00'),
            fecha_limite=hoy + timedelta(days=3),
        )

    def test_el_shim_delega_y_no_genera_un_segundo_envio(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            call_command('enviar_recordatorios', stdout=StringIO())
            call_command('enviar_recordatorios_pagos', stdout=StringIO(), stderr=StringIO())
        self.assertEqual(
            ComunicacionCliente.objects.filter(tipo='RECORDATORIO_PAGO').count(),
            2,  # el email y el WhatsApp de la primera corrida, sin repetir
        )

    def test_el_shim_respeta_dry_run(self):
        with patch('comunicacion.services.requests.post') as post:
            call_command(
                'enviar_recordatorios_pagos', '--dry-run',
                stdout=StringIO(), stderr=StringIO(),
            )
        post.assert_not_called()
        self.assertEqual(ComunicacionCliente.objects.count(), 0)
