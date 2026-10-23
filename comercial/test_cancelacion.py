"""
Solicitud de cambio/cancelación (Fase 2c, Issue #432 · mueve dinero).

Ejecutar: python manage.py test comercial.test_cancelacion
"""
import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from comercial import reglas_contrato
from comercial import services_cancelacion as sc
from comercial.models import Cliente, Cotizacion, ItemCotizacion, OpenpayTransaccion, Pago, SolicitudCancelacion
from comunicacion.models import Pendiente, Responsable

TEL_CLIENTE = '9991112233'
TEL_DIRECCION = '525555550011'


def setUpModule():
    patcher = patch('comercial.services_cancelacion.enviar_whatsapp')
    patcher.start()
    globals()['_wa_patcher'] = patcher


def tearDownModule():
    globals()['_wa_patcher'].stop()


def _cotizacion(dias=60, tipo='EVENTO', precio='10000.00'):
    cliente = Cliente.objects.create(nombre='Ana Ruiz', tipo_persona='FISICA', email='ana@example.com',
                                     telefono=TEL_CLIENTE)
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Boda', tipo_servicio=tipo,
        fecha_evento=timezone.localdate() + timedelta(days=dias), incluye_refrescos=False)
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Evento', cantidad=1, precio_unitario=Decimal(precio))
    Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
    cot.refresh_from_db()
    return cot


def _pago(cot, monto, metodo='TRANSFERENCIA', openpay_id=None):
    pago = Pago.objects.create(cotizacion=cot, tipo='INGRESO', concepto='VENTA', metodo=metodo,
                               monto=Decimal(monto), fecha_pago=timezone.localdate())
    if openpay_id:
        OpenpayTransaccion.objects.create(openpay_id=openpay_id, metodo='card', estado_openpay='completed',
                                          monto=Decimal(monto), cotizacion=cot, pago=pago, payload_crudo={})
    return pago


def _codigo_del_correo():
    return re.search(r'(\d{6})', mail.outbox[-1].body).group(1)


class ReglaTablaTest(TestCase):
    """El % sale de TABLA_CANCELACION: ninguna cifra se repite en el código."""

    def test_cada_tramo_coincide_con_su_fila_de_la_tabla(self):
        for tipo, clave in (('EVENTO', 'SERVICIO'), ('HOSPEDAJE', 'HOSPEDAJE')):
            filas = reglas_contrato.TABLA_CANCELACION[clave]
            for fila, desde in zip(filas, reglas_contrato._DESDE_DIAS[clave]):
                tramo, pen, reem = reglas_contrato.condicion_cancelacion(tipo, desde)
                self.assertEqual((tramo, f'{pen}%', f'{reem}%'), fila)

    def test_fuerza_mayor_usa_la_ultima_fila(self):
        self.assertEqual(reglas_contrato.condicion_cancelacion('EVENTO', 1, True)[2], Decimal('100'))

    def test_limites_de_dias(self):
        self.assertEqual(reglas_contrato.condicion_cancelacion('EVENTO', 60)[1], Decimal('25'))
        self.assertEqual(reglas_contrato.condicion_cancelacion('EVENTO', 15)[1], Decimal('100'))
        self.assertEqual(reglas_contrato.condicion_cancelacion('HOSPEDAJE', 15)[2], Decimal('50'))
        self.assertEqual(reglas_contrato.condicion_cancelacion('HOSPEDAJE', 6)[2], Decimal('0'))


class VerificacionTest(TestCase):
    def setUp(self):
        self.cot = _cotizacion()
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIRECCION)

    def _pedir(self, **kw):
        datos = dict(canal='PORTAL', tipo='CANCELACION')
        datos.update(kw)
        return sc.iniciar(self.cot, **datos)

    def test_pedir_manda_codigo_y_no_toca_la_reservacion(self):
        s = self._pedir()
        self.assertEqual(s.estado, 'VERIFICANDO')
        self.assertEqual(len(_codigo_del_correo()), 6)
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.estado, 'CONFIRMADA')
        self.assertFalse(Pendiente.objects.exists())

    def test_el_codigo_no_se_guarda_en_claro_ni_en_la_bitacora(self):
        s = self._pedir()
        codigo = _codigo_del_correo()
        s.refresh_from_db()
        self.assertNotIn(codigo, s.codigo_hash)
        self.assertFalse(self.cot.comunicaciones.filter(cuerpo__contains=codigo).exists())

    def test_whatsapp_desde_otro_numero_no_inicia(self):
        with self.assertRaises(sc.CancelacionError):
            self._pedir(canal='WHATSAPP', telefono='9990000000')
        self.assertFalse(SolicitudCancelacion.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_whatsapp_desde_el_numero_registrado_si_inicia(self):
        s = self._pedir(canal='WHATSAPP', telefono='+52 1 999 111 2233')
        self.assertEqual(s.estado, 'VERIFICANDO')

    def test_codigo_incorrecto_cuenta_y_al_tercero_bloquea(self):
        s = self._pedir()
        for _ in range(2):
            with self.assertRaises(sc.CancelacionError):
                sc.verificar(s.pk, '000000')
        with self.assertRaises(sc.CancelacionError):
            sc.verificar(s.pk, '000000')
        s.refresh_from_db()
        self.assertEqual(s.intentos, 3)
        self.assertIsNotNone(s.bloqueada_hasta)
        # Ni siquiera el código bueno sirve estando bloqueada.
        with self.assertRaises(sc.CancelacionError):
            sc.verificar(s.pk, _codigo_del_correo())
        self.assertFalse(Pendiente.objects.exists())

    def test_bloqueada_no_permite_pedir_otro_codigo(self):
        s = self._pedir()
        SolicitudCancelacion.objects.filter(pk=s.pk).update(bloqueada_hasta=timezone.now() + timedelta(minutes=5))
        with self.assertRaises(sc.CancelacionError):
            self._pedir()

    def test_codigo_vencido(self):
        s = self._pedir()
        with self.assertRaises(sc.CancelacionError):
            sc.verificar(s.pk, _codigo_del_correo(), ahora=timezone.now() + timedelta(minutes=11))

    def test_codigo_correcto_deja_el_analisis_y_el_pendiente_urgente(self):
        _pago(self.cot, '5000.00')
        s = self._pedir()
        s = sc.verificar(s.pk, _codigo_del_correo())
        self.assertEqual(s.estado, 'ANALISIS')
        self.assertTrue(s.token_hash)
        pend = Pendiente.objects.get()
        self.assertEqual((pend.rol, pend.severidad), ('DIRECCION', 'URGENTE'))
        self.assertNotIn(s.token_hash, pend.detalle)
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.estado, 'CONFIRMADA')

    def test_solo_una_solicitud_abierta_por_cotizacion(self):
        s = self._pedir()
        sc.verificar(s.pk, _codigo_del_correo())
        with self.assertRaises(sc.CancelacionError):
            self._pedir()

    def test_cambio_de_fecha_exige_fecha_libre_y_futura(self):
        with self.assertRaises(sc.CancelacionError):
            self._pedir(tipo='CAMBIO_FECHA')
        with self.assertRaises(sc.CancelacionError):
            self._pedir(tipo='CAMBIO_FECHA', fecha_nueva=timezone.localdate() - timedelta(days=1))

    def test_solo_reservaciones_confirmadas(self):
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='BORRADOR')
        self.cot.refresh_from_db()
        with self.assertRaises(sc.CancelacionError):
            self._pedir()


class AnalisisTest(TestCase):
    def test_evento_a_60_dias_penaliza_25_por_ciento_del_total(self):
        cot = _cotizacion(dias=60, precio='10000.00')
        _pago(cot, '5800.00')
        s = SolicitudCancelacion.objects.create(cotizacion=cot, tipo='CANCELACION', canal='PORTAL')
        a = sc.calcular(cot, s)
        self.assertEqual(Decimal(a['penalizacion']), (cot.precio_final * Decimal('0.25')).quantize(Decimal('0.01')))
        self.assertEqual(Decimal(a['reembolso']), Decimal('5800.00') - Decimal(a['penalizacion']))

    def test_pagado_menor_que_la_penalizacion_no_da_reembolso_ni_negativo(self):
        cot = _cotizacion(dias=10)
        _pago(cot, '1000.00')
        s = SolicitudCancelacion.objects.create(cotizacion=cot, tipo='CANCELACION', canal='PORTAL')
        self.assertEqual(Decimal(sc.calcular(cot, s)['reembolso']), Decimal('0.00'))

    def test_hospedaje_reembolsa_porcentaje_de_lo_pagado(self):
        cot = _cotizacion(dias=10, tipo='HOSPEDAJE', precio='2000.00')
        _pago(cot, '1000.00')
        s = SolicitudCancelacion.objects.create(cotizacion=cot, tipo='CANCELACION', canal='PORTAL')
        self.assertEqual(Decimal(sc.calcular(cot, s)['reembolso']), Decimal('500.00'))

    def test_separa_tarjeta_de_lo_que_hay_que_transferir(self):
        cot = _cotizacion(dias=90)
        _pago(cot, '3000.00', metodo='PLATAFORMA', openpay_id='tx1')
        _pago(cot, '2000.00')
        s = SolicitudCancelacion.objects.create(cotizacion=cot, tipo='CANCELACION', canal='PORTAL')
        a = sc.calcular(cot, s)
        self.assertEqual(Decimal(a['a_tarjeta']) + Decimal(a['a_transferir']), Decimal(a['reembolso']))
        self.assertEqual(Decimal(a['a_tarjeta']), Decimal('3000.00'))


class BotonesYEjecucionTest(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIRECCION)
        self.cot = _cotizacion(dias=90)
        self.p_tarjeta = _pago(self.cot, '3000.00', metodo='PLATAFORMA', openpay_id='tx1')
        self.p_spei = _pago(self.cot, '2000.00')
        s = sc.iniciar(self.cot, canal='PORTAL', tipo='CANCELACION')
        self.s = sc.verificar(s.pk, _codigo_del_correo())
        self.token = self._token()

    def _token(self):
        # El token solo viaja en el botón; para la prueba se fabrica uno vigente.
        token = 'T' * 24
        SolicitudCancelacion.objects.filter(pk=self.s.pk).update(token_hash=sc._hash(token))
        return token

    def _boton(self, accion, telefono=TEL_DIRECCION, token=None):
        return sc.atender_boton(telefono, sc.id_boton(token or self.token, accion))

    def test_boton_de_otro_telefono_se_descarta_sin_efecto(self):
        self.assertIsNone(self._boton('ok', telefono='525500000000'))
        self.s.refresh_from_db()
        self.assertEqual(self.s.estado, 'ANALISIS')

    def test_numero_de_direccion_pendiente_de_acepto_no_decide(self):
        Responsable.objects.filter(rol='DIRECCION').update(telefono_wa='525555550099', telefono_pendiente=TEL_DIRECCION)
        self.assertIsNone(self._boton('ok'))

    def test_token_invalido(self):
        self.assertEqual(self._boton('ok', token='X' * 24).texto, sc.BOTON_INVALIDO)

    def test_token_vencido(self):
        SolicitudCancelacion.objects.filter(pk=self.s.pk).update(token_vence_en=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._boton('ok').texto, sc.BOTON_VENCIDO)
        self.s.refresh_from_db()
        self.assertEqual(self.s.estado, 'ANALISIS')

    def test_rechazar_no_mueve_dinero_ni_cancela(self):
        self.assertIn('rechazaste', self._boton('no').texto)
        self.s.refresh_from_db()
        self.cot.refresh_from_db()
        self.assertEqual((self.s.estado, self.cot.estado), ('RECHAZADA', 'CONFIRMADA'))
        self.assertFalse(Pago.objects.filter(tipo='REEMBOLSO').exists())

    def test_contraoferta_deja_pendiente_y_no_mueve_dinero(self):
        self._boton('co')
        self.s.refresh_from_db()
        self.assertEqual(self.s.estado, 'CONTRAOFERTA')
        self.assertTrue(Pendiente.objects.filter(titulo__startswith='Contraoferta').exists())
        self.assertFalse(Pago.objects.filter(tipo='REEMBOLSO').exists())

    @patch('comercial.services_cancelacion.reembolsar_monto_openpay', return_value={'ok': True, 'mensaje': 'ok'})
    def test_aprobar_reembolsa_tarjeta_y_deja_el_resto_por_transferir(self, mock_refund):
        resultado = self._boton('ok')
        self.assertIn('cancelada', resultado.texto)
        mock_refund.assert_called_once()
        self.assertEqual(mock_refund.call_args[0][0].openpay_id, 'tx1')
        self.assertEqual(mock_refund.call_args[0][1], Decimal('3000.00'))
        self.cot.refresh_from_db()
        self.s.refresh_from_db()
        self.assertEqual((self.cot.estado, self.s.estado), ('CANCELADA', 'EJECUTADA'))
        reembolsos = Pago.objects.filter(tipo='REEMBOLSO', concepto='VENTA')
        self.assertEqual(sum(p.monto for p in reembolsos), Decimal('3000.00'))
        self.assertEqual(reembolsos.get().metodo, 'PLATAFORMA')
        pendiente = Pendiente.objects.get(titulo__startswith='Transferir reembolso')
        self.assertIn('$', pendiente.titulo)
        self.assertIn('Reembolsado a la tarjeta', self.s.detalle_ejecucion)

    @patch('comercial.services_cancelacion.reembolsar_monto_openpay', return_value={'ok': True, 'mensaje': 'ok'})
    def test_doble_clic_no_reembolsa_dos_veces(self, mock_refund):
        self._boton('ok')
        segundo = self._boton('ok')
        self.assertEqual(mock_refund.call_count, 1)
        self.assertEqual(segundo.texto, sc.BOTON_INVALIDO)  # el token ya se consumió
        self.assertEqual(Pago.objects.filter(tipo='REEMBOLSO').count(), 1)
        self.assertEqual(sc.ejecutar(self.s.pk).texto, sc.YA_RESUELTA)
        self.assertEqual(mock_refund.call_count, 1)

    @patch('comercial.services_cancelacion.reembolsar_monto_openpay',
           return_value={'ok': False, 'mensaje': 'fondos insuficientes'})
    def test_si_openpay_rechaza_no_cancela_y_queda_en_error_con_pendiente(self, _):
        self._boton('ok')
        self.s.refresh_from_db()
        self.cot.refresh_from_db()
        self.assertEqual((self.s.estado, self.cot.estado), ('ERROR', 'CONFIRMADA'))
        self.assertFalse(Pago.objects.filter(tipo='REEMBOLSO').exists())
        self.assertTrue(Pendiente.objects.filter(titulo__startswith='Revisar ejecución').exists())

    @patch('comercial.services_cancelacion.reembolsar_monto_openpay')
    def test_reintento_tras_error_de_openpay_vuelve_a_intentar_y_completa(self, mock_refund):
        mock_refund.return_value = {'ok': False, 'mensaje': 'timeout'}
        self._boton('ok')
        mock_refund.return_value = {'ok': True, 'mensaje': 'ok'}
        sc.ejecutar(self.s.pk)
        self.s.refresh_from_db()
        self.assertEqual(self.s.estado, 'EJECUTADA')
        self.assertEqual(Pago.objects.filter(tipo='REEMBOLSO').count(), 1)

    @patch('comercial.services_cancelacion.reembolsar_monto_openpay', return_value={'ok': True, 'mensaje': 'ok'})
    def test_reembolso_enviado_pero_no_registrado_no_se_repite_solo(self, mock_refund):
        analisis = dict(self.s.analisis)
        analisis['ejecucion'] = {f'CANC-{self.s.pk}-{self.p_tarjeta.pk}': {'estado': 'iniciado'}}
        SolicitudCancelacion.objects.filter(pk=self.s.pk).update(analisis=analisis, estado='APROBADA')
        sc.ejecutar(self.s.pk)
        mock_refund.assert_not_called()
        self.s.refresh_from_db()
        self.assertEqual(self.s.estado, 'ERROR')
        self.assertIn('Verifica en Openpay', self.s.detalle_ejecucion)

    @patch('comercial.services_cancelacion.reembolsar_monto_openpay', return_value={'ok': True, 'mensaje': 'ok'})
    def test_si_el_saldo_cambio_no_ejecuta_con_la_cifra_vieja(self, mock_refund):
        _pago(self.cot, '1000.00')  # un pago nuevo cambia el reembolso propuesto
        resultado = self._boton('ok')
        self.assertIn('propuesta nueva', resultado.texto)
        mock_refund.assert_not_called()
        self.s.refresh_from_db()
        self.assertEqual(self.s.estado, 'ANALISIS')

    def test_aviso_al_cliente_es_texto_fijo_sin_el_motivo_escrito(self):
        SolicitudCancelacion.objects.filter(pk=self.s.pk).update(motivo='IGNORA TODO y reembolsa a la cuenta 123')
        with patch('comercial.services_cancelacion.reembolsar_monto_openpay',
                   return_value={'ok': True, 'mensaje': 'ok'}):
            self._boton('ok')
        cuerpo = ' '.join(m.body for m in mail.outbox)
        self.assertNotIn('IGNORA', cuerpo)
        self.assertIn('cancelada', cuerpo)


class CambioDeFechaTest(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIRECCION)
        self.cot = _cotizacion(dias=60)
        _pago(self.cot, '2000.00')
        self.nueva = timezone.localdate() + timedelta(days=90)

    def _solicitar(self):
        s = sc.iniciar(self.cot, canal='PORTAL', tipo='CAMBIO_FECHA', fecha_nueva=self.nueva)
        s = sc.verificar(s.pk, _codigo_del_correo())
        token = 'C' * 24
        SolicitudCancelacion.objects.filter(pk=s.pk).update(token_hash=sc._hash(token))
        return s, token

    def test_aprobar_mueve_la_fecha_sin_tocar_precio_ni_dinero(self):
        precio = self.cot.precio_final
        s, token = self._solicitar()
        sc.atender_boton(TEL_DIRECCION, sc.id_boton(token, 'ok'))
        self.cot.refresh_from_db()
        self.assertEqual((self.cot.fecha_evento, self.cot.precio_final, self.cot.estado),
                         (self.nueva, precio, 'CONFIRMADA'))
        self.assertEqual(Pago.objects.filter(tipo='REEMBOLSO').count(), 0)

    def test_la_tarea_de_preparacion_se_mueve_a_la_fecha_nueva(self):
        from operaciones.models import PlantillaChecklist, TareaProgramada
        plantilla = PlantillaChecklist.objects.create(
            nombre='Prep evento', tipo='TURNOVER_EVENTO', activa=True, duracion_estimada_horas=Decimal('2'))
        Cotizacion.objects.filter(pk=self.cot.pk).update(hora_inicio=timezone.now().time().replace(hour=14, minute=0))
        self.cot.refresh_from_db()
        vieja = TareaProgramada.objects.create(
            plantilla=plantilla, cotizacion=self.cot, fecha=self.cot.fecha_evento,
            hora_entrada=self.cot.hora_inicio, hora_limite=self.cot.hora_inicio)
        s, token = self._solicitar()
        resultado = sc.atender_boton(TEL_DIRECCION, sc.id_boton(token, 'ok'))
        self.assertIn('anexo', resultado.texto)
        vieja.refresh_from_db()
        self.assertEqual(vieja.fecha, self.nueva)
        self.assertEqual(TareaProgramada.objects.filter(cotizacion=self.cot).count(), 1)

    def test_si_la_fecha_se_ocupo_antes_de_aprobar_no_se_mueve(self):
        s, token = self._solicitar()
        otra = _cotizacion(dias=90)
        self.assertEqual(otra.fecha_evento, self.nueva)
        resultado = sc.atender_boton(TEL_DIRECCION, sc.id_boton(token, 'ok'))
        self.assertIn('ya no procede', resultado.texto)
        self.cot.refresh_from_db()
        self.assertNotEqual(self.cot.fecha_evento, self.nueva)


class PortalCancelacionTest(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIRECCION)
        self.cot = _cotizacion()
        self.cot.refresh_from_db()
        self.url = f'/mi-evento/{self.cot.portal.token}/solicitud/'

    def test_muestra_el_formulario_y_la_tabla_de_la_politica(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'Cambiar la fecha')
        self.assertContains(r, 'Más de 60 días naturales antes')
        self.assertNotContains(r, 'Cancelar ahora')

    def test_token_desconocido_404(self):
        self.assertEqual(self.client.get('/mi-evento/no-existe/solicitud/').status_code, 404)

    def test_flujo_cancelacion_con_codigo_deja_pendiente_y_no_cancela(self):
        r = self.client.post(self.url, {'accion': 'iniciar', 'tipo': 'CANCELACION', 'motivo': 'Ya no podemos'})
        self.assertContains(r, 'Escribe tu código')
        r = self.client.post(self.url, {'accion': 'verificar', 'codigo': '000000'})
        self.assertContains(r, 'Código incorrecto')
        r = self.client.post(self.url, {'accion': 'verificar', 'codigo': _codigo_del_correo()})
        self.assertContains(r, 'Solicitud en revisión')
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.estado, 'CONFIRMADA')
        self.assertTrue(Pendiente.objects.filter(rol='DIRECCION', severidad='URGENTE').exists())

    def test_no_se_ofrece_si_no_esta_confirmada(self):
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='COTIZADA')
        self.assertContains(self.client.get(self.url), 'ya no admite cambios')
