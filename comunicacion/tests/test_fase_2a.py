"""Fase 2a (Issue #430): origen del anuncio, seguimiento en 3 pasos y agenda de visitas."""
import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Pago
from comunicacion import herramientas_agente, services_agente, services_pendientes, services_tablero, services_visitas
from comunicacion.models import (
    BajaWhatsApp,
    ComunicacionCliente,
    ConversacionWhatsApp,
    HorarioVisita,
    Pendiente,
    Responsable,
    Visita,
)
from core_erp.test_utils import login_superuser_con_totp
from legal.models import AceptacionLegal

from .test_agente import AGENTE
from .utils import TEL_CLIENTE, TEL_EMISOR, RespuestaFalsa, limpiar_cache_emisor, wa_settings

TEL_ENC = '525555550011'
TEL_DIR = '525555550012'
TEL_OTRO = '525555550013'
OK = SimpleNamespace(estado='ENVIADO', proveedor_id='')
REFERRAL = {'source_id': '120230001', 'source_url': 'https://fb.me/anuncio', 'headline': 'Pasadía en Umán',
            'ctwa_clid': 'CLID123', 'source_type': 'ad'}


# ───────────────────────────── 1. Origen del anuncio ──────────────────────────

class OrigenAnuncioTest(TestCase):
    def test_el_primer_mensaje_guarda_el_referral(self):
        conv = services_agente.recibir_mensaje(
            telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1', referral=REFERRAL)
        conv.refresh_from_db()
        self.assertEqual((conv.anuncio_id, conv.anuncio_titulo, conv.anuncio_url, conv.anuncio_clid),
                         ('120230001', 'Pasadía en Umán', 'https://fb.me/anuncio', 'CLID123'))

    def test_un_segundo_anuncio_no_pisa_el_primero(self):
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1',
                                        referral=REFERRAL)
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Otra vez', wamid='w2',
                                        referral={**REFERRAL, 'source_id': '999', 'headline': 'Otro'})
        self.assertEqual(ConversacionWhatsApp.objects.get().anuncio_id, '120230001')

    def test_sin_referral_queda_vacio_y_un_referral_raro_no_truena(self):
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1')
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w2',
                                        referral='basura')
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w3',
                                        referral={})
        self.assertEqual(ConversacionWhatsApp.objects.get().anuncio_id, '')

    def test_el_webhook_pasa_el_referral_del_mensaje(self):
        from comunicacion import views_whatsapp
        cambio = {'field': 'messages', 'value': {
            'contacts': [{'wa_id': TEL_CLIENTE, 'profile': {'name': 'Ana'}}],
            'messages': [{'from': TEL_CLIENTE, 'id': 'wamid.R1', 'type': 'text', 'text': {'body': 'Hola'},
                          'referral': REFERRAL}]}}
        views_whatsapp._procesar_cambio(json.loads(json.dumps(cambio)))
        self.assertEqual(ConversacionWhatsApp.objects.get().anuncio_titulo, 'Pasadía en Umán')

    def test_el_tablero_cuenta_por_anuncio(self):
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1',
                                        referral=REFERRAL)
        services_agente.recibir_mensaje(telefono='525555550099', nombre='Luis', texto='Hola', wamid='w2',
                                        referral=REFERRAL)
        services_agente.recibir_mensaje(telefono='525555550098', nombre='Eva', texto='Hola', wamid='w3')
        m = services_tablero.metricas_agente(30)
        self.assertEqual(len(m['por_anuncio']), 1)
        self.assertEqual((m['por_anuncio'][0]['titulo'], m['por_anuncio'][0]['conversaciones']),
                         ('Pasadía en Umán', 2))
        self.assertEqual(m['conversion']['nuevas'], 3)

    def test_el_admin_filtra_por_origen(self):
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1',
                                        referral=REFERRAL)
        services_agente.recibir_mensaje(telefono='525555550098', nombre='Eva', texto='Hola', wamid='w3')
        usuario = User.objects.create_superuser('dir2a', 'd@example.com', 'x')
        login_superuser_con_totp(self.client, usuario)
        url = reverse('admin:comunicacion_conversacionwhatsapp_changelist')
        for filtro, esperado in (('_con', 1), ('_sin', 1), ('120230001', 1), ('', 2)):
            resp = self.client.get(url, {'origen': filtro} if filtro else {})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.context['cl'].result_count, esperado, filtro)
        self.assertContains(self.client.get(url), 'Pasadía en Umán')


# ───────────────────────────── 2. Seguimiento en 3 pasos ──────────────────────

PLANTILLAS = dict(META_WA_PLANTILLA_SEGUIMIENTO='seg_dia3', META_WA_PLANTILLA_SEGUIMIENTO_DIA1='seg_dia1',
                  META_WA_PLANTILLA_SEGUIMIENTO_FECHA='seg_fecha', ERP_KOOXI_SEGUIMIENTO_DIAS=3)


@wa_settings(**PLANTILLAS)
class SeguimientoTresPasosTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.hoy = timezone.localdate()
        self.cliente = Cliente.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE[-10:])
        AceptacionLegal.objects.create(cliente=self.cliente, correo='ana@example.com',
                                       aceptado_en=timezone.now() - timedelta(days=9),
                                       origen='FORM_COTIZACION', finalidades_aceptadas=['MARKETING'])

    def _cot(self, antiguedad, dias_al_evento, cliente=None):
        cot = Cotizacion.objects.create(cliente=cliente or self.cliente, tipo_servicio='PASADIA', num_personas=15,
                                        fecha_evento=self.hoy + timedelta(days=dias_al_evento))
        Cotizacion.objects.filter(pk=cot.pk).update(
            estado='COTIZADA', precio_final=Decimal('2000.00'),
            created_at=timezone.now() - timedelta(days=antiguedad))
        return Cotizacion.objects.get(pk=cot.pk)

    def _correr(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
                patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            call_command('enviar_seguimientos', stdout=StringIO())

    def _plantillas_enviadas(self, cot):
        return sorted(ComunicacionCliente.objects.filter(tipo='SEGUIMIENTO', cotizacion=cot)
                      .values_list('clave_idempotencia', flat=True))

    def test_dia_1_sale_una_sola_vez(self):
        cot = self._cot(antiguedad=1, dias_al_evento=60)
        self._correr()
        self._correr()
        self.assertEqual(self._plantillas_enviadas(cot), [f'seguimiento:{cot.pk}:dia1:whatsapp'])

    def test_dia_3_conserva_su_clave_historica(self):
        cot = self._cot(antiguedad=3, dias_al_evento=60)
        self._correr()
        self.assertEqual(self._plantillas_enviadas(cot), [f'seguimiento:{cot.pk}:whatsapp'])

    def test_los_tres_pasos_salen_en_orden_y_nunca_hacia_atras(self):
        cot = self._cot(antiguedad=1, dias_al_evento=60)
        self._correr()                                    # día 1
        ComunicacionCliente.objects.update(fecha_envio=timezone.now() - timedelta(days=2))
        Cotizacion.objects.filter(pk=cot.pk).update(created_at=timezone.now() - timedelta(days=3))
        self._correr()                                    # día 3 (el día 1 no se repite)
        ComunicacionCliente.objects.update(fecha_envio=timezone.now() - timedelta(days=2))
        Cotizacion.objects.filter(pk=cot.pk).update(fecha_evento=self.hoy + timedelta(days=10))
        self._correr()                                    # fecha por ocuparse
        ComunicacionCliente.objects.update(fecha_envio=timezone.now() - timedelta(days=2))
        Cotizacion.objects.filter(pk=cot.pk).update(created_at=timezone.now() - timedelta(days=1))
        self._correr()                                    # el día 1 ya no vuelve
        self.assertEqual(self._plantillas_enviadas(cot), sorted([
            f'seguimiento:{cot.pk}:dia1:whatsapp', f'seguimiento:{cot.pk}:whatsapp',
            f'seguimiento:{cot.pk}:fecha:whatsapp']))

    def test_a_lo_mas_un_mensaje_cada_20_horas(self):
        cot = self._cot(antiguedad=3, dias_al_evento=10)  # cumple día 3 y fecha cercana
        self._correr()
        self._correr()
        self.assertEqual(self._plantillas_enviadas(cot), [f'seguimiento:{cot.pk}:fecha:whatsapp'])

    def test_fecha_por_ocuparse_cuando_faltan_14_dias_o_menos(self):
        cercana = self._cot(antiguedad=2, dias_al_evento=14)
        lejana = self._cot(antiguedad=2, dias_al_evento=15,
                           cliente=Cliente.objects.create(nombre='Luis', telefono='5555550777'))
        AceptacionLegal.objects.create(cliente=lejana.cliente, correo='l@example.com', aceptado_en=timezone.now(),
                                       origen='FORM_COTIZACION', finalidades_aceptadas=['MARKETING'])
        self._correr()
        self.assertEqual(self._plantillas_enviadas(cercana), [f'seguimiento:{cercana.pk}:fecha:whatsapp'])
        self.assertNotIn(f'seguimiento:{lejana.pk}:fecha:whatsapp', self._plantillas_enviadas(lejana))

    def test_fecha_por_ocuparse_cuando_otra_cotizacion_de_esa_fecha_avanza(self):
        cot = self._cot(antiguedad=2, dias_al_evento=60)
        otro = Cliente.objects.create(nombre='Otro', telefono='5555550888')
        rival = self._cot(antiguedad=2, dias_al_evento=60, cliente=otro)
        Pago.objects.bulk_create([Pago(cotizacion=rival, monto=Decimal('500.00'), metodo='EFECTIVO')])
        self._correr()
        self.assertEqual(self._plantillas_enviadas(cot), [f'seguimiento:{cot.pk}:fecha:whatsapp'])

    def test_sin_plantilla_ese_paso_no_sale(self):
        cot = self._cot(antiguedad=1, dias_al_evento=60)
        with self.settings(META_WA_PLANTILLA_SEGUIMIENTO_DIA1=''):
            self._correr()
        self.assertEqual(self._plantillas_enviadas(cot), [])

    def test_sin_consentimiento_o_con_baja_no_sale_ningun_paso(self):
        cot = self._cot(antiguedad=1, dias_al_evento=5)
        BajaWhatsApp.objects.create(telefono=TEL_CLIENTE, origen='PALABRA')
        self._correr()
        AceptacionLegal.objects.all().delete()
        BajaWhatsApp.objects.all().delete()
        self._correr()
        self.assertEqual(self._plantillas_enviadas(cot), [])

    def test_con_pagos_no_sale(self):
        cot = self._cot(antiguedad=1, dias_al_evento=60)
        Pago.objects.bulk_create([Pago(cotizacion=cot, monto=Decimal('500.00'), metodo='EFECTIVO')])
        self._correr()
        self.assertEqual(self._plantillas_enviadas(cot), [])


# ───────────────────────────── 3. Agenda de visitas ───────────────────────────

def _proximo(dia_semana, dias_min=2):
    """Primera fecha con ese día de la semana al menos `dias_min` días adelante."""
    d = timezone.localdate() + timedelta(days=dias_min)
    while d.weekday() != dia_semana:
        d += timedelta(days=1)
    return d


def _dt(fecha, hora):
    return timezone.make_aware(datetime.combine(fecha, hora))


class MocksVisitas:
    def setUp(self):
        super().setUp()
        for prefijo, destino, nombre in (
                ('pendientes', services_pendientes, 'enviar_whatsapp'),
                ('pendientes', services_pendientes, 'enviar_whatsapp_template'),
                ('pendientes', services_pendientes, 'enviar_whatsapp_botones'),
                ('visitas', services_visitas, 'enviar_whatsapp'),
                ('visitas', services_visitas, 'enviar_whatsapp_template')):
            p = patch.object(destino, nombre, return_value=OK)
            setattr(self, f'{prefijo}_{nombre}', p.start())
            self.addCleanup(p.stop)
        Responsable.objects.create(rol='ENCARGADO', nombre='Encargado', telefono_wa=TEL_ENC)
        Responsable.objects.create(rol='DIRECCION', nombre='Dirección', telefono_wa=TEL_DIR)
        self.dia = _proximo(1)  # martes
        HorarioVisita.objects.create(dia_semana=1, hora_inicio=time(16, 0), hora_fin=time(18, 0),
                                     duracion_minutos=60)
        self.conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1')


@wa_settings(**AGENTE)
class HorariosYAgendaTest(MocksVisitas, TestCase):
    def test_ofrece_solo_horarios_del_encargado(self):
        libres = services_visitas.horarios_disponibles()
        self.assertTrue(libres)
        self.assertTrue(all(h.weekday() == 1 and h.hour in (16, 17) for h in libres))
        self.assertIn(_dt(self.dia, time(16, 0)), libres)

    def test_no_ofrece_con_menos_de_3_horas_ni_horarios_ocupados(self):
        ahora = _dt(self.dia, time(14, 30))   # a las 16:00 faltan 1.5 h, a las 17:00 faltan 2.5 h
        self.assertNotIn(_dt(self.dia, time(16, 0)), services_visitas.horarios_disponibles(ahora=ahora))
        self.assertNotIn(_dt(self.dia, time(17, 0)), services_visitas.horarios_disponibles(ahora=ahora))
        Visita.objects.create(nombre='X', telefono=TEL_OTRO, inicio=_dt(self.dia, time(16, 0)))
        self.assertNotIn(_dt(self.dia, time(16, 0)), services_visitas.horarios_disponibles())

    def test_sin_horarios_configurados_la_herramienta_manda_a_un_humano(self):
        HorarioVisita.objects.update(activo=False)
        salida, error = herramientas_agente.ejecutar('horarios_de_visita', {})
        self.assertFalse(error)
        self.assertEqual(json.loads(salida)['horarios'], [])
        self.assertIn('pasar_a_humano', json.loads(salida)['nota'])

    def test_agendar_crea_la_visita_por_confirmar_y_avisa_al_encargado_con_botones(self):
        inicio = _dt(self.dia, time(16, 0)).strftime('%Y-%m-%dT%H:%M')
        salida, error = herramientas_agente.ejecutar(
            'agendar_visita', {'inicio': inicio, 'nombre': 'Ana Ruiz', 'personas': 4}, conv=self.conv)
        self.assertFalse(error, salida)
        visita = Visita.objects.get()
        self.assertEqual((visita.estado, visita.personas, visita.telefono), ('POR_CONFIRMAR', 4, TEL_CLIENTE))
        pendiente = Pendiente.objects.get(tipo='VISITA')
        self.assertEqual((pendiente.rol, pendiente.severidad, pendiente.estado), ('ENCARGADO', 'NORMAL', 'ABIERTO'))
        botones = [c.kwargs['botones'] for c in self.pendientes_enviar_whatsapp_botones.call_args_list] \
            if self.pendientes_enviar_whatsapp_botones.called else []
        destinos = {c.kwargs['telefono'] for c in self.pendientes_enviar_whatsapp_botones.call_args_list}
        # Con la ventana del encargado cerrada el aviso va por texto/plantilla; en ambos casos a los dos números.
        enviados = {c.kwargs['telefono'] for m in (self.pendientes_enviar_whatsapp_botones,
                                                   self.pendientes_enviar_whatsapp,
                                                   self.pendientes_enviar_whatsapp_template)
                    for c in m.call_args_list}
        self.assertEqual(enviados | destinos, {TEL_ENC, TEL_DIR})
        self.assertFalse(botones and any('Confirmar' not in str(b) for b in botones))

    def test_kooxi_no_puede_agendar_un_horario_que_no_ofrecio(self):
        malo = _dt(self.dia, time(20, 0)).strftime('%Y-%m-%dT%H:%M')
        salida, error = herramientas_agente.ejecutar('agendar_visita', {'inicio': malo, 'nombre': 'Ana'},
                                                     conv=self.conv)
        self.assertTrue(error, salida)
        self.assertFalse(Visita.objects.exists())

    def test_no_se_agenda_dos_veces_el_mismo_horario_ni_dos_visitas_al_mismo_cliente(self):
        inicio = _dt(self.dia, time(16, 0)).strftime('%Y-%m-%dT%H:%M')
        herramientas_agente.ejecutar('agendar_visita', {'inicio': inicio, 'nombre': 'Ana'}, conv=self.conv)
        otra = services_agente.recibir_mensaje(telefono=TEL_OTRO, nombre='Luis', texto='Hola', wamid='w9')
        _, error = herramientas_agente.ejecutar('agendar_visita', {'inicio': inicio, 'nombre': 'Luis'}, conv=otra)
        self.assertTrue(error)
        otro_horario = _dt(self.dia, time(17, 0)).strftime('%Y-%m-%dT%H:%M')
        _, error = herramientas_agente.ejecutar('agendar_visita', {'inicio': otro_horario, 'nombre': 'Ana'},
                                                conv=self.conv)
        self.assertTrue(error)
        self.assertEqual(Visita.objects.count(), 1)

    def test_la_base_impide_dos_visitas_vivas_en_el_mismo_horario(self):
        inicio = _dt(self.dia, time(16, 0))
        Visita.objects.create(nombre='A', telefono=TEL_CLIENTE, inicio=inicio)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Visita.objects.create(nombre='B', telefono=TEL_OTRO, inicio=inicio)
        Visita.objects.filter(telefono=TEL_CLIENTE).update(estado='RECHAZADA')
        Visita.objects.create(nombre='B', telefono=TEL_OTRO, inicio=inicio)  # liberado


@wa_settings(**AGENTE)
class ConfirmarVisitaTest(MocksVisitas, TestCase):
    def setUp(self):
        super().setUp()
        self.visita = Visita.objects.create(
            nombre='Ana Ruiz', telefono=TEL_CLIENTE, conversacion=self.conv, inicio=_dt(self.dia, time(16, 0)))
        services_visitas._avisar_encargado(self.visita)

    def _boton(self, telefono, accion, visita=None):
        return services_visitas.atender_boton(telefono, f'vis:{(visita or self.visita).pk}:{accion}')

    def test_el_encargado_confirma_y_el_cliente_recibe_aviso(self):
        respuesta = self._boton(TEL_ENC, 'ok')
        self.assertIn('quedó confirmada', respuesta)
        self.visita.refresh_from_db()
        self.assertEqual((self.visita.estado, self.visita.decidida_por), ('CONFIRMADA', TEL_ENC))
        self.assertEqual(Pendiente.objects.get(tipo='VISITA').estado, 'RESUELTO')
        self.assertIn('quedó confirmada', self.visitas_enviar_whatsapp.call_args.kwargs['mensaje'])
        self.assertEqual(self.visitas_enviar_whatsapp.call_args.kwargs['telefono'], TEL_CLIENTE)

    def test_direccion_tambien_puede_decidir(self):
        self.assertIn('quedó confirmada', self._boton(TEL_DIR, 'ok'))

    def test_un_numero_ajeno_o_el_cliente_no_pueden_confirmar(self):
        self.assertIsNone(self._boton(TEL_OTRO, 'ok'))
        self.assertIsNone(self._boton(TEL_CLIENTE, 'ok'))
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.estado, 'POR_CONFIRMAR')

    def test_un_numero_pendiente_de_aceptar_no_confirma(self):
        Responsable.objects.filter(rol='ENCARGADO').update(telefono_wa=TEL_OTRO, telefono_pendiente=TEL_ENC)
        self.assertIsNone(self._boton(TEL_ENC, 'ok'))

    def test_no_puedo_libera_el_horario_y_avisa_al_cliente(self):
        self.assertIn('El horario quedó libre', self._boton(TEL_ENC, 'no'))
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.estado, 'RECHAZADA')
        self.assertIn(_dt(self.dia, time(16, 0)), services_visitas.horarios_disponibles())
        self.assertIn('No pudimos confirmar', self.visitas_enviar_whatsapp.call_args.kwargs['mensaje'])

    def test_una_visita_ya_decidida_no_se_decide_otra_vez(self):
        self._boton(TEL_ENC, 'ok')
        self.assertIsNone(self._boton(TEL_ENC, 'no'))
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.estado, 'CONFIRMADA')

    def test_fuera_de_la_ventana_de_24_horas_el_cliente_recibe_la_plantilla(self):
        ConversacionWhatsApp.objects.get(pk=self.conv.pk).mensajes.update(
            created_at=timezone.now() - timedelta(days=3))
        with self.settings(META_WA_PLANTILLA_VISITA='visita_aviso'):
            self._boton(TEL_ENC, 'ok')
        self.assertEqual(self.visitas_enviar_whatsapp_template.call_args.kwargs['template_name'], 'visita_aviso')

    def test_el_boton_llega_por_el_agente_y_nunca_al_modelo(self):
        m = services_agente.recibir_mensaje(telefono=TEL_ENC, nombre='Enc', texto=f'vis:{self.visita.pk}:ok',
                                            wamid='wb1')
        with patch.object(services_agente, '_cliente_ia') as ia, \
                patch.object(services_agente, 'enviar_whatsapp', return_value=OK):
            services_agente._atender_pendientes(m.pk)
        ia.assert_not_called()
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.estado, 'CONFIRMADA')

    def test_cancelar_desde_el_admin_libera_y_avisa(self):
        usuario = User.objects.create_superuser('dirvis', 'd@example.com', 'x')
        self.assertTrue(services_visitas.cancelar(self.visita.pk, usuario))
        self.assertFalse(services_visitas.cancelar(self.visita.pk, usuario))
        self.assertIn('se canceló', self.visitas_enviar_whatsapp.call_args.kwargs['mensaje'])


@wa_settings(**AGENTE)
class RecordatorioVisitaTest(MocksVisitas, TestCase):
    def test_recuerda_el_dia_anterior_solo_las_confirmadas_y_una_vez(self):
        manana = timezone.localdate() + timedelta(days=1)
        confirmada = Visita.objects.create(nombre='A', telefono=TEL_CLIENTE, estado='CONFIRMADA',
                                           inicio=_dt(manana, time(16, 0)))
        Visita.objects.create(nombre='B', telefono=TEL_OTRO, estado='POR_CONFIRMAR', inicio=_dt(manana, time(17, 0)))
        Visita.objects.create(nombre='C', telefono='525555550014', estado='CONFIRMADA',
                              inicio=_dt(manana + timedelta(days=1), time(16, 0)))
        out = StringIO()
        call_command('enviar_recordatorios_visitas', stdout=out)
        call_command('enviar_recordatorios_visitas', stdout=out)
        self.assertEqual(self.visitas_enviar_whatsapp.call_count, 1)
        self.assertEqual(self.visitas_enviar_whatsapp.call_args.kwargs['telefono'], TEL_CLIENTE)
        self.assertIn('Recordatorio', self.visitas_enviar_whatsapp.call_args.kwargs['mensaje'])
        confirmada.refresh_from_db()
        self.assertIsNotNone(confirmada.recordatorio_en)


class AdminVisitasTest(TestCase):
    def test_las_listas_y_las_acciones_cargan(self):
        usuario = User.objects.create_superuser('dirvis2', 'd@example.com', 'x')
        login_superuser_con_totp(self.client, usuario)
        HorarioVisita.objects.create(dia_semana=2, hora_inicio=time(10, 0), hora_fin=time(12, 0))
        visita = Visita.objects.create(nombre='Ana', telefono=TEL_CLIENTE, inicio=timezone.now() + timedelta(days=3))
        for nombre in ('comunicacion_horariovisita_changelist', 'comunicacion_visita_changelist'):
            self.assertEqual(self.client.get(reverse(f'admin:{nombre}')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:comunicacion_visita_change', args=[visita.pk])).status_code,
                         200)
        with patch.object(services_visitas, 'enviar_whatsapp', return_value=OK):
            self.client.post(reverse('admin:comunicacion_visita_changelist'), {
                'action': 'confirmar_visitas', '_selected_action': [visita.pk]})
        visita.refresh_from_db()
        self.assertEqual(visita.estado, 'CONFIRMADA')
