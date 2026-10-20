"""Directorio de responsables (Issue #421): resolución de números, confirmación
«ACEPTO», avisos por rol y admin."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Pago
from comunicacion import services_agente, services_notificaciones
from comunicacion import services_responsables as sr
from comunicacion.models import CambioResponsable, Responsable
from core_erp.test_utils import login_superuser_con_totp

from .test_agente import AGENTE, _cliente_falso, _respuesta, _texto, _uso
from .utils import TEL_CLIENTE, TEL_NEGOCIO, wa_settings

TEL_A = '525555550011'
TEL_B = '525555550012'
TEL_C = '525555550013'


def _resp(rol, telefono, **kw):
    return Responsable.objects.create(rol=rol, nombre=f'{rol} test', telefono_wa=telefono, **kw)


class ServicioTest(TestCase):
    def test_sin_responsables_cae_al_numero_del_negocio_solo_para_encargado_y_direccion(self):
        with wa_settings():
            self.assertEqual(sr.telefono_de('ENCARGADO'), TEL_NEGOCIO)
            self.assertEqual(sr.telefono_de('DIRECCION'), TEL_NEGOCIO)
            self.assertEqual(sr.telefono_de('CONTADOR'), '')
            self.assertEqual(sr.telefonos_aviso('ENCARGADO'), [TEL_NEGOCIO])

    def test_con_responsables_manda_el_directorio(self):
        _resp('ENCARGADO', TEL_A)
        with wa_settings():
            self.assertEqual(sr.telefono_de('ENCARGADO'), TEL_A)
            self.assertEqual(sr.telefono_de('DIRECCION'), TEL_NEGOCIO)

    def test_direccion_en_vivo_recibe_copia_y_off_no(self):
        _resp('ENCARGADO', TEL_A)
        direccion = _resp('DIRECCION', TEL_B)
        self.assertEqual(sr.telefonos_aviso('ENCARGADO'), [TEL_A, TEL_B])
        self.assertEqual(sr.telefonos_aviso('DIRECCION'), [TEL_B])
        direccion.modo_copia = 'OFF'
        direccion.save()
        self.assertEqual(sr.telefonos_aviso('ENCARGADO'), [TEL_A])

    def test_sin_repetidos(self):
        _resp('ENCARGADO', TEL_A)
        _resp('DIRECCION', TEL_A)
        self.assertEqual(sr.telefonos_aviso('ENCARGADO'), [TEL_A])

    def test_numero_pendiente_no_cuenta_como_confirmado(self):
        Responsable.objects.create(rol='ENCARGADO', nombre='Nuevo', telefono_pendiente=TEL_A)
        with wa_settings():
            self.assertEqual(sr.telefono_de('ENCARGADO'), TEL_NEGOCIO)

    def test_suplente_con_rango_vigente_gana_y_fuera_del_rango_no(self):
        hoy = timezone.localdate()
        _resp('ENCARGADO', TEL_A)
        suplente = _resp('ENCARGADO', TEL_B, vigente_desde=hoy - timedelta(days=1),
                         vigente_hasta=hoy + timedelta(days=1))
        self.assertEqual(sr.telefono_de('ENCARGADO'), TEL_B)
        suplente.vigente_desde = hoy + timedelta(days=2)
        suplente.vigente_hasta = hoy + timedelta(days=5)
        suplente.save()
        self.assertEqual(sr.telefono_de('ENCARGADO'), TEL_A)
        suplente.vigente_desde = hoy - timedelta(days=5)
        suplente.vigente_hasta = hoy - timedelta(days=2)
        suplente.save()
        self.assertEqual(sr.telefono_de('ENCARGADO'), TEL_A)

    def test_inactivo_no_cuenta(self):
        _resp('ENCARGADO', TEL_A, activo=False)
        with wa_settings():
            self.assertEqual(sr.telefono_de('ENCARGADO'), TEL_NEGOCIO)

    def test_rol_de_telefono_incluye_pendientes_y_el_respaldo(self):
        _resp('CONTADOR', TEL_A)
        Responsable.objects.create(rol='COMPRAS', nombre='C', telefono_pendiente=TEL_B)
        with wa_settings():
            self.assertEqual(sr.rol_de_telefono(TEL_A), 'CONTADOR')
            self.assertEqual(sr.rol_de_telefono(TEL_B), 'COMPRAS')
            self.assertEqual(sr.rol_de_telefono(TEL_NEGOCIO), 'ENCARGADO')
            self.assertIsNone(sr.rol_de_telefono(TEL_CLIENTE))
            self.assertIsNone(sr.rol_de_telefono(''))


@wa_settings()
class AvisosPorRolTest(TestCase):
    def setUp(self):
        mail.outbox = []
        cliente = Cliente.objects.create(nombre='Perla García', telefono=TEL_CLIENTE)
        self.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Pasadía', fecha_evento=date.today() + timedelta(days=3),
            num_personas=20, precio_final=Decimal('1500.00'))
        Cotizacion.objects.filter(pk=self.cot.pk).update(precio_final=Decimal('1500.00'))
        self.cot.refresh_from_db()

    def _destinos(self, funcion, *args):
        with patch.object(services_notificaciones, 'enviar_whatsapp') as envio:
            funcion(*args)
        return [c.kwargs['telefono'] for c in envio.call_args_list], envio

    def test_sin_responsables_todo_sigue_al_numero_del_negocio(self):
        destinos, _ = self._destinos(services_notificaciones.alertar_equipo_nueva_cotizacion, self.cot)
        self.assertEqual(destinos, [TEL_NEGOCIO])
        pago = Pago.objects.create(cotizacion=self.cot, monto=Decimal('100.00'), metodo='TRANSFERENCIA',
                                   tipo='INGRESO')
        destinos, _ = self._destinos(services_notificaciones.alertar_equipo_pago, pago)
        self.assertEqual(destinos, [TEL_NEGOCIO])

    def test_cotizacion_nueva_avisa_a_encargado_y_direccion_y_con_off_solo_al_encargado(self):
        _resp('ENCARGADO', TEL_A)
        direccion = _resp('DIRECCION', TEL_B)
        destinos, envio = self._destinos(services_notificaciones.alertar_equipo_nueva_cotizacion, self.cot)
        self.assertEqual(destinos, [TEL_A, TEL_B])
        claves = [c.kwargs['clave_idempotencia'] for c in envio.call_args_list]
        self.assertEqual(len(set(claves)), 2)  # la copia no choca con el original
        direccion.modo_copia = 'OFF'
        direccion.save()
        self.cot.pk = None
        self.cot.save()
        destinos, _ = self._destinos(services_notificaciones.alertar_equipo_nueva_cotizacion, self.cot)
        self.assertEqual(destinos, [TEL_A])

    def test_pago_acreditado_avisa_a_encargado_y_direccion(self):
        _resp('ENCARGADO', TEL_A)
        _resp('DIRECCION', TEL_B)
        pago = Pago.objects.create(cotizacion=self.cot, monto=Decimal('100.00'), metodo='TRANSFERENCIA',
                                   tipo='INGRESO')
        destinos, _ = self._destinos(services_notificaciones.alertar_equipo_pago, pago)
        self.assertEqual(destinos, [TEL_A, TEL_B])

    def test_contracargo_avisa_solo_a_direccion(self):
        _resp('ENCARGADO', TEL_A)
        _resp('DIRECCION', TEL_B)
        contracargo = SimpleNamespace(
            cotizacion=self.cot, monto=Decimal('100.00'), estado='EN_DISPUTA', pk=1, openpay_id='tr1',
            fecha_limite_evidencia=None, requiere_vinculacion_manual=False, event_type='chargeback.created',
            get_estado_display=lambda: 'En disputa')
        destinos, _ = self._destinos(services_notificaciones.alertar_equipo_contracargo, contracargo)
        self.assertEqual(destinos, [TEL_B])


@wa_settings(**AGENTE)
class AgenteYAceptoTest(TestCase):
    def _procesar(self, conv, cliente_ia=None):
        with patch.object(services_agente, 'enviar_whatsapp', return_value=SimpleNamespace(proveedor_id='')) as envio, \
                patch.object(services_agente, '_cliente_ia', return_value=cliente_ia):
            services_agente.procesar_conversacion(conv.pk)
        return envio

    def test_el_agente_no_le_contesta_a_ningun_numero_del_directorio(self):
        _resp('CONTADOR', TEL_A)
        Responsable.objects.create(rol='COMPRAS', nombre='C', telefono_pendiente=TEL_B)
        for tel in (TEL_A, TEL_B):
            conv = services_agente.recibir_mensaje(telefono=tel, nombre='X', texto='Hola', wamid=f'w{tel}')
            cliente = _cliente_falso()
            self._procesar(conv, cliente).assert_not_called()
            cliente.beta.messages.create.assert_not_called()

    def test_acepto_desde_el_numero_pendiente_lo_activa_y_contesta_texto_fijo(self):
        resp = _resp('ENCARGADO', TEL_A, telefono_pendiente=TEL_C)
        conv = services_agente.recibir_mensaje(telefono=TEL_C, nombre='N', texto=' Acepto. ', wamid='w1')
        envio = self._procesar(conv)
        resp.refresh_from_db()
        self.assertEqual((resp.telefono_wa, resp.telefono_pendiente), (TEL_C, ''))
        self.assertEqual(envio.call_args.kwargs['mensaje'], sr.CONFIRMACION_ACEPTO)
        cambio = CambioResponsable.objects.get(responsable=resp, campo='telefono_wa')
        self.assertEqual((cambio.antes, cambio.despues), (TEL_A, TEL_C))

    def test_acepto_desde_un_numero_no_pendiente_sigue_el_flujo_normal(self):
        resp = _resp('ENCARGADO', TEL_A, telefono_pendiente=TEL_C)
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='ACEPTO', wamid='w2')
        cliente = _cliente_falso(_respuesta(_texto('¿Acepto qué? Cuéntame.')))
        envio = self._procesar(conv, cliente)
        resp.refresh_from_db()
        self.assertEqual((resp.telefono_wa, resp.telefono_pendiente), (TEL_A, TEL_C))
        self.assertEqual(envio.call_args.kwargs['mensaje'], '¿Acepto qué? Cuéntame.')
        self.assertFalse(CambioResponsable.objects.exists())

    def test_pase_a_humano_avisa_a_encargado_y_direccion(self):
        _resp('ENCARGADO', TEL_A)
        _resp('DIRECCION', TEL_B)
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Queja', wamid='w3')
        cliente = _cliente_falso(
            _respuesta(_uso('pasar_a_humano', {'motivo': 'Queja'}), stop='tool_use'),
            _respuesta(_texto('Te comunico con alguien.')))
        with wa_settings(**AGENTE, WA_TEMPLATE_OPERACIONES='aviso_operaciones'), \
                patch.object(services_agente, 'enviar_whatsapp_template') as plantilla:
            self._procesar(conv, cliente)
        self.assertEqual([c.kwargs['telefono'] for c in plantilla.call_args_list], [TEL_A, TEL_B])

    def test_proponer_telefono_pide_acepto_y_no_activa(self):
        resp = _resp('ENCARGADO', TEL_A)
        with wa_settings(WA_TEMPLATE_OPERACIONES='aviso_operaciones'), \
                patch.object(sr, 'enviar_whatsapp_template') as plantilla:
            self.assertTrue(sr.proponer_telefono(resp, TEL_C))
        resp.refresh_from_db()
        self.assertEqual((resp.telefono_wa, resp.telefono_pendiente), (TEL_A, TEL_C))
        kwargs = plantilla.call_args.kwargs
        self.assertEqual(kwargs['telefono'], TEL_C)
        self.assertEqual(kwargs['parametros'], [
            "Este número recibirá los avisos de Encargado de Quinta Ko'ox Tanil. Responde ACEPTO para activarlo."])
        self.assertEqual(sr.telefono_de('ENCARGADO'), TEL_A)  # mientras tanto sigue el anterior


class AdminResponsableTest(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('dir', 'd@example.com', 'x' * 20)
        login_superuser_con_totp(self.client, self.admin)

    def _datos(self, **extra):
        return {'rol': 'ENCARGADO', 'nombre': 'Juan', 'telefono': '555 555 0011', 'email': '', 'modo_copia': 'EN_VIVO',
                'activo': 'on', 'vigente_desde': '', 'vigente_hasta': '',
                'cambios-TOTAL_FORMS': '0', 'cambios-INITIAL_FORMS': '0', **extra}

    def test_alta_deja_el_numero_pendiente_y_bitacora(self):
        with patch.object(sr, 'enviar_whatsapp') as envio:
            r = self.client.post(reverse('admin:comunicacion_responsable_add'), self._datos())
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['adminform'].form.errors)
        resp = Responsable.objects.get()
        self.assertEqual((resp.telefono_wa, resp.telefono_pendiente), ('', TEL_A))
        self.assertEqual(resp.created_by, self.admin)
        self.assertTrue(CambioResponsable.objects.filter(responsable=resp, campo='alta').exists())
        envio.assert_called_once()

    def test_cambiar_el_numero_no_cambia_el_activo_y_queda_en_bitacora(self):
        resp = _resp('ENCARGADO', TEL_A)
        with patch.object(sr, 'enviar_whatsapp'):
            r = self.client.post(reverse('admin:comunicacion_responsable_change', args=[resp.pk]),
                                 self._datos(nombre='Juan Pérez', telefono=TEL_C))
        self.assertEqual(r.status_code, 302)
        resp.refresh_from_db()
        self.assertEqual((resp.telefono_wa, resp.telefono_pendiente, resp.nombre), (TEL_A, TEL_C, 'Juan Pérez'))
        campos = set(CambioResponsable.objects.filter(responsable=resp).values_list('campo', flat=True))
        self.assertEqual(campos, {'nombre', 'telefono_pendiente'})

    def test_no_se_borra_y_otros_staff_no_lo_ven(self):
        resp = _resp('ENCARGADO', TEL_A)
        self.assertEqual(self.client.get(reverse('admin:comunicacion_responsable_delete', args=[resp.pk])).status_code, 403)
        staff = get_user_model().objects.create_user('staff', 's@example.com', 'x' * 20, is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('admin:comunicacion_responsable_changelist')).status_code, 403)
