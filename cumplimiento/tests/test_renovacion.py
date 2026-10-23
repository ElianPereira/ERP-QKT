from datetime import timedelta
from unittest import mock

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from comunicacion.models import Pendiente, Responsable
from cumplimiento import services_renovacion as svc
from cumplimiento.models import DocumentoExpediente, RequisitoTramite, Tramite, Vigencia

STORAGES_TEST = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
HOY = timezone.localdate()
DIRECCION = '9994440000'
AVISO = mock.patch.object(svc, '_enviar_aviso')


def tramite(dias, nombre='Licencia de funcionamiento', **kw):
    t = Tramite.objects.create(nombre=nombre, periodicidad_meses=12, **kw)
    v = Vigencia.objects.create(tramite=t, estado='OBTENIDA', fecha_inicio=HOY - timedelta(days=365 - dias),
                                fecha_vencimiento=HOY + timedelta(days=dias))
    return t, v


@override_settings(STORAGES=STORAGES_TEST)
class RenovacionAutomaticaTest(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dueño', telefono_wa=DIRECCION)

    def test_solo_entra_dentro_de_45_dias_y_sin_renovacion_abierta(self):
        cerca, _ = tramite(45, 'Cerca')
        lejos, _ = tramite(46, 'Lejos')
        abierta, _ = tramite(10, 'Abierta')
        Vigencia.objects.create(tramite=abierta, estado='EN_TRAMITE')
        auto, _ = tramite(10, 'Contrato', tipo='CONTRATO', renovacion_automatica=True)
        nombres = {t.nombre for t, _, _ in svc.tramites_por_renovar(HOY)}
        self.assertEqual(nombres, {'Cerca'})

    def test_vencido_sin_renovar_tambien_entra(self):
        tramite(-5, 'Vencido')
        self.assertEqual([t.nombre for t, _, _ in svc.tramites_por_renovar(HOY)], ['Vencido'])

    def test_abre_prellenada_copia_expediente_y_avisa_una_sola_vez(self):
        t, actual = tramite(40)
        req1 = RequisitoTramite.objects.create(tramite=t, nombre='Identificación')
        RequisitoTramite.objects.create(tramite=t, nombre='Comprobante de domicilio')
        doc = DocumentoExpediente(vigencia=actual, requisito=req1, nombre='INE')
        doc.archivo.save('ine.pdf', ContentFile(b'%PDF'), save=True)
        with AVISO as aviso:
            nueva = svc.abrir_renovacion(t, actual, 40)
            self.assertIsNone(svc.abrir_renovacion(t, actual, 40))
        self.assertEqual((nueva.estado, nueva.por_confirmar), ('EN_TRAMITE', False))
        self.assertEqual(nueva.fecha_inicio, actual.fecha_vencimiento + timedelta(days=1))
        self.assertEqual(nueva.documentos.count(), 1)
        self.assertEqual(nueva.documentos.get().archivo.name, doc.archivo.name)
        self.assertEqual(aviso.call_count, 1)
        self.assertIn('Faltan: Comprobante de domicilio', aviso.call_args.args[1])
        p = Pendiente.objects.get()
        self.assertEqual((p.rol, p.tipo, p.severidad), ('DIRECCION', 'CUMPLIMIENTO', 'NORMAL'))

    def test_si_cancelan_la_renovacion_no_se_vuelve_a_abrir(self):
        t, actual = tramite(40)
        with AVISO:
            nueva = svc.abrir_renovacion(t, actual, 40)
            nueva.estado = 'CANCELADA'
            nueva.save()
            self.assertIsNone(svc.abrir_renovacion(t, actual, 40))

    def test_comando_simula_y_aplica(self):
        tramite(30)
        call_command('preparar_renovaciones')
        self.assertEqual(Vigencia.objects.filter(estado='EN_TRAMITE').count(), 0)
        with AVISO:
            call_command('preparar_renovaciones', '--aplicar')
        self.assertEqual(Vigencia.objects.filter(estado='EN_TRAMITE').count(), 1)


@override_settings(STORAGES=STORAGES_TEST)
class CierreDelTramiteTest(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dueño', telefono_wa=DIRECCION)
        self.t, self.actual = tramite(30)
        with AVISO:
            self.nueva = svc.abrir_renovacion(self.t, self.actual, 30)

    def _recibir(self, pie='', telefono=DIRECCION):
        with mock.patch.object(svc, 'descargar_media_wa', return_value=(b'%PDF', 'application/pdf', '.pdf')), \
                AVISO as aviso:
            return svc.recibir_documento(telefono, 'media1', pie), aviso

    def test_documento_de_direccion_queda_por_confirmar_con_boton(self):
        respuesta, aviso = self._recibir()
        self.assertIsNone(respuesta)
        self.nueva.refresh_from_db()
        self.assertTrue(self.nueva.por_confirmar)
        self.assertEqual(self.nueva.estado, 'EN_TRAMITE')
        self.assertTrue(self.nueva.documento.name.endswith('.pdf'))
        self.assertEqual(aviso.call_args.kwargs['botones'], [(svc.id_boton(self.nueva.pk), 'Confirmar')])
        self.assertIn(f'{self.nueva.fecha_inicio:%d/%m/%Y}', aviso.call_args.args[1])

    def test_archivo_de_otro_telefono_o_sin_renovaciones_no_aplica(self):
        self.assertIsNone(self._recibir(telefono='9990000000')[0])
        self.nueva.refresh_from_db()
        self.assertFalse(self.nueva.por_confirmar)
        Vigencia.objects.filter(pk=self.nueva.pk).update(estado='CANCELADA')
        self.assertIsNone(self._recibir()[0])

    def test_pie_con_cot_es_de_inspeccion_no_de_cumplimiento(self):
        self.assertIsNone(self._recibir(pie='daños cot 145')[0])
        self.nueva.refresh_from_db()
        self.assertFalse(self.nueva.por_confirmar)

    def test_varias_abiertas_pide_el_nombre_en_el_pie(self):
        otro, v = tramite(20, 'Protección civil')
        with AVISO:
            svc.abrir_renovacion(otro, v, 20)
        respuesta, _ = self._recibir()
        self.assertIn('nombre del trámite', respuesta)
        self.nueva.refresh_from_db()
        self.assertFalse(self.nueva.por_confirmar)
        respuesta, _ = self._recibir(pie='Protección civil 2027')
        self.assertIsNone(respuesta)
        self.assertTrue(Vigencia.objects.get(tramite=otro, estado='EN_TRAMITE').por_confirmar)

    def test_archivo_no_valido_se_explica(self):
        with mock.patch.object(svc, 'descargar_media_wa', side_effect=ValueError('Solo puedo recibir fotos (JPG, PNG) o PDF.')):
            self.assertEqual(svc.recibir_documento(DIRECCION, 'm'), 'Solo puedo recibir fotos (JPG, PNG) o PDF.')

    def test_confirmar_la_pasa_a_obtenida_y_cierra_el_pendiente(self):
        self._recibir()
        respuesta = svc.atender_boton(DIRECCION, svc.id_boton(self.nueva.pk))
        self.assertIn('quedó vigente hasta', respuesta)
        self.nueva.refresh_from_db()
        self.assertEqual((self.nueva.estado, self.nueva.por_confirmar), ('OBTENIDA', False))
        self.assertEqual(Pendiente.objects.get().estado, 'RESUELTO')

    def test_confirmar_desde_otro_numero_o_sin_documento_no_hace_nada(self):
        self._recibir()
        self.assertIsNone(svc.atender_boton('9990000000', svc.id_boton(self.nueva.pk)))
        self.nueva.refresh_from_db()
        self.assertEqual(self.nueva.estado, 'EN_TRAMITE')
        Vigencia.objects.filter(pk=self.nueva.pk).update(por_confirmar=False)
        self.assertIsNone(svc.atender_boton(DIRECCION, svc.id_boton(self.nueva.pk)))

    def test_confirmar_con_datos_invalidos_explica_el_motivo(self):
        self._recibir()
        Vigencia.objects.filter(pk=self.nueva.pk).update(documento='')
        respuesta = svc.atender_boton(DIRECCION, svc.id_boton(self.nueva.pk))
        self.assertIn('No pude confirmarla', respuesta)
        self.nueva.refresh_from_db()
        self.assertEqual(self.nueva.estado, 'EN_TRAMITE')

    def test_es_boton(self):
        self.assertTrue(svc.es_boton('vig:12:ok'))
        self.assertFalse(svc.es_boton('vig:12:no'))
