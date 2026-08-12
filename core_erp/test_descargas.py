"""
Tests de la descarga protegida de archivos sensibles (SEC-FILE-001).

El bucket de R2 sirve lectura anónima, así que cada `archivo.url` que el ERP
publica es una URL permanente sin credencial. Estas pruebas fijan que las
descargas pasen por el ERP —con sesión, permiso y sin cache— en vez de por la
ruta del storage.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, ContratoServicio, Cotizacion, PortalCliente
from core_erp.descargas import ARCHIVOS_PROTEGIDOS, url_descarga

PDF = b'%PDF-1.4 contrato de prueba'


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class DescargaProtegidaTest(TestCase):

    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Cliente Test', telefono='9991234567')
        self.cotizacion = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Evento Test',
            fecha_evento=date.today() + timedelta(days=30),
            estado='CONFIRMADA',
        )
        self.contrato = ContratoServicio.objects.create(
            cotizacion=self.cotizacion, numero='CONT-TEST-0001',
        )
        self.contrato.archivo.save('contrato.pdf', ContentFile(PDF), save=True)

        self.url = reverse(
            'descargar_archivo_privado',
            args=['comercial', 'contratoservicio', 'archivo', self.contrato.pk],
        )

    def _staff(self, con_permiso=True):
        usuario = get_user_model().objects.create_user(
            username=f'staff{con_permiso}', password='Segura-190!', is_staff=True,
        )
        if con_permiso:
            usuario.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label='comercial',
                    codename='view_contratoservicio',
                )
            )
        return usuario

    def test_sin_sesion_no_sirve_el_archivo(self):
        respuesta = self.client.get(self.url)

        self.assertIn(respuesta.status_code, (302, 403))
        self.assertNotIn(b'%PDF', respuesta.content or b'')

    def test_staff_sin_permiso_del_modelo_no_lo_ve(self):
        self.client.force_login(self._staff(con_permiso=False))

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 404)

    def test_staff_con_permiso_descarga_el_contenido(self):
        self.client.force_login(self._staff(con_permiso=True))

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(b''.join(respuesta.streaming_content), PDF)
        self.assertEqual(respuesta['Cache-Control'], 'private, no-store')

    def test_un_modelo_no_registrado_no_se_puede_leer(self):
        # Sin la lista blanca la vista sería un lector arbitrario del ORM.
        self.client.force_login(self._staff(con_permiso=True))
        url = reverse(
            'descargar_archivo_privado',
            args=['comercial', 'cliente', 'nombre', self.cliente.pk],
        )

        self.assertEqual(self.client.get(url).status_code, 404)

    def test_url_descarga_no_apunta_al_storage(self):
        url = url_descarga(self.contrato, 'archivo')

        self.assertTrue(url.startswith('/admin/archivo/'))
        self.assertNotIn('media.quintakooxtanil.com', url)

    def test_url_descarga_rechaza_campos_no_registrados(self):
        with self.assertRaises(ValueError):
            url_descarga(self.cliente, 'nombre')

    def test_url_descarga_devuelve_none_si_no_hay_archivo(self):
        vacio = ContratoServicio.objects.create(
            cotizacion=self.cotizacion, numero='CONT-TEST-0002',
        )

        self.assertIsNone(url_descarga(vacio, 'archivo'))

    def test_la_lista_blanca_solo_referencia_campos_que_existen(self):
        from django.apps import apps

        for app_label, model_name, campo in ARCHIVOS_PROTEGIDOS:
            modelo = apps.get_model(app_label, model_name)
            campos = {f.name for f in modelo._meta.get_fields()}
            self.assertIn(campo, campos, f'{app_label}.{model_name}.{campo} no existe')


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class PortalContratoTest(TestCase):
    """El portal sirve el contrato en vez de redirigir al bucket público."""

    def setUp(self):
        cliente = Cliente.objects.create(nombre='Cliente Portal', telefono='9990001122')
        self.cotizacion = Cotizacion.objects.create(
            cliente=cliente,
            nombre_evento='Boda Portal',
            fecha_evento=date.today() + timedelta(days=25),
            estado='CONFIRMADA',
        )
        contrato = ContratoServicio.objects.create(
            cotizacion=self.cotizacion, numero='CONT-TEST-0100',
        )
        contrato.archivo.save('contrato_portal.pdf', ContentFile(PDF), save=True)
        # Cotizacion.save() ya crea el portal al confirmar (models.py:754).
        self.portal, _ = PortalCliente.objects.get_or_create(cotizacion=self.cotizacion)

    def test_sirve_el_pdf_sin_redirigir(self):
        respuesta = self.client.get(
            reverse('portal_descargar_contrato', args=[self.portal.token])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertEqual(respuesta['Cache-Control'], 'private, no-store')
        self.assertEqual(b''.join(respuesta.streaming_content), PDF)

    def test_token_invalido_no_sirve_nada(self):
        respuesta = self.client.get(
            reverse('portal_descargar_contrato', args=['token-que-no-existe'])
        )

        self.assertEqual(respuesta.status_code, 404)


class MensajeDeAccesoAlPortalTest(TestCase):
    """SEC-AUTHN-001a: los fallos de acceso no deben ser distinguibles."""

    def setUp(self):
        cliente = Cliente.objects.create(nombre='Cliente Enum', telefono='9995556677')
        self.cotizacion = Cotizacion.objects.create(
            cliente=cliente,
            nombre_evento='Evento Enum',
            fecha_evento=date.today() + timedelta(days=35),
        )
        self.url = reverse('portal_acceso')

    def _error(self, codigo, telefono):
        respuesta = self.client.post(self.url, {'codigo': codigo, 'telefono': telefono})
        return respuesta.context['error']

    def test_codigo_inexistente_y_telefono_incorrecto_dan_el_mismo_mensaje(self):
        inexistente = self._error('999999', '1234')
        telefono_malo = self._error(str(self.cotizacion.id), '0000')

        self.assertEqual(inexistente, telefono_malo)
        self.assertNotIn('No encontramos', inexistente)
