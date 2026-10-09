"""
Funcionamiento de los reportes con un mes contable real (Issue #373).

No basta con que el PDF se genere: se arma un mes con pólizas sobre las
cuentas reales del catálogo sembrado (banco de nivel 4, IVA, capital,
costo, gasto, ingreso financiero 402) y se comprueban las cifras tanto en
el servicio como en el texto del PDF que entrega la vista.

Mes de prueba (todo en la unidad QUINTA, enero 2026):
  - Aportación de capital ............ Banco 100,000.00 / Capital social
  - Venta de evento con IVA .......... Banco 11,600.00 / Ingreso 10,000.00 + IVA 1,600.00
  - Costo de alimentos ............... Costo 3,000.00 / Banco
  - Gasto con IVA acreditable ........ Gasto 2,000.00 + IVA 320.00 / Banco 2,320.00
  - Intereses bancarios .............. Banco 50.00 / Ingresos financieros (402.01)

Resultado esperado: ingresos 10,000.00, otros 50.00, costos 3,000.00,
gastos 2,000.00 → utilidad antes de impuestos 5,050.00. Activo 106,650.00
(banco 106,330.00 + IVA acreditable 320.00) = pasivo 1,600.00 + capital
100,000.00 + resultado 5,050.00.
"""
import io
from datetime import date
from decimal import Decimal

import pdfplumber
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from contabilidad.models import CuentaContable, MovimientoContable, Poliza, UnidadNegocio
from core_erp.test_utils import login_superuser_con_totp

D = Decimal


class MesContableTest(TestCase):

    FECHA = date(2026, 1, 15)
    PERIODO = {'fecha_inicio': '2026-01-01', 'fecha_fin': '2026-01-31'}

    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        cls.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        c = {codigo: CuentaContable.objects.get(codigo_sat=codigo) for codigo in (
            '102.02.01', '108.01', '208.01', '301.01', '401.01.01', '402.01', '501.01', '601.02.05',
        )}
        cls.cuentas = c
        cls._poliza('I', 'Aportación de capital', [
            (c['102.02.01'], '100000.00', '0'), (c['301.01'], '0', '100000.00')])
        cls._poliza('I', 'Venta evento COT-001', [
            (c['102.02.01'], '11600.00', '0'), (c['401.01.01'], '0', '10000.00'),
            (c['208.01'], '0', '1600.00')])
        cls._poliza('E', 'Compra de alimentos', [
            (c['501.01'], '3000.00', '0'), (c['102.02.01'], '0', '3000.00')])
        cls._poliza('E', 'Gasto con factura', [
            (c['601.02.05'], '2000.00', '0'), (c['108.01'], '320.00', '0'),
            (c['102.02.01'], '0', '2320.00')])
        cls._poliza('I', 'Intereses bancarios', [
            (c['102.02.01'], '50.00', '0'), (c['402.01'], '0', '50.00')])

    @classmethod
    def _poliza(cls, tipo, concepto, lineas):
        poliza = Poliza.objects.create(
            tipo=tipo, folio=Poliza.siguiente_folio(tipo, cls.FECHA), fecha=cls.FECHA,
            concepto=concepto, unidad_negocio=cls.unidad, estado='APLICADA',
            origen='MANUAL', created_by=cls.usuario,
        )
        for cuenta, debe, haber in lineas:
            MovimientoContable.objects.create(
                poliza=poliza, cuenta=cuenta, concepto=concepto, debe=D(debe), haber=D(haber),
            )

    def setUp(self):
        login_superuser_con_totp(self.client, self.usuario)

    def _texto_pdf(self, nombre_url, **params):
        respuesta = self.client.get(reverse(f'reportes:{nombre_url}'), params)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        with pdfplumber.open(io.BytesIO(respuesta.content)) as pdf:
            return '\n'.join(pagina.extract_text() or '' for pagina in pdf.pages)

    # ----- Estado de resultados -----

    def test_estado_de_resultados_cifras(self):
        from reportes.services.contabilidad import EstadoResultadosService
        datos = EstadoResultadosService.generar(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(datos['total_ingresos'], D('10000.00'))
        self.assertEqual(datos['total_costos'], D('3000.00'))
        self.assertEqual(datos['total_gastos'], D('2000.00'))
        self.assertEqual(datos['total_otros'], D('50.00'))
        self.assertEqual(datos['utilidad_antes_impuestos'], D('5050.00'))

    def test_estado_de_resultados_pdf(self):
        texto = self._texto_pdf('estado_resultados', **self.PERIODO)
        self.assertIn('Estado de resultados', texto)
        self.assertIn('Periodo: 01/01/2026 al 31/01/2026', texto)
        self.assertIn('Total ingresos $10,000.00', texto)
        self.assertIn('Utilidad bruta $7,000.00', texto)
        self.assertIn('Utilidad de operación $5,000.00', texto)
        self.assertIn('Utilidad antes de impuestos $5,050.00', texto)

    def test_estado_de_resultados_fuera_del_periodo_sale_en_cero(self):
        texto = self._texto_pdf('estado_resultados', fecha_inicio='2026-02-01', fecha_fin='2026-02-28')
        self.assertIn('Utilidad antes de impuestos $0.00', texto)

    # ----- Balance general -----

    def test_balance_general_cuadra_e_incluye_el_banco(self):
        from reportes.services.contabilidad import BalanceGeneralService
        datos = BalanceGeneralService.generar(date(2026, 1, 31))
        self.assertIn('102.02.01', [l['codigo'] for l in datos['activos']])
        self.assertEqual(datos['total_activo'], D('106650.00'))
        self.assertEqual(datos['total_pasivo'], D('1600.00'))
        self.assertEqual(datos['resultado_ejercicio'], D('5050.00'))
        self.assertTrue(datos['cuadra'], datos['diferencia'])

    def test_balance_general_pdf(self):
        texto = self._texto_pdf('balance_general', fecha_fin='2026-01-31')
        self.assertIn('Total activo $106,650.00', texto)
        self.assertIn('Total pasivo + capital $106,650.00', texto)
        self.assertIn('La ecuación contable cuadra', texto)

    # ----- Balanza, libro mayor y auxiliar -----

    def test_balanza_pdf_cuadra_cargos_y_abonos(self):
        texto = self._texto_pdf('balanza', nivel='4', **self.PERIODO)
        self.assertIn('Balanza de comprobación', texto)
        # Cargos = abonos = suma de todas las pólizas del mes.
        self.assertIn('$116,970.00 $116,970.00', texto)

    def test_balanza_por_defecto_incluye_el_banco(self):
        # Sin elegir nivel (como llega desde el selector) el banco de nivel 4 cuenta.
        texto = self._texto_pdf('balanza', **self.PERIODO)
        self.assertIn('102.02.01', texto)
        self.assertIn('$116,970.00 $116,970.00', texto)

    def test_balanza_mismos_totales_en_cualquier_nivel(self):
        from contabilidad.services import BalanzaComprobacionService
        for nivel in (1, 2, 3, 4):
            with self.subTest(nivel=nivel):
                datos = BalanzaComprobacionService.generar(
                    date(2026, 1, 1), date(2026, 1, 31), nivel_detalle=nivel)
                raices = [r for r in datos if r['es_raiz']]
                self.assertEqual(sum(r['cargos'] for r in raices), D('116970.00'))
                self.assertEqual(sum(r['abonos'] for r in raices), D('116970.00'))
                self.assertEqual(
                    sum(r['saldo_final_debe'] for r in raices),
                    sum(r['saldo_final_haber'] for r in raices),
                )

    def test_balanza_nivel_2_acumula_el_banco_en_su_grupo(self):
        from contabilidad.services import BalanzaComprobacionService
        datos = BalanzaComprobacionService.generar(
            date(2026, 1, 1), date(2026, 1, 31), nivel_detalle=2)
        grupo = next(r for r in datos if r['codigo'] == '102')
        self.assertEqual(grupo['saldo_final_debe'], D('106330.00'))
        activo = next(r for r in datos if r['codigo'] == '100')
        self.assertEqual(activo['saldo_final_debe'], D('106650.00'))  # banco + IVA acreditable

    def test_balanza_pdf_nivel_2(self):
        texto = self._texto_pdf('balanza', nivel='2', **self.PERIODO)
        self.assertIn('$116,970.00 $116,970.00', texto)
        self.assertNotIn('102.02.01', texto)  # nivel 4 no se muestra, pero se suma

    def test_libro_mayor_del_banco(self):
        texto = self._texto_pdf('libro_mayor', cuenta_id=self.cuentas['102.02.01'].pk, **self.PERIODO)
        self.assertIn('Venta evento COT-001', texto)
        self.assertIn('$111,650.00', texto)   # cargos
        self.assertIn('$5,320.00', texto)     # abonos
        self.assertIn('$106,330.00', texto)   # saldo final

    def test_auxiliar_de_ingresos(self):
        padre = CuentaContable.objects.get(codigo_sat='401.01')
        texto = self._texto_pdf('auxiliar', cuenta_padre_id=padre.pk, **self.PERIODO)
        self.assertIn('401.01.01', texto)
        self.assertIn('$10,000.00', texto)

    # ----- Registro de auditoría -----

    def test_cada_reporte_queda_registrado(self):
        from reportes.models import ReporteGenerado
        self._texto_pdf('estado_resultados', **self.PERIODO)
        registro = ReporteGenerado.objects.latest('pk')
        self.assertEqual(registro.tipo, 'EDO_RESULTADOS')
        self.assertEqual(registro.created_by, self.usuario)


class ReportesComercialesTest(TestCase):
    """Cartera, cotizaciones y facturas con una cotización y un pago reales."""

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta

        from django.utils import timezone

        from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago

        cls.usuario = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        cls.hoy = timezone.localdate()
        cliente = Cliente.objects.create(nombre='Ana Pech Canul', telefono='5555550001')
        cls.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Boda Pech', tipo_servicio='EVENTO',
            fecha_evento=cls.hoy + timedelta(days=20), incluye_refrescos=False,
        )
        ItemCotizacion.objects.create(
            cotizacion=cls.cot, descripcion='Servicio', cantidad=1, precio_unitario=D('10000.00'),
        )
        # 50% de anticipo: la confirma y genera su solicitud de factura (signal).
        Pago.objects.create(cotizacion=cls.cot, monto=D('5800.00'), metodo='TRANSFERENCIA')
        cls.cot.refresh_from_db()

    def setUp(self):
        login_superuser_con_totp(self.client, self.usuario)

    def _texto_pdf(self, nombre_url, **params):
        respuesta = self.client.get(reverse(f'reportes:{nombre_url}'), params)
        self.assertEqual(respuesta.status_code, 200)
        with pdfplumber.open(io.BytesIO(respuesta.content)) as pdf:
            return '\n'.join(pagina.extract_text() or '' for pagina in pdf.pages)

    def _periodo(self):
        return {'fecha_inicio': f'{self.hoy.year}-01-01', 'fecha_fin': f'{self.hoy.year + 1}-12-31'}

    def test_la_cotizacion_quedo_confirmada(self):
        self.assertEqual(self.cot.estado, 'CONFIRMADA')
        self.assertEqual(self.cot.precio_final, D('11600.00'))

    def test_cartera_muestra_el_saldo(self):
        texto = self._texto_pdf('cxc', fecha_corte=self.hoy.isoformat())
        self.assertIn('Ana Pech Canul', texto)
        self.assertIn('$11,600.00 $5,800.00 $5,800.00 50%', texto)
        self.assertIn('Próximo', texto)  # faltan 20 días
        self.assertIn('Total (1 cotizaciones) $5,800.00', texto)

    def test_cotizaciones_por_periodo(self):
        texto = self._texto_pdf('cotizaciones', **self._periodo())
        self.assertIn('Boda Pech', texto)
        self.assertIn('Evento', texto)       # tipo de servicio, no el nombre del evento
        self.assertIn('Confirmada', texto)
        self.assertIn('Totales (1) $11,600.00 $5,800.00 $5,800.00', texto)

    def test_cotizaciones_filtradas_por_estado(self):
        texto = self._texto_pdf('cotizaciones', estado_cotizacion='CANCELADA', **self._periodo())
        self.assertIn('Estado: Cancelada', texto)
        self.assertIn('Sin cotizaciones en el periodo.', texto)

    def test_facturas_excluye_las_canceladas(self):
        from facturacion.models import SolicitudFactura
        solicitud = SolicitudFactura.objects.get(cotizacion=self.cot)
        self.assertEqual(solicitud.monto, D('5800.00'))
        texto = self._texto_pdf('facturas', **self._periodo())
        self.assertIn('Total (1 facturas) $5,800.00', texto)
        self.assertIn('XAXX010101000', texto)  # RFC de la solicitud, no «None»
        self.assertNotIn('None', texto)

        SolicitudFactura.objects.filter(pk=solicitud.pk).update(estado='CANCELADA')
        texto = self._texto_pdf('facturas', **self._periodo())
        self.assertIn('Sin facturas en el periodo.', texto)
