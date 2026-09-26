"""
Asientos generados desde el estado de cuenta (Issue #329).

Los conceptos replican el formato real de BBVA Maestra PYME (agosto 2026),
con nombres y cuentas cambiados.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core_erp.test_utils import login_superuser_con_totp

from .models import (
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoContable,
    MovimientoEstadoCuenta,
    Poliza,
    ReglaConciliacion,
    UnidadNegocio,
)
from .services_estados_cuenta import emparejar_y_asentar
from .services_reglas_banco import clasificar_movimiento, clave_aprendizaje

CARGO_TRASPASO = 'PAGO CUENTA DE TERCERO 0047613019 BNET 1112223334 Traspaso'
CARGO_TRASPASO_TYPO = 'PAGO CUENTA DE TERCERO 0081158420 BNET 1112223334 Traspasi'
ABONO_TRASPASO = 'SPEI RECIBIDOSANTANDER 0174899148 014 0595539TRASPASO A QKT 00014910569118634338'
COMISION = 'SERV BANCA INTERNET OPS SERV BCA IN'
IVA_COMISION = 'IVA COM SERV BCA INTERNET'
TARJETA_USD = 'RAILWAY ******3288 USD 6.30TC017.3587AUT: 879462'
TARJETA_RFC = 'FACEBOOK MEXICO S DE R ******3288 RFC: FME 120803935 10:55 AUT: 958680'
DEPOSITO_CLIENTE = 'SPEI RECIBIDOHSBC 0119876101 021 0090826Transferencia SPEI 00021919063875963188 CLIENTE PRUEBA'


class ReglasBancoBase(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('contador_reglas', password='x', is_staff=True)
        self.cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.retiros = CuentaContable.objects.create(
            codigo_sat='399.91', nombre='Retiros del dueño', tipo='CAPITAL', naturaleza='A', nivel=3,
        )
        self.aportaciones = CuentaContable.objects.create(
            codigo_sat='399.92', nombre='Aportaciones del dueño', tipo='CAPITAL', naturaleza='A', nivel=3,
        )
        self.gasto = CuentaContable.objects.filter(tipo='GASTO', permite_movimientos=True).first()
        self.comisiones = CuentaContable.objects.create(
            codigo_sat='699.91', nombre='Comisiones bancarias', tipo='GASTO', naturaleza='D', nivel=3,
        )
        self.iva = CuentaContable.objects.create(
            codigo_sat='199.91', nombre='IVA acreditable', tipo='ACTIVO', naturaleza='D', nivel=3,
        )
        for operacion, cuenta in [
            ('RETIROS_DUENO', self.retiros), ('APORTACIONES_DUENO', self.aportaciones),
            ('GASTO_BANCARIOS', self.comisiones), ('IVA_ACREDITABLE', self.iva),
        ]:
            ConfiguracionContable.objects.update_or_create(
                operacion=operacion, defaults={'cuenta': cuenta, 'activa': True},
            )
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Reglas', banco='BBVA', clabe='012345678901234999',
            cuenta_contable=self.cuenta_banco,
            unidad_negocio=UnidadNegocio.objects.get(clave='QUINTA'),
        )
        self.estado = self._estado(8)

    def _estado(self, mes):
        return EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA', periodo_mes=mes, periodo_anio=2026,
            formato='PDF', estado='PROCESADO', fecha_corte_real=date(2026, mes, 28),
            saldo_inicial_estado=Decimal('0.00'), saldo_final_estado=Decimal('0.00'),
        )

    def _mov(self, descripcion, cargo='0.00', abono='0.00', dia=10, estado=None):
        estado = estado or self.estado
        return MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado, fecha=date(2026, estado.periodo_mes, dia), descripcion=descripcion,
            cargo=Decimal(cargo), abono=Decimal(abono),
        )

    def _polizas_banco(self, mov):
        return Poliza.objects.filter(
            content_type=ContentType.objects.get_for_model(MovimientoEstadoCuenta),
            object_id=mov.pk, origen='BANCO',
        )


class ReglasDeSistemaTest(ReglasBancoBase):

    def test_las_reglas_de_sistema_vienen_sembradas(self):
        self.assertEqual(ReglaConciliacion.objects.filter(origen='SISTEMA', activa=True).count(), 11)

    def test_traspaso_a_cuenta_propia_se_asienta_y_empareja_solo(self):
        mov = self._mov(CARGO_TRASPASO, cargo='450.00')
        resumen = emparejar_y_asentar(self.estado, usuario=self.usuario)

        mov.refresh_from_db()
        self.assertEqual(resumen['aplicadas'], [mov])
        poliza = self._polizas_banco(mov).get()
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertEqual(poliza.tipo, 'E')
        self.assertEqual(poliza.fecha, mov.fecha)
        self.assertTrue(poliza.movimientos.filter(cuenta=self.retiros, debe=Decimal('450.00')).exists())
        self.assertEqual(mov.movimiento_contable.cuenta, self.cuenta_banco)
        self.assertEqual(mov.movimiento_contable.haber, Decimal('450.00'))
        self.assertTrue(mov.match_automatico)
        self.assertFalse(mov.confirmado)

    def test_traspaso_recibido_es_aportacion_no_ingreso(self):
        mov = self._mov(ABONO_TRASPASO, abono='300.00')
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        poliza = self._polizas_banco(mov).get()
        self.assertEqual(poliza.tipo, 'I')
        self.assertTrue(poliza.movimientos.filter(cuenta=self.aportaciones, haber=Decimal('300.00')).exists())
        self.assertTrue(poliza.movimientos.filter(cuenta=self.cuenta_banco, debe=Decimal('300.00')).exists())

    def test_comision_y_su_iva_van_a_cuentas_distintas(self):
        comision = self._mov(COMISION, cargo='71.50')
        iva = self._mov(IVA_COMISION, cargo='11.44')
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertTrue(self._polizas_banco(comision).get().movimientos.filter(cuenta=self.comisiones).exists())
        self.assertTrue(self._polizas_banco(iva).get().movimientos.filter(cuenta=self.iva).exists())

    def test_deposito_de_cliente_no_se_asienta_por_regla(self):
        mov = self._mov(DEPOSITO_CLIENTE, abono='1100.00')
        resumen = emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertEqual(resumen['sin_regla'], [mov])
        self.assertFalse(self._polizas_banco(mov).exists())

    def test_correr_dos_veces_no_duplica(self):
        mov = self._mov(CARGO_TRASPASO, cargo='450.00')
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        MovimientoEstadoCuenta.objects.filter(pk=mov.pk).update(movimiento_contable=None)
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertEqual(self._polizas_banco(mov).count(), 1)
        mov.refresh_from_db()
        self.assertIsNotNone(mov.movimiento_contable_id)

    def test_movimiento_con_asiento_existente_no_recibe_poliza_de_regla(self):
        """Lo existente (Pago, Compra, póliza manual) siempre gana."""
        mov = self._mov(CARGO_TRASPASO, cargo='450.00')
        manual = Poliza.objects.create(
            tipo='E', folio=Poliza.siguiente_folio('E', mov.fecha), fecha=mov.fecha, concepto='Manual',
            unidad_negocio=self.cuenta_bancaria.unidad_negocio, estado='APLICADA', origen='MANUAL',
            created_by=self.usuario,
        )
        MovimientoContable.objects.create(poliza=manual, cuenta=self.retiros, debe=Decimal('450.00'))
        linea = MovimientoContable.objects.create(poliza=manual, cuenta=self.cuenta_banco, haber=Decimal('450.00'))

        emparejar_y_asentar(self.estado, usuario=self.usuario)
        mov.refresh_from_db()
        self.assertEqual(mov.movimiento_contable, linea)
        self.assertFalse(self._polizas_banco(mov).exists())

    def test_operacion_sin_cuenta_configurada_no_truena(self):
        ConfiguracionContable.objects.filter(operacion='RETIROS_DUENO').delete()
        mov = self._mov(CARGO_TRASPASO, cargo='450.00')
        resumen = emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertEqual(resumen['sin_cuenta'], [mov])
        self.assertFalse(self._polizas_banco(mov).exists())

    def test_regla_en_borrador_se_vincula_cuando_la_aplican(self):
        ReglaConciliacion.objects.filter(patrones='TRASPAS*|RETIRO*').update(aplicar_automaticamente=False)
        mov = self._mov(CARGO_TRASPASO, cargo='450.00')
        resumen = emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertEqual(resumen['borrador'], [mov])
        mov.refresh_from_db()
        self.assertIsNone(mov.movimiento_contable_id)

        self._polizas_banco(mov).get().aplicar(self.usuario)
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        mov.refresh_from_db()
        self.assertEqual(mov.movimiento_contable.cuenta, self.cuenta_banco)
        self.assertEqual(self._polizas_banco(mov).count(), 1)


class AprendizajeTest(ReglasBancoBase):

    def test_clave_por_rfc_cuenta_y_comercio(self):
        casos = [
            (TARJETA_RFC, {'patrones': 'RFC: FME 120803935', 'cuenta_tercero': ''}),
            (CARGO_TRASPASO_TYPO, {'patrones': '', 'cuenta_tercero': '1112223334'}),
            (ABONO_TRASPASO, {'patrones': '', 'cuenta_tercero': '014910569118634338'}),
            (TARJETA_USD, {'patrones': 'RAILWAY', 'cuenta_tercero': ''}),
            ('ANTHROPIC* CLAUDE SUB ******3288 USD 20.00', {'patrones': 'ANTHROPIC', 'cuenta_tercero': ''}),
            ('SMARTPY*SESSERVKANASIN ******3288', {'patrones': 'SMARTPY*SESSERVKANASIN', 'cuenta_tercero': ''}),
        ]
        for descripcion, esperado in casos:
            with self.subTest(descripcion=descripcion):
                clave = clave_aprendizaje(self._mov(descripcion, cargo='1.00'))
                self.assertEqual({k: clave[k] for k in esperado}, esperado)
        self.assertIsNone(clave_aprendizaje(self._mov(COMISION, cargo='1.00')))

    def test_lo_clasificado_se_asienta_solo_el_mes_siguiente(self):
        agosto = self._mov(TARJETA_USD, cargo='109.36')
        poliza, regla = clasificar_movimiento(agosto, self.gasto, self.usuario, nombre_regla='Hosting Railway')

        agosto.refresh_from_db()
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertEqual(agosto.movimiento_contable.cuenta, self.cuenta_banco)
        self.assertEqual(regla.origen, 'APRENDIDA')
        self.assertEqual(regla.created_by, self.usuario)

        septiembre = self._estado(9)
        otro = self._mov('RAILWAY ******3288 USD 7.80TC017.1487AUT: 807041', cargo='133.76', estado=septiembre)
        emparejar_y_asentar(septiembre, usuario=self.usuario)
        otro.refresh_from_db()
        self.assertTrue(self._polizas_banco(otro).get().movimientos.filter(cuenta=self.gasto).exists())

    def test_traspaso_con_typo_calza_con_la_regla_de_texto(self):
        mov = self._mov(CARGO_TRASPASO_TYPO, cargo='85.00')
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertTrue(self._polizas_banco(mov).get().movimientos.filter(cuenta=self.retiros).exists())

    def test_cuenta_propia_aprendida_cubre_conceptos_sin_la_palabra_traspaso(self):
        primero = self._mov('PAGO CUENTA DE TERCERO 0000000002 BNET 1112223334 Pago tarjeta', cargo='85.00')
        self.assertEqual(emparejar_y_asentar(self.estado, usuario=self.usuario)['sin_regla'], [primero])
        clasificar_movimiento(primero, self.retiros, self.usuario)

        segundo = self._mov('PAGO CUENTA DE TERCERO 0000000001 BNET 1112223334 para gasolina', cargo='200.00')
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertTrue(self._polizas_banco(segundo).get().movimientos.filter(cuenta=self.retiros).exists())

    def test_reclasificar_la_misma_clave_actualiza_la_regla(self):
        mov1 = self._mov(TARJETA_USD, cargo='10.00')
        _, regla = clasificar_movimiento(mov1, self.gasto, self.usuario)
        mov2 = self._mov(TARJETA_USD, cargo='20.00', dia=12)
        MovimientoEstadoCuenta.objects.filter(pk=mov2.pk).update(movimiento_contable=None)
        _, regla2 = clasificar_movimiento(mov2, self.comisiones, self.usuario)
        self.assertEqual(regla.pk, regla2.pk)
        self.assertEqual(ReglaConciliacion.objects.filter(origen='APRENDIDA').count(), 1)
        regla2.refresh_from_db()
        self.assertEqual(regla2.cuenta, self.comisiones)


class ClasificarVistaTest(ReglasBancoBase):

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser('dir_reglas', 'd@x.com', 'x')
        login_superuser_con_totp(self.client, self.admin)
        self.mov = self._mov(TARJETA_USD, cargo='109.36')
        self.url = reverse('contabilidad:clasificar_movimiento', args=[self.mov.pk])

    def test_la_pantalla_del_estado_de_cuenta_ofrece_clasificar(self):
        respuesta = self.client.get(
            reverse('admin:contabilidad_estadocuentabancario_change', args=[self.estado.pk]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, self.url)

    def test_clasificar_asienta_y_recuerda(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        respuesta = self.client.post(self.url, {'cuenta': self.gasto.pk, 'recordar': 'on', 'nombre': 'Railway'})
        self.assertEqual(respuesta.status_code, 302)
        self.mov.refresh_from_db()
        self.assertIsNotNone(self.mov.movimiento_contable_id)
        self.assertTrue(ReglaConciliacion.objects.filter(patrones='RAILWAY', origen='APRENDIDA').exists())

    def test_no_permite_la_cuenta_de_bancos_como_contrapartida(self):
        respuesta = self.client.post(self.url, {'cuenta': self.cuenta_banco.pk})
        self.assertEqual(respuesta.status_code, 200)
        self.mov.refresh_from_db()
        self.assertIsNone(self.mov.movimiento_contable_id)

    def test_sin_permiso_de_polizas_da_403(self):
        staff = User.objects.create_user('sin_permiso_reglas', password='x', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_accion_volver_a_emparejar(self):
        self._mov(CARGO_TRASPASO, cargo='450.00')
        respuesta = self.client.post(
            reverse('admin:contabilidad_estadocuentabancario_changelist'),
            {'action': 'reemparejar', '_selected_action': [self.estado.pk]}, follow=True,
        )
        self.assertContains(respuesta, '1 de 2')


class MixCfdiYPalabrasClaveTest(ReglasBancoBase):
    """Carga masiva de CFDI + palabras clave del concepto, sin doble registro."""

    def setUp(self):
        super().setUp()
        ConfiguracionContable.objects.update_or_create(
            operacion='GASTO_INSUMOS', defaults={'cuenta': self.gasto, 'activa': True},
        )

    def _compra(self, total, fecha, rfc=''):
        from comercial.models import Compra
        return Compra.objects.create(
            proveedor_nombre='Proveedor CFDI', subtotal=Decimal(total), total=Decimal(total),
            fecha_emision=fecha, rfc_emisor=rfc, unidad_negocio=self.cuenta_bancaria.unidad_negocio,
        )

    def test_cfdi_cargado_antes_gana_a_la_palabra_clave(self):
        compra = self._compra('359.60', date(2026, 8, 7))
        mov = self._mov('PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 INSUMOS bolis', cargo='359.60', dia=8)
        emparejar_y_asentar(self.estado, usuario=self.usuario)

        mov.refresh_from_db()
        compra.refresh_from_db()
        self.assertEqual(compra.cuenta_pago, self.cuenta_bancaria)
        self.assertEqual(mov.movimiento_contable.poliza.origen, 'COMPRA')
        self.assertFalse(self._polizas_banco(mov).exists())

    def test_rfc_impreso_empareja_factura_pagada_semanas_despues(self):
        compra = self._compra('127.60', date(2026, 7, 20), rfc='LEMW821126M4A')
        mov = self._mov('TALL LLANTERA EL FENIX ******3288 RFC: LEMW821126M4A 20:43 AUT: 260599',
                        cargo='127.60', dia=25)
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        mov.refresh_from_db()
        self.assertEqual(mov.movimiento_contable.poliza.object_id, compra.pk)

    def test_palabra_clave_asienta_provisional_y_el_cfdi_tardio_lo_sustituye(self):
        mov = self._mov('PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 INSUMOS bolis', cargo='359.60', dia=8)
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        provisional = self._polizas_banco(mov).get()
        self.assertEqual(provisional.estado, 'APLICADA')
        self.assertTrue(provisional.movimientos.filter(cuenta=self.gasto, debe=Decimal('359.60')).exists())

        compra = self._compra('359.60', date(2026, 8, 7))  # llega el XML por carga masiva
        emparejar_y_asentar(self.estado, usuario=self.usuario)

        provisional.refresh_from_db()
        mov.refresh_from_db()
        self.assertEqual(provisional.estado, 'CANCELADA')
        self.assertIn(f"Compra #{compra.pk}", provisional.motivo_cancelacion)
        self.assertEqual(mov.movimiento_contable.poliza.origen, 'COMPRA')
        # Una sola póliza vigente sobre ese dinero.
        self.assertEqual(
            MovimientoContable.objects.filter(
                cuenta=self.cuenta_banco, haber=Decimal('359.60'), poliza__estado='APLICADA',
            ).count(), 1,
        )

    def test_un_traspaso_nunca_se_sustituye_por_una_compra(self):
        mov = self._mov(CARGO_TRASPASO, cargo='450.00')
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self._compra('450.00', date(2026, 8, 9))
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertEqual(self._polizas_banco(mov).get().estado, 'APLICADA')

    def test_con_dos_facturas_posibles_no_adivina(self):
        mov = self._mov('PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 INSUMOS bolis', cargo='359.60', dia=8)
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self._compra('359.60', date(2026, 8, 7))
        self._compra('359.60', date(2026, 8, 9))
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertEqual(self._polizas_banco(mov).get().estado, 'APLICADA')

    def test_inversion_va_a_activo_en_ambos_sentidos(self):
        inversiones = CuentaContable.objects.create(
            codigo_sat='199.95', nombre='Inversiones', tipo='ACTIVO', naturaleza='D', nivel=3,
        )
        ConfiguracionContable.objects.update_or_create(
            operacion='INVERSIONES', defaults={'cuenta': inversiones, 'activa': True},
        )
        sale = self._mov('PAGO CUENTA DE TERCERO 0000000003 BNET 9998887776 INVERSION cetes', cargo='5000.00')
        regresa = self._mov('SPEI RECIBIDO 0000000004 INVERSION rendimiento', abono='5040.00', dia=20)
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertTrue(self._polizas_banco(sale).get().movimientos.filter(
            cuenta=inversiones, debe=Decimal('5000.00')).exists())
        self.assertTrue(self._polizas_banco(regresa).get().movimientos.filter(
            cuenta=inversiones, haber=Decimal('5040.00')).exists())

    def test_cfdi_sin_cuenta_de_pago_sustituye_al_provisional(self):
        """Con dos cuentas activas la Compra nace sin cuenta de pago (póliza en
        borrador): la sustitución la completa desde esta cuenta."""
        CuentaBancaria.objects.create(
            nombre='Otra cuenta', banco='BBVA', clabe='012345678901234888',
            unidad_negocio=self.cuenta_bancaria.unidad_negocio,
        )
        mov = self._mov('PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 INSUMOS bolis', cargo='359.60', dia=8)
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        compra = self._compra('359.60', date(2026, 8, 7))
        self.assertIsNone(compra.cuenta_pago_id)

        emparejar_y_asentar(self.estado, usuario=self.usuario)
        mov.refresh_from_db()
        compra.refresh_from_db()
        self.assertEqual(compra.cuenta_pago, self.cuenta_bancaria)
        self.assertEqual(mov.movimiento_contable.poliza.object_id, compra.pk)
        self.assertEqual(self._polizas_banco(mov).get().estado, 'CANCELADA')


class AbreviacionesTest(ReglasBancoBase):
    """Palabras clave cortas en el concepto, sin falsos positivos."""

    def setUp(self):
        super().setUp()
        for operacion in ('GASTO_INSUMOS', 'GASTO_MANTENIMIENTO', 'GASTO_PUBLICIDAD',
                          'GASTO_VEHICULOS', 'GASTO_IMPUESTOS', 'SUELDOS_SALARIOS'):
            ConfiguracionContable.objects.update_or_create(
                operacion=operacion, defaults={'cuenta': self.gasto, 'activa': True},
            )

    def _regla_de(self, concepto, cargo='100.00'):
        mov = self._mov(concepto, cargo=cargo)
        return next((r for r in ReglaConciliacion.objects.filter(activa=True) if r.coincide(mov)), None)

    def test_abreviaciones_calzan_como_palabra(self):
        casos = {
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 INS bolis': 'INS / INSUMOS',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 Mtto alberca': 'MANT / MTTO',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 mant jardin': 'MANT / MTTO',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 PUB facebook': 'PUB / PUBLICIDAD',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 gas camioneta': 'GAS / GASOLINA',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 IMP predial': 'IMP / IMPUESTOS',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 Nóm semana 35': 'NOM / NOMINA',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 INV cetes': 'INV / INVERSION',
        }
        for concepto, esperado in casos.items():
            with self.subTest(concepto=concepto):
                self.assertIn(esperado, self._regla_de(concepto).nombre)

    def test_concepto_pegado_a_la_referencia_sigue_calzando(self):
        mov = self._mov('SPEI RECIBIDOSANTANDER 0174899148 014 0595539TRASPASO A QKT', abono='300.00')
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        self.assertTrue(self._polizas_banco(mov).get().movimientos.filter(cuenta=self.aportaciones).exists())

    def test_la_abreviacion_no_calza_dentro_de_otra_palabra(self):
        for concepto in (
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 INSURGENTES renta',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 PUBLICO general',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 NOMBRE cliente',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 GASTON perez',
            'PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 IMPRESIONES lona',
        ):
            with self.subTest(concepto=concepto):
                self.assertIsNone(self._regla_de(concepto))


class CuentasSembradasTest(TestCase):
    """Migración 0027: las reglas de sistema tienen cuenta desde el deploy."""

    def test_operaciones_de_las_reglas_quedan_configuradas(self):
        esperado = {
            'RETIROS_DUENO': '301.04', 'APORTACIONES_DUENO': '301.03', 'INVERSIONES': '103.01',
            'GASTO_NO_DEDUCIBLE': '601.05', 'PARTIDAS_POR_IDENTIFICAR': '205.04',
            'GASTO_MANTENIMIENTO': '601.02.05', 'GASTO_PUBLICIDAD': '601.04.01',
            'GASTO_VEHICULOS': '601.02.10',
        }
        for operacion, codigo in esperado.items():
            with self.subTest(operacion=operacion):
                cuenta = ConfiguracionContable.obtener_cuenta(operacion)
                self.assertEqual(cuenta.codigo_sat, codigo)
                self.assertTrue(cuenta.permite_movimientos)

    def test_retiros_es_capital_deudora_y_aportaciones_acreedora(self):
        retiros = CuentaContable.objects.get(codigo_sat='301.04')
        aportaciones = CuentaContable.objects.get(codigo_sat='301.03')
        self.assertEqual((retiros.tipo, retiros.naturaleza), ('CAPITAL', 'D'))
        self.assertEqual((aportaciones.tipo, aportaciones.naturaleza), ('CAPITAL', 'A'))
