"""Bandeja de pendientes (Issue #422): registro idempotente, avisos urgentes,
escalamiento, botones, resumen diario, copia por hora y admin."""
from datetime import time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comunicacion import services_agente, services_pendientes
from comunicacion.models import ComunicacionCliente, MensajeWhatsApp, Pendiente, Responsable
from comunicacion.services import alertar_equipo_email
from comunicacion.views_whatsapp import _texto
from core_erp.test_utils import login_superuser_con_totp
from nomina.models import Empleado
from operaciones.models import ItemChecklist, PlantillaChecklist, TareaProgramada

from .test_agente import AGENTE, _cliente_falso
from .utils import TEL_CLIENTE, RespuestaFalsa, wa_settings

TEL_ENC = '525555550011'
TEL_DIR = '525555550012'
TEL_OTRO = '525555550013'
OK = SimpleNamespace(estado='ENVIADO', proveedor_id='')


def _resp(rol, telefono, **kw):
    return Responsable.objects.create(rol=rol, nombre=f'{rol} test', telefono_wa=telefono, **kw)


def _pend(**kw):
    datos = dict(titulo='Revisar algo', rol='ENCARGADO', tipo='ALERTA', severidad='NORMAL',
                 vence_en=timezone.now() + timedelta(hours=24))
    datos.update(kw)
    return Pendiente.objects.create(**datos)


def _abrir_ventana(telefono):
    conv = services_agente._conversacion(telefono)
    MensajeWhatsApp.objects.create(conversacion=conv, direccion='ENTRADA', texto='hola', procesado=True)


class MocksWA:
    """Intercepta los tres transportes de WhatsApp de la bandeja."""

    def setUp(self):
        super().setUp()
        mail.outbox = []
        patchers = {n: patch.object(services_pendientes, n, return_value=OK)
                    for n in ('enviar_whatsapp', 'enviar_whatsapp_template', 'enviar_whatsapp_botones')}
        for nombre, p in patchers.items():
            setattr(self, nombre, p.start())
            self.addCleanup(p.stop)

    def destinos(self, mock):
        return [c.kwargs['telefono'] for c in mock.call_args_list]


@wa_settings(WA_TEMPLATE_OPERACIONES='aviso_operaciones')
class RegistroTest(MocksWA, TestCase):
    def test_toda_alerta_deja_un_pendiente_para_direccion_y_la_clave_no_lo_duplica(self):
        for _ in range(2):
            alertar_equipo_email(None, asunto='Algo pasó', cuerpo='Detalle', clave_idempotencia='k1')
        p = Pendiente.objects.get()
        self.assertEqual((p.rol, p.tipo, p.severidad, p.estado), ('DIRECCION', 'ALERTA', 'NORMAL', 'ABIERTO'))
        self.assertEqual(p.titulo, 'Algo pasó')
        self.assertEqual(p.clave_idempotencia, 'alerta:k1')
        self.assertEqual(len(mail.outbox), 1)

    def test_sin_clave_cada_llamada_deja_su_pendiente_y_pendiente_false_no_deja_ninguno(self):
        alertar_equipo_email(None, asunto='A', cuerpo='x')
        alertar_equipo_email(None, asunto='B', cuerpo='x')
        alertar_equipo_email(None, asunto='C', cuerpo='x', pendiente=False)
        self.assertEqual(Pendiente.objects.count(), 2)

    def test_si_la_bandeja_falla_la_alerta_sale_igual(self):
        with patch.object(services_pendientes, 'registrar_pendiente', side_effect=RuntimeError('boom')):
            comm = alertar_equipo_email(None, asunto='Importante', cuerpo='x', clave_idempotencia='k2')
        self.assertEqual(comm.estado, 'ENVIADO')
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(Pendiente.objects.exists())

    def test_registrar_pendiente_nunca_lanza(self):
        with patch.object(Pendiente.objects, 'create', side_effect=RuntimeError('boom')):
            self.assertIsNone(services_pendientes.registrar_pendiente(titulo='X', rol='ENCARGADO'))

    def test_plazos_por_defecto(self):
        antes = timezone.now()
        urgente = services_pendientes.registrar_pendiente(titulo='U', rol='ENCARGADO', severidad='URGENTE')
        normal = services_pendientes.registrar_pendiente(titulo='N', rol='ENCARGADO')
        self.assertAlmostEqual((urgente.vence_en - antes).total_seconds(), 2 * 3600, delta=5)
        self.assertAlmostEqual((normal.vence_en - antes).total_seconds(), 24 * 3600, delta=5)

    def test_el_detalle_de_un_pase_a_humano_no_lleva_el_texto_del_cliente(self):
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='MI SECRETO', wamid='w1')
        services_agente._pasar_a_humano(conv, 'Quiere negociar')
        p = Pendiente.objects.get()
        self.assertEqual((p.tipo, p.severidad, p.rol), ('PASE_HUMANO', 'URGENTE', 'ENCARGADO'))
        self.assertNotIn('MI SECRETO', p.titulo + p.detalle)
        self.assertNotIn('MI SECRETO', ' '.join(str(c) for c in self.enviar_whatsapp_template.call_args_list))


@wa_settings(WA_TEMPLATE_OPERACIONES='aviso_operaciones')
class AvisosTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        _resp('ENCARGADO', TEL_ENC)
        _resp('DIRECCION', TEL_DIR)

    def test_urgente_avisa_al_rol_y_a_direccion_en_vivo(self):
        p = services_pendientes.registrar_pendiente(titulo='Cae el cobro', rol='ENCARGADO', severidad='URGENTE')
        self.assertEqual(self.destinos(self.enviar_whatsapp_template), [TEL_ENC, TEL_DIR])
        self.assertIn(f'/admin/comunicacion/pendiente/{p.pk}/change/',
                      self.enviar_whatsapp_template.call_args.kwargs['parametros'][0])

    def test_urgente_con_direccion_cada_hora_solo_avisa_al_rol(self):
        Responsable.objects.filter(rol='DIRECCION').update(modo_copia='CADA_HORA')
        services_pendientes.registrar_pendiente(titulo='U', rol='ENCARGADO', severidad='URGENTE')
        self.assertEqual(self.destinos(self.enviar_whatsapp_template), [TEL_ENC])

    def test_normal_no_avisa_hasta_el_resumen(self):
        services_pendientes.registrar_pendiente(titulo='N', rol='ENCARGADO')
        for mock in (self.enviar_whatsapp, self.enviar_whatsapp_template, self.enviar_whatsapp_botones):
            mock.assert_not_called()

    def test_con_ventana_abierta_va_con_botones_resuelto_y_intervengo(self):
        _abrir_ventana(TEL_ENC)
        _abrir_ventana(TEL_DIR)
        p = services_pendientes.registrar_pendiente(titulo='U', rol='ENCARGADO', severidad='URGENTE')
        botones = {c.kwargs['telefono']: c.kwargs['botones'] for c in self.enviar_whatsapp_botones.call_args_list}
        self.assertEqual(botones[TEL_ENC], [(f'pend:{p.pk}:ok', 'Resuelto')])
        self.assertEqual(botones[TEL_DIR], [(f'pend:{p.pk}:tomar', 'Intervengo')])
        self.enviar_whatsapp_template.assert_not_called()

    def test_si_falla_el_envio_de_whatsapp_el_pendiente_queda_registrado(self):
        self.enviar_whatsapp_template.side_effect = RuntimeError('Meta caído')
        p = services_pendientes.registrar_pendiente(titulo='U', rol='ENCARGADO', severidad='URGENTE')
        self.assertTrue(Pendiente.objects.filter(pk=p.pk, estado='ABIERTO').exists())

    def test_si_whatsapp_responde_fallido_el_pendiente_queda_registrado(self):
        self.enviar_whatsapp_template.return_value = SimpleNamespace(estado='FALLIDO', proveedor_id='')
        p = services_pendientes.registrar_pendiente(titulo='U', rol='ENCARGADO', severidad='URGENTE')
        self.assertTrue(Pendiente.objects.filter(pk=p.pk).exists())


@wa_settings(WA_TEMPLATE_OPERACIONES='aviso_operaciones')
class EscalamientoTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        _resp('ENCARGADO', TEL_ENC)
        _resp('DIRECCION', TEL_DIR)

    def test_un_pendiente_vencido_del_encargado_pasa_a_direccion_una_sola_vez(self):
        p = _pend(vence_en=timezone.now() - timedelta(minutes=1))
        vigente = _pend(titulo='Vigente')
        self.assertEqual(services_pendientes.escalar_vencidos(), 1)
        p.refresh_from_db()
        vigente.refresh_from_db()
        self.assertEqual((p.rol, vigente.rol), ('DIRECCION', 'ENCARGADO'))
        self.assertIsNotNone(p.escalado_en)
        self.assertEqual(self.destinos(self.enviar_whatsapp_template), [TEL_DIR])
        self.assertEqual(services_pendientes.escalar_vencidos(), 0)
        self.assertEqual(self.enviar_whatsapp_template.call_count, 1)

    def test_no_escala_lo_resuelto_ni_lo_de_otros_roles(self):
        _pend(estado='RESUELTO', vence_en=timezone.now() - timedelta(hours=1))
        _pend(rol='CONTADOR', vence_en=timezone.now() - timedelta(hours=1))
        self.assertEqual(services_pendientes.escalar_vencidos(), 0)

    def test_comando_revisar_pendientes(self):
        from django.core.management import call_command
        _pend(vence_en=timezone.now() - timedelta(minutes=1))
        call_command('revisar_pendientes', stdout=SimpleNamespace(write=lambda *a, **k: None))
        self.assertEqual(Pendiente.objects.get().rol, 'DIRECCION')


@wa_settings(WA_TEMPLATE_OPERACIONES='aviso_operaciones')
class CopiaPorHoraTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        _resp('ENCARGADO', TEL_ENC)
        self.direccion = _resp('DIRECCION', TEL_DIR, modo_copia='CADA_HORA')

    def test_una_vez_por_hora_en_un_solo_mensaje(self):
        _pend(titulo='Uno')
        _pend(titulo='Dos')
        self.assertEqual(services_pendientes.copiar_cada_hora(), 2)
        self.assertEqual(self.destinos(self.enviar_whatsapp_template), [TEL_DIR])
        texto = self.enviar_whatsapp_template.call_args.kwargs['parametros'][0]
        self.assertIn('Uno', texto)
        self.assertIn('Dos', texto)
        _pend(titulo='Tres')
        self.assertEqual(services_pendientes.copiar_cada_hora(), 0)  # misma hora
        self.assertEqual(services_pendientes.copiar_cada_hora(timezone.now() + timedelta(hours=1, minutes=1)), 1)

    def test_sin_modo_cada_hora_no_copia(self):
        Responsable.objects.filter(rol='DIRECCION').update(modo_copia='EN_VIVO')
        _pend()
        self.assertEqual(services_pendientes.copiar_cada_hora(), 0)
        self.enviar_whatsapp_template.assert_not_called()

    def test_un_envio_fallido_se_reintenta(self):
        _pend()
        self.enviar_whatsapp_template.return_value = SimpleNamespace(estado='FALLIDO', proveedor_id='')
        self.assertEqual(services_pendientes.copiar_cada_hora(), 0)
        self.enviar_whatsapp_template.return_value = OK
        self.assertEqual(services_pendientes.copiar_cada_hora(), 1)


@wa_settings(WA_TEMPLATE_OPERACIONES='aviso_operaciones', **AGENTE)
class BotonesTest(TestCase):
    def setUp(self):
        mail.outbox = []
        _resp('ENCARGADO', TEL_ENC)
        _resp('DIRECCION', TEL_DIR)
        self.pend = _pend()
        patcher = patch.object(services_pendientes, 'enviar_whatsapp_template', return_value=OK)
        self.aviso = patcher.start()
        self.addCleanup(patcher.stop)

    def _boton(self, telefono, accion, pendiente=None):
        pendiente = pendiente or self.pend
        conv = services_agente.recibir_mensaje(
            telefono=telefono, nombre='X', texto=f'pend:{pendiente.pk}:{accion}', wamid=f'w{telefono}{accion}')
        cliente = _cliente_falso()
        with patch.object(services_agente, 'enviar_whatsapp', return_value=SimpleNamespace(proveedor_id='')) as envio, \
                patch.object(services_agente, '_cliente_ia', return_value=cliente):
            services_agente.procesar_conversacion(conv.pk)
        cliente.beta.messages.create.assert_not_called()  # nunca llega al agente
        return envio

    def test_intervengo_desde_direccion_lo_toma_y_avisa_al_encargado(self):
        envio = self._boton(TEL_DIR, 'tomar')
        self.pend.refresh_from_db()
        self.assertEqual((self.pend.rol, self.pend.tomado_por_direccion), ('DIRECCION', True))
        self.assertEqual(self.aviso.call_args.kwargs['telefono'], TEL_ENC)
        self.assertIn('Lo tomó Dirección', self.aviso.call_args.kwargs['parametros'][0])
        self.assertIn('tomaste', envio.call_args.kwargs['mensaje'])

    def test_intervengo_desde_otro_numero_no_hace_nada(self):
        for telefono in (TEL_OTRO, TEL_ENC):
            self._boton(telefono, 'tomar')
        self.pend.refresh_from_db()
        self.assertEqual((self.pend.rol, self.pend.tomado_por_direccion), ('ENCARGADO', False))
        self.aviso.assert_not_called()

    def test_resuelto_solo_vale_desde_el_responsable_del_rol(self):
        self._boton(TEL_DIR, 'ok')
        self._boton(TEL_OTRO, 'ok')
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, 'ABIERTO')
        self._boton(TEL_ENC, 'ok')
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, 'RESUELTO')
        self.assertIsNotNone(self.pend.resuelto_en)

    def test_el_webhook_conserva_el_id_del_boton_de_la_bandeja(self):
        mensaje = {'type': 'interactive', 'interactive': {'button_reply': {'id': 'pend:7:ok', 'title': 'Resuelto'}}}
        self.assertEqual(_texto(mensaje), 'pend:7:ok')
        mensaje['interactive']['button_reply']['id'] = 'otro'
        self.assertEqual(_texto(mensaje), 'Resuelto')


@wa_settings(WA_TEMPLATE_OPERACIONES='aviso_operaciones')
class ResumenDiarioTest(TestCase):
    def setUp(self):
        mail.outbox = []
        for nombre, patcher in (('emisor', patch('comunicacion.services.numero_emisor_wa', return_value='')),
                                ('post', patch('comunicacion.services.requests.post',
                                               return_value=RespuestaFalsa()))):
            setattr(self, nombre, patcher.start())
            self.addCleanup(patcher.stop)
        _resp('ENCARGADO', TEL_ENC)
        _resp('DIRECCION', TEL_DIR)
        self.hoy = timezone.localdate()

    def _destinos(self):
        return [c.kwargs['json']['to'] for c in self.post.call_args_list]

    def test_sale_una_vez_por_responsable_por_dia_aunque_corra_dos_veces(self):
        _pend(titulo='Del encargado', tipo='SAT')
        _pend(titulo='De dirección', rol='DIRECCION')
        self.assertEqual(services_pendientes.enviar_resumen_diario(self.hoy), 2)
        self.assertEqual(services_pendientes.enviar_resumen_diario(self.hoy), 0)
        self.assertEqual(sorted(self._destinos()), sorted([TEL_ENC, TEL_DIR]))
        self.assertEqual(ComunicacionCliente.objects.filter(
            clave_idempotencia__startswith='pend:resumen:').count(), 2)

    def test_lleva_conteo_por_tipo_los_mas_viejos_y_el_enlace_a_la_bandeja(self):
        for i in range(7):
            _pend(titulo=f'Pendiente {i}', tipo='SAT' if i % 2 else 'CRON')
        services_pendientes.enviar_resumen_diario(self.hoy)
        texto = self.post.call_args_list[0].kwargs['json']['template']['components'][0]['parameters'][0]['text']
        self.assertIn('7', texto)
        self.assertIn('4 Proceso automático', texto)
        self.assertIn('3 SAT', texto)
        self.assertIn('Pendiente 0', texto)
        self.assertIn('Pendiente 4', texto)
        self.assertNotIn('Pendiente 5', texto)
        self.assertIn('/admin/comunicacion/pendiente/', texto)

    def test_direccion_recibe_ademas_los_resueltos_de_ayer(self):
        ayer = timezone.now() - timedelta(days=1)
        _pend(estado='RESUELTO', resuelto_en=ayer)
        _pend(estado='RESUELTO', resuelto_en=timezone.now() - timedelta(days=3))
        services_pendientes.enviar_resumen_diario(self.hoy)
        self.assertEqual(self._destinos(), [TEL_DIR])
        texto = self.post.call_args.kwargs['json']['template']['components'][0]['parameters'][0]['text']
        self.assertIn('Resueltos ayer: 1', texto)

    def test_sin_nada_que_decir_no_manda(self):
        self.assertEqual(services_pendientes.enviar_resumen_diario(self.hoy), 0)
        self.post.assert_not_called()

    def test_integra_el_resumen_de_operaciones_y_lo_marca_enviado(self):
        empleado = Empleado.objects.create(nombre='Juan', telefono='525555550009')
        plantilla = PlantillaChecklist.objects.create(nombre='Preparación', tipo='TURNOVER_EVENTO')
        ItemChecklist.objects.create(plantilla=plantilla, orden=1, texto='Tarea 1')
        tarea = TareaProgramada.objects.create(
            plantilla=plantilla, responsable=empleado, fecha=self.hoy, hora_entrada=time(8, 0),
            hora_limite=time(13, 0))
        services_pendientes.enviar_resumen_diario(self.hoy)
        texto = self.post.call_args.kwargs['json']['template']['components'][0]['parameters'][0]['text']
        self.assertIn('Operación: hoy Juan', texto)
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado_resumen_propietario, 'ENVIADO')
        self.assertEqual(services_pendientes.enviar_resumen_diario(self.hoy), 0)

    def test_comando_enviar_resumen_diario(self):
        from django.core.management import call_command
        _pend()
        call_command('enviar_resumen_diario', stdout=SimpleNamespace(write=lambda *a, **k: None))
        self.assertEqual(self._destinos(), [TEL_ENC])


class AdminPendientesTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        self.usuario = get_user_model().objects.create_superuser('dir', 'dir@example.com', 'x' * 20)
        login_superuser_con_totp(self.client, self.usuario)
        _resp('ENCARGADO', TEL_ENC)
        self.pend = _pend(severidad='URGENTE')
        self.url = reverse('admin:comunicacion_pendiente_changelist')

    def _accion(self, nombre, **extra):
        return self.client.post(self.url, {'action': nombre, '_selected_action': [self.pend.pk], **extra})

    def test_lista_y_detalle_abren_y_no_se_puede_crear_ni_borrar(self):
        self.assertContains(self.client.get(self.url), 'Revisar algo')
        self.assertEqual(self.client.get(reverse('admin:comunicacion_pendiente_change', args=[self.pend.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:comunicacion_pendiente_add')).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:comunicacion_pendiente_delete', args=[self.pend.pk])).status_code, 403)

    def test_filtros_de_estado_rol_severidad_y_tipo(self):
        _pend(titulo='Resuelto viejo', estado='RESUELTO', rol='CONTADOR', severidad='NORMAL', tipo='SAT')
        for filtro in ('estado__exact=ABIERTO', 'rol__exact=ENCARGADO', 'severidad__exact=URGENTE', 'tipo__exact=ALERTA'):
            respuesta = self.client.get(f'{self.url}?{filtro}')
            self.assertContains(respuesta, 'Revisar algo')
            self.assertNotContains(respuesta, 'Resuelto viejo')

    def test_resolver(self):
        self._accion('resolver')
        self.pend.refresh_from_db()
        self.assertEqual((self.pend.estado, self.pend.resuelto_por), ('RESUELTO', self.usuario))

    def test_descartar_pide_confirmacion_y_no_borra(self):
        self._accion('descartar')
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, 'ABIERTO')
        self._accion('descartar', confirmar='si')
        self.pend.refresh_from_db()
        self.assertEqual(self.pend.estado, 'DESCARTADO')
        self.assertTrue(Pendiente.objects.filter(pk=self.pend.pk).exists())

    def test_tomar(self):
        self._accion('tomar')
        self.pend.refresh_from_db()
        self.assertEqual((self.pend.rol, self.pend.tomado_por_direccion), ('DIRECCION', True))

