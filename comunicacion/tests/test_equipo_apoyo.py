"""Equipo de apoyo temporal (rol APOYO del directorio): quién recibe qué, sus
botones y el parte del día."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Pago
from comunicacion import services_apoyo, services_compras, services_notificaciones, services_pendientes
from comunicacion import services_responsables as sr
from comunicacion.admin import ResponsableForm
from comunicacion.models import Pendiente, Responsable, Visita
from core_erp.test_utils import login_superuser_con_totp

from .utils import TEL_CLIENTE, wa_settings

TEL_ENC = '525555550021'
TEL_DIR = '525555550022'
TEL_IRVING = '525555550023'
TEL_HERMANA = '525555550024'
TEL_CESAR = '525555550025'
TEL_ARCADIO = '525555550026'

HOY = date(2026, 12, 5)


def _apoyo(nombre, telefono, desde=HOY - timedelta(days=1), hasta=HOY + timedelta(days=10), **temas):
    return Responsable.objects.create(rol='APOYO', nombre=nombre, telefono_wa=telefono,
                                      vigente_desde=desde, vigente_hasta=hasta, **temas)


def _equipo():
    Responsable.objects.create(rol='ENCARGADO', nombre='Hermano', telefono_wa=TEL_ENC)
    Responsable.objects.create(rol='DIRECCION', nombre='Dirección', telefono_wa=TEL_DIR)
    _apoyo('Irving', TEL_IRVING, avisa_eventos=True, avisa_operacion=True, avisa_compras=True)
    _apoyo('Hermana', TEL_HERMANA, avisa_eventos=True, avisa_pagos=True)
    _apoyo('César', TEL_CESAR, avisa_eventos=True, avisa_llegadas=True)
    _apoyo('Arcadio', TEL_ARCADIO, avisa_eventos=True)


class VigenciaTest(TestCase):
    def test_solo_cuenta_dentro_del_rango_y_con_numero_confirmado(self):
        _apoyo('Irving', TEL_IRVING, avisa_pagos=True)
        _apoyo('Vencido', TEL_CESAR, desde=HOY - timedelta(days=9), hasta=HOY - timedelta(days=1), avisa_pagos=True)
        Responsable.objects.create(rol='APOYO', nombre='Sin fechas', telefono_wa=TEL_ARCADIO, avisa_pagos=True)
        Responsable.objects.create(rol='APOYO', nombre='Pendiente', telefono_pendiente=TEL_HERMANA,
                                   vigente_desde=HOY, vigente_hasta=HOY, avisa_pagos=True)
        self.assertEqual(sr.telefonos_apoyo('PAGOS', HOY), [TEL_IRVING])
        self.assertEqual(sr.telefonos_apoyo('LLEGADAS', HOY), [])

    def test_el_apoyo_no_cambia_a_quien_le_llega_cada_rol(self):
        _equipo()
        with patch('django.utils.timezone.localdate', return_value=HOY):
            self.assertEqual(sr.telefonos_aviso('ENCARGADO'), [TEL_ENC, TEL_DIR])
            self.assertEqual(sr.telefono_de('DIRECCION'), TEL_DIR)
        self.assertEqual(sr.rol_de_telefono(TEL_IRVING), 'APOYO')

    def test_el_formulario_exige_fechas_y_un_tema(self):
        form = ResponsableForm(data={'rol': 'APOYO', 'nombre': 'X', 'telefono': '9995550000', 'modo_copia': 'OFF',
                                     'activo': True})
        self.assertFalse(form.is_valid())
        self.assertIn('vigente_hasta', form.errors)
        self.assertIn('avisa_eventos', form.errors)


@wa_settings()
class PendientesTest(TestCase):
    def setUp(self):
        _equipo()
        patcher = patch('django.utils.timezone.localdate', return_value=HOY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _registrar(self, **kw):
        with patch.object(services_pendientes, '_enviar_aviso') as envio:
            pendiente = services_pendientes.registrar_pendiente(severidad='URGENTE', **kw)
        return pendiente, {c.args[0]: c.kwargs.get('botones') for c in envio.call_args_list}

    def test_llegada_llega_a_cesar_con_boton_y_a_irving_como_copia_sin_boton(self):
        pendiente, envios = self._registrar(titulo='Luis llegó', rol='ENCARGADO', tipo='LLEGADA',
                                            clave_idempotencia='lleg:1')
        self.assertEqual(set(envios), {TEL_ENC, TEL_DIR, TEL_CESAR, TEL_IRVING})
        self.assertEqual(envios[TEL_CESAR][0][0], f'pend:{pendiente.pk}:ok')
        self.assertIsNone(envios[TEL_IRVING])
        self.assertNotIn(TEL_HERMANA, envios)
        self.assertNotIn(TEL_ARCADIO, envios)

    def test_pendiente_de_direccion_no_llega_al_apoyo(self):
        _, envios = self._registrar(titulo='Contracargo', rol='DIRECCION', tipo='CONTRACARGO')
        self.assertEqual(set(envios), {TEL_DIR})

    def test_cesar_marca_atendida_su_llegada_pero_no_otra_cosa(self):
        llegada, _ = self._registrar(titulo='Luis llegó', rol='ENCARGADO', tipo='LLEGADA')
        queja, _ = self._registrar(titulo='Queja', rol='ENCARGADO', tipo='QUEJA')
        self.assertIsNone(services_pendientes.atender_boton(TEL_CESAR, f'pend:{queja.pk}:ok'))
        self.assertIsNone(services_pendientes.atender_boton(TEL_IRVING, f'pend:{llegada.pk}:ok'))
        self.assertIsNone(services_pendientes.atender_boton(TEL_CESAR, f'pend:{llegada.pk}:tomar'))
        self.assertIn('atendido', services_pendientes.atender_boton(TEL_CESAR, f'pend:{llegada.pk}:ok'))
        llegada.refresh_from_db()
        queja.refresh_from_db()
        self.assertEqual((llegada.estado, queja.estado), ('RESUELTO', 'ABIERTO'))
        self.assertIn('Atendido por César', llegada.detalle)

    def test_fuera_de_vigencia_el_boton_ya_no_vale(self):
        llegada, _ = self._registrar(titulo='Luis llegó', rol='ENCARGADO', tipo='LLEGADA')
        with patch('django.utils.timezone.localdate', return_value=HOY + timedelta(days=30)):
            self.assertIsNone(services_pendientes.atender_boton(TEL_CESAR, f'pend:{llegada.pk}:ok'))


@wa_settings()
class PagosYComprasTest(TestCase):
    def setUp(self):
        _equipo()
        patcher = patch('django.utils.timezone.localdate', return_value=HOY)
        patcher.start()
        self.addCleanup(patcher.stop)
        cliente = Cliente.objects.create(nombre='Perla García', telefono=TEL_CLIENTE)
        self.cot = Cotizacion.objects.create(cliente=cliente, nombre_evento='Boda', fecha_evento=HOY + timedelta(2),
                                             num_personas=80, tipo_servicio='EVENTO')
        Cotizacion.objects.filter(pk=self.cot.pk).update(precio_final=Decimal('1500.00'))
        self.cot.refresh_from_db()

    def test_pago_acreditado_llega_tambien_a_quien_tiene_pagos(self):
        pago = Pago.objects.create(cotizacion=self.cot, monto=Decimal('100.00'), metodo='TRANSFERENCIA',
                                   tipo='INGRESO')
        with patch.object(services_notificaciones, 'enviar_whatsapp') as envio:
            services_notificaciones.alertar_equipo_pago(pago)
        destinos = [c.kwargs['telefono'] for c in envio.call_args_list]
        self.assertEqual(destinos, [TEL_ENC, TEL_DIR, TEL_HERMANA])

    def test_lista_de_compras_llega_a_irving_con_boton(self):
        contexto = {'lista': {'Licores': [{'item': 'Ron'}]}, 'total_estimado': Decimal('0'), 'pendientes': 0}
        with patch('comercial.views.contexto_lista_compras', return_value=contexto), \
                patch('comunicacion.services_compras._enviar_aviso') as envio, \
                patch('comunicacion.services_compras._enviar_pdf'):
            pendiente = services_compras.enviar_lista(self.cot)
        destinos = {c.args[0]: c.kwargs['botones'] for c in envio.call_args_list}
        self.assertEqual(set(destinos), {TEL_ENC, TEL_IRVING})
        self.assertEqual(destinos[TEL_IRVING][0][0], f'pend:{pendiente.pk}:ok')
        self.assertIn('atendido', services_pendientes.atender_boton(TEL_IRVING, f'pend:{pendiente.pk}:ok'))


@wa_settings()
class ParteDelDiaTest(TestCase):
    def setUp(self):
        cliente = Cliente.objects.create(nombre='Perla García', telefono='9995550001')
        self.hoy = Cotizacion.objects.create(cliente=cliente, nombre_evento='Boda', fecha_evento=HOY,
                                             num_personas=80, tipo_servicio='EVENTO',
                                             hora_inicio=time(15, 0), hora_fin=time(21, 0))
        self.manana = Cotizacion.objects.create(cliente=cliente, nombre_evento='Pasadía',
                                                fecha_evento=HOY + timedelta(days=1), num_personas=20,
                                                tipo_servicio='PASADIA')
        self.lejos = Cotizacion.objects.create(cliente=cliente, nombre_evento='XV', fecha_evento=HOY + timedelta(9),
                                               num_personas=50)
        Cotizacion.objects.filter(pk__in=[self.hoy.pk, self.manana.pk, self.lejos.pk]).update(
            estado='CONFIRMADA', precio_final=Decimal('10000.00'))
        Visita.objects.create(nombre='Ana', telefono='529995550009', estado='CONFIRMADA',
                              inicio=timezone.make_aware(datetime.combine(HOY, time(12, 0))))

    def _enviar(self):
        with patch.object(services_apoyo, '_enviar_aviso') as envio:
            enviados = services_apoyo.enviar_parte_del_dia(HOY)
        return enviados, {c.args[0]: '\n'.join(c.args[1].lineas) for c in envio.call_args_list}

    def test_sin_equipo_de_apoyo_no_manda_nada(self):
        Responsable.objects.create(rol='ENCARGADO', nombre='Hermano', telefono_wa=TEL_ENC)
        self.assertEqual(self._enviar()[1], {})

    def test_cada_quien_recibe_solo_lo_suyo(self):
        _equipo()
        _, textos = self._enviar()
        self.assertEqual(set(textos), {TEL_ENC, TEL_DIR, TEL_IRVING, TEL_HERMANA, TEL_CESAR, TEL_ARCADIO})
        for texto in textos.values():
            self.assertIn(f'Hoy: COT-{self.hoy.pk:03d}', texto)
            self.assertIn(f'Mañana: COT-{self.manana.pk:03d}', texto)
            self.assertNotIn(f'COT-{self.lejos.pk:03d}', texto)
        self.assertIn('3:00 p.m.', textos[TEL_ARCADIO])
        self.assertNotIn('Perla', textos[TEL_ARCADIO])
        self.assertNotIn('saldo', textos[TEL_ARCADIO])
        self.assertNotIn('Visita', textos[TEL_ARCADIO])
        self.assertIn('saldo por cobrar $10,000.00', textos[TEL_HERMANA])
        self.assertNotIn('Visita', textos[TEL_HERMANA])
        self.assertIn('Perla', textos[TEL_CESAR])
        self.assertIn('Visita hoy', textos[TEL_CESAR])
        self.assertNotIn('saldo', textos[TEL_CESAR])
        self.assertIn('saldo por cobrar', textos[TEL_ENC])

    def test_lo_agenda_el_programador(self):
        from core_erp.programador import TAREAS
        self.assertIn('enviar_parte_del_dia', {t.comando for t in TAREAS})


class AdminTest(TestCase):
    def test_alta_de_apoyo_desde_el_admin(self):
        from django.contrib.auth import get_user_model
        usuario = get_user_model().objects.create_superuser('dir', 'dir@x.mx', 'x')
        login_superuser_con_totp(self.client, usuario)
        self.assertEqual(self.client.get(reverse('admin:comunicacion_responsable_add')).status_code, 200)
        with patch.object(sr, '_pedir_acepto'):
            r = self.client.post(reverse('admin:comunicacion_responsable_add'), {
                'rol': 'APOYO', 'nombre': 'Irving', 'telefono': '9995550023', 'email': '', 'modo_copia': 'OFF',
                'activo': 'on', 'vigente_desde': '2026-12-01', 'vigente_hasta': '2026-12-15',
                'avisa_eventos': 'on', 'avisa_compras': 'on',
                'cambios-TOTAL_FORMS': '0', 'cambios-INITIAL_FORMS': '0'})
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context.get('errors'))
        resp = Responsable.objects.get(nombre='Irving')
        self.assertEqual(resp.telefono_pendiente, '529995550023')
        self.assertEqual(self.client.get(reverse('admin:comunicacion_responsable_changelist')).status_code, 200)
