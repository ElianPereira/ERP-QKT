"""
Listas del admin de la fase 1 del Issue #322: filtros de negocio, barra de
controles y columnas con los componentes de core_erp/admin_ui.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, Compra, Cotizacion, ItemCotizacion, Pago, Producto
from contabilidad.models import Poliza, UnidadNegocio
from core_erp.test_utils import login_superuser_con_totp
from facturacion.models import SolicitudFactura

URL_COT = '/admin/comercial/cotizacion/'


def _cotizacion(nombre, dias, precio=Decimal('1000.00'), **kwargs):
    cliente = Cliente.objects.create(nombre=f'Cliente {nombre}', tipo_persona='FISICA', telefono='9991234567')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento=nombre,
        fecha_evento=timezone.localdate() + timedelta(days=dias),
        incluye_refrescos=False, **kwargs,
    )
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=precio)
    cot.refresh_from_db()
    return cot


class ListaAdminBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        login_superuser_con_totp(self.client, self.admin)

    def ids(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return {obj.pk for obj in response.context['cl'].result_list}


class FiltrosCotizacionTest(ListaAdminBase):
    def test_fecha_proximos_7_incluye_hoy_y_excluye_pasados_y_lejanos(self):
        hoy = _cotizacion('Hoy', 0)
        en_7 = _cotizacion('En siete', 7)
        en_8 = _cotizacion('En ocho', 8)
        ayer = _cotizacion('Ayer', -1)

        self.assertEqual(self.ids(URL_COT, cuando='prox7'), {hoy.pk, en_7.pk})
        self.assertEqual(self.ids(URL_COT, cuando='pasados'), {ayer.pk})
        self.assertIn(en_8.pk, self.ids(URL_COT, cuando='prox30'))

    def test_pago_con_saldo_pagada_y_sin_pagos(self):
        sin_pagos = _cotizacion('Sin pagos', 20)
        parcial = _cotizacion('Parcial', 20)
        pagada = _cotizacion('Pagada', 20)
        Cotizacion.objects.filter(pk__in=[sin_pagos.pk, parcial.pk, pagada.pk]).update(estado='CONFIRMADA')
        Pago.objects.create(cotizacion=parcial, monto=Decimal('100.00'), metodo='EFECTIVO', usuario=self.admin)
        Pago.objects.create(cotizacion=pagada, monto=pagada.precio_final, metodo='EFECTIVO', usuario=self.admin)

        self.assertEqual(self.ids(URL_COT, pago='saldo'), {sin_pagos.pk, parcial.pk})
        self.assertEqual(self.ids(URL_COT, pago='pagada'), {pagada.pk})
        self.assertEqual(self.ids(URL_COT, pago='sin'), {sin_pagos.pk})

    def test_saldo_no_cuenta_reembolsos_como_pago(self):
        cot = _cotizacion('Reembolsada', 20)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        Pago.objects.create(cotizacion=cot, monto=cot.precio_final, metodo='EFECTIVO', usuario=self.admin)
        Pago.objects.create(cotizacion=cot, tipo='REEMBOLSO', monto=cot.precio_final, metodo='EFECTIVO',
                            usuario=self.admin)

        self.assertIn(cot.pk, self.ids(URL_COT, pago='saldo'))

    def test_servicio_y_paquete_de_barra(self):
        evento = _cotizacion('Evento', 10)
        pasadia = _cotizacion('Pasadía', 10, tipo_servicio='PASADIA')
        Cotizacion.objects.filter(pk=evento.pk).update(incluye_cerveza=True, incluye_refrescos=True,
                                                       incluye_licor_nacional=True)

        self.assertEqual(self.ids(URL_COT, tipo_servicio__exact='PASADIA'), {pasadia.pk})
        self.assertEqual(self.ids(URL_COT, barra='plus'), {evento.pk})
        self.assertEqual(self.ids(URL_COT, barra='sin'), {pasadia.pk})

    def test_columnas_y_barra_nueva(self):
        cot = _cotizacion('Boda', 5)
        response = self.client.get(URL_COT, {'cuando': 'prox7'})
        html = response.content.decode()

        self.assertIn('qkt-barra', html)
        self.assertIn('Fecha del evento:', html)
        # chip del filtro aplicado con su enlace para quitarlo
        self.assertIn('qkt-chip', html)
        self.assertIn('Próximos 7 días', html)
        # acciones por fila: PDF a la vista y menú con la lista de compras
        self.assertIn(f'/cotizacion/{cot.pk}/pdf/', html)
        self.assertIn('<details class="qkt-menu">', html)
        self.assertIn('Lista de compras', html)
        # ya no quedan los filtros que no servían para buscar
        self.assertNotIn('Licores Nacionales:', html)
        self.assertNotIn('Clima:', html)


class FiltrosPagoTest(ListaAdminBase):
    URL = '/admin/comercial/pago/'

    def test_facturacion_y_estado_de_la_cotizacion(self):
        cot = _cotizacion('Pagos', 30)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        con = Pago.objects.create(cotizacion=cot, monto=Decimal('100.00'), metodo='EFECTIVO', usuario=self.admin)
        sin = Pago.objects.create(cotizacion=cot, monto=Decimal('50.00'), metodo='EFECTIVO', usuario=self.admin)
        if not SolicitudFactura.objects.filter(pago=con).exists():
            self.skipTest('El signal de facturación no creó la solicitud en este entorno')
        SolicitudFactura.objects.filter(pago=sin).delete()

        self.assertEqual(self.ids(self.URL, factura='solicitada'), {con.pk})
        self.assertEqual(self.ids(self.URL, factura='sin'), {sin.pk})
        self.assertEqual(self.ids(self.URL, estado_cot='CONFIRMADA'), {con.pk, sin.pk})
        self.assertEqual(self.ids(self.URL, estado_cot='CANCELADA'), set())

    def test_periodo_este_mes(self):
        cot = _cotizacion('Periodo', 30)
        hoy = timezone.localdate()
        este = Pago.objects.create(cotizacion=cot, monto=Decimal('10.00'), metodo='EFECTIVO', fecha_pago=hoy,
                                   usuario=self.admin)
        viejo = Pago.objects.create(cotizacion=cot, monto=Decimal('10.00'), metodo='EFECTIVO',
                                    fecha_pago=hoy - timedelta(days=70), usuario=self.admin)

        ids = self.ids(self.URL, fecha_pago_periodo='mes')
        self.assertIn(este.pk, ids)
        self.assertNotIn(viejo.pk, ids)

    def test_reembolso_en_rojo_y_negativo(self):
        cot = _cotizacion('Reembolso', 30)
        Pago.objects.create(cotizacion=cot, monto=Decimal('100.00'), metodo='EFECTIVO', usuario=self.admin)
        Pago.objects.create(cotizacion=cot, tipo='REEMBOLSO', monto=Decimal('75.00'), metodo='EFECTIVO',
                            usuario=self.admin)
        html = self.client.get(self.URL).content.decode()
        self.assertIn('qkt-num--error', html)
        self.assertIn('-$75.00', html)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class FiltrosCompraTest(ListaAdminBase):
    URL = '/admin/comercial/compra/'

    def test_cfdi_y_poliza(self):
        hoy = timezone.localdate()
        con_cfdi = Compra.objects.create(fecha_emision=hoy, total=Decimal('100.00'), proveedor_nombre='A',
                                         uuid='A1B2C3D4-0000-0000-0000-000000000001')
        sin_cfdi = Compra.objects.create(fecha_emision=hoy, total=Decimal('200.00'), proveedor_nombre='B')

        self.assertEqual(self.ids(self.URL, cfdi='con'), {con_cfdi.pk})
        self.assertEqual(self.ids(self.URL, cfdi='sin'), {sin_cfdi.pk})

        ct = ContentType.objects.get_for_model(Compra)
        polizas = Poliza.objects.filter(content_type=ct)
        aplicadas = set(polizas.filter(estado='APLICADA').values_list('object_id', flat=True))
        borrador = set(polizas.filter(estado='BORRADOR').values_list('object_id', flat=True)) - aplicadas
        con_poliza = set(polizas.exclude(estado='CANCELADA').values_list('object_id', flat=True))
        todas = {con_cfdi.pk, sin_cfdi.pk}

        self.assertEqual(self.ids(self.URL, poliza='aplicada'), aplicadas & todas)
        self.assertEqual(self.ids(self.URL, poliza='borrador'), borrador & todas)
        self.assertEqual(self.ids(self.URL, poliza='sin'), todas - con_poliza)

    def test_sin_cfdi_se_marca_como_alerta(self):
        Compra.objects.create(fecha_emision=timezone.localdate(), total=Decimal('1.00'), proveedor_nombre='C')
        html = self.client.get(self.URL).content.decode()
        self.assertIn('Sin CFDI', html)
        self.assertIn('Fiscal:', html)


class FiltrosProductoTest(ListaAdminBase):
    URL = '/admin/comercial/producto/'

    def test_servicio_junta_los_interruptores(self):
        evento = Producto.objects.create(nombre='Silla', cotizador_evento=True)
        hospedaje = Producto.objects.create(nombre='Desayuno', cotizador_hospedaje=True)

        self.assertEqual(self.ids(self.URL, servicio='evento') & {evento.pk, hospedaje.pk}, {evento.pk})
        self.assertEqual(self.ids(self.URL, servicio='hospedaje') & {evento.pk, hospedaje.pk}, {hospedaje.pk})

    def test_titulos_de_negocio_en_la_barra(self):
        html = self.client.get(self.URL).content.decode()
        self.assertIn('Visible en cotizador:', html)
        self.assertNotIn('Mostrar en cotizador web:', html)


class ListaPolizaTest(ListaAdminBase):
    URL = '/admin/contabilidad/poliza/'

    def test_unidad_de_negocio_se_oculta_con_una_sola_activa(self):
        UnidadNegocio.objects.exclude(pk=UnidadNegocio.objects.order_by('pk').first().pk).update(activa=False)
        html = self.client.get(self.URL).content.decode()
        self.assertNotIn('Unidad de negocio:', html)
        self.assertIn('Origen:', html)

    def test_estado_en_badge_de_la_guia(self):
        unidad = UnidadNegocio.objects.first()
        Poliza.objects.create(tipo='D', fecha=timezone.localdate(), concepto='Prueba', unidad_negocio=unidad,
                              folio=1, estado='CANCELADA', created_by=self.admin)
        html = self.client.get(self.URL).content.decode()
        self.assertIn('qkt-badge qkt-badge--error', html)
