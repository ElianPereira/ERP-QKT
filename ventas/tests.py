"""Atribución de ventas, grupo Vendedor y candados (Issue #505, PR 1B)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago, Producto
from comunicacion.models import Visita
from core_erp.test_utils import login_superuser_con_totp
from nomina.models import CondicionesPago, Empleado
from ventas import services_atribucion as svc
from ventas.permisos import es_vendedor

STORAGE = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
CLAVE = 'x' * 20


def crear_vendedora(nombre='Ana', telefono='9991234567', username='ana'):
    usuario = User.objects.create_user(username, password=CLAVE, is_staff=True)
    # El candado del acuerdo firmado se cubre en nomina; aquí se liga con update().
    e = Empleado.objects.create(nombre=nombre, puesto='VENTAS', telefono=telefono)
    Empleado.objects.filter(pk=e.pk).update(usuario=usuario)
    e.refresh_from_db()
    return e


def crear_cotizacion(cliente=None, usuario=None, estado='BORRADOR', **extra):
    cliente = cliente or Cliente.objects.create(nombre='Cliente Uno', telefono='9997654321')
    cot = Cotizacion.objects.create(cliente=cliente, nombre_evento='Evento', usuario=usuario,
                                    fecha_evento=timezone.localdate() + timedelta(days=60),
                                    incluye_refrescos=False, **extra)
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1,
                                  precio_unitario=Decimal('10000.00'))
    if estado != 'BORRADOR':
        Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
    cot.refresh_from_db()
    return cot


def visita_atendida(vendedor, telefono='9997654321', dias_atras=5):
    inicio = timezone.now() - timedelta(days=dias_atras)
    return Visita.objects.create(nombre='Visitante', telefono=telefono, inicio=inicio, estado='CONFIRMADA',
                                 vendedor=vendedor, resultado='ATENDIDA')


class AtribuirTest(TestCase):

    def setUp(self):
        self.ana = crear_vendedora()
        self.otra = crear_vendedora('Bea', '9990000001', 'bea')

    def test_no_pisa_una_atribucion_existente(self):
        cot = crear_cotizacion()
        self.assertTrue(svc.atribuir(cot, self.ana, 'COTIZO'))
        self.assertFalse(svc.atribuir(cot, self.otra, 'VISITA'))
        cot.refresh_from_db()
        self.assertEqual((cot.vendedor, cot.vendedor_origen), (self.ana, 'COTIZO'))
        self.assertIsNotNone(cot.vendedor_asignado_en)

    def test_se_congela_al_confirmarse(self):
        cot = crear_cotizacion(estado='CONFIRMADA')
        self.assertFalse(svc.atribuir(cot, self.ana, 'VISITA'))
        cot.refresh_from_db()
        self.assertIsNone(cot.vendedor)

    def test_solo_superusuario_cambia_despues_de_confirmar(self):
        cot = crear_cotizacion(estado='CONFIRMADA')
        sin_poder = User.objects.create_user('x', password=CLAVE, is_staff=True)
        self.assertFalse(svc.atribuir(cot, self.ana, 'MANUAL', usuario=sin_poder, forzar=True))
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        self.assertTrue(svc.atribuir(cot, self.ana, 'MANUAL', usuario=su, forzar=True))
        self.assertTrue(svc.atribuir(cot, self.otra, 'MANUAL', usuario=su, forzar=True))
        cot.refresh_from_db()
        self.assertEqual((cot.vendedor, cot.vendedor_origen, cot.vendedor_asignado_por), (self.otra, 'MANUAL', su))

    def test_historica_nunca_se_atribuye(self):
        cot = crear_cotizacion()
        Cotizacion.objects.filter(pk=cot.pk).update(es_historica=True)
        cot.refresh_from_db()
        self.assertFalse(svc.atribuir(cot, self.ana, 'COTIZO'))

    def test_no_recotiza(self):
        cot = crear_cotizacion()
        precio = cot.precio_final
        svc.atribuir(cot, self.ana, 'COTIZO')
        cot.refresh_from_db()
        self.assertEqual(cot.precio_final, precio)

    def test_vendedor_de_usuario(self):
        self.assertEqual(svc.vendedor_de_usuario(self.ana.usuario), self.ana)
        Empleado.objects.filter(pk=self.ana.pk).update(activo=False)
        self.assertIsNone(svc.vendedor_de_usuario(self.ana.usuario))
        self.assertIsNone(svc.vendedor_de_usuario(User.objects.create_user('z', password=CLAVE)))
        mesero = Empleado.objects.create(nombre='Luis', puesto='MESERO')
        u = User.objects.create_user('luis', password=CLAVE)
        Empleado.objects.filter(pk=mesero.pk).update(usuario=u)
        self.assertIsNone(svc.vendedor_de_usuario(u))

    def test_baja_vencida_ya_no_atribuye(self):
        Empleado.objects.filter(pk=self.ana.pk).update(fecha_baja=timezone.localdate() - timedelta(days=1))
        self.assertIsNone(svc.vendedor_de_usuario(self.ana.usuario))


class AtribucionAutomaticaTest(TestCase):
    """Cotizaciones de la web o Kooxi: sin usuario del ERP."""

    def setUp(self):
        self.ana = crear_vendedora()

    def test_visita_atendida_reciente_atribuye(self):
        visita_atendida(self.ana)
        cot = crear_cotizacion()
        self.assertEqual((cot.vendedor, cot.vendedor_origen), (self.ana, 'VISITA'))

    def test_visita_vieja_no_atribuye(self):
        visita_atendida(self.ana, dias_atras=45)
        self.assertIsNone(crear_cotizacion().vendedor)

    def test_visita_que_no_llego_o_de_otro_telefono_no_atribuye(self):
        v = visita_atendida(self.ana)
        Visita.objects.filter(pk=v.pk).update(resultado='NO_LLEGO')
        self.assertIsNone(crear_cotizacion().vendedor)
        Visita.objects.filter(pk=v.pk).update(resultado='ATENDIDA', telefono='9990000099')
        self.assertIsNone(crear_cotizacion().vendedor)

    def test_telefono_con_otro_formato_coincide(self):
        visita_atendida(self.ana, telefono='+52 999 765 4321')
        self.assertEqual(crear_cotizacion().vendedor, self.ana)

    def test_la_ventana_sale_de_las_condiciones(self):
        from datetime import date

        c = CondicionesPago.objects.create(empleado=self.ana, vigente_desde=date(2026, 1, 1), pago_visita=Decimal('100'),
                                           dias_atribucion=60, estado='ACEPTADA')
        self.assertEqual(c.estado, 'ACEPTADA')
        visita_atendida(self.ana, dias_atras=45)
        self.assertEqual(crear_cotizacion().vendedor, self.ana)

    def test_seguimiento_de_su_cotizacion_reciente(self):
        cliente = Cliente.objects.create(nombre='C', telefono='9990001111')
        suya = crear_cotizacion(cliente=cliente, usuario=self.ana.usuario)
        svc.atribuir(suya, self.ana, 'COTIZO')
        nueva = crear_cotizacion(cliente=cliente)
        self.assertEqual((nueva.vendedor, nueva.vendedor_origen), (self.ana, 'SEGUIMIENTO'))

    def test_cliente_recurrente_fuera_de_ventana_no_es_suyo(self):
        cliente = Cliente.objects.create(nombre='C', telefono='9990001111')
        suya = crear_cotizacion(cliente=cliente, usuario=self.ana.usuario)
        svc.atribuir(suya, self.ana, 'COTIZO')
        Cotizacion.objects.filter(pk=suya.pk).update(created_at=timezone.now() - timedelta(days=40))
        self.assertIsNone(crear_cotizacion(cliente=cliente).vendedor)

    def test_una_cotizacion_de_usuario_no_pasa_por_las_reglas_automaticas(self):
        visita_atendida(self.ana)
        otro = User.objects.create_user('otro', password=CLAVE, is_staff=True)
        self.assertIsNone(crear_cotizacion(usuario=otro).vendedor)

    def test_cerrar_visita_atribuye_las_abiertas_del_telefono(self):
        abierta = crear_cotizacion()
        confirmada = crear_cotizacion(estado='CONFIRMADA')
        visita = visita_atendida(self.ana, dias_atras=0)
        self.assertEqual(svc.atribuir_por_visita(visita), 1)
        abierta.refresh_from_db()
        confirmada.refresh_from_db()
        self.assertEqual(abierta.vendedor, self.ana)
        self.assertIsNone(confirmada.vendedor)

    def test_un_fallo_no_tumba_el_alta(self):
        from unittest.mock import patch

        with patch('ventas.services_atribucion.atribuir_automatica', side_effect=RuntimeError('boom')):
            self.assertIsNotNone(crear_cotizacion().pk)


@override_settings(STORAGES=STORAGE)
class AdminCotizacionTest(TestCase):

    def setUp(self):
        call_command('crear_grupos_permisos', verbosity=0)
        self.ana = crear_vendedora()
        self.ana.usuario.groups.add(Group.objects.get(name='Vendedor'))
        self.cliente = Cliente.objects.create(nombre='Cliente Uno', telefono='9997654321')

    def test_alta_en_el_admin_la_atribuye_a_quien_la_crea(self):
        from django.contrib import admin as dj_admin
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from comercial.admin import CotizacionAdmin

        peticion = RequestFactory().post('/')
        peticion.user = self.ana.usuario
        peticion.session = {}
        peticion._messages = FallbackStorage(peticion)
        cot = Cotizacion(cliente=self.cliente, nombre_evento='Boda', tipo_servicio='EVENTO',
                         fecha_evento=timezone.localdate() + timedelta(days=90), num_personas=50)
        CotizacionAdmin(Cotizacion, dj_admin.site).save_model(peticion, cot, form=None, change=False)
        cot.refresh_from_db()
        self.assertEqual((cot.vendedor, cot.vendedor_origen, cot.usuario), (self.ana, 'COTIZO', self.ana.usuario))

    def test_alta_por_un_usuario_sin_empleado_no_se_atribuye(self):
        from django.contrib import admin as dj_admin
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from comercial.admin import CotizacionAdmin

        peticion = RequestFactory().post('/')
        peticion.user = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        peticion.session = {}
        peticion._messages = FallbackStorage(peticion)
        cot = Cotizacion(cliente=self.cliente, nombre_evento='Boda', tipo_servicio='EVENTO',
                         fecha_evento=timezone.localdate() + timedelta(days=90), num_personas=50)
        CotizacionAdmin(Cotizacion, dj_admin.site).save_model(peticion, cot, form=None, change=False)
        cot.refresh_from_db()
        self.assertIsNone(cot.vendedor)

    def test_folio_muestra_vendedor_en_lista_y_pdf(self):
        cot = crear_cotizacion()
        svc.atribuir(cot, self.ana, 'COTIZO')
        self.client.force_login(self.ana.usuario)
        html = self.client.get(reverse('admin:comercial_cotizacion_changelist')).content.decode()
        self.assertIn('Ana', html)
        r = self.client.get(reverse('admin:comercial_cotizacion_change', args=[cot.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('Atendido por', r.content.decode())
        from django.template.loader import render_to_string

        from comercial.views import obtener_contexto_cotizacion
        texto = render_to_string('cotizaciones/pdf_recibo.html', {**obtener_contexto_cotizacion(cot)})
        self.assertIn('Atendido por', texto)
        self.assertIn('Ana', texto)

    def test_cotizacion_sin_vendedor_no_dice_atendido_por_en_el_pdf(self):
        from django.template.loader import render_to_string

        from comercial.views import obtener_contexto_cotizacion
        texto = render_to_string('cotizaciones/pdf_recibo.html', {**obtener_contexto_cotizacion(crear_cotizacion())})
        self.assertNotIn('Atendido por', texto)

    def test_accion_asignar_vendedor_solo_superusuario(self):
        cot = crear_cotizacion(estado='CONFIRMADA')
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        url = reverse('admin:comercial_cotizacion_changelist')
        base = {'action': 'asignar_vendedor', ACTION_CHECKBOX_NAME: [cot.pk]}
        r = self.client.post(url, base)
        self.assertEqual(r.status_code, 200)  # pide elegir y confirmar
        cot.refresh_from_db()
        self.assertIsNone(cot.vendedor)
        self.client.post(url, {**base, 'confirmar': 'si', 'vendedor': self.ana.pk})
        cot.refresh_from_db()
        self.assertEqual((cot.vendedor, cot.vendedor_origen), (self.ana, 'MANUAL'))

    def test_vendedor_no_ve_acciones(self):
        crear_cotizacion()
        self.client.force_login(self.ana.usuario)
        r = self.client.post(reverse('admin:comercial_cotizacion_changelist'),
                             {'action': 'asignar_vendedor', ACTION_CHECKBOX_NAME: [1]})
        self.assertNotEqual(r.status_code, 500)
        self.assertFalse(Cotizacion.objects.filter(vendedor__isnull=False).exists())


@override_settings(STORAGES=STORAGE)
class GrupoVendedorTest(TestCase):

    def setUp(self):
        call_command('crear_grupos_permisos', verbosity=0)
        self.ana = crear_vendedora()
        self.ana.usuario.groups.add(Group.objects.get(name='Vendedor'))
        self.client.force_login(self.ana.usuario)

    def test_permisos_del_grupo(self):
        permisos = set(Group.objects.get(name='Vendedor').permissions.values_list('content_type__app_label',
                                                                                  'codename'))
        for esperado in (('comercial', 'add_cotizacion'), ('comercial', 'change_cliente'), ('comercial', 'add_pago'),
                         ('comercial', 'view_producto'), ('comunicacion', 'change_visita'),
                         ('comercial', 'view_bloqueofecha')):
            self.assertIn(esperado, permisos)
        for prohibido in ('delete_cotizacion', 'delete_pago', 'change_pago', 'delete_cliente', 'add_descuento',
                          'view_descuento', 'view_compra', 'view_constantesistema', 'add_producto',
                          'view_recibonomina', 'view_poliza'):
            self.assertNotIn(prohibido, {c for _, c in permisos})
        apps = {a for a, _ in permisos}
        self.assertTrue(apps <= {'comercial', 'comunicacion', 'ventas'})
        # De `ventas` solo el permiso de su propia pantalla «Mis comisiones».
        self.assertEqual({c for a, c in permisos if a == 'ventas'}, {'ver_mis_comisiones'})

    def test_es_vendedor(self):
        self.assertTrue(es_vendedor(self.ana.usuario))
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        su.groups.add(Group.objects.get(name='Vendedor'))
        self.assertFalse(es_vendedor(su))

    def test_vistas_financieras_prohibidas(self):
        for nombre in ('contabilidad:balanza', 'contabilidad:estado_resultados', 'cargar_nomina', 'sync_jibble',
                       'crear_solicitud', 'reportes:balanza', 'reportes:estado_resultados', 'reportes:cxc',
                       'reportes:cotizaciones', 'reportes:rentabilidad', 'reportes:pagos', 'cartera_cxc',
                       'configurar_plantilla_barra'):
            with self.subTest(vista=nombre):
                r = self.client.get(reverse(nombre))
                self.assertIn(r.status_code, (302, 403, 404))
                if r.status_code == 302:
                    self.assertNotIn('/change/', r['Location'])

    def test_modulos_prohibidos_en_el_admin(self):
        for modelo in ('comercial_descuento', 'comercial_compra', 'comercial_constantesistema',
                       'nomina_recibonomina', 'nomina_empleado', 'contabilidad_poliza', 'facturacion_solicitudfactura',
                       'comercial_portalcliente'):
            with self.subTest(modelo=modelo):
                r = self.client.get(reverse(f'admin:{modelo}_changelist'))
                self.assertIn(r.status_code, (302, 403))

    def test_dashboard_sin_finanzas(self):
        crear_cotizacion(estado='CONFIRMADA')
        r = self.client.get(reverse('admin_dashboard'))
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        for oculto in ('Ventas del mes', 'Gastos del mes', 'Utilidad del mes', 'financeChart', 'Finanzas'):
            self.assertNotIn(oculto, html)
        self.assertNotIn('ventas_mes_quinta', r.context)
        self.assertIn('Agenda', html)

    def test_dashboard_de_dirección_sigue_igual(self):
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        html = self.client.get(reverse('admin_dashboard')).content.decode()
        self.assertIn('Ventas del mes', html)

    def test_no_ve_costos_ni_margenes_en_productos(self):
        producto = Producto.objects.create(nombre='Silla', precio_venta_fijo=Decimal('100.00'),
                                           margen_ganancia=Decimal('1.80'))
        lista = self.client.get(reverse('admin:comercial_producto_changelist')).content.decode()
        self.assertNotIn('Costo (sin IVA)', lista)
        self.assertIn('Silla', lista)
        ficha = self.client.get(reverse('admin:comercial_producto_change', args=[producto.pk]))
        self.assertEqual(ficha.status_code, 200)
        self.assertNotIn('margen_ganancia', ficha.content.decode())

    def test_no_ve_costos_en_la_cotizacion(self):
        cot = crear_cotizacion()
        html = self.client.get(reverse('admin:comercial_cotizacion_change', args=[cot.pk])).content.decode()
        for oculto in ('factor_utilidad_barra', 'Reporte Ejecutivo', 'Insumos Base', 'insumo_hielo'):
            self.assertNotIn(oculto, html)
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        html = self.client.get(reverse('admin:comercial_cotizacion_change', args=[cot.pk])).content.decode()
        self.assertIn('factor_utilidad_barra', html)

    def test_no_cancela_cotizaciones(self):
        cot = crear_cotizacion()
        from django.contrib import admin as dj_admin
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from comercial.admin import CotizacionAdmin

        peticion = RequestFactory().post('/')
        peticion.user = self.ana.usuario
        peticion.session = {}
        peticion._messages = FallbackStorage(peticion)
        cot.estado, cot.motivo_cancelacion = 'CANCELADA', 'x'
        ma = CotizacionAdmin(Cotizacion, dj_admin.site)
        ma.save_model(peticion, cot, form=None, change=True)
        cot.refresh_from_db()
        self.assertNotEqual(cot.estado, 'CANCELADA')

    def test_no_aplica_descuentos(self):
        cot = crear_cotizacion()
        r = self.client.get(reverse('admin:cotizacion_descuentos', args=[cot.pk]))
        self.assertEqual(r.status_code, 302)

    def test_pagos_no_reembolso_ni_condonacion(self):
        cot = crear_cotizacion()
        from django.contrib import admin as dj_admin
        from django.test import RequestFactory

        from comercial.admin import PagoAdmin

        peticion = RequestFactory().get('/')
        peticion.user = self.ana.usuario
        Form = PagoAdmin(Pago, dj_admin.site).get_form(peticion)
        base = {'cotizacion': cot.pk, 'monto': '100', 'fecha_pago': timezone.localdate().isoformat(),
                'referencia': '', 'notas': '', 'concepto': 'VENTA'}
        malo = Form({**base, 'tipo': 'REEMBOLSO', 'metodo': 'EFECTIVO'})
        self.assertFalse(malo.is_valid())
        self.assertIn('Dirección', ' '.join(malo.non_field_errors()))
        malo = Form({**base, 'tipo': 'INGRESO', 'metodo': 'CONDONACION'})
        self.assertFalse(malo.is_valid())
        bueno = Form({**base, 'tipo': 'INGRESO', 'metodo': 'EFECTIVO'})
        self.assertNotIn('Dirección', ' '.join(bueno.non_field_errors()))

    def test_pago_nuevo_desde_el_admin_guarda_quien_lo_registro(self):
        cot = crear_cotizacion(estado='CONFIRMADA')
        r = self.client.post(reverse('admin:comercial_pago_add'), {
            'tipo': 'INGRESO', 'concepto': 'VENTA', 'cotizacion': cot.pk, 'fecha_pago': timezone.localdate().isoformat(),
            'monto': '500.00', 'metodo': 'EFECTIVO', 'referencia': '', 'notas': ''})
        pago = Pago.objects.filter(cotizacion=cot).first()
        self.assertIsNotNone(pago, f'status {r.status_code}')
        self.assertEqual(pago.usuario, self.ana.usuario)

    def test_calendario_y_agenda_accesibles(self):
        self.assertEqual(self.client.get(reverse('calendario_unificado')).status_code, 200)


class PagoPorTerminalUsuarioTest(TestCase):

    def test_usuario_de_telefono(self):
        ana = crear_vendedora(telefono='9991234567')
        self.assertEqual(svc.usuario_de_telefono('+52 1 999 123 4567'), ana.usuario)
        self.assertIsNone(svc.usuario_de_telefono('9990000000'))
        self.assertIsNone(svc.usuario_de_telefono(''))


class AvisosConVendedorTest(TestCase):

    def setUp(self):
        self.ana = crear_vendedora()

    def test_alerta_de_cotizacion_nueva_dice_quien_atiende(self):
        from unittest.mock import patch

        from comunicacion import services_notificaciones as sn

        cot = crear_cotizacion()
        svc.atribuir(cot, self.ana, 'VISITA')
        with patch.object(sn, 'alertar_equipo_email') as correo, patch.object(sn, 'telefonos_aviso', return_value=[]):
            sn.alertar_equipo_nueva_cotizacion(cot)
        self.assertIn('Atendido por: Ana', correo.call_args.kwargs['cuerpo'])

    def test_alerta_sin_vendedor_no_lo_menciona(self):
        from unittest.mock import patch

        from comunicacion import services_notificaciones as sn

        with patch.object(sn, 'alertar_equipo_email') as correo, patch.object(sn, 'telefonos_aviso', return_value=[]):
            sn.alertar_equipo_nueva_cotizacion(crear_cotizacion())
        self.assertNotIn('Atendido por', correo.call_args.kwargs['cuerpo'])

    def test_evento_del_calendario_lleva_al_vendedor(self):
        from comercial.views_calendario import _construir_eventos_calendario

        cot = crear_cotizacion()
        svc.atribuir(cot, self.ana, 'COTIZO')
        hoy = timezone.localdate()
        eventos = _construir_eventos_calendario(hoy, hoy + timedelta(days=120))
        titulos = [e['title'] for e in eventos if e.get('extendedProps', {}).get('tipo') == 'evento']
        self.assertTrue(any(t.endswith('· Ana') for t in titulos), titulos)

    @override_settings(META_WA_KOOXI_NUMERO='5299916919191')
    def test_portal_muestra_tu_asesora(self):
        cot = crear_cotizacion()
        svc.atribuir(cot, self.ana, 'COTIZO')
        cot.refresh_from_db()
        from comercial.models import PortalCliente

        portal = PortalCliente.objects.get(cotizacion=cot)
        html = self.client.get(reverse('portal_evento', args=[portal.token])).content.decode()
        self.assertIn('Tu asesora', html)
        self.assertIn('Ana', html)
        self.assertNotIn('9991234567', html)  # nunca su número personal
        self.assertIn('wa.me/', html)

    def test_portal_sin_vendedor_no_muestra_asesora(self):
        from comercial.models import PortalCliente

        cot = crear_cotizacion()
        portal = PortalCliente.objects.get(cotizacion=cot)
        html = self.client.get(reverse('portal_evento', args=[portal.token])).content.decode()
        self.assertNotIn('Tu asesora', html)
