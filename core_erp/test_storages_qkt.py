from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import SimpleTestCase, override_settings

from core_erp import storages_qkt

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_PRUEBA)
class StorageConRespaldoTest(SimpleTestCase):
    def setUp(self):
        patcher = __import__('unittest.mock', fromlist=['patch']).patch.object(
            storages_qkt, '_hay_bucket_privado', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.st = storages_qkt.storage_privado_con_respaldo()

    def test_lee_del_publico_si_solo_esta_ahi(self):
        storages['default'].save('contratos_pdf/solo-publico.pdf', ContentFile(b'viejo'))
        self.assertTrue(self.st.exists('contratos_pdf/solo-publico.pdf'))
        with self.st.open('contratos_pdf/solo-publico.pdf') as f:
            self.assertEqual(f.read(), b'viejo')
        self.assertEqual(self.st.size('contratos_pdf/solo-publico.pdf'), 5)

    def test_escribe_en_el_privado_y_prefiere_el_privado(self):
        nombre = self.st.save('xml_compras/nuevo.xml', ContentFile(b'privado'))
        self.assertTrue(storages['privado'].exists(nombre))
        self.assertFalse(storages['default'].exists(nombre))
        storages['default'].save('xml_compras/dup.xml', ContentFile(b'publico'))
        storages['privado'].save('xml_compras/dup.xml', ContentFile(b'privado'))
        with self.st.open('xml_compras/dup.xml') as f:
            self.assertEqual(f.read(), b'privado')

    def test_no_reutiliza_un_nombre_que_ya_ocupa_el_publico(self):
        storages['default'].save('facturas_pdf/a.pdf', ContentFile(b'x'))
        nombre = self.st.save('facturas_pdf/a.pdf', ContentFile(b'y'))
        self.assertNotEqual(nombre, 'facturas_pdf/a.pdf')


class PrivadoObligatorioTest(SimpleTestCase):
    @override_settings(STORAGES=STORAGES_PRUEBA, ERP_R2_PRIVADO_OBLIGATORIO=True)
    def test_sin_bucket_lanza_error_claro(self):
        with self.assertRaises(ImproperlyConfigured) as cm:
            storages_qkt.storage_privado().exists('x')
        self.assertIn('CLOUDFLARE_R2_PRIVADO_BUCKET', str(cm.exception))

    @override_settings(STORAGES=STORAGES_PRUEBA, ERP_R2_PRIVADO_OBLIGATORIO=False)
    def test_sin_flag_cae_al_default(self):
        self.assertFalse(storages_qkt.storage_privado().exists('x'))


@override_settings(STORAGES=STORAGES_PRUEBA)
class CamposConRespaldoTest(SimpleTestCase):
    """Los lectores (correo de factura, portal, SAT, Compra.save) pasan por el
    storage del campo: si este lee del público, siguen funcionando sin migrar."""

    CAMPOS = (
        ('comercial', 'Cotizacion', 'archivo_pdf'),
        ('comercial', 'Cotizacion', 'archivo_contrato'),
        ('comercial', 'Compra', 'archivo_xml'),
        ('comercial', 'Compra', 'archivo_pdf'),
        ('comercial', 'Gasto', 'archivo_xml'),
        ('comercial', 'Gasto', 'archivo_pdf'),
        ('facturacion', 'SolicitudFactura', 'archivo_zip'),
        ('facturacion', 'SolicitudFactura', 'archivo_pdf'),
        ('facturacion', 'SolicitudFactura', 'archivo_xml'),
    )

    def test_cada_campo_lee_un_archivo_que_solo_esta_en_el_publico(self):
        from unittest.mock import patch

        from django.apps import apps
        with patch.object(storages_qkt, '_hay_bucket_privado', return_value=True):
            for app, modelo, campo in self.CAMPOS:
                with self.subTest(campo=f'{modelo}.{campo}'):
                    field = apps.get_model(app, modelo)._meta.get_field(campo)
                    nombre = f'{field.upload_to}heredado-{modelo}-{campo}'
                    storages['default'].save(nombre, ContentFile(b'solo publico'))
                    with field.storage.open(nombre) as f:
                        self.assertEqual(f.read(), b'solo publico')
