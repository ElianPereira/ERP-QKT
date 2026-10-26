from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from comunicacion import services_pendientes as sp
from comunicacion.models import Responsable
from cumplimiento import services_avisos as sa
from cumplimiento.models import AvisoVencimiento, Tramite, Vigencia

HOY = timezone.localdate()
WA = mock.patch.object(sp, 'enviar_whatsapp_template')
WA_TEXTO = mock.patch.object(sp, 'enviar_whatsapp')
MAIL = mock.patch.object(sa, 'alertar_equipo_email')


def _ok():
    return mock.Mock(estado='ENVIADO', error='')


def _fallo():
    return mock.Mock(estado='FALLIDO', error='Meta 131047')


def tramite_con_vigencia(dias, nombre='Lic', **kw):
    t = Tramite.objects.create(nombre=nombre, periodicidad_meses=12, **kw)
    v = Vigencia.objects.create(tramite=t, estado='OBTENIDA', fecha_vencimiento=HOY + timedelta(days=dias))
    return t, v


class UmbralesTest(TestCase):
    def umbrales(self, dias, **kw):
        t, _ = tramite_con_vigencia(dias, **kw)
        return [a['umbral'] for a in sa.calcular_avisos(HOY)]

    def test_umbrales_exactos(self):
        casos = {61: [], 60: [60], 31: [60], 30: [30], 8: [30], 7: [7], 1: [7], 0: [0], -1: [-1], -30: [-1]}
        for dias, esperado in casos.items():
            with self.subTest(dias=dias):
                Vigencia.objects.all().delete()
                Tramite.objects.all().delete()
                self.assertEqual(self.umbrales(dias), esperado)

    def test_dias_aviso_propio(self):
        self.assertEqual(self.umbrales(90, dias_aviso=90), [90])

    def test_renovacion_obtenida_posterior_silencia_a_la_vieja(self):
        t, _ = tramite_con_vigencia(5)
        Vigencia.objects.create(tramite=t, estado='OBTENIDA', fecha_vencimiento=HOY + timedelta(days=400))
        self.assertEqual(sa.calcular_avisos(HOY), [])

    def test_tramite_inactivo_y_en_tramite_no_avisan(self):
        t, _ = tramite_con_vigencia(5, activo=False)
        t2 = Tramite.objects.create(nombre='Otro', periodicidad_meses=12)
        Vigencia.objects.create(tramite=t2, estado='EN_TRAMITE', fecha_vencimiento=HOY + timedelta(days=5))
        self.assertEqual(sa.calcular_avisos(HOY), [])

    def test_preaviso_de_cancelacion_de_contrato(self):
        t = Tramite.objects.create(nombre='Gas', tipo='CONTRATO', periodicidad_meses=12,
                                   renovacion_automatica=True, dias_preaviso_cancelacion=30)
        Vigencia.objects.create(tramite=t, estado='OBTENIDA', fecha_vencimiento=HOY + timedelta(days=30))
        umbrales = [a['umbral'] for a in sa.calcular_avisos(HOY)]
        self.assertIn(sa.UMBRAL_CANCELACION, umbrales)
        Vigencia.objects.all().update(fecha_vencimiento=HOY + timedelta(days=31))
        self.assertNotIn(sa.UMBRAL_CANCELACION, [a['umbral'] for a in sa.calcular_avisos(HOY)])

    def test_criticos_primero(self):
        tramite_con_vigencia(3, nombre='Normal')
        tramite_con_vigencia(20, nombre='Critico', critico=True)
        self.assertEqual([a['tramite'].nombre for a in sa.calcular_avisos(HOY)], ['Critico', 'Normal'])


@override_settings(ERP_WA_NEGOCIO_NUMERO='9991234567', META_WA_PLANTILLA_OPERACIONES='aviso_operaciones')
class EnvioTest(TestCase):
    def setUp(self):
        self.t, self.v = tramite_con_vigencia(7)

    def test_simulacion_no_escribe_ni_envia(self):
        with WA as wa, MAIL as mail:
            r = sa.procesar_avisos(HOY, aplicar=False)
        self.assertEqual(len(r['avisos']), 1)
        wa.assert_not_called()
        mail.assert_not_called()
        self.assertEqual(AvisoVencimiento.objects.count(), 0)

    def test_envia_una_vez_y_no_duplica_en_la_segunda_corrida(self):
        with WA as wa, MAIL as mail:
            wa.return_value = _ok()
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
            sa.procesar_avisos(HOY, aplicar=True)
        self.assertEqual(wa.call_count, 1)
        self.assertEqual(mail.call_count, 1)
        self.assertEqual(AvisoVencimiento.objects.filter(estado='ENVIADO').count(), 2)

    def test_avisa_al_numero_de_direccion_del_directorio(self):
        Responsable.objects.create(rol='ENCARGADO', nombre='E', telefono_wa='525555550011')
        Responsable.objects.create(rol='DIRECCION', nombre='D', telefono_wa='525555550012')
        with WA as wa, MAIL as mail:
            wa.return_value = _ok()
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        self.assertEqual(wa.call_count, 1)
        self.assertEqual(wa.call_args.kwargs['telefono'], '525555550012')

    def test_mensaje_consolidado(self):
        tramite_con_vigencia(2, nombre='Otro', critico=True)
        with WA as wa, MAIL as mail:
            wa.return_value = _ok()
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        self.assertEqual(wa.call_count, 1)
        resumen = wa.call_args.kwargs['parametros'][0]
        self.assertIn('Otro', resumen)
        self.assertIn('Lic', resumen)
        self.assertEqual(wa.call_args.kwargs['template_name'], 'aviso_operaciones')

    def test_canal_fallido_se_reintenta_sin_bloquear_al_otro(self):
        with WA as wa, MAIL as mail:
            wa.return_value = _fallo()
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
            self.assertEqual(AvisoVencimiento.objects.get(canal='WHATSAPP').estado, 'FALLIDO')
            self.assertEqual(AvisoVencimiento.objects.get(canal='EMAIL').estado, 'ENVIADO')
            wa.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        self.assertEqual(wa.call_count, 2)
        self.assertEqual(mail.call_count, 1)
        self.assertEqual(AvisoVencimiento.objects.get(canal='WHATSAPP').estado, 'ENVIADO')

    def test_excepcion_en_envio_queda_fallida(self):
        with WA as wa, MAIL as mail:
            wa.side_effect = RuntimeError('boom')
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        fila = AvisoVencimiento.objects.get(canal='WHATSAPP')
        self.assertEqual((fila.estado, fila.detalle), ('FALLIDO', 'boom'))

    def test_pendiente_reciente_de_otra_corrida_no_se_repite(self):
        AvisoVencimiento.objects.create(vigencia=self.v, umbral=7, canal='WHATSAPP', estado='PENDIENTE')
        with WA as wa, MAIL as mail:
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        wa.assert_not_called()

    def test_pendiente_viejo_se_retoma(self):
        fila = AvisoVencimiento.objects.create(vigencia=self.v, umbral=7, canal='WHATSAPP', estado='PENDIENTE')
        AvisoVencimiento.objects.filter(pk=fila.pk).update(actualizado_en=timezone.now() - timedelta(hours=2))
        with WA as wa, MAIL as mail:
            wa.return_value = _ok()
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        self.assertEqual(wa.call_count, 1)

    def test_nuevo_umbral_avisa_de_nuevo(self):
        with WA as wa, MAIL as mail:
            wa.return_value = _ok()
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
            Vigencia.objects.update(fecha_vencimiento=HOY)  # ahora toca el umbral 0
            sa.procesar_avisos(HOY, aplicar=True)
        self.assertEqual(wa.call_count, 2)

    def test_copia_al_responsable_con_correo(self):
        resp = User.objects.create_user('resp', email='resp@example.com', password='x-segura-123')
        Tramite.objects.update(responsable=resp)
        with WA as wa, MAIL as mail, mock.patch.object(sa, 'send_mail') as copia:
            wa.return_value = _ok()
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        self.assertEqual(copia.call_args.args[3], ['resp@example.com'])

    @override_settings(META_WA_PLANTILLA_OPERACIONES='')
    def test_sin_plantilla_manda_texto_libre(self):
        with WA as plantilla, WA_TEXTO as texto, MAIL as mail:
            texto.return_value = _ok()
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        plantilla.assert_not_called()
        self.assertEqual(texto.call_count, 1)

    @override_settings(ERP_WA_NEGOCIO_NUMERO='')
    def test_sin_numero_de_negocio_no_truena_y_manda_correo(self):
        with WA as wa, MAIL as mail:
            mail.return_value = _ok()
            sa.procesar_avisos(HOY, aplicar=True)
        wa.assert_not_called()
        self.assertEqual(mail.call_count, 1)
        self.assertFalse(AvisoVencimiento.objects.filter(canal='WHATSAPP').exists())


@override_settings(ERP_WA_NEGOCIO_NUMERO='9991234567', META_WA_PLANTILLA_OPERACIONES='aviso_operaciones')
class ComandoTest(TestCase):
    def test_simula_por_defecto_y_aplica_con_flag(self):
        tramite_con_vigencia(3)
        salida = StringIO()
        with WA, MAIL:
            call_command('avisar_vencimientos', stdout=salida)
            self.assertIn('SIMULACIÓN', salida.getvalue())
            self.assertEqual(AvisoVencimiento.objects.count(), 0)
        with WA as wa, MAIL as mail:
            wa.return_value = _ok()
            mail.return_value = _ok()
            salida = StringIO()
            call_command('avisar_vencimientos', '--aplicar', stdout=salida)
        self.assertIn('WHATSAPP: 1 enviado', salida.getvalue())
        self.assertEqual(AvisoVencimiento.objects.count(), 2)
