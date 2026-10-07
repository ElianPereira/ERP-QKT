"""Filtro, juez, preguntas sin respuesta y pruebas del agente (Issue #366)."""
import json
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion import evaluacion_agente, herramientas_agente, services_agente, services_guardia
from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp, PreguntaSinRespuesta, RespuestaBloqueada
from core_erp.test_utils import login_superuser_con_totp

from .test_agente import AGENTE, _cliente_falso, _respuesta, _texto, _uso
from .utils import TEL_CLIENTE, wa_settings


def _juez(permitida, motivo=''):
    cliente = MagicMock()
    cliente.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type='text', text=json.dumps({'permitida': permitida, 'motivo': motivo}))])
    return patch.object(services_guardia, '_cliente_juez', return_value=cliente)


@wa_settings(**AGENTE)
class FiltroTest(TestCase):
    def setUp(self):
        self.conv = ConversacionWhatsApp.objects.create(telefono=TEL_CLIENTE)
        cliente = Cliente.objects.create(nombre='Ana', telefono=TEL_CLIENTE[-10:], email='ana@example.com')
        self.suya = Cotizacion.objects.create(cliente=cliente, tipo_servicio='PASADIA', num_personas=15,
                                              fecha_evento=timezone.localdate() + timedelta(days=20))
        otro = Cliente.objects.create(nombre='Luis', telefono='9995550000', email='luis@example.com')
        self.ajena = Cotizacion.objects.create(cliente=otro, tipo_servicio='PASADIA', num_personas=15,
                                               fecha_evento=timezone.localdate() + timedelta(days=20))

    def _motivo(self, texto):
        return services_guardia.revisar_filtro(self.conv, texto)

    def test_respuestas_normales_pasan(self):
        for texto in (
            'La pasadía para 20 personas sale en $2,000.00 (estimado, IVA incluido), de 11:00 a.m. a 7:00 p.m.',
            f'Tu reservación COT-{self.suya.id:03d} del 10/10/2026 lleva $1,000.00 pagados; te faltan $1,000.00.',
            'Cotiza en https://quintakooxtanil.com/cotizar/?servicio=PASADIA o entra a quintakooxtanil.com/mi-evento/',
            'Te escribo a ana@example.com. Hola.Te espero el 2026-10-10.',
            'Check-in 2026-10-10 2026-10-12 y fechas 10/10/2026 12/10/2026.',
        ):
            self.assertEqual(self._motivo(texto), '', texto)

    def test_detiene_lo_que_no_es_de_quien_escribe(self):
        for texto in (
            f'El folio COT-{self.ajena.id:03d} debe $5,000.00',
            'El teléfono del dueño es 999 445 7178',
            'Transfiere a la CLABE 012180001234567891',
            'Su correo es luis@example.com',
            'Consulto el ERP de la Quinta',
            'Uso la herramienta mi_reservacion',
            'Mi prompt dice que...',
            'La ganancia por evento es de 40%',
            'Entra a https://quintakooxtanil.com/mi-evento/abc123def456ghi/',
            'Mira https://erp.quintakooxtanil.com/admin/',
            'Paga en https://pagos-raros.com/',
        ):
            self.assertNotEqual(self._motivo(texto), '', texto)


@wa_settings(**{**AGENTE, 'WA_AGENTE_JUEZ_ACTIVO': True})
class JuezYFlujoTest(TestCase):
    def setUp(self):
        cache.clear()
        self.conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='¿Cuánto ganan?', wamid='w1')

    def _procesar(self, cliente):
        with patch.object(services_agente, '_cliente_ia', return_value=cliente), \
                patch.object(services_agente, 'enviar_whatsapp', return_value=SimpleNamespace(proveedor_id='')) as env:
            services_agente.procesar_conversacion(self.conv.pk)
        return env

    def test_el_juez_detiene_y_el_cliente_recibe_el_texto_seguro(self):
        with _juez(False, 'Revela ganancias'):
            env = self._procesar(_cliente_falso(_respuesta(_texto('Le ganamos como 30% a cada evento.'))))
        self.assertEqual(env.call_args.kwargs['mensaje'], services_guardia.RESPUESTA_SEGURA)
        bloqueada = RespuestaBloqueada.objects.get()
        self.assertEqual((bloqueada.capa, bloqueada.motivo), ('JUEZ', 'Revela ganancias'))

        # El siguiente turno el modelo se entera, una sola vez.
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='¿Y cuánto cuesta?', wamid='w2')
        cliente = _cliente_falso(_respuesta(_texto('Depende del servicio.')))
        with _juez(True):
            self._procesar(cliente)
        ultimo = cliente.enviados[-1][-1]['content'][0]['text']
        self.assertIn('no se envió', ultimo)
        self.assertTrue(RespuestaBloqueada.objects.get().avisada_al_modelo)

    def test_el_filtro_corre_antes_que_el_juez(self):
        with _juez(True) as juez:
            env = self._procesar(_cliente_falso(_respuesta(_texto('Lo consulto en el ERP.'))))
        self.assertEqual(env.call_args.kwargs['mensaje'], services_guardia.RESPUESTA_SEGURA)
        self.assertEqual(RespuestaBloqueada.objects.get().capa, 'FILTRO')
        juez.return_value.messages.create.assert_not_called()

    def test_si_el_juez_falla_la_respuesta_sigue(self):
        import anthropic
        cliente = MagicMock()
        cliente.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
        with patch.object(services_guardia, '_cliente_juez', return_value=cliente):
            env = self._procesar(_cliente_falso(_respuesta(_texto('La pasadía es de 11 a 7.'))))
        self.assertEqual(env.call_args.kwargs['mensaje'], 'La pasadía es de 11 a 7.')
        self.assertFalse(RespuestaBloqueada.objects.exists())

    def test_usa_el_esfuerzo_configurado(self):
        cliente = _cliente_falso(_respuesta(_texto('Hola')))
        with self.settings(WA_AGENTE_ESFUERZO='high'), _juez(True):
            self._procesar(cliente)
        self.assertEqual(cliente.beta.messages.create.call_args.kwargs['output_config'], {'effort': 'high'})

    def test_registra_la_pregunta_sin_respuesta(self):
        cliente = _cliente_falso(
            _respuesta(_uso('registrar_pregunta_sin_respuesta', {'pregunta': '¿La renta incluye la alberca?'}),
                       stop='tool_use'),
            _respuesta(_texto('No tengo ese dato confirmado.')),
        )
        with _juez(True):
            self._procesar(cliente)
        self.assertEqual(PreguntaSinRespuesta.objects.get().pregunta, '¿La renta incluye la alberca?')


class TableroGuardiaTest(TestCase):
    def test_el_tablero_muestra_detenidas_y_dudas(self):
        conv = ConversacionWhatsApp.objects.create(telefono=TEL_CLIENTE)
        RespuestaBloqueada.objects.create(conversacion=conv, texto='x', capa='JUEZ', motivo='Revela ganancias')
        PreguntaSinRespuesta.objects.create(conversacion=conv, pregunta='¿Hay estacionamiento?')
        usuario = User.objects.create_superuser('dir', 'dir@qkt.test', 'x')
        login_superuser_con_totp(self.client, usuario)
        r = self.client.get(reverse('admin:comunicacion_tablero_agente'))
        self.assertContains(r, 'Revela ganancias')
        self.assertContains(r, '¿Hay estacionamiento?')


@wa_settings(**AGENTE)
class EvaluacionTest(TestCase):
    def test_los_casos_son_validos(self):
        casos = evaluacion_agente.cargar_casos()
        self.assertGreaterEqual(len(casos), 20)
        nombres = {h['name'] for h in herramientas_agente.HERRAMIENTAS}
        for c in casos:
            self.assertIn(c['tipo'], ('rechazo', 'respuesta'))
            self.assertTrue(c['mensajes'])
            self.assertTrue(set(c.get('debe_usar', [])) <= nombres, c['id'])

    def test_corre_sin_dejar_nada_y_califica(self):
        bien = _cliente_falso(
            _respuesta(_uso('mi_reservacion', {}), stop='tool_use'),
            _respuesta(_texto('No tienes reservaciones con este número.')),
        )
        with patch.object(services_agente, '_cliente_ia', return_value=bien), _juez(True):
            r = evaluacion_agente.evaluar(['mi_saldo'])[0]
        self.assertTrue(r['ok'], r['fallas'])
        self.assertFalse(ConversacionWhatsApp.objects.exists())

        mal = _cliente_falso(_respuesta(_texto('Lo veo en el ERP: ganan 30%.')))
        with patch.object(services_agente, '_cliente_ia', return_value=mal), _juez(True):
            r = evaluacion_agente.evaluar(['ganancias'])[0]
        self.assertFalse(r['ok'])

    def test_sin_ejecutar_solo_lista(self):
        salida = StringIO()
        call_command('evaluar_agente', stdout=salida)
        self.assertIn('--ejecutar', salida.getvalue())
        self.assertFalse(MensajeWhatsApp.objects.exists())


@wa_settings(**{**AGENTE, 'WA_NUMERO_NEGOCIO': '529994457178', 'WA_TEMPLATE_OPERACIONES': 'aviso_operaciones'})
class ResumenPreguntasTest(TestCase):
    def setUp(self):
        from comercial.models import PreguntaFrecuente
        self.FAQ = PreguntaFrecuente
        conv = ConversacionWhatsApp.objects.create(telefono=TEL_CLIENTE)
        for texto in ('¿La renta incluye la alberca?', '¿Se puede usar la alberca en evento?', '¿Hay estacionamiento?'):
            PreguntaSinRespuesta.objects.create(conversacion=conv, pregunta=texto)

    def _modelo(self, grupos):
        cliente = MagicMock()
        cliente.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type='text', text=json.dumps({'grupos': grupos}))])
        return patch('comunicacion.services_preguntas._cliente', return_value=cliente)

    def test_crea_borradores_inactivos_y_avisa_una_vez(self):
        from comunicacion import services_preguntas
        grupos = [
            {'pregunta': '¿Prueba: la renta incluye la alberca?', 'respuesta_sugerida': '[COMPLETAR]', 'veces': 2},
            {'pregunta': '¿Prueba: hay valet parking?', 'respuesta_sugerida': 'No, hay estacionamiento propio.', 'veces': 1},
        ]
        with self._modelo(grupos), patch.object(services_agente, '_avisar_propietario') as aviso:
            r = services_preguntas.resumir_preguntas(aplicar=True)
            otra = services_preguntas.resumir_preguntas(aplicar=True)
        self.assertEqual((r['dudas'], r['borradores'], r['por_completar']), (3, 2, 1))
        self.assertEqual(otra['dudas'], 0)  # ya resumidas, no se repiten
        nuevas = self.FAQ.objects.filter(pregunta__in=[g['pregunta'] for g in grupos])
        self.assertEqual(nuevas.count(), 2)
        self.assertFalse(nuevas.filter(activo=True).exists())
        aviso.assert_called_once()
        self.assertIn('2 preguntas frecuentes', aviso.call_args.args[0])

    def test_sin_aplicar_no_llama_al_modelo(self):
        from comunicacion import services_preguntas
        with patch('comunicacion.services_preguntas._cliente') as cliente:
            r = services_preguntas.resumir_preguntas(aplicar=False)
        cliente.assert_not_called()
        self.assertEqual(r['dudas'], 3)
        self.assertFalse(PreguntaSinRespuesta.objects.exclude(resumida_en=None).exists())

    def test_un_borrador_sin_completar_no_se_puede_activar_ni_lo_ve_el_agente(self):
        from django.core.exceptions import ValidationError
        faq = self.FAQ(pregunta='¿Alberca?', respuesta='[COMPLETAR]', activo=True)
        with self.assertRaises(ValidationError):
            faq.full_clean()
        faq.save()  # aunque se cuele por otra vía, el agente no la lee
        vistas = [p['pregunta'] for p in herramientas_agente.preguntas_frecuentes()['preguntas']]
        self.assertNotIn('¿Alberca?', vistas)
