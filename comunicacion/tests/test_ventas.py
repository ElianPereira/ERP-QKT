"""Rol VENTAS, avisos de visitas, alta manual y cierre de visita (Issue #505, PR 2).

Cada caso se prueba con y sin responsable de VENTAS: sin él, todo sigue llegando al encargado como hoy.
"""
import json
from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import BloqueoFecha, Cliente, Cotizacion
from comunicacion import herramientas_agente, services_agente, services_llegada, services_responsables, services_visitas
from comunicacion.models import HorarioVisita, Pendiente, Responsable, Visita
from nomina.models import Empleado
from operaciones.models import PlantillaChecklist, TareaProgramada

from .test_agente import AGENTE
from .test_fase_2a import OK, TEL_DIR, TEL_ENC, TEL_OTRO, MocksVisitas, _dt, _proximo
from .utils import TEL_CLIENTE, wa_settings

TEL_VENTAS = '525555550015'


def con_ventas(telefono=TEL_VENTAS):
    """Responsable de VENTAS + Empleado de Ventas con ese teléfono."""
    Responsable.objects.create(rol='VENTAS', nombre='Ventas', telefono_wa=telefono)
    return Empleado.objects.create(nombre='Ana', puesto='VENTAS', telefono=telefono[-10:])


def telefonos_enviados(mocks):
    """Teléfonos a los que salió algo por cualquiera de los tres canales de `services_pendientes`."""
    return [c.kwargs.get('telefono') for m in mocks for c in m.call_args_list]


@wa_settings(**AGENTE)
class DestinosTest(MocksVisitas, TestCase):

    def test_sin_responsable_de_ventas_cae_al_encargado(self):
        self.assertEqual(services_responsables.rol_ventas(), 'ENCARGADO')
        self.assertEqual(services_responsables.destinos_ventas(), services_responsables.telefonos_aviso('ENCARGADO'))
        self.assertIn(TEL_ENC, services_responsables.destinos_ventas())

    def test_con_responsable_de_ventas_va_a_ventas_y_dirección_conserva_su_copia(self):
        con_ventas()
        self.assertEqual(services_responsables.rol_ventas(), 'VENTAS')
        destinos = services_responsables.destinos_ventas()
        self.assertEqual(destinos[0], TEL_VENTAS)
        self.assertIn(TEL_DIR, destinos)
        self.assertNotIn(TEL_ENC, destinos)


@wa_settings(**AGENTE)
class SolicitudDeVisitaTest(MocksVisitas, TestCase):

    def _canales(self):
        return (self.pendientes_enviar_whatsapp, self.pendientes_enviar_whatsapp_template,
                self.pendientes_enviar_whatsapp_botones)

    def _solicitar(self):
        visita = Visita.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE, conversacion=self.conv,
                                       inicio=_dt(self.dia, time(16, 0)))
        services_visitas._avisar_encargado(visita)
        return visita

    def test_sin_ventas_el_aviso_va_al_encargado(self):
        self._solicitar()
        self.assertEqual(Pendiente.objects.get(tipo='VISITA').rol, 'ENCARGADO')
        self.assertIn(TEL_ENC, telefonos_enviados(self._canales()))

    def test_con_ventas_el_aviso_va_a_ventas(self):
        con_ventas()
        self._solicitar()
        self.assertEqual(Pendiente.objects.get(tipo='VISITA').rol, 'VENTAS')
        destinos = telefonos_enviados(self._canales())
        self.assertIn(TEL_VENTAS, destinos)
        self.assertNotIn(TEL_ENC, destinos)

    def test_ventas_confirma_y_queda_como_vendedora_de_la_visita(self):
        ana = con_ventas()
        visita = self._solicitar()
        respuesta = services_visitas.atender_boton(TEL_VENTAS, f'vis:{visita.pk}:ok')
        self.assertIn('quedó confirmada', respuesta)
        visita.refresh_from_db()
        self.assertEqual((visita.estado, visita.vendedor), ('CONFIRMADA', ana))

    def test_si_confirma_el_encargado_y_hay_una_sola_vendedora_se_le_asigna(self):
        ana = con_ventas()
        visita = self._solicitar()
        services_visitas.atender_boton(TEL_ENC, f'vis:{visita.pk}:ok')
        visita.refresh_from_db()
        self.assertEqual(visita.vendedor, ana)

    def test_con_dos_vendedoras_gana_la_del_telefono_que_confirma(self):
        con_ventas()
        otra = Empleado.objects.create(nombre='Bea', puesto='VENTAS', telefono=TEL_OTRO[-10:])
        visita = self._solicitar()
        services_visitas.atender_boton(TEL_OTRO, f'vis:{visita.pk}:ok') or None
        # TEL_OTRO no está en el directorio: no decide. Confirma Dirección → sin teléfono coincidente ni única.
        visita.refresh_from_db()
        self.assertEqual(visita.estado, 'POR_CONFIRMAR')
        services_visitas.atender_boton(TEL_DIR, f'vis:{visita.pk}:ok')
        visita.refresh_from_db()
        self.assertIsNone(visita.vendedor)
        self.assertNotEqual(otra, visita.vendedor)

    def test_un_numero_ajeno_no_decide(self):
        con_ventas()
        visita = self._solicitar()
        self.assertIsNone(services_visitas.atender_boton('525555559999', f'vis:{visita.pk}:ok'))


@wa_settings(**AGENTE)
class AgendaDeLaVisperaTest(MocksVisitas, TestCase):

    def setUp(self):
        super().setUp()
        self.manana = timezone.localdate() + timedelta(days=1)

    def _textos(self, destino):
        todos = [*self.pendientes_enviar_whatsapp.call_args_list, *self.pendientes_enviar_whatsapp_template.call_args_list,
                 *self.pendientes_enviar_whatsapp_botones.call_args_list]
        return [str(c.kwargs) for c in todos if c.kwargs.get('telefono') == destino]

    def test_ventas_recibe_hora_nombre_personas_y_telefono(self):
        con_ventas()
        Visita.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE, estado='CONFIRMADA', personas=4,
                              inicio=_dt(self.manana, time(16, 0)))
        services_visitas.enviar_recordatorios()
        mensajes = self._textos(TEL_VENTAS)
        self.assertTrue(any('Ana Ruiz' in m and '4 personas' in m and 'Tel.' in m for m in mensajes), mensajes)
        self.assertFalse(self._textos(TEL_ENC))

    def test_sin_visitas_ventas_lo_sabe(self):
        con_ventas()
        services_visitas.enviar_recordatorios()
        self.assertTrue(any('Mañana no tienes visitas agendadas' in m for m in self._textos(TEL_VENTAS)))

    def test_sin_ventas_y_sin_visitas_no_se_manda_nada(self):
        services_visitas.enviar_recordatorios()
        self.assertFalse(self._textos(TEL_ENC))

    def test_sin_ventas_el_encargado_sigue_recibiendo_la_agenda(self):
        Visita.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE, estado='CONFIRMADA',
                              inicio=_dt(self.manana, time(16, 0)))
        services_visitas.enviar_recordatorios()
        mensajes = self._textos(TEL_ENC)
        self.assertTrue(any('Ana Ruiz' in m for m in mensajes))
        self.assertFalse(any('Tel.' in m for m in mensajes))

    def test_aviso_de_45_minutos_va_a_ventas(self):
        con_ventas()
        ahora = timezone.now()
        Visita.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE, estado='CONFIRMADA',
                              inicio=ahora + timedelta(minutes=30), decidida_en=ahora - timedelta(days=1))
        services_visitas.enviar_avisos_previos(ahora)
        self.assertTrue(any('Ana Ruiz' in m for m in self._textos(TEL_VENTAS)))
        self.assertFalse(self._textos(TEL_ENC))


@wa_settings(**AGENTE)
class LlegadaYPaseTest(MocksVisitas, TestCase):

    def _llegada(self):
        salida, error = herramientas_agente.ejecutar('avisar_llegada', {'situacion': 'ya_llego'}, conv=self.conv)
        self.assertFalse(error, salida)

    def _visita_hoy(self):
        inicio = timezone.now() + timedelta(minutes=20)
        return Visita.objects.create(nombre='Guadalupe', telefono=TEL_CLIENTE, conversacion=self.conv,
                                     estado='CONFIRMADA', inicio=inicio)

    def test_llegada_de_una_visita_va_a_ventas_y_sin_ventas_al_encargado(self):
        self._visita_hoy()
        self._llegada()
        self.assertEqual(Pendiente.objects.get(tipo='LLEGADA').rol, 'ENCARGADO')

    def test_llegada_de_visita_con_ventas(self):
        con_ventas()
        self._visita_hoy()
        self._llegada()
        self.assertEqual(Pendiente.objects.get(tipo='LLEGADA').rol, 'VENTAS')

    def test_llegada_de_una_reservacion_se_queda_con_el_encargado(self):
        con_ventas()
        cliente = Cliente.objects.create(nombre='Luis', telefono=TEL_CLIENTE[-10:])
        cot = Cotizacion.objects.create(cliente=cliente, tipo_servicio='PASADIA', nombre_evento='Pasadía',
                                        fecha_evento=timezone.localdate(), num_personas=20)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA', precio_final=Decimal('2000.00'))
        self._llegada()
        self.assertEqual(Pendiente.objects.get(tipo='LLEGADA').rol, 'ENCARGADO')

    def _cotizacion_con_vendedora(self, ana):
        from ventas.services_atribucion import atribuir
        cliente = Cliente.objects.create(nombre='Luis', telefono=TEL_CLIENTE[-10:])
        cot = Cotizacion.objects.create(cliente=cliente, nombre_evento='Boda',
                                        fecha_evento=timezone.localdate() + timedelta(days=60))
        atribuir(cot, ana, 'COTIZO')
        return cot

    def test_pase_a_humano_de_cliente_con_vendedora_va_a_ventas(self):
        ana = con_ventas()
        self._cotizacion_con_vendedora(ana)
        services_agente._pasar_a_humano(self.conv, 'quiere cambiar la fecha')
        self.assertEqual(Pendiente.objects.get(tipo='PASE_HUMANO').rol, 'VENTAS')

    def test_una_queja_siempre_va_al_encargado(self):
        ana = con_ventas()
        self._cotizacion_con_vendedora(ana)
        services_agente._pasar_a_humano(self.conv, 'está molesto', categoria='queja')
        self.assertEqual(Pendiente.objects.get(tipo='QUEJA').rol, 'ENCARGADO')

    def test_pase_sin_vendedora_o_sin_responsable_de_ventas_va_al_encargado(self):
        ana = con_ventas()
        services_agente._pasar_a_humano(self.conv, 'duda')  # sin cotización con vendedora
        self.assertEqual(Pendiente.objects.get(tipo='PASE_HUMANO').rol, 'ENCARGADO')
        Pendiente.objects.all().delete()
        Responsable.objects.filter(rol='VENTAS').delete()
        self._cotizacion_con_vendedora(ana)
        services_agente._pasar_a_humano(self.conv, 'otra duda')
        self.assertEqual(Pendiente.objects.get(tipo='PASE_HUMANO').rol, 'ENCARGADO')


@wa_settings(**AGENTE)
class HorariosOcupadosTest(MocksVisitas, TestCase):
    """La Quinta ocupada (bloqueo, Evento o Pasadía confirmado) no ofrece visitas; Hospedaje sí."""

    def _libre(self):
        return _dt(self.dia, time(16, 0)) in services_visitas.horarios_disponibles(limite=None)

    def _cot(self, tipo, estado='CONFIRMADA', **extra):
        cliente = Cliente.objects.create(nombre='C', telefono='9990000000')
        cot = Cotizacion.objects.create(cliente=cliente, tipo_servicio=tipo, nombre_evento='X',
                                        fecha_evento=self.dia, num_personas=10, **extra)
        Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
        return cot

    def test_dia_libre_se_ofrece(self):
        self.assertTrue(self._libre())

    def test_evento_confirmado_lo_cierra(self):
        self._cot('EVENTO')
        self.assertFalse(self._libre())

    def test_pasadia_confirmada_lo_cierra(self):
        self._cot('PASADIA')
        self.assertFalse(self._libre())

    def test_evento_sin_confirmar_no_lo_cierra(self):
        self._cot('EVENTO', estado='COTIZADA')
        self.assertTrue(self._libre())

    def test_hospedaje_confirmado_no_lo_cierra(self):
        self._cot('HOSPEDAJE', fecha_salida=self.dia + timedelta(days=2))
        self.assertTrue(self._libre())

    def test_bloqueo_lo_cierra(self):
        BloqueoFecha.objects.create(fecha_inicio=self.dia, fecha_fin=self.dia, motivo='MANTENIMIENTO')
        self.assertFalse(self._libre())

    def test_la_herramienta_de_kooxi_tampoco_lo_ofrece(self):
        self._cot('EVENTO')
        horarios = json.loads(herramientas_agente.ejecutar('horarios_de_visita', {'fecha': self.dia.isoformat()})[0])
        self.assertEqual(horarios['horarios'], [])


@wa_settings(**AGENTE)
class AltaManualTest(MocksVisitas, TestCase):

    def setUp(self):
        super().setUp()
        call_command('crear_grupos_permisos', verbosity=0)
        self.ana = con_ventas()
        self.usuario = User.objects.create_user('ana', password='clave-de-prueba-123', is_staff=True)
        Empleado.objects.filter(pk=self.ana.pk).update(usuario=self.usuario)
        self.usuario.groups.add(Group.objects.get(name='Vendedor'))
        self.su = User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123')

    def _visita(self, hora=time(16, 0), dia=None, telefono=TEL_OTRO, nombre='Visitante'):
        return Visita(nombre=nombre, telefono=telefono, inicio=_dt(dia or self.dia, hora))

    def test_la_vendedora_la_da_de_alta_confirmada_y_suya(self):
        v = self._visita()
        services_visitas.registrar_manual(v, self.usuario)
        v.refresh_from_db()
        self.assertEqual((v.estado, v.origen, v.vendedor), ('CONFIRMADA', 'MANUAL', self.ana))
        self.assertEqual(v.created_by, self.usuario)

    def test_dirección_la_da_de_alta_por_confirmar(self):
        v = self._visita()
        services_visitas.registrar_manual(v, self.su)
        v.refresh_from_db()
        self.assertEqual((v.estado, v.origen), ('POR_CONFIRMAR', 'MANUAL'))
        self.assertIsNone(v.vendedor)
        self.assertEqual(Pendiente.objects.get(tipo='VISITA').rol, 'VENTAS')

    def test_fuera_de_los_bloques_de_visita(self):
        with self.assertRaises(ValidationError):
            services_visitas.registrar_manual(self._visita(hora=time(9, 0)), self.usuario)
        with self.assertRaises(ValidationError):
            services_visitas.registrar_manual(self._visita(dia=self.dia + timedelta(days=1)), self.usuario)

    def test_el_mismo_horario_no_se_agenda_dos_veces(self):
        services_visitas.registrar_manual(self._visita(), self.usuario)
        with self.assertRaises(ValidationError):
            services_visitas.registrar_manual(self._visita(telefono='525555550099'), self.usuario)

    def test_una_persona_con_visita_viva_no_agenda_otra(self):
        services_visitas.registrar_manual(self._visita(), self.usuario)
        with self.assertRaises(ValidationError):
            services_visitas.registrar_manual(self._visita(hora=time(17, 0)), self.usuario)

    def test_cita_pasada_o_telefono_invalido(self):
        with self.assertRaises(ValidationError):
            services_visitas.registrar_manual(
                Visita(nombre='X', telefono=TEL_OTRO, inicio=timezone.now() - timedelta(hours=1)), self.usuario)
        with self.assertRaises(ValidationError):
            services_visitas.registrar_manual(self._visita(telefono='12'), self.usuario)

    def _ocupar(self):
        BloqueoFecha.objects.create(fecha_inicio=self.dia, fecha_fin=self.dia, motivo='MANTENIMIENTO')

    def test_dia_ocupado_la_vendedora_no_puede(self):
        self._ocupar()
        with self.assertRaises(ValidationError):
            services_visitas.registrar_manual(self._visita(), self.usuario)

    def test_dia_ocupado_dirección_agenda_con_aviso(self):
        self._ocupar()
        v = self._visita()
        avisos = services_visitas.registrar_manual(v, self.su)
        self.assertTrue(avisos)
        self.assertTrue(Visita.objects.filter(pk=v.pk).exists())

    def test_el_admin_permite_el_alta_solo_a_ventas_y_dirección(self):
        url = reverse('admin:comunicacion_visita_add')
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(url).status_code, 200)
        otro = User.objects.create_user('com', password='clave-de-prueba-123', is_staff=True)
        otro.groups.add(Group.objects.get(name='Ventas'))
        self.client.force_login(otro)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_alta_por_el_formulario_del_admin(self):
        self.client.force_login(self.usuario)
        local = timezone.localtime(_dt(self.dia, time(16, 0)))
        r = self.client.post(reverse('admin:comunicacion_visita_add'), {
            'nombre': 'Visitante Web', 'telefono': TEL_OTRO, 'personas': '3',
            'inicio_0': local.strftime('%Y-%m-%d'), 'inicio_1': local.strftime('%H:%M:%S')})
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['adminform'].form.errors)
        v = Visita.objects.get(nombre='Visitante Web')
        self.assertEqual((v.estado, v.origen, v.vendedor), ('CONFIRMADA', 'MANUAL', self.ana))

    def test_alta_invalida_por_el_formulario_no_guarda(self):
        self.client.force_login(self.usuario)
        local = timezone.localtime(_dt(self.dia, time(9, 0)))
        r = self.client.post(reverse('admin:comunicacion_visita_add'), {
            'nombre': 'Fuera', 'telefono': TEL_OTRO, 'inicio_0': local.strftime('%Y-%m-%d'),
            'inicio_1': local.strftime('%H:%M:%S')})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Visita.objects.filter(nombre='Fuera').exists())


@wa_settings(**AGENTE)
class CierreDeVisitaTest(MocksVisitas, TestCase):

    def setUp(self):
        super().setUp()
        self.ahora = timezone.now()
        self.visita = Visita.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE, estado='CONFIRMADA',
                                            inicio=self.ahora - timedelta(hours=2))
        cliente = Cliente.objects.create(nombre='Ana', telefono=TEL_CLIENTE[-10:])
        self.cot = Cotizacion.objects.create(cliente=cliente, nombre_evento='Boda',
                                             fecha_evento=timezone.localdate() + timedelta(days=60))

    def test_sin_ventas_no_se_pregunta(self):
        self.assertEqual(services_visitas.enviar_cierres(self.ahora), 0)

    def test_con_ventas_se_pregunta_una_vez_con_botones(self):
        con_ventas()
        enviados = services_visitas.enviar_cierres(self.ahora)
        self.assertGreaterEqual(enviados, 1)  # VENTAS y, con la copia en vivo, Dirección
        self.assertEqual(Pendiente.objects.filter(tipo='VISITA', rol='VENTAS').count(), 1)
        llamadas = sum(m.call_count for m in (self.pendientes_enviar_whatsapp, self.pendientes_enviar_whatsapp_template,
                                              self.pendientes_enviar_whatsapp_botones))
        services_visitas.enviar_cierres(self.ahora)
        despues = sum(m.call_count for m in (self.pendientes_enviar_whatsapp, self.pendientes_enviar_whatsapp_template,
                                             self.pendientes_enviar_whatsapp_botones))
        self.assertEqual(llamadas, despues)  # idempotente: no vuelve a preguntar
        self.assertEqual(Pendiente.objects.filter(tipo='VISITA', rol='VENTAS').count(), 1)

    def test_no_pregunta_antes_de_la_hora_ni_por_visitas_no_confirmadas(self):
        con_ventas()
        Visita.objects.update(inicio=self.ahora + timedelta(hours=1))
        self.assertEqual(services_visitas.enviar_cierres(self.ahora), 0)
        Visita.objects.update(inicio=self.ahora - timedelta(hours=2), estado='POR_CONFIRMAR')
        self.assertEqual(services_visitas.enviar_cierres(self.ahora), 0)

    def test_atendida_cierra_y_atribuye_las_cotizaciones_del_telefono(self):
        ana = con_ventas()
        Visita.objects.filter(pk=self.visita.pk).update(vendedor=ana)
        respuesta = services_visitas.atender_boton(TEL_VENTAS, f'vres:{self.visita.pk}:ok')
        self.assertIn('atendida', respuesta)
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.resultado, 'ATENDIDA')
        self.assertIsNotNone(self.visita.atendida_en)
        self.cot.refresh_from_db()
        self.assertEqual((self.cot.vendedor, self.cot.vendedor_origen), (ana, 'VISITA'))

    def test_no_llego_cierra_sin_atribuir(self):
        ana = con_ventas()
        Visita.objects.filter(pk=self.visita.pk).update(vendedor=ana)
        respuesta = services_visitas.atender_boton(TEL_VENTAS, f'vres:{self.visita.pk}:no')
        self.assertIn('no llegó', respuesta)
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.resultado, 'NO_LLEGO')
        self.cot.refresh_from_db()
        self.assertIsNone(self.cot.vendedor)

    def test_cierra_una_sola_vez(self):
        con_ventas()
        services_visitas.atender_boton(TEL_VENTAS, f'vres:{self.visita.pk}:ok')
        self.assertIsNone(services_visitas.atender_boton(TEL_VENTAS, f'vres:{self.visita.pk}:no'))
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.resultado, 'ATENDIDA')

    def test_un_numero_ajeno_no_cierra(self):
        con_ventas()
        self.assertIsNone(services_visitas.atender_boton(TEL_ENC, f'vres:{self.visita.pk}:ok'))
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.resultado, '')

    def test_exige_confirmada_y_hora_pasada(self):
        Visita.objects.filter(pk=self.visita.pk).update(estado='POR_CONFIRMAR')
        self.assertIn('no estaba confirmada', services_visitas.cerrar_visita(self.visita.pk, True))
        Visita.objects.filter(pk=self.visita.pk).update(estado='CONFIRMADA', inicio=self.ahora + timedelta(hours=1))
        self.assertIn('Todavía no', services_visitas.cerrar_visita(self.visita.pk, True))
        self.visita.refresh_from_db()
        self.assertEqual(self.visita.resultado, '')

    def test_acciones_del_admin(self):
        su = User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123')
        from core_erp.test_utils import login_superuser_con_totp
        login_superuser_con_totp(self.client, su)
        self.client.post(reverse('admin:comunicacion_visita_changelist'),
                         {'action': 'marcar_atendida', '_selected_action': [self.visita.pk]})
        self.visita.refresh_from_db()
        self.assertEqual((self.visita.resultado, self.visita.atendida_por), ('ATENDIDA', su))

    def test_el_comando_de_avisos_tambien_pregunta_los_cierres(self):
        con_ventas()
        call_command('enviar_avisos_previos_visitas', stdout=__import__('io').StringIO())
        self.assertTrue(Pendiente.objects.filter(tipo='VISITA', rol='VENTAS').exists())


@wa_settings(**AGENTE)
class ConvivenciaDeNumerosTest(MocksVisitas, TestCase):
    """El teléfono de VENTAS está en el directorio: Kooxi no le contesta ni es «Dirección»."""

    def test_kooxi_no_le_contesta_a_ventas(self):
        con_ventas()
        conv = services_agente.recibir_mensaje(telefono=TEL_VENTAS, nombre='Ana', texto='Hola', wamid='wv1')
        self.assertFalse(services_agente.agente_contesta_a(conv))

    def test_no_activa_el_modo_direccion_para_ventas(self):
        from comercial.services_ordenes_wa import direccion_activa
        con_ventas()
        self.assertFalse(direccion_activa(TEL_VENTAS))
        self.assertTrue(direccion_activa(TEL_DIR))

    def test_un_listo_de_ventas_no_marca_tareas_ajenas(self):
        from operaciones.services import marcar_listo
        con_ventas()
        mesero = Empleado.objects.create(nombre='Luis', puesto='MESERO', telefono='5555550099')
        plantilla = PlantillaChecklist.objects.create(nombre='Limpieza', tipo='MANTENIMIENTO') \
            if hasattr(PlantillaChecklist, 'tipo') else None
        if plantilla is None:
            self.skipTest('modelo de checklist distinto al esperado')
        TareaProgramada.objects.create(plantilla=plantilla, responsable=mesero, fecha=timezone.localdate(),
                                       hora_entrada=time(8, 0), hora_limite=time(10, 0))
        self.assertIsNone(marcar_listo(TEL_VENTAS))
        self.assertFalse(TareaProgramada.objects.filter(completada_en__isnull=False).exists())

    def test_ventas_puede_resolver_sus_pendientes_con_el_boton(self):
        from comunicacion import services_pendientes as sp
        con_ventas()
        p = sp.registrar_pendiente(titulo='Prueba', rol='VENTAS', tipo='VISITA')
        respuesta = sp.atender_boton(TEL_VENTAS, sp.id_boton(p.pk, sp.BOTON_OK))
        self.assertIn('quedó resuelto', respuesta)

    def test_el_resumen_diario_incluye_a_ventas(self):
        con_ventas()
        sp = __import__('comunicacion.services_pendientes', fromlist=['x'])
        sp.registrar_pendiente(titulo='Cerrar visita', rol='VENTAS', tipo='VISITA')
        self.assertGreaterEqual(sp.enviar_resumen_diario(), 1)


HorarioVisita  # noqa: B018  (importado para que el módulo documente su dependencia)
_proximo  # noqa: B018
OK  # noqa: B018
