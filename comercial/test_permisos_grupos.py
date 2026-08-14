"""
Tests de SEC-AUTHZ-001a-e (Issue #199, backlog órdenes 14-18): grupos por
área (Ventas, Contabilidad, Nómina) y permisos finos en las vistas que antes
solo exigían `is_staff`.

Vive en `comercial/` a propósito, no en `reportes/` ni repartido por app:
el comando `manage.py test` documentado en CLAUDE.md y usado en CI no
incluye `reportes`, y este archivo cruza comercial/airbnb/contabilidad/
facturacion/nomina/reportes/comunicacion. `comercial` sí corre en CI, así
que poner las pruebas aquí es lo único que garantiza que se ejecuten.

Ejecutar: python manage.py test comercial.test_permisos_grupos --verbosity=2
"""
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, ContratoServicio, Cotizacion, ItemCotizacion

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# (nombre_url, kwargs) de cada vista protegida con @permission_required,
# probada sin argumentos reales: el decorador corta antes de tocar la BD,
# así que un pk inexistente sigue devolviendo 403 para un usuario sin grupo.
VISTAS_PROTEGIDAS = [
    ('configurar_plantilla_barra', {}),
    ('generar_lista_compras', {}),
    ('cotizacion_lista_compras', {'cotizacion_id': 99999}),
    ('cotizacion_pdf', {'cotizacion_id': 99999}),
    ('cotizacion_email', {'cotizacion_id': 99999}),
    ('exportar_reporte_cotizaciones', {}),
    ('reporte_pagos', {}),
    ('producto_ficha_pdf', {'producto_id': 99999}),
    ('cartera_cxc', {}),
    ('generar_plan_pagos', {'cotizacion_id': 99999}),
    ('plan_pagos_pdf', {'cotizacion_id': 99999}),
    ('cotizacion_contrato', {'cotizacion_id': 99999}),
    ('contrato_email', {'contrato_id': 99999}),
    ('ver_calendario', {}),
    ('reporte_pagos_airbnb', {}),
    ('bloquear_en_airbnb', {'cotizacion_id': 99999}),
    ('reporte_fiscal_airbnb', {}),
    ('conciliacion_depositos_airbnb', {}),
    ('contabilidad:balanza', {}),
    ('contabilidad:estado_resultados', {}),
    ('cargar_nomina', {}),
    ('sync_jibble', {}),
    ('jibble_diagnostico', {}),
    ('crear_solicitud', {}),
    ('reportes:balanza', {}),
    ('reportes:estado_resultados', {}),
    ('reportes:balance_general', {}),
    ('reportes:libro_mayor', {}),
    ('reportes:auxiliar', {}),
    ('reportes:cxc', {}),
    ('reportes:cotizaciones', {}),
    ('reportes:ocupacion', {}),
    ('reportes:comparativo_airbnb', {}),
    ('reportes:facturas', {}),
]


def _crear_staff(username, grupos=()):
    user = User.objects.create_user(username, f'{username}@quintakooxtanil.com', 'clave-de-prueba', is_staff=True)
    for nombre_grupo in grupos:
        user.groups.add(Group.objects.get(name=nombre_grupo))
    return user


class PermisosSinGrupoTest(TestCase):
    """Un staff sin ningún grupo no debe pasar ninguna de las vistas protegidas."""

    @classmethod
    def setUpTestData(cls):
        call_command('crear_grupos_permisos')

    def setUp(self):
        self.user = _crear_staff('sin_grupo')
        self.client.force_login(self.user)

    def test_403_en_todas_las_vistas_protegidas(self):
        for nombre_url, kwargs in VISTAS_PROTEGIDAS:
            with self.subTest(vista=nombre_url):
                respuesta = self.client.get(reverse(nombre_url, kwargs=kwargs))
                self.assertEqual(respuesta.status_code, 403)

    def test_subvistas_admin_de_facturacion_redirigen_sin_aplicar(self):
        """Estas usan el patrón de comercial/admin.py (has_perm inline + redirect),
        no @permission_required, así que el rechazo es 302, no 403."""
        for nombre_url in (
            'admin:solicitudfactura_generar_pdf',
            'admin:solicitudfactura_enviar_whatsapp',
            'admin:solicitudfactura_enviar_email',
        ):
            with self.subTest(vista=nombre_url):
                respuesta = self.client.get(reverse(nombre_url, args=[99999]))
                self.assertEqual(respuesta.status_code, 302)

    def test_marcar_enviada_view_devuelve_403_json(self):
        respuesta = self.client.get(reverse('admin:solicitudfactura_marcar_enviada', args=[99999]))
        self.assertEqual(respuesta.status_code, 403)


class PermisosPorGrupoTest(TestCase):
    """Cada grupo accede a lo suyo y no a lo ajeno."""

    @classmethod
    def setUpTestData(cls):
        call_command('crear_grupos_permisos')

    def setUp(self):
        self.ventas = _crear_staff('vendedora', grupos=['Ventas'])
        self.contabilidad = _crear_staff('contadora', grupos=['Contabilidad'])
        self.nomina = _crear_staff('rrhh', grupos=['Nómina'])

    def test_ventas_accede_a_lo_suyo(self):
        self.client.force_login(self.ventas)
        for nombre_url in ('cartera_cxc', 'ver_calendario', 'reportes:cxc', 'reportes:cotizaciones',
                           'reportes:ocupacion', 'reportes:comparativo_airbnb'):
            with self.subTest(vista=nombre_url):
                respuesta = self.client.get(reverse(nombre_url))
                self.assertEqual(respuesta.status_code, 200)

    def test_ventas_no_accede_a_contabilidad_ni_nomina(self):
        self.client.force_login(self.ventas)
        for nombre_url in ('contabilidad:balanza', 'cargar_nomina', 'reportes:balanza', 'reportes:facturas'):
            with self.subTest(vista=nombre_url):
                respuesta = self.client.get(reverse(nombre_url))
                self.assertEqual(respuesta.status_code, 403)

    def test_contabilidad_accede_a_lo_suyo(self):
        self.client.force_login(self.contabilidad)
        for nombre_url in ('contabilidad:balanza', 'contabilidad:estado_resultados',
                           'reportes:balanza', 'reportes:estado_resultados', 'reportes:facturas'):
            with self.subTest(vista=nombre_url):
                respuesta = self.client.get(reverse(nombre_url))
                self.assertEqual(respuesta.status_code, 200)

    def test_contabilidad_no_accede_a_ventas_ni_nomina(self):
        self.client.force_login(self.contabilidad)
        for nombre_url in ('cartera_cxc', 'ver_calendario', 'cargar_nomina', 'reportes:cxc'):
            with self.subTest(vista=nombre_url):
                respuesta = self.client.get(reverse(nombre_url))
                self.assertEqual(respuesta.status_code, 403)

    def test_nomina_accede_a_lo_suyo(self):
        self.client.force_login(self.nomina)
        respuesta = self.client.get(reverse('jibble_diagnostico'))
        self.assertEqual(respuesta.status_code, 200)

    def test_nomina_no_accede_a_ventas_ni_contabilidad(self):
        self.client.force_login(self.nomina)
        for nombre_url in ('cartera_cxc', 'contabilidad:balanza', 'reportes:balanza'):
            with self.subTest(vista=nombre_url):
                respuesta = self.client.get(reverse(nombre_url))
                self.assertEqual(respuesta.status_code, 403)


class PermisosSuperusuarioTest(TestCase):
    """Un superusuario sigue accediendo a todo sin pertenecer a ningún grupo."""

    @classmethod
    def setUpTestData(cls):
        call_command('crear_grupos_permisos')

    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'jefa', 'jefa@quintakooxtanil.com', 'clave-de-prueba'
        )
        self.client.force_login(self.superusuario)

    def test_ninguna_vista_protegida_da_403(self):
        # 'configurar_plantilla_barra' se excluye: su plantilla
        # admin/comercial/configurar_plantilla_barra.html no existe en el
        # repo (bug preexistente, no relacionado con SEC-AUTHZ-001 — el
        # decorador de permiso funciona igual, pero invocar el cuerpo de la
        # vista revienta con TemplateDoesNotExist antes de llegar al render).
        for nombre_url, kwargs in VISTAS_PROTEGIDAS:
            if nombre_url == 'configurar_plantilla_barra':
                continue
            with self.subTest(vista=nombre_url):
                respuesta = self.client.get(reverse(nombre_url, kwargs=kwargs))
                self.assertNotEqual(respuesta.status_code, 403)


class ConstanteSistemaPermisoLecturaTest(TestCase):
    """Ventas consulta precios de referencia pero no los cambia (excepción del Issue #199)."""

    @classmethod
    def setUpTestData(cls):
        call_command('crear_grupos_permisos')

    def setUp(self):
        self.client.force_login(_crear_staff('vendedora2', grupos=['Ventas']))

    def test_puede_ver_la_lista_pero_no_agregar(self):
        self.assertEqual(
            self.client.get(reverse('admin:comercial_constantesistema_changelist')).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse('admin:comercial_constantesistema_add')).status_code, 403
        )


class SelectorReportesFiltradoTest(TestCase):
    """El centro de reportes no ofrece enlaces a áreas fuera del permiso del usuario."""

    @classmethod
    def setUpTestData(cls):
        call_command('crear_grupos_permisos')

    def test_ventas_no_ve_la_seccion_de_contabilidad(self):
        self.client.force_login(_crear_staff('vendedora3', grupos=['Ventas']))
        respuesta = self.client.get(reverse('reportes:selector'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context['puede_contabilidad'])
        self.assertTrue(respuesta.context['puede_comercial'])
        self.assertNotContains(respuesta, 'Balanza de Comprobación')

    def test_contabilidad_no_ve_la_seccion_comercial(self):
        self.client.force_login(_crear_staff('contadora2', grupos=['Contabilidad']))
        respuesta = self.client.get(reverse('reportes:selector'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context['puede_contabilidad'])
        self.assertFalse(respuesta.context['puede_comercial'])
        self.assertNotContains(respuesta, 'CxC — Antigüedad de Saldos')


class ComunicacionAdminPermisoTest(TestCase):
    """La bitácora de comunicación con clientes es de Ventas, no de las otras áreas."""

    @classmethod
    def setUpTestData(cls):
        call_command('crear_grupos_permisos')

    def test_ventas_ve_la_bitacora_otras_areas_no(self):
        self.client.force_login(_crear_staff('vendedora4', grupos=['Ventas']))
        self.assertEqual(
            self.client.get(reverse('admin:comunicacion_comunicacioncliente_changelist')).status_code, 200
        )

        self.client.logout()
        self.client.force_login(_crear_staff('contadora3', grupos=['Contabilidad']))
        self.assertEqual(
            self.client.get(reverse('admin:comunicacion_comunicacioncliente_changelist')).status_code, 403
        )


@override_settings(STORAGES=STORAGES_PRUEBA)
class DescargarArchivoPrivadoAlineadoTest(TestCase):
    """core_erp/descargas.py no se modificó: ya usa has_perm(view_<modelo>)
    dinámico (comentario propio en el archivo). Confirma que los grupos
    nuevos lo alinean solos, sin tocar esa vista."""

    @classmethod
    def setUpTestData(cls):
        call_command('crear_grupos_permisos')

    def setUp(self):
        cliente = Cliente.objects.create(nombre='Cliente Contrato', telefono='5550001111')
        cotizacion = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Evento', estado='CONFIRMADA',
            fecha_evento=date.today() + timedelta(days=30),
        )
        ItemCotizacion.objects.create(
            cotizacion=cotizacion, descripcion='Servicio', cantidad=1, precio_unitario=1000,
        )
        self.contrato = ContratoServicio(cotizacion=cotizacion, numero='CTR-TEST-001')
        self.contrato.archivo.save('contrato.pdf', ContentFile(b'%PDF-1.4 contenido de prueba'), save=False)
        self.contrato.save()
        self.url = reverse(
            'descargar_archivo_privado',
            args=['comercial', 'contratoservicio', 'archivo', self.contrato.pk],
        )

    def test_ventas_accede_nomina_no(self):
        self.client.force_login(_crear_staff('vendedora5', grupos=['Ventas']))
        self.assertEqual(self.client.get(self.url).status_code, 200)

        self.client.logout()
        self.client.force_login(_crear_staff('rrhh2', grupos=['Nómina']))
        self.assertEqual(self.client.get(self.url).status_code, 404)


class CrearGruposPermisosComandoTest(TestCase):
    """El comando en sí: idempotente y con la excepción de ConstanteSistema."""

    def test_correrlo_dos_veces_no_duplica_ni_falla(self):
        call_command('crear_grupos_permisos')
        total_primera = Group.objects.get(name='Ventas').permissions.count()
        call_command('crear_grupos_permisos')
        total_segunda = Group.objects.get(name='Ventas').permissions.count()
        self.assertEqual(total_primera, total_segunda)
        self.assertEqual(Group.objects.count(), 3)

    def test_constantesistema_solo_view_para_ventas(self):
        call_command('crear_grupos_permisos')
        ventas = Group.objects.get(name='Ventas')
        codenames = set(
            ventas.permissions.filter(
                content_type__app_label='comercial', content_type__model='constantesistema',
            ).values_list('codename', flat=True)
        )
        self.assertEqual(codenames, {'view_constantesistema'})
