"""Pauta publicitaria: proveedor Meta mockeado, upsert, conciliación y admin."""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from comunicacion.models import Pendiente
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
from core_erp.test_utils import login_superuser_con_totp
from marketing.models import Anuncio, Campana, GastoPublicitario, Lead
from marketing.services import conciliacion, gasto
from marketing.services.proveedores import (
    EstadoCredencial,
    ProveedorAnuncios,
    ProveedorError,
    ProveedorMeta,
    TokenVencidoError,
)

User = get_user_model()


def respuesta(cuerpo, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = cuerpo
    return r


def fila(dia, ad='A1', campana='C1', spend='100.005', freq='1.456'):
    return {'date_start': dia, 'campaign_id': campana, 'campaign_name': 'Campaña uno', 'ad_id': ad,
            'ad_name': f'Anuncio {ad}', 'spend': spend, 'impressions': '1500', 'clicks': '40', 'frequency': freq}


class MetaTest(SimpleTestCase):
    def meta(self):
        return ProveedorMeta(token='tok-secreto', cuenta_id='123', version='v23.0')

    def test_apagado_sin_credenciales(self):
        self.assertFalse(ProveedorMeta(token='', cuenta_id='123').configurado())
        self.assertFalse(ProveedorMeta(token='x', cuenta_id='').configurado())
        self.assertTrue(self.meta().configurado())
        self.assertEqual(self.meta().cuenta, 'act_123')

    def test_gasto_pagina_y_parsea_con_decimal(self):
        siguiente = 'https://graph.facebook.com/v23.0/act_123/insights?after=XYZ'
        with patch('requests.get', side_effect=[
                respuesta({'data': [fila('2026-10-01')], 'paging': {'next': siguiente}}),
                respuesta({'data': [fila('2026-10-02', ad='A2')]})]) as get:
            filas = list(self.meta().gasto_diario(date(2026, 10, 1), date(2026, 10, 2)))
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0].monto, Decimal('100.01'))
        self.assertIsInstance(filas[0].monto, Decimal)
        self.assertEqual(filas[0].frecuencia, Decimal('1.46'))
        self.assertEqual((filas[0].impresiones, filas[0].clics), (1500, 40))
        self.assertEqual(get.call_args_list[1].args[0], siguiente)
        params = get.call_args_list[0].kwargs['params']
        self.assertEqual((params['level'], params['time_increment']), ('ad', 1))

    def test_campanas_presupuesto_en_centavos_de_la_cuenta(self):
        with patch('requests.get', return_value=respuesta({'data': [
                {'id': '9', 'name': 'Bodas', 'objective': 'OUTCOME_LEADS', 'effective_status': 'ACTIVE',
                 'daily_budget': '25050'}]})):
            c = list(self.meta().listar_campanas())[0]
        self.assertEqual(c.presupuesto_diario, Decimal('250.50'))

    def test_token_vencido_y_error_sin_filtrar_el_token(self):
        error = {'error': {'code': 190, 'message': 'Token tok-secreto expirado'}}
        with patch('requests.get', return_value=respuesta(error, 400)):
            with self.assertRaises(TokenVencidoError) as ctx:
                list(self.meta().listar_campanas())
        self.assertNotIn('tok-secreto', str(ctx.exception))
        with patch('requests.get', return_value=respuesta({'error': {'code': 17, 'message': 'límite'}}, 400)):
            with self.assertRaises(ProveedorError):
                list(self.meta().listar_anuncios())
        with patch('requests.get', side_effect=requests.ConnectionError('caído tok-secreto')):
            with self.assertRaises(ProveedorError) as ctx:
                self.meta().moneda()
        self.assertNotIn('tok-secreto', str(ctx.exception))

    def test_debug_token(self):
        with patch('requests.get', return_value=respuesta({'data': {'is_valid': True, 'expires_at': 0}})):
            self.assertEqual(self.meta().estado_credencial(), EstadoCredencial(True, None))
        with patch('requests.get', return_value=respuesta({'data': {'is_valid': True, 'expires_at': 1893456000}})):
            self.assertIsNotNone(self.meta().estado_credencial().vence_en)
        with patch('requests.get', return_value=respuesta({'error': {'code': 190, 'message': 'x'}}, 400)):
            self.assertFalse(self.meta().estado_credencial().valida)

    def test_acciones_son_de_la_fase_5(self):
        with self.assertRaises(NotImplementedError):
            self.meta().pausar('1')


class Falso(ProveedorAnuncios):
    """Proveedor de prueba para el servicio de sincronización."""
    plataforma = 'META'

    def __init__(self, filas=None, moneda='MXN', error=None, estado=None, activo=True):
        self.filas, self._moneda, self.error, self.estado, self.activo = filas or [], moneda, error, estado, activo

    def configurado(self):
        return self.activo

    def moneda(self):
        if self.error:
            raise self.error
        return self._moneda

    def listar_campanas(self):
        return iter([])

    def listar_anuncios(self):
        return iter([])

    def gasto_diario(self, desde, hasta):
        return iter(self.filas)

    def estado_credencial(self):
        return self.estado or EstadoCredencial(True)


def gd(dia, monto='100.00', ad='A1', campana='C1'):
    from marketing.services.proveedores import GastoDiario
    return GastoDiario(fecha=dia, campana_id=campana, anuncio_id=ad, monto=Decimal(monto), impresiones=10,
                       clics=2, frecuencia=Decimal('1.20'), campana_nombre='Campaña uno', anuncio_nombre='Anuncio')


class SincronizacionTest(TestCase):
    def test_sin_credenciales_no_hace_nada(self):
        r = gasto.sincronizar(proveedor=Falso(activo=False))
        self.assertTrue(r['omitido'])
        self.assertFalse(GastoPublicitario.objects.exists())
        salida = StringIO()
        with patch('marketing.management.commands.sincronizar_gasto_anuncios.proveedor_meta',
                   return_value=Falso(activo=False)):
            call_command('sincronizar_gasto_anuncios', stdout=salida)
        self.assertIn('apagada', salida.getvalue())

    def test_upsert_idempotente_y_correccion(self):
        d = date(2026, 10, 1)
        self.assertEqual(gasto.sincronizar(d, d, proveedor=Falso([gd(d)]))['filas'], 1)
        self.assertEqual(gasto.sincronizar(d, d, proveedor=Falso([gd(d)]))['filas'], 0)
        self.assertEqual(GastoPublicitario.objects.count(), 1)
        self.assertEqual((Campana.objects.count(), Anuncio.objects.count()), (1, 1))
        GastoPublicitario.objects.update(estado_conciliacion='CONCILIADO')
        self.assertEqual(gasto.sincronizar(d, d, proveedor=Falso([gd(d, '120.00')]))['filas'], 1)
        g = GastoPublicitario.objects.get()
        self.assertEqual((g.monto, g.estado_conciliacion), (Decimal('120.00'), 'PENDIENTE'))

    def test_no_pisa_la_atribucion_del_propietario(self):
        d = date(2026, 10, 1)
        gasto.sincronizar(d, d, proveedor=Falso([gd(d)]))
        Campana.objects.update(tipo_servicio='EVENTO', objetivo='PREMIUM')
        gasto.sincronizar(d, d, proveedor=Falso([gd(d, '130.00')]))
        c = Campana.objects.get()
        self.assertEqual((c.tipo_servicio, c.objetivo), ('EVENTO', 'PREMIUM'))

    def test_moneda_distinta_no_importa(self):
        d = date(2026, 10, 1)
        with self.assertRaises(ProveedorError):
            gasto.sincronizar(d, d, proveedor=Falso([gd(d)], moneda='USD'))
        self.assertFalse(GastoPublicitario.objects.exists())
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia__startswith='marketing_sync:').exists())

    def test_token_vencido_deja_pendiente_urgente(self):
        with self.assertRaises(TokenVencidoError):
            gasto.sincronizar(proveedor=Falso(error=TokenVencidoError('vencido')))
        p = Pendiente.objects.get(clave_idempotencia__startswith='marketing_token:invalido')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'DIRECCION'))

    def test_comando_falla_con_error_de_plataforma(self):
        with patch('marketing.management.commands.sincronizar_gasto_anuncios.proveedor_meta',
                   return_value=Falso(error=ProveedorError('caído'))):
            with self.assertRaises(CommandError):
                call_command('sincronizar_gasto_anuncios', stdout=StringIO())

    def test_token_por_vencer_avisa_una_sola_vez(self):
        estado = EstadoCredencial(True, vence_en=timezone.now() + timedelta(days=5))
        gasto.revisar_credencial(Falso(estado=estado))
        gasto.revisar_credencial(Falso(estado=estado))
        self.assertEqual(Pendiente.objects.filter(clave_idempotencia__startswith='marketing_token:vence').count(), 1)
        gasto.revisar_credencial(Falso(estado=EstadoCredencial(True, vence_en=timezone.now() + timedelta(days=60))))
        self.assertEqual(Pendiente.objects.count(), 1)

    def test_lead_se_liga_al_anuncio_por_id_externo(self):
        d = date(2026, 10, 1)
        gasto.sincronizar(d, d, proveedor=Falso([gd(d)]))
        lead = Lead.objects.create(nombre='X', telefono='525555550777', anuncio_id='A1')
        self.assertEqual(lead.anuncio.nombre, 'Anuncio')
        self.assertEqual(list(Anuncio.objects.get().prospectos), [lead])
        self.assertIsNone(Lead(anuncio_id='').anuncio)


class ConciliacionTest(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('conta_mkt', password='x', is_staff=True)
        self.banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.publicidad = CuentaContable.objects.create(
            codigo_sat='699.81', nombre='Publicidad', tipo='GASTO', naturaleza='D', nivel=3)
        ConfiguracionContable.objects.update_or_create(
            operacion='GASTO_PUBLICIDAD', defaults={'cuenta': self.publicidad, 'activa': True})
        self.cuenta = CuentaBancaria.objects.create(
            nombre='BBVA Mkt', banco='BBVA', clabe='012345678901234888', cuenta_contable=self.banco,
            unidad_negocio=UnidadNegocio.objects.get(clave='QUINTA'))
        self.estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta, banco='BBVA', periodo_mes=9, periodo_anio=2026, formato='PDF',
            estado='PROCESADO', fecha_corte_real=date(2026, 9, 30), saldo_inicial_estado=Decimal('0'),
            saldo_final_estado=Decimal('0'))
        gasto.sincronizar(date(2026, 9, 1), date(2026, 9, 2), proveedor=Falso([
            gd(date(2026, 9, 1), '1000.00'), gd(date(2026, 9, 2), '500.00', ad='A2')]))

    def cargo(self, monto, descripcion='COMPRA FACEBK ADS', clasificar=True, dia=10):
        mov = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado, fecha=date(2026, 9, dia), descripcion=descripcion, cargo=Decimal(monto))
        if clasificar:
            poliza = Poliza.objects.create(
                tipo='E', folio=Poliza.siguiente_folio('E', mov.fecha), fecha=mov.fecha, concepto='Meta',
                unidad_negocio=self.cuenta.unidad_negocio, estado='APLICADA', origen='MANUAL',
                created_by=self.usuario)
            MovimientoContable.objects.create(poliza=poliza, cuenta=self.publicidad, debe=Decimal(monto))
            linea = MovimientoContable.objects.create(poliza=poliza, cuenta=self.banco, haber=Decimal(monto))
            mov.movimiento_contable = linea
            mov.save()
        return mov

    def test_cuadra_con_iva_incluido(self):
        self.cargo('1740.00')  # 1,500 + 16% de IVA
        r = conciliacion.conciliar_mes(2026, 9)
        self.assertEqual((r['esperado'], r['banco'], r['estado']), (Decimal('1740.00'), Decimal('1740.00'), 'CONCILIADO'))
        self.assertEqual(set(GastoPublicitario.objects.values_list('estado_conciliacion', flat=True)), {'CONCILIADO'})
        self.assertFalse(Pendiente.objects.filter(clave_idempotencia__startswith='marketing_conciliacion').exists())

    def test_dentro_de_la_tolerancia(self):
        self.cargo('1800.00')  # +60, bajo el piso de 500
        self.assertEqual(conciliacion.conciliar_mes(2026, 9)['estado'], 'CONCILIADO')

    def test_no_cuadra_deja_un_pendiente_por_mes(self):
        self.cargo('900.00')
        for _ in range(2):
            r = conciliacion.conciliar_mes(2026, 9)
        self.assertEqual((r['estado'], r['diferencia']), ('DIFERENCIA', Decimal('-840.00')))
        self.assertEqual(Pendiente.objects.filter(clave_idempotencia='marketing_conciliacion:2026-09').count(), 1)
        self.assertEqual(set(GastoPublicitario.objects.values_list('estado_conciliacion', flat=True)), {'DIFERENCIA'})

    def test_tolerancia_es_parametro(self):
        self.cargo('1800.00')
        self.assertEqual(conciliacion.conciliar_mes(2026, 9, tolerancia_pct='0', tolerancia_minima='10')['estado'],
                         'DIFERENCIA')

    def test_cargo_sin_clasificar_no_cuenta_pero_se_reporta(self):
        self.cargo('1740.00', clasificar=False)
        self.cargo('50.00', descripcion='PAGO DE LUZ')
        r = conciliacion.conciliar_mes(2026, 9)
        self.assertEqual((r['banco'], r['sin_clasificar'], r['estado']), (Decimal('0.00'), 1, 'DIFERENCIA'))

    def test_sin_estado_de_cuenta_no_evalua(self):
        self.estado.movimientos.all().delete()
        r = conciliacion.conciliar_mes(2026, 9)
        self.assertEqual(r['estado'], 'PENDIENTE')
        self.assertFalse(Pendiente.objects.filter(clave_idempotencia__startswith='marketing_conciliacion').exists())


class AdminPautaTest(TestCase):
    def test_listas_y_alta_manual(self):
        d = date(2026, 10, 1)
        gasto.sincronizar(d, d, proveedor=Falso([gd(d)]))
        su = User.objects.create_superuser('root_mkt', 'r@example.com', 'x')
        login_superuser_con_totp(self.client, su)
        for nombre in ('campana', 'anuncio', 'gastopublicitario'):
            r = self.client.get(reverse(f'admin:marketing_{nombre}_changelist'))
            self.assertEqual(r.status_code, 200, nombre)
        g = GastoPublicitario.objects.get()
        self.assertEqual(self.client.get(reverse('admin:marketing_gastopublicitario_change', args=[g.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:marketing_campana_add')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:marketing_gastopublicitario_add')).status_code, 200)
