"""
Retiro de los datos de Airbnb (Issue #311, fase 2): `contabilidad.retiro_airbnb`.

Cada test siembra una unidad AIRBNB completa junto a datos de la Quinta
(`get_or_create`: según la fase, las migraciones ya sembraron o ya borraron
la unidad y sus cuentas) y comprueba que el borrado se lleva todo lo de
Airbnb sin tocar un centavo de la Quinta.

Ejecutar: python manage.py test contabilidad.test_retiro_airbnb --verbosity=2
"""
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from comercial.models import Cliente, Compra
from contabilidad.models import (
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoContable,
    MovimientoEstadoCuenta,
    Poliza,
    UnidadNegocio,
)
from contabilidad.retiro_airbnb import ConflictosRetiroAirbnb, diagnostico, ejecutar
from core_erp.test_utils import login_superuser_con_totp
from facturacion.models import SolicitudFactura
from reportes.models import ReporteGenerado


class RetiroAirbnbTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('conta_retiro', password='x')
        self.quinta = UnidadNegocio.objects.get(clave='QUINTA')
        self.airbnb, _ = UnidadNegocio.objects.get_or_create(
            clave='AIRBNB', defaults={'nombre': 'Hospedaje Airbnb'})

        # Quinta: Maestra + una compra con su póliza automática.
        self.banco_quinta = CuentaBancaria.objects.create(
            nombre='BBVA Maestra', banco='BBVA', clabe='012345678901234511',
            cuenta_contable=CuentaContable.objects.get(codigo_sat='102.02.01'),
            unidad_negocio=self.quinta,
        )
        self.compra_quinta = Compra.objects.create(
            proveedor_nombre='Proveedor Quinta', subtotal=Decimal('1000.00'),
            iva=Decimal('160.00'), total=Decimal('1160.00'),
            cuenta_pago=self.banco_quinta, unidad_negocio=self.quinta,
        )

        # Airbnb: cuentas, Libretón, póliza de pago, compra, estado de cuenta,
        # configuración, solicitud de factura y reporte.
        ingreso_padre, _ = CuentaContable.objects.get_or_create(codigo_sat='401.02', defaults={
            'nombre': 'Ingresos por hospedaje Airbnb', 'tipo': 'INGRESO', 'naturaleza': 'A',
            'nivel': 3, 'permite_movimientos': False,
            'padre': CuentaContable.objects.get(codigo_sat='401'),
        })
        self.ingreso_airbnb, _ = CuentaContable.objects.get_or_create(codigo_sat='401.02.01', defaults={
            'nombre': 'Hospedaje Habitación 1', 'tipo': 'INGRESO', 'naturaleza': 'A',
            'nivel': 4, 'permite_movimientos': True, 'padre': ingreso_padre,
        })
        self.banco_libreton_contable = CuentaContable.objects.create(
            codigo_sat='102.02.09', nombre='BBVA Libretón', tipo='ACTIVO',
            naturaleza='D', nivel=4, permite_movimientos=True,
            padre=CuentaContable.objects.get(codigo_sat='102.02'),
        )
        self.libreton = CuentaBancaria.objects.create(
            nombre='BBVA Libretón', banco='BBVA', clabe='012345678901234522',
            cuenta_contable=self.banco_libreton_contable, unidad_negocio=self.airbnb,
        )
        self.poliza_airbnb = self._poliza(
            'PAGO_AIRBNB', self.airbnb, self.banco_libreton_contable, self.ingreso_airbnb, Decimal('900.00'))
        self.compra_airbnb = Compra.objects.create(
            proveedor_nombre='Proveedor Airbnb', subtotal=Decimal('200.00'), total=Decimal('200.00'),
            cuenta_pago=self.libreton, unidad_negocio=self.airbnb,
        )
        estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.libreton, banco='BBVA',
            periodo_mes=8, periodo_anio=2026, formato='PDF', estado='PROCESADO',
        )
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado, fecha=date(2026, 8, 5),
            descripcion='Depósito Airbnb', abono=Decimal('900.00'),
        )
        ConfiguracionContable.objects.get_or_create(
            operacion='INGRESO_AIRBNB', defaults={'cuenta': self.ingreso_airbnb})
        cliente = Cliente.objects.create(nombre='Huésped')
        SolicitudFactura.objects.create(
            cliente=cliente, linea_negocio='AIRBNB', monto=Decimal('900.00'),
            concepto='Airbnb', rfc='XAXX010101000', razon_social='PUBLICO EN GENERAL',
            codigo_postal='97238', regimen_fiscal='616',
        )
        ReporteGenerado.objects.create(
            tipo='OCUPACION', formato='PDF', fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 8, 31))

    def _poliza(self, origen, unidad, cuenta_debe, cuenta_haber, monto):
        poliza = Poliza.objects.create(
            tipo='I', folio=Poliza.siguiente_folio('I', date(2026, 8, 5)),
            fecha=date(2026, 8, 5), concepto=origen, unidad_negocio=unidad,
            estado='APLICADA', origen=origen, created_by=self.user,
        )
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta_debe, debe=monto, haber=Decimal('0.00'), concepto='x')
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta_haber, debe=Decimal('0.00'), haber=monto, concepto='x')
        return poliza

    def _foto_quinta(self):
        return {
            'polizas': sorted(Poliza.objects.filter(unidad_negocio=self.quinta).values_list('pk', flat=True)),
            'compras': sorted(Compra.objects.filter(unidad_negocio=self.quinta).values_list('pk', flat=True)),
            # Solo movimientos de pólizas de la Quinta: hay cuentas (gastos,
            # IVA) que ambas unidades comparten.
            'movimientos': sorted(MovimientoContable.objects.filter(
                poliza__unidad_negocio=self.quinta,
            ).values_list('cuenta__codigo_sat', 'debe', 'haber')),
        }

    def test_borra_todo_lo_de_airbnb(self):
        ejecutar(apps)

        self.assertFalse(UnidadNegocio.objects.filter(clave='AIRBNB').exists())
        self.assertFalse(Poliza.objects.filter(origen='PAGO_AIRBNB').exists())
        self.assertFalse(Compra.objects.filter(pk=self.compra_airbnb.pk).exists())
        self.assertFalse(Poliza.objects.filter(origen='COMPRA', object_id=self.compra_airbnb.pk).exists())
        self.assertFalse(CuentaBancaria.objects.filter(pk=self.libreton.pk).exists())
        self.assertFalse(EstadoCuentaBancario.objects.filter(banco='BBVA', periodo_mes=8).exists())
        self.assertFalse(CuentaContable.objects.filter(codigo_sat__startswith='401.02').exists())
        self.assertFalse(CuentaContable.objects.filter(codigo_sat='102.02.09').exists())
        self.assertFalse(ConfiguracionContable.objects.filter(operacion='INGRESO_AIRBNB').exists())
        self.assertFalse(SolicitudFactura.objects.filter(linea_negocio='AIRBNB').exists())
        self.assertFalse(ReporteGenerado.objects.filter(tipo='OCUPACION').exists())

    def test_no_toca_nada_de_la_quinta(self):
        antes = self._foto_quinta()
        self.assertTrue(antes['movimientos'])

        ejecutar(apps)

        self.assertEqual(self._foto_quinta(), antes)
        self.assertTrue(Compra.objects.filter(pk=self.compra_quinta.pk).exists())
        self.assertTrue(CuentaBancaria.objects.filter(pk=self.banco_quinta.pk).exists())

    def test_con_conflicto_no_borra_nada(self):
        # Una compra de la Quinta pagada desde la Libretón: borrar la cuenta la
        # dejaría rota, así que el retiro se detiene.
        Compra.objects.create(
            proveedor_nombre='Mezclada', subtotal=Decimal('50.00'), total=Decimal('50.00'),
            cuenta_pago=self.libreton, unidad_negocio=self.quinta,
        )

        with self.assertRaises(ConflictosRetiroAirbnb):
            ejecutar(apps)

        self.assertTrue(UnidadNegocio.objects.filter(clave='AIRBNB').exists())
        self.assertTrue(Poliza.objects.filter(pk=self.poliza_airbnb.pk).exists())

    def test_poliza_de_la_quinta_en_cuenta_libreton_es_conflicto(self):
        self._poliza('MANUAL', self.quinta, self.banco_libreton_contable,
                     CuentaContable.objects.get(codigo_sat='102.02.01'), Decimal('100.00'))

        informe = diagnostico(apps)

        self.assertEqual(len(informe['conflictos']), 1)
        with self.assertRaises(ConflictosRetiroAirbnb):
            ejecutar(apps)

    def test_banco_principal_sobre_la_libreton_es_conflicto(self):
        ConfiguracionContable.objects.update_or_create(
            operacion='BANCO_PRINCIPAL', defaults={'cuenta': self.banco_libreton_contable})

        with self.assertRaises(ConflictosRetiroAirbnb):
            ejecutar(apps)

    def test_diagnostico_cuenta_sin_borrar(self):
        informe = diagnostico(apps)

        self.assertEqual(informe['conflictos'], {})
        self.assertEqual(informe['alcance']['Unidades de negocio'], 1)
        # 401.02, 401.02.01 y la Libretón, más las sembradas por migración
        # mientras sigan existiendo.
        self.assertGreaterEqual(informe['alcance']['Cuentas contables'], 3)
        self.assertEqual(informe['alcance']['Pólizas'], 2)  # pago + compra
        self.assertTrue(UnidadNegocio.objects.filter(clave='AIRBNB').exists())


class RetiroAirbnbVistaTest(TestCase):

    def test_solo_direccion(self):
        staff = User.objects.create_user('staff_retiro', password='x', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('contabilidad:retiro_airbnb')).status_code, 403)

    def test_direccion_ve_el_diagnostico(self):
        jefe = User.objects.create_user('jefe_retiro', password='x', is_staff=True, is_superuser=True)
        login_superuser_con_totp(self.client, jefe)

        respuesta = self.client.get(reverse('contabilidad:retiro_airbnb'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Sin conflictos')
