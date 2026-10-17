from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.test import TestCase, override_settings
from django.urls import reverse

from core_erp.descargas import url_descarga
from core_erp.test_utils import login_superuser_con_totp
from cumplimiento.models import DocumentoExpediente, Tramite, Vigencia

STORAGES_TEST = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(STORAGES=STORAGES_TEST)
class VigenciaModeloTest(TestCase):
    def setUp(self):
        self.tramite = Tramite.objects.create(nombre='Licencia de funcionamiento', tipo='LICENCIA',
                                              periodicidad_meses=12)

    def _vigencia(self, **kw):
        datos = dict(tramite=self.tramite, estado='OBTENIDA', fecha_inicio=date(2026, 1, 1),
                     fecha_vencimiento=date(2026, 12, 31), costo=Decimal('1500.50'))
        datos.update(kw)
        return Vigencia(**datos)

    def test_obtenida_exige_documento(self):
        with self.assertRaises(ValidationError) as ctx:
            self._vigencia().full_clean()
        self.assertIn('documento', ctx.exception.message_dict)

    def test_obtenida_con_documento_es_valida(self):
        v = self._vigencia()
        v.documento.save('lic.pdf', ContentFile(b'%PDF'), save=False)
        v.full_clean()

    def test_en_tramite_no_exige_documento(self):
        self._vigencia(estado='EN_TRAMITE', fecha_vencimiento=None).full_clean()

    def test_vencimiento_obligatorio_con_periodicidad(self):
        v = self._vigencia(fecha_vencimiento=None)
        v.documento.save('lic.pdf', ContentFile(b'%PDF'), save=False)
        with self.assertRaises(ValidationError) as ctx:
            v.full_clean()
        self.assertIn('fecha_vencimiento', ctx.exception.message_dict)

    def test_sin_periodicidad_no_exige_vencimiento(self):
        self.tramite.periodicidad_meses = None
        self.tramite.save()
        v = self._vigencia(fecha_vencimiento=None)
        v.documento.save('lic.pdf', ContentFile(b'%PDF'), save=False)
        v.full_clean()

    def test_vencimiento_no_anterior_al_inicio(self):
        with self.assertRaises(ValidationError):
            self._vigencia(estado='EN_TRAMITE', fecha_vencimiento=date(2025, 1, 1)).full_clean()

    def test_costo_es_decimal(self):
        v = self._vigencia(estado='EN_TRAMITE')
        v.save()
        v.refresh_from_db()
        self.assertEqual(v.costo, Decimal('1500.50'))


@override_settings(STORAGES=STORAGES_TEST)
class AdminCumplimientoTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        login_superuser_con_totp(self.client, self.admin)
        self.tramite = Tramite.objects.create(nombre='Protección Civil', tipo='CONSTANCIA',
                                              periodicidad_meses=12, critico=True)
        self.vigencia = Vigencia.objects.create(tramite=self.tramite, estado='OBTENIDA',
                                                fecha_vencimiento=date.today() + timedelta(days=100))
        self.vigencia.documento.save('pc.pdf', ContentFile(b'%PDF-1.4 x'), save=True)

    def test_sin_borrado_fisico(self):
        for modelo, obj in (('tramite', self.tramite), ('vigencia', self.vigencia)):
            r = self.client.post(reverse(f'admin:cumplimiento_{modelo}_delete', args=[obj.pk]), {'post': 'yes'})
            self.assertEqual(r.status_code, 403)
        self.assertTrue(Vigencia.objects.filter(pk=self.vigencia.pk).exists())

    def test_alta_audita_created_by_y_updated_by(self):
        r = self.client.post(reverse('admin:cumplimiento_tramite_add'), {
            'nombre': 'Licencia de uso de suelo', 'tipo': 'LICENCIA', 'dias_aviso': 90, 'activo': 'on',
            'requisitos-TOTAL_FORMS': 0, 'requisitos-INITIAL_FORMS': 0,
            'requisitos-MIN_NUM_FORMS': 0, 'requisitos-MAX_NUM_FORMS': 1000,
        })
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['adminform'].form.errors)
        t = Tramite.objects.get(nombre='Licencia de uso de suelo')
        self.assertEqual(t.created_by, self.admin)
        self.assertEqual(t.updated_by, self.admin)

    def test_descarga_protegida_con_permiso(self):
        url = url_descarga(self.vigencia, 'documento')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Cache-Control'], 'private, no-store')

    def test_descarga_sin_permiso_es_404(self):
        staff = User.objects.create_user('ayuda', password='x-segura-123', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url_descarga(self.vigencia, 'documento')).status_code, 404)
        staff.user_permissions.add(Permission.objects.get(codename='view_vigencia'))
        staff = User.objects.get(pk=staff.pk)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url_descarga(self.vigencia, 'documento')).status_code, 200)

    def test_documento_expediente_registrado_para_descarga(self):
        doc = DocumentoExpediente.objects.create(vigencia=self.vigencia, nombre='Dictamen')
        doc.archivo.save('d.pdf', ContentFile(b'%PDF'), save=True)
        self.assertEqual(self.client.get(url_descarga(doc, 'archivo')).status_code, 200)

    def test_formulario_no_expone_url_publica(self):
        html = self.client.get(reverse('admin:cumplimiento_vigencia_change', args=[self.vigencia.pk])).content.decode()
        self.assertNotIn(self.vigencia.documento.name, html)
        self.assertIn(url_descarga(self.vigencia, 'documento'), html)
