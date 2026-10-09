"""
Reportes nuevos de la fase 5 (Issue #373) con datos de un mes armado a mano.

Periodo de prueba: febrero de 2026 (28 días; 8 sábados y domingos). Cada
reporte se comprueba en el servicio (cifras) y en el PDF o Excel que entrega
la vista; lo que cae fuera del periodo o no debe contar se crea a propósito.
"""
import io
from datetime import date, datetime, time
from decimal import Decimal

import openpyxl
import pdfplumber
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core_erp.test_utils import login_superuser_con_totp

D = Decimal
INICIO, FIN = date(2026, 2, 1), date(2026, 2, 28)
PERIODO = {'fecha_inicio': '2026-02-01', 'fecha_fin': '2026-02-28'}


class Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')

    def setUp(self):
        login_superuser_con_totp(self.client, self.usuario)

    def _texto(self, nombre, **params):
        r = self.client.get(reverse(f'reportes:{nombre}'), {**PERIODO, **params})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            return '\n'.join(p.extract_text() or '' for p in pdf.pages)

    def _excel(self, nombre):
        r = self.client.get(reverse(f'reportes:{nombre}'), {**PERIODO, 'formato': 'excel'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('_20260201_20260228.xlsx', r['Content-Disposition'])
        return openpyxl.load_workbook(io.BytesIO(r.content))

    @staticmethod
    def _cotizacion(nombre, fecha, tipo='EVENTO', estado='CONFIRMADA', personas=50, salida=None, precio='10000.00'):
        from comercial.models import Cliente, Cotizacion, ItemCotizacion

        cliente = Cliente.objects.create(nombre=nombre, telefono='5555550101')
        cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento=f'Evento {nombre}', tipo_servicio=tipo, fecha_evento=fecha,
            fecha_salida=salida, num_personas=personas, incluye_refrescos=False,
        )
        ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=D(precio))
        Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
        cot.refresh_from_db()
        return cot


class NominaTest(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from nomina.models import Empleado, ReciboNomina

        ana = Empleado.objects.create(nombre='Ana Pech', tarifa_base=D('50.00'))
        luis = Empleado.objects.create(nombre='Luis Can', tarifa_base=D('60.00'))
        for empleado, periodo, horas, total, estado in (
            (ana, '2026-02-02 al 2026-02-08', '40', '2000.00', 'PAGADO'),
            (ana, '2026-02-09 al 2026-02-15', '30', '1500.00', 'CALCULADO'),
            (luis, '2026-02-23 al 2026-03-01', '10', '600.00', 'PAGADO'),
            (ana, '2026-03-02 al 2026-03-08', '40', '2000.00', 'CALCULADO'),  # otro mes
            (luis, '2026-02-16 al 2026-02-22', '20', '1200.00', 'CANCELADO'),  # no cuenta
        ):
            ReciboNomina.objects.create(empleado=empleado, periodo=periodo, horas_trabajadas=D(horas),
                                        tarifa_aplicada=empleado.tarifa_base, total_pagado=D(total), estado=estado)

    def test_servicio(self):
        from reportes.services.operacion import NominaPeriodoService

        datos = NominaPeriodoService.generar(INICIO, FIN)
        self.assertEqual(len(datos['recibos']), 3)
        self.assertEqual(datos['total'], D('4100.00'))
        self.assertEqual(datos['pagado'], D('2600.00'))
        self.assertEqual(datos['pendiente'], D('1500.00'))
        self.assertEqual(datos['horas'], D('80'))
        self.assertEqual([e['nombre'] for e in datos['empleados']], ['Ana Pech', 'Luis Can'])

    def test_pdf_y_excel(self):
        texto = self._texto('nomina')
        self.assertIn('Nómina por periodo', texto)
        self.assertIn('Total 80.00 $4,100.00 $2,600.00 $1,500.00', texto)
        hoja = self._excel('nomina')['Por empleado']
        self.assertEqual([c.value for c in hoja[2]], ['Ana Pech', 'Mesero', 2, 70, 3500, 2000, 1500])


class GastosTest(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from comercial.models import Compra

        Compra.objects.create(proveedor_nombre='Abarrotes', categoria='INSUMOS', fecha_emision=date(2026, 2, 5),
                              subtotal=D('1000.00'), iva=D('160.00'), total=D('1160.00'),
                              uuid='aaaaaaaa-1111-2222-3333-444444444444')
        Compra.objects.create(proveedor_nombre='Tlapalería', categoria='LIMPIEZA', fecha_emision=date(2026, 2, 6),
                              subtotal=D('500.00'), total=D('500.00'))
        Compra.objects.create(proveedor_nombre='Sin clasificar', fecha_emision=date(2026, 2, 7),
                              subtotal=D('100.00'), total=D('100.00'))
        Compra.objects.create(proveedor_nombre='Marzo', categoria='INSUMOS', fecha_emision=date(2026, 3, 1),
                              subtotal=D('9000.00'), total=D('9000.00'))

    def test_servicio(self):
        from reportes.services.finanzas import GastosCategoriaService

        datos = GastosCategoriaService.generar(INICIO, FIN)
        t = datos['totales']
        self.assertEqual((t['total'], t['con_factura'], t['sin_factura'], t['iva']),
                         (D('1760.00'), D('1160.00'), D('600.00'), D('160.00')))
        self.assertEqual(datos['categorias'][0]['nombre'], 'Insumos para eventos')
        self.assertEqual(datos['sin_clasificar'], 1)

    def test_pdf_y_excel(self):
        texto = self._texto('gastos')
        self.assertIn('1 compra sin clasificar', texto)
        self.assertIn('Insumos para eventos 1 $1,000.00 $160.00 $0.00 $1,160.00', texto)
        hoja = self._excel('gastos')['Compras']
        self.assertEqual(hoja.max_row, 4)


class FlujoTest(Base):
    """Pólizas a mano sobre el banco 102.02.01: una de enero y dos de febrero."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from contabilidad.models import CuentaBancaria, CuentaContable, MovimientoContable, Poliza, UnidadNegocio

        banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        CuentaBancaria.objects.create(nombre='BBVA Maestra PYME', banco='BBVA', clabe='012345678901234599',
                                      cuenta_contable=banco)
        unidad = UnidadNegocio.objects.get(clave='QUINTA')
        contra = {c: CuentaContable.objects.get(codigo_sat=c) for c in ('301.01', '401.01.01', '601.02.05')}
        for fecha, origen, cuenta, debe, haber in (
            (date(2026, 1, 31), 'APERTURA', '301.01', '100000.00', '0'),
            (date(2026, 2, 10), 'PAGO_CLIENTE', '401.01.01', '11600.00', '0'),
            (date(2026, 2, 20), 'COMPRA', '601.02.05', '0', '2320.00'),
            (date(2026, 3, 2), 'COMPRA', '601.02.05', '0', '500.00'),
        ):
            tipo = 'I' if D(debe) else 'E'
            p = Poliza.objects.create(tipo=tipo, folio=Poliza.siguiente_folio(tipo, fecha), fecha=fecha,
                                      concepto=origen, unidad_negocio=unidad, estado='APLICADA', origen=origen,
                                      created_by=cls.usuario)
            MovimientoContable.objects.create(poliza=p, cuenta=banco, concepto=origen, debe=D(debe), haber=D(haber))
            MovimientoContable.objects.create(poliza=p, cuenta=contra[cuenta], concepto=origen,
                                              debe=D(haber), haber=D(debe))

    def test_servicio(self):
        from reportes.services.finanzas import FlujoEfectivoService

        datos = FlujoEfectivoService.generar(INICIO, FIN)
        self.assertEqual((datos['saldo_inicial'], datos['entradas'], datos['salidas'], datos['saldo_final']),
                         (D('100000.00'), D('11600.00'), D('2320.00'), D('109280.00')))
        self.assertEqual({o['nombre']: o['neto'] for o in datos['origenes']},
                         {'Pago de cliente': D('11600.00'), 'Compra/Gasto': D('-2320.00')})

    def test_pdf_y_excel(self):
        texto = self._texto('flujo')
        self.assertIn('Flujo neto $11,600.00 $2,320.00 $9,280.00', texto)
        self.assertIn('Febrero 2026 $11,600.00 $2,320.00 $9,280.00 $109,280.00', texto)
        hoja = self._excel('flujo')['Por cuenta']
        self.assertEqual([c.value for c in hoja[2]][1:], [100000, 11600, 2320, 109280])


class OcupacionTest(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from comercial.models import BloqueoFecha

        cls._cotizacion('Boda Sábado', date(2026, 2, 7))
        cls._cotizacion('Estancia', date(2026, 2, 13), tipo='HOSPEDAJE', estado='EJECUTADA', personas=4,
                        salida=date(2026, 2, 15), precio='2000.00')
        cls._cotizacion('Borrador', date(2026, 2, 20), estado='BORRADOR')
        BloqueoFecha.objects.create(fecha_inicio=date(2026, 2, 25), fecha_fin=date(2026, 2, 26))

    def test_servicio(self):
        from reportes.services.operacion import OcupacionService

        datos = OcupacionService.generar(INICIO, FIN)
        self.assertEqual((datos['dias_ocupados'], datos['dias_bloqueados'], datos['dias_libres']), (3, 2, 23))
        self.assertEqual(datos['ocupacion'], D('11.5'))       # 3 de 26 días vendibles
        self.assertEqual((datos['fines_ocupados'], datos['fines_semana']), (2, 8))
        self.assertEqual(len(datos['reservaciones']), 2, 'El borrador no aparta fecha')

    def test_pdf(self):
        texto = self._texto('ocupacion')
        self.assertIn('11.5 %', texto)
        self.assertIn('Hospedaje 1 2', texto)
        self.assertNotIn('Borrador', texto)


class DepositosTest(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from comercial.models import DepositoGarantia, MovimientoDeposito

        abierto = DepositoGarantia.objects.create(cotizacion=cls._cotizacion('Boda', date(2026, 2, 7)),
                                                  monto=D('1000.00'))
        MovimientoDeposito.objects.create(deposito=abierto, tipo='RECEPCION', monto=D('1000.00'),
                                          fecha=date(2026, 2, 3), metodo='TRANSFERENCIA')
        cerrado = DepositoGarantia.objects.create(cotizacion=cls._cotizacion('Fiesta', date(2026, 2, 14)),
                                                  monto=D('500.00'))
        for fecha, tipo, monto, metodo in (
            (date(2026, 1, 20), 'RECEPCION', '500.00', 'EFECTIVO'),
            (date(2026, 2, 16), 'RETENCION_SERVICIO', '200.00', 'NO_APLICA'),
            (date(2026, 2, 17), 'DEVOLUCION', '300.00', 'TRANSFERENCIA'),
        ):
            MovimientoDeposito.objects.create(deposito=cerrado, tipo=tipo, monto=D(monto), fecha=fecha, metodo=metodo)

    def test_servicio(self):
        from reportes.services.operacion import DepositosGarantiaService

        datos = DepositosGarantiaService.generar(INICIO, FIN)
        self.assertEqual((datos['recibido'], datos['devuelto'], datos['retenido']),
                         (D('1000.00'), D('300.00'), D('200.00')))
        self.assertEqual([a['folio'] for a in datos['abiertos']], ['COT-001'])
        self.assertEqual(datos['en_custodia'], D('1000.00'))
        self.assertEqual(datos['vencidos'], 1)  # el evento fue el 7: debía devolverse a más tardar el 14

    def test_pdf(self):
        texto = self._texto('depositos')
        self.assertIn('1 depósito con plazo de devolución vencido', texto)
        self.assertIn('Retención por servicio', texto)


class CortesiasTest(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from comercial.models import Descuento, DescuentoAplicado, Pago

        venta = cls._cotizacion('Boda', date(2026, 2, 21))
        borrador = cls._cotizacion('Solo cotizó', date(2026, 2, 22), estado='BORRADOR')
        cortesia = Descuento.objects.create(nombre='Cortesía familia', valor=D('10'), es_cortesia=True)
        promo = Descuento.objects.create(nombre='Promo febrero', valor=D('5'))
        for cot, regla, monto in ((venta, cortesia, '1000.00'), (venta, promo, '500.00'), (borrador, promo, '300.00')):
            DescuentoAplicado.objects.create(cotizacion=cot, descuento=regla, monto_aplicado=D(monto),
                                             modo_aplicacion='MANUAL')
        DescuentoAplicado.objects.update(fecha_aplicacion=timezone.make_aware(datetime.combine(date(2026, 2, 10), time(12))))
        Pago.objects.create(cotizacion=venta, monto=D('200.00'), metodo='CONDONACION', fecha_pago=date(2026, 2, 12))

    def test_servicio(self):
        from reportes.services.operacion import CortesiasDescuentosService

        datos = CortesiasDescuentosService.generar(INICIO, FIN)
        self.assertEqual((datos['cortesias'], datos['promociones'], datos['condonado'], datos['total']),
                         (D('1000.00'), D('500.00'), D('200.00'), D('1700.00')))
        self.assertEqual(datos['no_concretado'], D('300.00'))

    def test_pdf(self):
        texto = self._texto('cortesias')
        self.assertIn('Cortesía familia Cortesía 1 $1,000.00', texto)
        self.assertIn('$300.00 se aplicaron en cotizaciones que no se concretaron', texto)


class ConciliacionTest(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from contabilidad.models import (
            ConciliacionBancaria,
            CuentaBancaria,
            CuentaContable,
            EstadoCuentaBancario,
            MovimientoEstadoCuenta,
        )

        cuenta = CuentaBancaria.objects.create(nombre='BBVA Maestra PYME', banco='BBVA', clabe='012345678901234598',
                                               cuenta_contable=CuentaContable.objects.get(codigo_sat='102.02.01'))
        ConciliacionBancaria.objects.create(cuenta_bancaria=cuenta, mes=2, anio=2026, estado='CONCILIADA',
                                            saldo_segun_banco=D('5000.00'), saldo_segun_libros=D('5000.00'))
        ConciliacionBancaria.objects.create(cuenta_bancaria=cuenta, mes=3, anio=2026, diferencia=D('99.00'))
        estado = EstadoCuentaBancario.objects.create(cuenta_bancaria=cuenta, periodo_mes=2, periodo_anio=2026,
                                                     formato='PDF', estado='PROCESADO')
        MovimientoEstadoCuenta.objects.create(estado_cuenta=estado, fecha=date(2026, 2, 12),
                                              descripcion='COMISION MANEJO CUENTA', cargo=D('150.00'))

    def test_servicio(self):
        from reportes.services.finanzas import ConciliacionBancariaService

        datos = ConciliacionBancariaService.generar(INICIO, FIN)
        self.assertEqual(len(datos['conciliaciones']), 1, 'La de marzo queda fuera')
        self.assertEqual((datos['conciliadas'], datos['con_diferencia']), (1, 0))
        self.assertEqual(datos['pendientes_cargos'], D('150.00'))

    def test_pdf(self):
        texto = self._texto('conciliacion')
        self.assertIn('COMISION MANEJO CUENTA', texto)
        self.assertIn('Total sin conciliar $150.00', texto)


class KooxiTest(Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp, PaseAHumano

        en_febrero = timezone.make_aware(datetime.combine(date(2026, 2, 10), time(12)))
        conv = ConversacionWhatsApp.objects.create(telefono='529990000001')
        ConversacionWhatsApp.objects.filter(pk=conv.pk).update(created_at=en_febrero)
        for direccion, modelo, tokens in (('ENTRADA', '', 0), ('ENTRADA', '', 0),
                                          ('AGENTE', 'claude-sonnet-5-5', 1_000_000)):
            MensajeWhatsApp.objects.create(conversacion=conv, direccion=direccion, texto='hola', modelo=modelo,
                                           tokens_entrada=tokens, created_at=en_febrero)
        MensajeWhatsApp.objects.create(conversacion=conv, direccion='ENTRADA', texto='marzo',
                                       created_at=timezone.make_aware(datetime.combine(date(2026, 3, 1), time(12))))
        PaseAHumano.objects.create(conversacion=conv, motivo='Quiere hablar del pago', created_at=en_febrero)

    def test_servicio(self):
        from reportes.services.operacion import KooxiService

        datos = KooxiService.generar(INICIO, FIN)
        self.assertEqual((datos['mensajes_clientes'], datos['respuestas_ia'], datos['pases_a_humano']), (2, 1, 1))
        self.assertEqual(datos['consumo']['costo_usd'], D('2.00'))  # 1 M tokens de entrada a US$2
        self.assertEqual(datos['tasa_humano'], D('100.0'))

    def test_pdf_y_excel(self):
        texto = self._texto('kooxi')
        self.assertIn('US$2.00', texto)
        self.assertIn('Quiere hablar del pago 1', texto)
        hoja = self._excel('kooxi')['Resumen']
        self.assertEqual(hoja['B4'].value, 1)  # respuestas de la IA


class AccesoTest(Base):
    """Cada reporte exige el permiso de su área y queda en el historial."""

    REPORTES = ('nomina', 'gastos', 'flujo', 'ocupacion', 'depositos', 'cortesias', 'conciliacion', 'kooxi')

    def test_responden_vacios_en_pdf_y_excel_y_se_registran(self):
        from reportes.models import ReporteGenerado

        for nombre in self.REPORTES:
            with self.subTest(reporte=nombre):
                self._texto(nombre)
                self._excel(nombre)
        self.assertEqual(ReporteGenerado.objects.filter(formato='EXCEL').count(), len(self.REPORTES))

    def test_staff_sin_permiso_recibe_403(self):
        User.objects.create_user('ventas', password='clave-de-prueba-123', is_staff=True)
        self.client.login(username='ventas', password='clave-de-prueba-123')
        for nombre in self.REPORTES:
            with self.subTest(reporte=nombre):
                self.assertEqual(self.client.get(reverse(f'reportes:{nombre}'), PERIODO).status_code, 403)

    def test_el_selector_los_ofrece(self):
        html = self.client.get(reverse('reportes:selector')).content.decode()
        for nombre in self.REPORTES:
            self.assertIn(reverse(f'reportes:{nombre}'), html)
