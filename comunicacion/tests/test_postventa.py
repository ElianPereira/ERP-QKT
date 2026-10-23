from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion import services_compras
from comunicacion import services_encuesta as svc
from comunicacion.models import ComunicacionCliente, EncuestaPostEvento, Pendiente, Responsable
from legal.models import AceptacionLegal

TELEFONO = '9991234567'


def _cliente(marketing=True, telefono=TELEFONO):
    c = Cliente.objects.create(nombre='Ana López', telefono=telefono)
    if marketing:
        AceptacionLegal.objects.create(
            cliente=c, finalidades_aceptadas=['MARKETING'], aceptado_en=timezone.now() - timedelta(days=400))
    return c


def _cot(cliente, dias, estado='EJECUTADA', **kw):
    return Cotizacion.objects.create(
        cliente=cliente, estado=estado, nombre_evento='Boda de Ana', num_personas=50,
        fecha_evento=timezone.localdate() + timedelta(days=dias), **kw)


def _comm(estado='ENVIADO'):
    return ComunicacionCliente(estado=estado, proveedor_id='wamid.X')


@override_settings(META_WA_PLANTILLA_ENCUESTA='encuesta_postevento', ERP_RESENA_GOOGLE_URL='https://g.page/r/qkt')
class EncuestaTest(TestCase):
    def setUp(self):
        self.cliente = _cliente()

    def _enviar(self, cot):
        with patch('comunicacion.services_encuesta.enviar_whatsapp_template') as env, \
                patch('comunicacion.services_agente.registrar_mensaje_automatico'):
            env.return_value = _comm()
            comm = svc.enviar_encuesta(cot)
        return comm, env

    def test_sale_un_dia_despues_con_tres_botones_y_es_idempotente(self):
        cot = _cot(self.cliente, -1)
        comm, env = self._enviar(cot)
        self.assertIsNotNone(comm)
        encuesta = EncuestaPostEvento.objects.get(cotizacion=cot)
        botones = [c for c in env.call_args.kwargs['components'] if c['type'] == 'button']
        self.assertEqual([b['parameters'][0]['payload'] for b in botones],
                         [f'enc:{encuesta.pk}:5', f'enc:{encuesta.pk}:3', f'enc:{encuesta.pk}:1'])
        comm2, env2 = self._enviar(cot)
        self.assertIsNone(comm2)
        env2.assert_not_called()

    def test_ventana_de_fechas(self):
        ayer, hoy, hace_semana = _cot(self.cliente, -1), _cot(self.cliente, 0), _cot(self.cliente, -8)
        ids = {c.pk for c in svc.cotizaciones_para_encuesta()}
        self.assertIn(ayer.pk, ids)
        self.assertNotIn(hoy.pk, ids)
        self.assertNotIn(hace_semana.pk, ids)

    def test_hospedaje_cuenta_desde_la_salida(self):
        hoy = timezone.localdate()
        cot = _cot(self.cliente, -5, tipo_servicio='HOSPEDAJE', fecha_salida=hoy - timedelta(days=1))
        self.assertIn(cot.pk, {c.pk for c in svc.cotizaciones_para_encuesta()})

    def test_sin_marketing_o_con_baja_no_se_envia(self):
        sin = _cot(_cliente(marketing=False, telefono='9995550000'), -1)
        self.assertIn('MARKETING', svc.motivo_para_no_encuestar(sin))
        cot = _cot(self.cliente, -1)
        with patch('comunicacion.services_bajas.dado_de_baja', return_value=True):
            self.assertIn('BAJA', svc.motivo_para_no_encuestar(cot))
            self.assertIsNone(svc.enviar_encuesta(cot))
        self.assertFalse(EncuestaPostEvento.objects.exists())

    @override_settings(META_WA_PLANTILLA_ENCUESTA='')
    def test_sin_plantilla_no_envia(self):
        self.assertIsNone(svc.enviar_encuesta(_cot(self.cliente, -1)))

    def test_envio_fallido_no_deja_encuesta_abierta(self):
        cot = _cot(self.cliente, -1)
        with patch('comunicacion.services_encuesta.enviar_whatsapp_template', return_value=_comm('FALLIDO')):
            svc.enviar_encuesta(cot)
        self.assertFalse(EncuestaPostEvento.objects.exists())

    def test_calificacion_buena_pide_resena(self):
        cot = _cot(self.cliente, -1)
        self._enviar(cot)
        e = EncuestaPostEvento.objects.get()
        atendido, resp = svc.atender_respuesta('529991234567', f'enc:{e.pk}:5')
        self.assertTrue(atendido)
        self.assertIn('https://g.page/r/qkt', resp)
        e.refresh_from_db()
        self.assertEqual((e.estado, e.calificacion, e.resena_solicitada), ('CONTESTADA', 5, True))
        self.assertFalse(Pendiente.objects.exists())

    @override_settings(ERP_RESENA_GOOGLE_URL='')
    def test_calificacion_buena_sin_enlace_solo_agradece(self):
        self._enviar(_cot(self.cliente, -1))
        e = EncuestaPostEvento.objects.get()
        _, resp = svc.atender_respuesta(TELEFONO, f'enc:{e.pk}:4')
        self.assertNotIn('http', resp)

    def test_calificacion_mala_abre_pendiente_urgente_a_direccion(self):
        cot = _cot(self.cliente, -1)
        self._enviar(cot)
        e = EncuestaPostEvento.objects.get()
        with patch('comunicacion.services_pendientes._enviar_aviso'):
            _, resp = svc.atender_respuesta(TELEFONO, f'enc:{e.pk}:1')
        self.assertIn('equipo', resp)
        p = Pendiente.objects.get()
        self.assertEqual((p.rol, p.tipo, p.severidad), ('DIRECCION', 'QUEJA', 'URGENTE'))
        self.assertIn('1 de 5', p.detalle)

    def test_numero_en_texto_solo_vale_con_encuesta_abierta(self):
        self.assertEqual(svc.atender_respuesta(TELEFONO, '3'), (False, None))
        self._enviar(_cot(self.cliente, -1))
        with patch('comunicacion.services_pendientes._enviar_aviso'):
            atendido, _ = svc.atender_respuesta(TELEFONO, ' 3 ')
        self.assertTrue(atendido)
        self.assertEqual(EncuestaPostEvento.objects.get().calificacion, 3)
        self.assertEqual(svc.atender_respuesta(TELEFONO, '7'), (False, None))

    def test_segunda_respuesta_no_cambia_la_calificacion(self):
        self._enviar(_cot(self.cliente, -1))
        e = EncuestaPostEvento.objects.get()
        svc.atender_respuesta(TELEFONO, f'enc:{e.pk}:5')
        _, resp = svc.atender_respuesta(TELEFONO, f'enc:{e.pk}:1')
        self.assertEqual(resp, svc.YA_CONTESTADA)
        e.refresh_from_db()
        self.assertEqual(e.calificacion, 5)
        self.assertFalse(Pendiente.objects.exists())

    def test_boton_de_otro_telefono_se_descarta(self):
        self._enviar(_cot(self.cliente, -1))
        e = EncuestaPostEvento.objects.get()
        self.assertEqual(svc.atender_respuesta('9990000000', f'enc:{e.pk}:5'), (True, None))
        e.refresh_from_db()
        self.assertEqual(e.estado, 'ENVIADA')

    def test_comando_dry_run_no_escribe(self):
        _cot(self.cliente, -1)
        with patch('comunicacion.services_encuesta.enviar_whatsapp_template') as env:
            call_command('enviar_encuestas', '--dry-run')
        env.assert_not_called()
        self.assertFalse(EncuestaPostEvento.objects.exists())


@override_settings(META_WA_PLANTILLA_REACTIVACION='reactivacion_aniversario')
class ReactivacionTest(TestCase):
    def setUp(self):
        self.cliente = _cliente()
        self.hoy = timezone.localdate()

    def _aniversario(self, **kw):
        fecha = self.hoy.replace(year=self.hoy.year - 1)
        return Cotizacion.objects.create(
            cliente=self.cliente, estado='EJECUTADA', nombre_evento='Boda', num_personas=40,
            fecha_evento=fecha, **kw)

    def test_aniversario_envia_una_vez_por_cliente_y_anio(self):
        cot = self._aniversario()
        self.assertIn(cot.pk, {c.pk for c in svc.cotizaciones_para_reactivacion(self.hoy)})
        with patch('comunicacion.services_encuesta.enviar_whatsapp_template', return_value=_comm()) as env, \
                patch('comunicacion.services_agente.registrar_mensaje_automatico'):
            self.assertIsNotNone(svc.enviar_reactivacion(cot, self.hoy))
            self.assertEqual(env.call_args.kwargs['clave_idempotencia'], f'reactivacion:{self.cliente.pk}:{self.hoy.year}')
            self.assertEqual(len(env.call_args.kwargs['parametros']), 4)

    def test_fuera_de_ventana_no_sale(self):
        cot = Cotizacion.objects.create(
            cliente=self.cliente, estado='EJECUTADA', nombre_evento='Otra', num_personas=40,
            fecha_evento=self.hoy.replace(year=self.hoy.year - 1) - timedelta(days=20))
        self.assertNotIn(cot.pk, {c.pk for c in svc.cotizaciones_para_reactivacion(self.hoy)})

    def test_respeta_marketing_baja_reservacion_futura_y_mala_calificacion(self):
        cot = self._aniversario()
        with patch('comunicacion.services_bajas.dado_de_baja', return_value=True):
            self.assertIn('BAJA', svc.motivo_para_no_reactivar(cot, self.hoy))
        sin = _cliente(marketing=False, telefono='9995550000')
        otra = Cotizacion.objects.create(
            cliente=sin, estado='EJECUTADA', nombre_evento='X', num_personas=10,
            fecha_evento=self.hoy.replace(year=self.hoy.year - 1))
        self.assertIn('MARKETING', svc.motivo_para_no_reactivar(otra, self.hoy))
        EncuestaPostEvento.objects.create(cotizacion=cot, telefono=TELEFONO, estado='CONTESTADA', calificacion=2)
        self.assertIn('calificó mal', svc.motivo_para_no_reactivar(cot, self.hoy))
        EncuestaPostEvento.objects.all().delete()
        _cot(self.cliente, 30, estado='CONFIRMADA')
        self.assertIn('reservación', svc.motivo_para_no_reactivar(cot, self.hoy))

    @override_settings(META_WA_PLANTILLA_REACTIVACION='')
    def test_sin_plantilla_no_envia(self):
        self.assertIsNone(svc.enviar_reactivacion(self._aniversario(), self.hoy))


class ComprasTest(TestCase):
    LISTA = {'Licores y Alcohol': [{'item': 'Ron', 'cantidad': 4, 'unidad': 'Botellas'}]}

    def setUp(self):
        self.cliente = _cliente()
        self.cot = _cot(self.cliente, 3, estado='CONFIRMADA', tipo_servicio='EVENTO')

    def _contexto(self, lista=None):
        return {'lista': self.LISTA if lista is None else lista, 'total_estimado': Decimal('1200.00'), 'pendientes': 0,
                'cotizacion': self.cot, 'titulo': 'x'}

    def _enviar(self, lista=None):
        with patch('comercial.views.contexto_lista_compras', return_value=self._contexto(lista)), \
                patch('comunicacion.services_compras._enviar_aviso') as aviso, \
                patch('comunicacion.services_compras._enviar_pdf') as pdf:
            return services_compras.enviar_lista(self.cot), aviso, pdf

    def test_ventana_de_tres_dias_solo_confirmadas_de_evento(self):
        lejos = _cot(self.cliente, 10, estado='CONFIRMADA')
        sin_confirmar = _cot(self.cliente, 2, estado='COTIZADA')
        ids = {c.pk for c in services_compras.cotizaciones_para_compras()}
        self.assertIn(self.cot.pk, ids)
        self.assertNotIn(lejos.pk, ids)
        self.assertNotIn(sin_confirmar.pk, ids)

    @override_settings(ERP_WA_NEGOCIO_NUMERO='9998887777')
    def test_sin_compras_cae_al_encargado_y_es_idempotente(self):
        pendiente, aviso, _ = self._enviar()
        self.assertEqual((pendiente.rol, pendiente.tipo), ('ENCARGADO', 'COMPRAS'))
        self.assertEqual(aviso.call_args.args[0], '529998887777')
        self.assertEqual(aviso.call_args.kwargs['botones'][0][1], 'Ya compré')
        otro, aviso2, _ = self._enviar()
        self.assertIsNone(otro)
        aviso2.assert_not_called()

    def test_con_responsable_de_compras_le_llega_a_el(self):
        Responsable.objects.create(rol='COMPRAS', nombre='Luis', telefono_wa='9991112222', email='luis@x.mx')
        pendiente, aviso, pdf = self._enviar()
        self.assertEqual(pendiente.rol, 'COMPRAS')
        self.assertEqual(aviso.call_args.args[0], '529991112222')
        pdf.assert_called_once()

    @override_settings(ERP_WA_NEGOCIO_NUMERO='9998887777')
    def test_lista_vacia_no_manda_nada(self):
        pendiente, aviso, _ = self._enviar(lista={})
        self.assertIsNone(pendiente)
        aviso.assert_not_called()
        self.assertFalse(Pendiente.objects.exists())
