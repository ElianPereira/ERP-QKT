import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal

import openpyxl
import pdfplumber
from django.contrib.auth.models import Permission, User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core_erp.test_utils import login_superuser_con_totp
from cumplimiento import services_expediente
from cumplimiento.models import DocumentoExpediente, RequisitoTramite, Tramite, Vigencia

STORAGES_TEST = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
HOY = timezone.localdate()


def crear(nombre, dias, folio='F-1', costo='100.00', inicio=None, **kw):
    t = Tramite.objects.create(nombre=nombre, periodicidad_meses=12, **kw)
    v = Vigencia.objects.create(tramite=t, estado='OBTENIDA', folio=folio, costo=Decimal(costo),
                                fecha_inicio=inicio or HOY, fecha_vencimiento=HOY + timedelta(days=dias))
    v.documento.save('oficial.pdf', ContentFile(f'PDF {nombre}'.encode()), save=True)
    return t, v


@override_settings(STORAGES=STORAGES_TEST)
class ExpedienteTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        login_superuser_con_totp(self.client, self.admin)
        self.vigente, self.v1 = crear('Licencia vigente', 200, folio='LIC-1')
        self.por_vencer, _ = crear('Constancia por vencer', 10, folio='PC-9')
        self.vencido, _ = crear('Permiso vencido', -3, folio='OLD-2')

    def test_pdf_renderiza_con_datos_reales(self):
        r = self.client.get(reverse('cumplimiento:expediente_pdf'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('QKT_Cumplimiento_', r['Content-Disposition'])
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            texto = '\n'.join(p.extract_text() or '' for p in pdf.pages)
        for esperado in ('Licencia vigente', 'LIC-1', 'Permiso vencido', 'Vencido', 'Por vencer'):
            self.assertIn(esperado, texto)

    def test_zip_solo_incluye_lo_vigente(self):
        r = self.client.get(reverse('cumplimiento:expediente_zip'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            nombres = zf.namelist()
            self.assertEqual(len([n for n in nombres if n.endswith('.pdf')]), 3)  # expediente + 2 vigentes
            self.assertTrue(any(n.startswith('licencia-vigente_lic-1_') for n in nombres))
            self.assertTrue(any(n.startswith('constancia-por-vencer_pc-9_') for n in nombres))
            self.assertFalse(any('vencido' in n and 'permiso' in n for n in nombres))
            contenido = zf.read(next(n for n in nombres if n.startswith('licencia-vigente')))
        self.assertEqual(contenido, b'PDF Licencia vigente')

    def test_zip_con_requisitos(self):
        req = RequisitoTramite.objects.create(tramite=self.vigente, nombre='Dictamen')
        doc = DocumentoExpediente.objects.create(vigencia=self.v1, requisito=req, nombre='Dictamen eléctrico')
        doc.archivo.save('d.pdf', ContentFile(b'DICT'), save=True)
        r = self.client.get(reverse('cumplimiento:expediente_zip'), {'requisitos': '1'})
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            self.assertTrue(any(n.startswith('requisitos/') for n in zf.namelist()))
        sin = self.client.get(reverse('cumplimiento:expediente_zip'))
        with zipfile.ZipFile(io.BytesIO(sin.content)) as zf:
            self.assertFalse(any(n.startswith('requisitos/') for n in zf.namelist()))

    def test_archivo_ilegible_queda_en_leeme(self):
        self.v1.documento.storage.delete(self.v1.documento.name)
        contenido, _, omitidos = services_expediente.armar_zip()
        self.assertEqual(len(omitidos), 1)
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            self.assertIn('LEEME.txt', zf.namelist())

    def test_zip_demasiado_grande(self):
        with self.settings():
            original = services_expediente.MAX_BYTES_ZIP
            services_expediente.MAX_BYTES_ZIP = 5
            try:
                r = self.client.get(reverse('cumplimiento:expediente_zip'))
            finally:
                services_expediente.MAX_BYTES_ZIP = original
        self.assertRedirects(r, reverse('cumplimiento:tablero'), fetch_redirect_response=False)

    def test_permisos(self):
        staff = User.objects.create_user('ayuda', password='x-segura-123', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('cumplimiento:expediente_pdf')).status_code, 403)
        self.assertEqual(self.client.get(reverse('cumplimiento:expediente_zip')).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename='view_tramite'))
        self.client.force_login(User.objects.get(pk=staff.pk))
        self.assertEqual(self.client.get(reverse('cumplimiento:expediente_pdf')).status_code, 200)
        self.assertEqual(self.client.get(reverse('cumplimiento:expediente_zip')).status_code, 403)


class ReporteCostoTest(TestCase):
    PERIODO = {'fecha_inicio': '2026-01-01', 'fecha_fin': '2026-12-31'}

    def setUp(self):
        self.admin = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        login_superuser_con_totp(self.client, self.admin)
        a = Tramite.objects.create(nombre='Licencia A', tipo='LICENCIA')
        b = Tramite.objects.create(nombre='Contrato B', tipo='CONTRATO')
        Vigencia.objects.create(tramite=a, estado='OBTENIDA', fecha_inicio=date(2026, 2, 1), costo=Decimal('100.10'))
        Vigencia.objects.create(tramite=a, estado='EN_TRAMITE', fecha_inicio=date(2026, 6, 1), costo=Decimal('200.20'))
        Vigencia.objects.create(tramite=b, estado='OBTENIDA', fecha_inicio=date(2026, 3, 1), costo=Decimal('50.05'))
        Vigencia.objects.create(tramite=b, estado='CANCELADA', fecha_inicio=date(2026, 4, 1), costo=Decimal('999.00'))
        Vigencia.objects.create(tramite=b, estado='OBTENIDA', fecha_inicio=date(2025, 4, 1), costo=Decimal('777.00'))

    def test_servicio_suma_decimal(self):
        from reportes.services.cumplimiento import CostoCumplimientoService
        datos = CostoCumplimientoService.generar(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(datos['total'], Decimal('350.35'))
        self.assertEqual({t['nombre']: t['costo'] for t in datos['tramites']},
                         {'Licencia A': Decimal('300.30'), 'Contrato B': Decimal('50.05')})

    def test_pdf_y_excel(self):
        r = self.client.get(reverse('reportes:cumplimiento_costo'), self.PERIODO)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            texto = '\n'.join(p.extract_text() or '' for p in pdf.pages)
        self.assertIn('$350.35', texto)
        r = self.client.get(reverse('reportes:cumplimiento_costo'), {**self.PERIODO, 'formato': 'excel'})
        hoja = openpyxl.load_workbook(io.BytesIO(r.content))['Por trámite']
        self.assertEqual([c.value for c in hoja[2]][:2], ['Licencia A', 'Licencia'])

    def test_permiso_y_selector(self):
        html = self.client.get(reverse('reportes:selector')).content.decode()
        self.assertIn(reverse('reportes:cumplimiento_costo'), html)
        self.client.force_login(User.objects.create_user('ventas', is_staff=True))
        self.assertEqual(self.client.get(reverse('reportes:cumplimiento_costo'), self.PERIODO).status_code, 403)
