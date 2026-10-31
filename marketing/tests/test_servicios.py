"""Motivos de pérdida, retención, barrido y admin de prospectos."""
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core_erp.test_utils import login_superuser_con_totp
from marketing.models import Lead, LeadEvento
from marketing.services import barrido, motivos, retencion
from marketing.tests.test_pipeline import TEL, Base

User = get_user_model()


def respuesta(motivo):
    return SimpleNamespace(content=[SimpleNamespace(type='text', text='{"motivo": "%s"}' % motivo)])


class MotivosTest(Base):
    def perdido(self, con_mensajes=True):
        if con_mensajes:
            self.mensaje(texto='Me parece muy caro, mejor otro lugar')
        else:
            self.cotizacion(self.cliente())
        Lead.objects.update(etapa='PERDIDO', etapa_cambiada_en=timezone.now())
        return Lead.objects.get()

    @override_settings(ANTHROPIC_API_KEY_KOOXI='llave-de-prueba')
    def test_kooxi_clasifica_el_motivo(self):
        lead = self.perdido()
        cliente = MagicMock()
        cliente.messages.create.return_value = respuesta('PRECIO')
        with patch.object(motivos, '_cliente', return_value=cliente):
            self.assertEqual(motivos.clasificar_pendientes(), 1)
        lead.refresh_from_db()
        self.assertEqual((lead.motivo_perdida, lead.motivo_perdida_origen), ('PRECIO', 'KOOXI'))
        self.assertTrue(lead.eventos.filter(tipo='MOTIVO', origen='KOOXI').exists())
        enviado = cliente.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('Me parece muy caro', enviado)

    @override_settings(ANTHROPIC_API_KEY_KOOXI='')
    def test_sin_llave_aplica_la_regla(self):
        self.perdido()
        with patch.object(motivos, '_cliente') as cliente:
            motivos.clasificar_pendientes()
            cliente.assert_not_called()
        lead = Lead.objects.get()
        self.assertEqual((lead.motivo_perdida, lead.motivo_perdida_origen), ('SIN_RESPUESTA', 'SISTEMA'))

    @override_settings(ANTHROPIC_API_KEY_KOOXI='llave-de-prueba')
    def test_sin_mensajes_del_cliente_aplica_la_regla_y_cancelada_es_cancelo(self):
        lead = self.perdido(con_mensajes=False)
        cot = lead.cotizacion
        self.estado(cot, 'CANCELADA')
        with patch.object(motivos, '_cliente') as cliente:
            motivos.clasificar(lead)
            cliente.assert_not_called()
        lead.refresh_from_db()
        self.assertEqual((lead.motivo_perdida, lead.motivo_perdida_origen), ('CANCELO', 'SISTEMA'))

    @override_settings(ANTHROPIC_API_KEY_KOOXI='llave-de-prueba')
    def test_error_de_la_api_cae_a_la_regla(self):
        self.perdido()
        cliente = MagicMock()
        cliente.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
        with patch.object(motivos, '_cliente', return_value=cliente):
            motivos.clasificar_pendientes()
        self.assertEqual(Lead.objects.get().motivo_perdida_origen, 'SISTEMA')

    @override_settings(ANTHROPIC_API_KEY_KOOXI='llave-de-prueba')
    def test_maximo_por_corrida(self):
        for i in range(3):
            Lead.objects.create(telefono=f'52555555{i:04d}', etapa='PERDIDO')
        with patch.object(motivos, '_cliente') as cliente:
            cliente.return_value.messages.create.return_value = respuesta('OTRO')
            self.assertEqual(motivos.clasificar_pendientes(maximo=2), 2)


class RetencionTest(TestCase):
    def lead(self, dias, etapa='PERDIDO', **kw):
        return Lead.objects.create(
            telefono=kw.pop('telefono', TEL), nombre='Ana', etapa=etapa, anuncio_clid='clid', anuncio_id='AD1',
            fuente='META_ANUNCIO', motivo_perdida='PRECIO',
            fecha_ultima_interaccion=timezone.now() - timedelta(days=dias), **kw)

    def test_anonimiza_a_un_ano_y_conserva_lo_medible(self):
        lead = self.lead(400)
        self.assertEqual(retencion.anonimizar_vencidos(), 1)
        lead.refresh_from_db()
        self.assertEqual((lead.nombre, lead.telefono, lead.anuncio_clid), ('', '', ''))
        self.assertIsNone(lead.conversacion)
        self.assertIsNone(lead.cliente)
        self.assertIsNotNone(lead.anonimizado_en)
        self.assertEqual((lead.fuente, lead.anuncio_id, lead.motivo_perdida), ('META_ANUNCIO', 'AD1', 'PRECIO'))
        self.assertTrue(lead.eventos.filter(tipo='ANONIMIZADO').exists())
        self.assertEqual(retencion.anonimizar_vencidos(), 0)

    def test_no_toca_recientes_abiertos_ni_ganados(self):
        reciente = self.lead(100, telefono='525555550201')
        abierto = self.lead(400, etapa='COTIZADO', telefono='525555550202')
        antes_ganado = self.lead(400, telefono='525555550203')
        LeadEvento.objects.create(lead=antes_ganado, tipo='ETAPA', etapa_nueva='GANADO')
        self.assertEqual(retencion.anonimizar_vencidos(), 0)
        for lead in (reciente, abierto, antes_ganado):
            lead.refresh_from_db()
            self.assertEqual(lead.nombre, 'Ana')


class BarridoTest(Base):
    def test_comando_reconstruye_el_historico(self):
        self.mensaje()
        cliente = self.cliente(telefono='525555550301')
        self.cotizacion(cliente)
        salida = StringIO()
        call_command('sincronizar_prospectos', '--desde', '2020-01-01', stdout=salida)
        self.assertIn('Prospectos:', salida.getvalue())
        self.assertEqual(Lead.objects.count(), 2)

    def test_barrido_diario_pierde_por_silencio_y_no_reabre_lo_cerrado(self):
        self.mensaje()
        lead = Lead.objects.get()
        resultado = barrido.barrer(ahora=timezone.now() + timedelta(days=16))
        lead.refresh_from_db()
        self.assertEqual(lead.etapa, 'PERDIDO')
        self.assertEqual(resultado['motivos'], 1)
        barrido.barrer(ahora=timezone.now() + timedelta(days=17))
        lead.refresh_from_db()
        self.assertEqual(lead.etapa, 'PERDIDO')
        self.assertEqual(Lead.objects.count(), 1)

    def test_comando_rechaza_fecha_invalida(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('sincronizar_prospectos', '--desde', 'ayer')


class AdminTest(Base):
    def setUp(self):
        self.super = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        self.staff = User.objects.create_user('ven', 'ven@example.com', 'x-segura-123', is_staff=True)

    def test_solo_superusuario(self):
        url = reverse('admin:marketing_lead_changelist')
        self.client.force_login(self.staff)
        self.assertIn(self.client.get(url).status_code, (302, 403))
        login_superuser_con_totp(self.client, self.super)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_corregir_motivo_deja_evento_de_usuario(self):
        self.mensaje()
        Lead.objects.update(etapa='PERDIDO', motivo_perdida='SIN_RESPUESTA', motivo_perdida_origen='SISTEMA')
        lead = Lead.objects.get()
        login_superuser_con_totp(self.client, self.super)
        url = reverse('admin:marketing_lead_change', args=[lead.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {
            'motivo_perdida': 'PRECIO',
            'eventos-TOTAL_FORMS': '0', 'eventos-INITIAL_FORMS': '0',
            'eventos-MIN_NUM_FORMS': '0', 'eventos-MAX_NUM_FORMS': '1000'})
        self.assertEqual(r.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual((lead.motivo_perdida, lead.motivo_perdida_origen), ('PRECIO', 'USUARIO'))
        evento = lead.eventos.filter(tipo='MOTIVO').latest('id')
        self.assertEqual((evento.origen, evento.created_by), ('USUARIO', self.super))
        self.assertEqual(evento.detalle, {'antes': 'SIN_RESPUESTA', 'despues': 'PRECIO'})

    def test_sin_alta_ni_borrado(self):
        login_superuser_con_totp(self.client, self.super)
        self.assertEqual(self.client.get(reverse('admin:marketing_lead_add')).status_code, 403)

    def test_la_bitacora_no_se_edita_ni_se_borra(self):
        from django.core.exceptions import ValidationError
        self.mensaje()
        evento = Lead.objects.get().eventos.first()
        with self.assertRaises(ValidationError):
            evento.save()
        with self.assertRaises(ValidationError):
            evento.delete()
