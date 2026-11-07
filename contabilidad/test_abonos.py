"""Transferencias de clientes identificadas por folio (abonos del estado de cuenta)."""
import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from comercial import datos_bancarios
from comercial.models import Cliente, Cotizacion, Pago
from comunicacion import herramientas_agente, services_guardia
from comunicacion.models import ConversacionWhatsApp, Pendiente, Responsable

from .models import (
    AbonoIdentificado,
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoEstadoCuenta,
    Poliza,
    ReglaConciliacion,
    UnidadNegocio,
)
from .services_abonos import atender_boton, identificar_abonos
from .services_estados_cuenta import emparejar_y_asentar
from .services_reglas_banco import aplicar_reglas
from .tests import _crear_cotizacion

TEL_DIRECCION = '525511112222'
TEL_AJENO = '525533334444'
SPEI = 'SPEI RECIBIDOHSBC 0119876101 021 0090826Transferencia SPEI 00021919063875963188 {}'


class AbonosBase(TestCase):
    def setUp(self):
        self.cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Abonos', banco='BBVA', clabe='012345678901234777', cuenta_contable=self.cuenta_banco,
            unidad_negocio=UnidadNegocio.objects.get(clave='QUINTA'))
        self.estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA', periodo_mes=11, periodo_anio=2026, formato='PDF',
            estado='PROCESADO', fecha_corte_real=date(2026, 11, 30), saldo_inicial_estado=Decimal('0.00'),
            saldo_final_estado=Decimal('0.00'))
        self.cliente = Cliente.objects.create(nombre='Ana María López Pérez', tipo_persona='FISICA')
        self.cot = _crear_cotizacion(self.cliente, Decimal('10000.00'))
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIRECCION)
        enviar = patch('comunicacion.services_pendientes._enviar_aviso')
        self.enviar_aviso = enviar.start()
        self.addCleanup(enviar.stop)

    def _mov(self, descripcion, abono='5000.00', dia=3):
        return MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado, fecha=date(2026, 11, dia), descripcion=descripcion, abono=Decimal(abono))

    @property
    def folio(self):
        return f'COT-{self.cot.pk:03d}'

    def _token_del_aviso(self):
        botones = self.enviar_aviso.call_args.kwargs['botones']
        return botones[0][0].split(':')[1], botones


class IdentificacionPorFolioTest(AbonosBase):
    def test_folio_crea_el_pago_y_liga_el_renglon(self):
        mov = self._mov(SPEI.format(f'CLIENTE {self.folio}'), abono='5000.00')
        emparejar_y_asentar(self.estado)

        pago = Pago.objects.get(cotizacion=self.cot)
        self.assertEqual((pago.monto, pago.metodo, pago.tipo, pago.concepto, pago.fecha_pago),
                         (Decimal('5000.00'), 'TRANSFERENCIA', 'INGRESO', 'VENTA', date(2026, 11, 3)))
        self.assertTrue(pago.referencia.startswith('BANCO-'))
        mov.refresh_from_db()
        self.assertEqual(mov.movimiento_contable.poliza.origen, 'PAGO_CLIENTE')
        self.assertEqual(mov.movimiento_contable.cuenta, self.cuenta_banco)
        bit = AbonoIdentificado.objects.get()
        self.assertEqual((bit.estado, bit.capa, bit.pago, bit.cotizacion), ('REGISTRADO', 'FOLIO', pago, self.cot))

    def test_folio_con_ceros_y_espacio_tambien_se_reconoce(self):
        self._mov(SPEI.format(f'pago cot {self.cot.pk:05d}'), abono='5000.00')
        identificar_abonos(self.estado)
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 1)

    def test_reproceso_no_duplica_ni_con_renglones_nuevos(self):
        self._mov(SPEI.format(self.folio), abono='5000.00')
        emparejar_y_asentar(self.estado)
        emparejar_y_asentar(self.estado)
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 1)

        # Reprocesar el PDF recrea los renglones con otro pk: la huella los reconoce.
        self.estado.movimientos.all().delete()
        nuevo = self._mov(SPEI.format(self.folio), abono='5000.00')
        emparejar_y_asentar(self.estado)
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 1)
        self.assertEqual(AbonoIdentificado.objects.count(), 1)
        nuevo.refresh_from_db()
        self.assertIsNotNone(nuevo.movimiento_contable_id)
        self.assertEqual(AbonoIdentificado.objects.get().movimiento, nuevo)

    def test_dos_abonos_identicos_el_mismo_dia_son_dos_pagos(self):
        self._mov(SPEI.format(self.folio), abono='2000.00')
        self._mov(SPEI.format(self.folio), abono='2000.00')
        identificar_abonos(self.estado)
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 2)

    def test_importe_mayor_al_saldo_se_propone_sin_botones(self):
        self._mov(SPEI.format(self.folio), abono='10001.00')
        emparejar_y_asentar(self.estado)

        self.assertFalse(Pago.objects.exists())
        bit = AbonoIdentificado.objects.get()
        self.assertEqual((bit.estado, bit.capa), ('PROPUESTO', 'FOLIO'))
        self.assertIn('mayor al saldo', bit.motivo)
        self.assertEqual(bit.token_hash, '')
        botones = self.enviar_aviso.call_args.kwargs['botones']
        self.assertFalse(any(b[0].startswith('abo:') for b in botones))

    def test_cotizacion_cancelada_no_admite_pago_y_se_propone(self):
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='CANCELADA')
        self._mov(SPEI.format(self.folio), abono='5000.00')
        identificar_abonos(self.estado)
        self.assertFalse(Pago.objects.exists())
        self.assertEqual(AbonoIdentificado.objects.get().estado, 'PROPUESTO')

    def test_folio_inexistente_cae_a_las_otras_capas(self):
        self._mov(SPEI.format('COT-99999'), abono='5000.00')
        identificar_abonos(self.estado)
        bit = AbonoIdentificado.objects.get()
        self.assertEqual((bit.estado, bit.capa, bit.cotizacion), ('PROPUESTO', 'MONTO', self.cot))
        self.assertFalse(Pago.objects.exists())

    def test_cuenta_que_no_es_banco_principal_no_crea_pago(self):
        otra = CuentaContable.objects.create(
            codigo_sat='102.02.99', nombre='Otro banco', tipo='ACTIVO', naturaleza='D', nivel=4)
        CuentaBancaria.objects.filter(pk=self.cuenta_bancaria.pk).update(cuenta_contable=otra)
        self.estado = EstadoCuentaBancario.objects.get(pk=self.estado.pk)
        self._mov(SPEI.format(self.folio), abono='5000.00')
        identificar_abonos(self.estado)

        self.assertFalse(Pago.objects.exists())
        bit = AbonoIdentificado.objects.get()
        self.assertEqual(bit.estado, 'ERROR')
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia=f'abono:{bit.huella}', rol='DIRECCION').exists())

    def test_el_abono_que_ya_tiene_pago_manual_no_se_duplica(self):
        Pago.objects.create(cotizacion=self.cot, monto=Decimal('5000.00'), metodo='TRANSFERENCIA',
                            fecha_pago=date(2026, 11, 3))
        mov = self._mov(SPEI.format(self.folio), abono='5000.00')
        emparejar_y_asentar(self.estado)
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 1)
        mov.refresh_from_db()
        self.assertIsNotNone(mov.movimiento_contable_id)
        self.assertFalse(AbonoIdentificado.objects.exists())

    def test_un_abono_de_regla_no_deja_bitacora(self):
        ReglaConciliacion.objects.create(
            nombre='Depósito de prueba', tipo_movimiento='ABONO', patrones='DEPOSITO SUELTO*',
            cuenta=CuentaContable.objects.filter(tipo='INGRESO', permite_movimientos=True).first(), prioridad=1)
        self._mov('DEPOSITO SUELTO 123', abono='5000.00')
        identificar_abonos(self.estado)
        self.assertFalse(AbonoIdentificado.objects.exists())


class CapasDebilesTest(AbonosBase):
    def test_nombre_y_monto_se_proponen_con_botones(self):
        self._mov(SPEI.format('ANA MARIA LOPEZ'), abono='5000.00')  # 50 % de 10,000
        emparejar_y_asentar(self.estado)

        self.assertFalse(Pago.objects.exists())
        bit = AbonoIdentificado.objects.get()
        self.assertEqual((bit.estado, bit.capa, bit.cotizacion), ('PROPUESTO', 'NOMBRE', self.cot))
        token, botones = self._token_del_aviso()
        self.assertEqual([b[0].split(':')[2] for b in botones], ['ok', 'no'])
        self.assertTrue(token)
        self.assertEqual(self.enviar_aviso.call_args.args[0], TEL_DIRECCION)

    def test_solo_monto_se_propone_si_hay_una_sola_candidata(self):
        self._mov(SPEI.format('JUAN SIN NOMBRE'), abono='5000.00')
        identificar_abonos(self.estado)
        bit = AbonoIdentificado.objects.get()
        self.assertEqual((bit.estado, bit.capa), ('PROPUESTO', 'MONTO'))

    def test_dos_candidatas_por_monto_quedan_sin_identificar(self):
        otro = Cliente.objects.create(nombre='Otro Cliente', tipo_persona='FISICA')
        _crear_cotizacion(otro, Decimal('10000.00'))
        self._mov(SPEI.format('JUAN SIN NOMBRE'), abono='5000.00')
        identificar_abonos(self.estado)

        bit = AbonoIdentificado.objects.get()
        self.assertEqual((bit.estado, bit.capa), ('SIN_IDENTIFICAR', 'NINGUNA'))
        pendiente = Pendiente.objects.get(clave_idempotencia=f'abono:{bit.huella}')
        self.assertEqual(pendiente.rol, 'ENCARGADO')

    def test_monto_que_no_coincide_con_nada_queda_sin_identificar(self):
        self._mov(SPEI.format('ALGUIEN'), abono='1234.56')
        identificar_abonos(self.estado)
        self.assertEqual(AbonoIdentificado.objects.get().estado, 'SIN_IDENTIFICAR')

    def test_evento_fuera_de_la_ventana_no_cuenta_para_solo_monto(self):
        Cotizacion.objects.filter(pk=self.cot.pk).update(fecha_evento=date(2027, 11, 1))
        self._mov(SPEI.format('ALGUIEN'), abono='5000.00')
        identificar_abonos(self.estado)
        self.assertEqual(AbonoIdentificado.objects.get().estado, 'SIN_IDENTIFICAR')

    def test_aplicar_reglas_no_toca_lo_propuesto(self):
        mov = self._mov('DEPOSITO PROPUESTO 77', abono='5000.00')
        AbonoIdentificado.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, fecha=mov.fecha, importe=mov.abono, huella='h' * 64,
            movimiento=mov, estado='PROPUESTO', cotizacion=self.cot, capa='NOMBRE')
        ReglaConciliacion.objects.create(
            nombre='Regla de prueba', tipo_movimiento='ABONO', patrones='DEPOSITO PROPUESTO*',
            cuenta=CuentaContable.objects.filter(tipo='INGRESO', permite_movimientos=True).first(), prioridad=1)

        aplicar_reglas(self.estado)
        self.assertFalse(Poliza.objects.filter(
            content_type=ContentType.objects.get_for_model(MovimientoEstadoCuenta), object_id=mov.pk).exists())


class BotonDeDireccionTest(AbonosBase):
    def _proponer(self, descripcion='ANA MARIA LOPEZ', abono='5000.00'):
        self._mov(SPEI.format(descripcion), abono=abono)
        identificar_abonos(self.estado)
        token, _ = self._token_del_aviso()
        return AbonoIdentificado.objects.get(), token

    def test_registrar_crea_el_pago_liga_y_resuelve_el_pendiente(self):
        bit, token = self._proponer()
        resultado = atender_boton(TEL_DIRECCION, f'abo:{token}:ok')

        self.assertIsNotNone(resultado.pago)
        bit.refresh_from_db()
        self.assertEqual((bit.estado, bit.decidido_por_telefono), ('REGISTRADO', TEL_DIRECCION))
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 1)
        self.assertIsNotNone(bit.movimiento.movimiento_contable_id)
        self.assertEqual(Pendiente.objects.get(clave_idempotencia=f'abono:{bit.huella}').estado, 'RESUELTO')

    def test_boton_de_otro_telefono_se_ignora_sin_contestar(self):
        bit, token = self._proponer()
        self.assertIsNone(atender_boton(TEL_AJENO, f'abo:{token}:ok'))
        self.assertIsNone(atender_boton('525544445555', f'abo:{token}:no'))
        bit.refresh_from_db()
        self.assertEqual(bit.estado, 'PROPUESTO')
        self.assertFalse(Pago.objects.exists())

    def test_el_encargado_tampoco_puede_decidir(self):
        Responsable.objects.create(rol='ENCARGADO', nombre='Enc', telefono_wa=TEL_AJENO)
        bit, token = self._proponer()
        self.assertIsNone(atender_boton(TEL_AJENO, f'abo:{token}:ok'))
        self.assertFalse(Pago.objects.exists())

    def test_token_vencido_se_rechaza(self):
        bit, token = self._proponer()
        AbonoIdentificado.objects.filter(pk=bit.pk).update(token_vence=timezone.now() - timedelta(minutes=1))
        resultado = atender_boton(TEL_DIRECCION, f'abo:{token}:ok')
        self.assertIn('venci', resultado.texto)
        self.assertFalse(Pago.objects.exists())

    def test_token_usado_no_registra_dos_veces(self):
        bit, token = self._proponer()
        atender_boton(TEL_DIRECCION, f'abo:{token}:ok')
        segundo = atender_boton(TEL_DIRECCION, f'abo:{token}:ok')
        self.assertIsNone(segundo.pago)
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 1)

    def test_token_desconocido_no_hace_nada(self):
        self._proponer()
        resultado = atender_boton(TEL_DIRECCION, 'abo:' + 'x' * 32 + ':ok')
        self.assertIn('no es válido', resultado.texto)
        self.assertFalse(Pago.objects.exists())

    def test_saldo_cambiado_antes_de_confirmar_no_registra(self):
        bit, token = self._proponer()
        # Entra otro pago por la vía de siempre y el abono ya no cabe en el saldo.
        Pago.objects.create(cotizacion=self.cot, monto=Decimal('9000.00'), metodo='EFECTIVO', fecha_pago=date(2026, 11, 4))
        resultado = atender_boton(TEL_DIRECCION, f'abo:{token}:ok')

        self.assertIsNone(resultado.pago)
        self.assertIn('mayor al saldo', resultado.texto)
        bit.refresh_from_db()
        self.assertEqual(bit.estado, 'PROPUESTO')
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 1)

    def test_no_es_descarta_y_avisa_al_encargado(self):
        bit, token = self._proponer()
        resultado = atender_boton(TEL_DIRECCION, f'abo:{token}:no')

        self.assertIn('descartado', resultado.texto)
        bit.refresh_from_db()
        self.assertEqual(bit.estado, 'DESCARTADO')
        self.assertFalse(Pago.objects.exists())
        aviso = Pendiente.objects.get(clave_idempotencia=f'abono_descartado:{bit.huella}')
        self.assertEqual(aviso.rol, 'ENCARGADO')

    def test_confirmar_con_la_cuenta_mal_configurada_no_registra(self):
        bit, token = self._proponer()
        ConfiguracionContable.objects.filter(operacion='BANCO_PRINCIPAL').update(
            cuenta=CuentaContable.objects.create(
                codigo_sat='102.02.98', nombre='Otro', tipo='ACTIVO', naturaleza='D', nivel=4))
        resultado = atender_boton(TEL_DIRECCION, f'abo:{token}:ok')
        self.assertIsNone(resultado.pago)
        self.assertFalse(Pago.objects.exists())


class InstruccionAlClienteTest(AbonosBase):
    def test_instruccion_lleva_clabe_titular_y_folio(self):
        texto = datos_bancarios.instruccion_transferencia(self.cot)
        self.assertIn(datos_bancarios.CLABE, texto)
        self.assertIn(datos_bancarios.TITULAR, texto)
        self.assertIn(self.folio, texto)

    def test_mi_reservacion_trae_los_datos_para_transferir_con_su_folio(self):
        Cliente.objects.filter(pk=self.cliente.pk).update(telefono='5219995550001')
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='COTIZADA')
        conv = SimpleNamespace(telefono='5219995550001')
        salida, _ = herramientas_agente.ejecutar('mi_reservacion', {}, conv=conv)
        datos = json.loads(salida)['reservaciones'][0]['datos_transferencia']
        self.assertEqual((datos['clabe'], datos['concepto']), (datos_bancarios.CLABE, self.folio))

    def test_condiciones_de_pago_sin_reservacion_no_da_cuenta(self):
        conv = SimpleNamespace(telefono='5210000000000')
        salida, _ = herramientas_agente.ejecutar('condiciones_de_pago', {'servicio': 'EVENTO'}, conv=conv)
        self.assertNotIn('datos_transferencia', json.loads(salida))
        self.assertNotIn(datos_bancarios.CLABE, salida)

    def test_guardia_deja_la_clabe_propia_y_detiene_otra(self):
        conv = ConversacionWhatsApp.objects.create(telefono='5219995550001')
        Cliente.objects.filter(pk=self.cliente.pk).update(telefono='5219995550001')
        propia = f'Transfiere a la CLABE {datos_bancarios.CLABE} y escribe {self.folio} en el concepto.'
        self.assertEqual(services_guardia.revisar_filtro(conv, propia), '')
        ajena = 'Transfiere a la CLABE 012910004895703999 y escribe tu folio.'
        self.assertIn('cuenta', services_guardia.revisar_filtro(conv, ajena))
        mezcla = f'La mía es {datos_bancarios.CLABE} y la otra 646180157000000004.'
        self.assertIn('cuenta', services_guardia.revisar_filtro(conv, mezcla))

    def test_portal_muestra_el_bloque_de_transferencia_con_el_folio(self):
        from django.urls import reverse

        from comercial.models import PortalCliente
        portal = PortalCliente.objects.get(cotizacion=self.cot)
        html = self.client.get(reverse('portal_evento', args=[portal.token]), secure=True).content.decode()
        self.assertIn('card-transferencia-bancaria', html)
        self.assertIn(datos_bancarios.CLABE, html)
        self.assertIn(self.folio, html)
        self.assertIn('btn-copiar-transferencia', html)

    def test_portal_no_muestra_el_bloque_si_ya_no_hay_saldo_o_no_admite_pago(self):
        from django.urls import reverse

        from comercial.models import PortalCliente
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='CANCELADA')
        portal = PortalCliente.objects.get(cotizacion=self.cot)
        html = self.client.get(reverse('portal_evento', args=[portal.token]), secure=True).content.decode()
        self.assertNotIn('card-transferencia-bancaria', html)
