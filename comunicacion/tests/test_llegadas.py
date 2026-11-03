"""Visitas de punta a punta (confirmación, recordatorios, llegada) y formato de avisos al equipo."""
import json
from datetime import time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion import herramientas_agente, services_agente, services_llegada, services_pendientes, services_visitas
from comunicacion.formato_wa import Aviso
from comunicacion.models import ConversacionWhatsApp, Pendiente, Visita
from core_erp.programador import TAREAS

from .test_agente import AGENTE
from .test_fase_2a import OK, TEL_DIR, TEL_ENC, MocksVisitas, _dt
from .utils import TEL_CLIENTE, wa_settings


def _enviados(*mocks):
    """(teléfono, texto o parámetros) de todo lo que salió por esos mocks."""
    salida = []
    for m in mocks:
        for c in m.call_args_list:
            salida.append((c.kwargs['telefono'], c.kwargs.get('mensaje') or c.kwargs.get('parametros')))
    return salida


class FormatoAvisoTest(TestCase):
    def setUp(self):
        self.aviso = Aviso(titulo='Ana llegó a la Quinta', categoria='Llegada', urgente=True,
                           lineas=['Visita confirmada: 4:00 p.m.', '', '3 personas'], cierre='Que alguien la reciba.',
                           enlace='https://erp.test/x')

    def test_el_texto_va_en_bloques(self):
        texto = self.aviso.texto()
        self.assertTrue(texto.startswith('🔴 Urgente · Llegada\n*Ana llegó a la Quinta*'))
        self.assertIn('• Visita confirmada: 4:00 p.m.\n• 3 personas', texto)
        self.assertIn('\n\nAbrir en el ERP:\nhttps://erp.test/x', texto)
        self.assertNotIn('• \n', texto)

    def test_los_parametros_de_la_plantilla_no_llevan_saltos(self):
        parametros = self.aviso.parametros()
        self.assertEqual(len(parametros), 4)
        self.assertEqual(parametros[1], 'Ana llegó a la Quinta')
        self.assertTrue(all('\n' not in p for p in parametros))
        self.assertIn('Visita confirmada: 4:00 p.m. · 3 personas', parametros[2])
        self.assertNotIn('\n', self.aviso.plano())


@wa_settings(**AGENTE)
class EnvioAvisoTest(MocksVisitas, TestCase):
    def test_sin_ventana_usa_la_plantilla_del_equipo_con_4_parametros(self):
        with self.settings(META_WA_PLANTILLA_AVISO_EQUIPO='aviso_equipo', META_WA_PLANTILLA_OPERACIONES='aviso_ops'):
            services_pendientes._enviar_aviso(TEL_ENC, Aviso(titulo='Hola', lineas=['a']))
        llamada = self.pendientes_enviar_whatsapp_template.call_args.kwargs
        self.assertEqual(llamada['template_name'], 'aviso_equipo')
        self.assertEqual(len(llamada['parametros']), 4)

    def test_sin_plantilla_del_equipo_cae_a_la_de_operaciones_en_un_renglon(self):
        with self.settings(META_WA_PLANTILLA_AVISO_EQUIPO='', META_WA_PLANTILLA_OPERACIONES='aviso_ops'):
            services_pendientes._enviar_aviso(TEL_ENC, Aviso(titulo='Hola', lineas=['a', 'b']))
        llamada = self.pendientes_enviar_whatsapp_template.call_args.kwargs
        self.assertEqual(llamada['template_name'], 'aviso_ops')
        self.assertEqual(llamada['parametros'], ['🔵 Aviso · Hola · a · b'])

    def test_con_ventana_abierta_va_en_texto_libre_con_formato(self):
        services_agente.recibir_mensaje(telefono=TEL_ENC, nombre='Enc', texto='hola', wamid='we1')
        with self.settings(META_WA_PLANTILLA_AVISO_EQUIPO='aviso_equipo'):
            services_pendientes._enviar_aviso(TEL_ENC, Aviso(titulo='Hola', lineas=['a']))
        self.pendientes_enviar_whatsapp_template.assert_not_called()
        self.assertIn('*Hola*\n\n• a', self.pendientes_enviar_whatsapp.call_args.kwargs['mensaje'])


@wa_settings(**AGENTE)
class LlegadaTest(MocksVisitas, TestCase):
    def _llegada(self, situacion='ya_llego', **extra):
        salida, error = herramientas_agente.ejecutar('avisar_llegada', {'situacion': situacion, **extra},
                                                     conv=self.conv)
        self.assertFalse(error, salida)
        return json.loads(salida)

    def _visita_hoy(self, estado='CONFIRMADA', minutos=20):
        return Visita.objects.create(nombre='Guadalupe Requena', telefono=TEL_CLIENTE, conversacion=self.conv,
                                     personas=3, estado=estado, inicio=timezone.now() + timedelta(minutes=minutos))

    def test_la_herramienta_y_la_regla_estan_en_el_agente(self):
        self.assertIn('avisar_llegada', [h['name'] for h in herramientas_agente.HERRAMIENTAS])
        self.assertIn('avisar_llegada', services_agente.SYSTEM_PROMPT)
        self.assertIn('avisar_llegada', herramientas_agente._EJECUTORES_DEL_CLIENTE)

    def test_con_visita_de_hoy_avisa_urgente_al_encargado_con_sus_datos(self):
        visita = self._visita_hoy()
        r = self._llegada()
        self.assertTrue(r['ok'])
        self.assertIn('alguien lo recibe', r['nota'])
        p = Pendiente.objects.get(tipo='LLEGADA')
        self.assertEqual((p.rol, p.severidad, p.objeto), ('ENCARGADO', 'URGENTE', visita))
        self.assertIn('Guadalupe Requena llegó', p.titulo)
        self.assertIn('Visita confirmada', p.detalle)
        self.assertIn('3 personas', p.detalle)
        self.assertTrue(services_visitas.llego(visita))
        textos = [t for _, t in _enviados(self.pendientes_enviar_whatsapp, self.pendientes_enviar_whatsapp_template)]
        self.assertTrue(any('Guadalupe' in str(t) for t in textos))

    def test_no_repite_el_aviso_pero_en_camino_y_luego_ya_llego_si(self):
        self._visita_hoy()
        self._llegada('en_camino', minutos=15)
        self.assertTrue(self._llegada('en_camino')['ya_estaba_avisado'])
        self._llegada('ya_llego')
        self.assertTrue(self._llegada('ya_llego')['ya_estaba_avisado'])
        titulos = list(Pendiente.objects.filter(tipo='LLEGADA').values_list('titulo', flat=True))
        self.assertEqual(len(titulos), 2)
        self.assertTrue(any('llega en ~15 min' in t for t in titulos))

    def test_con_reservacion_de_hoy_trae_el_saldo_por_cobrar(self):
        cliente = Cliente.objects.create(nombre='Luis Pérez', telefono=TEL_CLIENTE[-10:])
        cot = Cotizacion.objects.create(cliente=cliente, tipo_servicio='PASADIA', nombre_evento='Pasadía',
                                        fecha_evento=timezone.localdate(), num_personas=20)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA', precio_final=Decimal('2000.00'))
        self._llegada()
        p = Pendiente.objects.get(tipo='LLEGADA')
        self.assertEqual(p.objeto, cot)
        self.assertIn('Luis llegó', p.titulo)
        self.assertIn(f'COT-{cot.id:03d}', p.detalle)
        self.assertIn('Saldo por cobrar: $2,000.00', p.detalle)

    def test_sin_cita_tambien_avisa_y_dice_si_ya_habia_visitado(self):
        Visita.objects.create(nombre='Ana', telefono=TEL_CLIENTE, estado='CONFIRMADA',
                              inicio=timezone.now() - timedelta(days=10))
        r = self._llegada()
        self.assertIn('No tiene cita', r['nota'])
        p = Pendiente.objects.get(tipo='LLEGADA')
        self.assertIn('Sin cita ni reservación', p.detalle)
        self.assertIn('Ya visitó la Quinta', p.detalle)

    def test_el_contexto_de_hoy_solo_aparece_si_viene_hoy(self):
        self.assertEqual(services_llegada.contexto_de_hoy(self.conv), '')
        self._visita_hoy(minutos=120)
        self.assertIn('avisar_llegada', services_llegada.contexto_de_hoy(self.conv))


@wa_settings(**AGENTE)
class FlujoVisitaTest(MocksVisitas, TestCase):
    def _visita(self, inicio, decidida_hace=timedelta(days=1)):
        return Visita.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE, conversacion=self.conv,
                                     estado='CONFIRMADA', inicio=inicio, decidida_en=timezone.now() - decidida_hace)

    def test_al_confirmar_el_cliente_sabe_como_avisar_y_el_otro_numero_del_equipo_se_entera(self):
        visita = Visita.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE, conversacion=self.conv,
                                       inicio=_dt(self.dia, time(16, 0)))
        respuesta = services_visitas.atender_boton(TEL_DIR, f'vis:{visita.pk}:ok')
        self.assertIn('Recordatorios:', respuesta)
        mensaje = self.visitas_enviar_whatsapp.call_args.kwargs['mensaje']
        self.assertIn('quedó confirmada', mensaje)
        self.assertIn('«ya llegué»', mensaje)
        self.assertIn('google.com/maps', mensaje)
        destinos = {t for t, _ in _enviados(self.pendientes_enviar_whatsapp, self.pendientes_enviar_whatsapp_template)}
        self.assertIn(TEL_ENC, destinos)

    def test_el_mismo_dia_el_cliente_2_h_antes_y_el_equipo_45_min_antes_una_vez(self):
        ahora = timezone.now()
        visita = self._visita(ahora + timedelta(minutes=100))
        services_visitas.enviar_avisos_previos(ahora)
        self.assertEqual(self.visitas_enviar_whatsapp_botones.call_count, 1)
        self.assertIn('Hoy es tu visita', self.visitas_enviar_whatsapp_botones.call_args.kwargs['mensaje'])
        self.assertFalse(_enviados(self.pendientes_enviar_whatsapp, self.pendientes_enviar_whatsapp_template))

        cerca = visita.inicio - timedelta(minutes=40)
        self.assertGreater(services_visitas.enviar_avisos_previos(cerca), 0)
        self.assertGreater(len(_enviados(self.pendientes_enviar_whatsapp, self.pendientes_enviar_whatsapp_template)), 0)
        # La siguiente vuelta repite las mismas claves: el envío real (reservar_comunicacion) no sale dos veces.
        services_visitas.enviar_avisos_previos(cerca + timedelta(minutes=10))
        claves = [c.kwargs.get('clave_idempotencia') for m in (self.pendientes_enviar_whatsapp,
                                                               self.pendientes_enviar_whatsapp_template,
                                                               self.visitas_enviar_whatsapp_botones)
                  for c in m.call_args_list]
        self.assertTrue(all(claves))
        self.assertEqual(len(set(claves)), 3)  # cliente previo + equipo previo a 2 números

    def test_confirmada_hace_poco_no_recibe_el_aviso_de_2_h(self):
        ahora = timezone.now()
        self._visita(ahora + timedelta(minutes=90), decidida_hace=timedelta(minutes=10))
        services_visitas.enviar_avisos_previos(ahora)
        self.visitas_enviar_whatsapp.assert_not_called()
        self.visitas_enviar_whatsapp_botones.assert_not_called()

    def test_si_ya_aviso_que_llego_no_se_le_recuerda_a_nadie(self):
        ahora = timezone.now()
        visita = self._visita(ahora + timedelta(minutes=30))
        services_llegada.avisar_llegada(self.conv, 'ya_llego')
        self.pendientes_enviar_whatsapp.reset_mock()
        self.pendientes_enviar_whatsapp_template.reset_mock()
        self.assertEqual(services_visitas.enviar_avisos_previos(ahora), 0)
        self.visitas_enviar_whatsapp.assert_not_called()
        self.visitas_enviar_whatsapp_botones.assert_not_called()
        self.assertTrue(services_visitas.llego(visita))

    def test_la_vispera_el_equipo_recibe_el_resumen_de_mañana(self):
        manana = timezone.localdate() + timedelta(days=1)
        self._visita(_dt(manana, time(16, 0)))
        Visita.objects.create(nombre='Luis', telefono='525555550099', inicio=_dt(manana, time(17, 0)))
        services_visitas.enviar_recordatorios()
        resumen = [t for _, t in _enviados(self.pendientes_enviar_whatsapp, self.pendientes_enviar_whatsapp_template)]
        self.assertTrue(any('tienes 2 visitas' in str(t) and 'POR CONFIRMAR' in str(t) for t in resumen))
        self.assertIn('«ya llegué»', self.visitas_enviar_whatsapp.call_args.kwargs['mensaje'])

    def test_el_plan_de_recordatorios_depende_de_cuanto_falta(self):
        ahora = timezone.now()
        lejos = Visita(inicio=ahora + timedelta(days=3))
        self.assertIn('la víspera', services_visitas._plan_recordatorios(lejos, ahora))
        pronto = Visita(inicio=ahora + timedelta(minutes=30))
        self.assertIn('Ya no da tiempo', services_visitas._plan_recordatorios(pronto, ahora))

    def test_el_programador_corre_los_avisos_previos(self):
        self.assertIn('enviar_avisos_previos_visitas', [t.comando for t in TAREAS])


@wa_settings(**AGENTE)
class BotonYaLlegueTest(MocksVisitas, TestCase):
    def setUp(self):
        super().setUp()
        self.botones = self.visitas_enviar_whatsapp_botones
        self.conv.mensajes.update(procesado=True)
        self.ahora = timezone.now()
        self.visita = Visita.objects.create(
            nombre='Ana Ruiz', telefono=TEL_CLIENTE, conversacion=self.conv, estado='CONFIRMADA',
            inicio=self.ahora + timedelta(minutes=100), decidida_en=self.ahora - timedelta(days=1))

    def test_con_ventana_abierta_el_recordatorio_lleva_el_boton(self):
        services_visitas.enviar_avisos_previos(self.ahora)
        llamada = self.botones.call_args.kwargs
        self.assertEqual(llamada['botones'], [(f'lleg:{self.visita.pk}', 'Ya llegué')])
        self.assertEqual(llamada['clave_idempotencia'], f'visita:{self.visita.pk}:previo')
        self.visitas_enviar_whatsapp.assert_not_called()

    def test_sin_ventana_sale_la_plantilla_sin_boton(self):
        ConversacionWhatsApp.objects.get(pk=self.conv.pk).mensajes.update(
            created_at=timezone.now() - timedelta(days=3))
        with self.settings(META_WA_PLANTILLA_VISITA='visita_aviso'):
            services_visitas.enviar_avisos_previos(self.ahora)
        self.botones.assert_not_called()
        self.assertIn('Ya llegué', str(self.visitas_enviar_whatsapp_template.call_args.kwargs['parametros']))

    def test_tocar_el_boton_avisa_al_equipo_sin_pasar_por_el_modelo(self):
        m = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto=f'lleg:{self.visita.pk}',
                                            wamid='wl1')
        with patch.object(services_agente, '_cliente_ia') as ia, \
                patch.object(services_agente, 'enviar_whatsapp', return_value=OK) as enviar:
            services_agente._atender_pendientes(m.pk)
        ia.assert_not_called()
        self.assertIn('Ya le avisé al equipo', enviar.call_args.kwargs['mensaje'])
        p = Pendiente.objects.get(tipo='LLEGADA')
        self.assertEqual((p.objeto, p.severidad), (self.visita, 'URGENTE'))

    def test_el_boton_desde_otro_numero_no_hace_nada(self):
        self.assertIsNone(services_visitas.atender_boton(TEL_DIR, f'lleg:{self.visita.pk}'))
        self.assertFalse(Pendiente.objects.filter(tipo='LLEGADA').exists())
