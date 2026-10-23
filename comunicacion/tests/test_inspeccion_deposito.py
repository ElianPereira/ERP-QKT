"""Inspección del depósito en garantía por WhatsApp (Fase 3, Issue #434). MUEVE DINERO:
cada prueba revisa los movimientos reales del depósito y cuántas veces se llamó a Openpay."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cotizacion, MovimientoDeposito, OpenpayTransaccion
from comercial.services_deposito import asegurar_deposito, registrar_recepcion
from comercial.services_openpay import _confirmar_cargo_completado
from comercial.test_deposito_garantia import _cotizacion
from comunicacion import services_inspeccion as svc
from comunicacion.models import FotoInspeccion, InspeccionDeposito, Pendiente, Responsable

ENCARGADO = '9991110000'
DIRECCION = '9994440000'
AJENO = '9990000000'
STORAGES_TEST = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
OK = MagicMock(status_code=200, json=lambda: {})


class Base(TestCase):
    """Un depósito de $1,160.00 de una boda que terminó ayer."""

    def setUp(self):
        Responsable.objects.create(rol='ENCARGADO', nombre='Encargado', telefono_wa=ENCARGADO)
        Responsable.objects.create(rol='DIRECCION', nombre='Dueño', telefono_wa=DIRECCION, modo_copia='OFF')
        self.cot = _cotizacion()
        Cotizacion.objects.filter(pk=self.cot.pk).update(
            fecha_evento=timezone.localdate() - timedelta(days=1), estado='EJECUTADA')
        self.cot.refresh_from_db()
        self.deposito = asegurar_deposito(self.cot, Decimal('1160.00'))
        avisos = patch.object(svc, '_enviar_aviso')
        self.aviso = avisos.start()
        self.addCleanup(avisos.stop)

    def con_tarjeta(self, monto='1160.00'):
        registro = OpenpayTransaccion.objects.create(
            openpay_id='tx-dep-1', metodo='card', monto=Decimal(monto), cotizacion=self.cot,
            payload_crudo={}, destino='DEPOSITO', procesado=True, estado_openpay='completed')
        _confirmar_cargo_completado(registro, self.cot, Decimal(monto), {}, 'card')

    def en_efectivo(self, monto='1160.00'):
        registrar_recepcion(self.deposito, monto=Decimal(monto), metodo='EFECTIVO')

    def inspeccion(self, **kw):
        defaults = {'deposito': self.deposito, 'vence_en': timezone.now() + timedelta(hours=48)}
        return InspeccionDeposito.objects.create(**{**defaults, **kw})

    def movimientos(self):
        return {(m.tipo, m.monto) for m in MovimientoDeposito.objects.filter(deposito=self.deposito)
                if m.tipo != 'RECEPCION'}


class SolicitudTest(Base):
    def test_candidatos_solo_servicios_terminados_con_dinero_en_custodia(self):
        self.assertEqual(svc.depositos_para_inspeccion(), [])  # nada recibido
        self.en_efectivo()
        self.assertEqual([d.pk for d in svc.depositos_para_inspeccion()], [self.deposito.pk])
        Cotizacion.objects.filter(pk=self.cot.pk).update(fecha_evento=timezone.localdate())
        self.assertEqual(svc.depositos_para_inspeccion(), [])  # el servicio es hoy
        Cotizacion.objects.filter(pk=self.cot.pk).update(fecha_evento=timezone.localdate() - timedelta(days=30))
        self.assertEqual(svc.depositos_para_inspeccion(), [])  # demasiado viejo: se atiende a mano

    def test_no_se_pide_si_ya_hay_inspeccion_o_ya_se_liquido(self):
        self.en_efectivo()
        self.inspeccion()
        self.assertEqual(svc.depositos_para_inspeccion(), [])

    def test_solicitud_manda_boton_al_encargado_y_es_idempotente(self):
        self.en_efectivo()
        inspeccion = svc.solicitar_inspeccion(self.deposito)
        self.assertEqual(inspeccion.estado, 'SOLICITADA')
        destino, texto = self.aviso.call_args.args
        self.assertEqual(destino, f'52{ENCARGADO}')
        self.assertEqual(self.aviso.call_args.kwargs['botones'], [(f'insp:{inspeccion.pk}:ok', 'Sin daños')])
        self.assertIn('se devuelve', texto)
        pendiente = Pendiente.objects.get()
        self.assertEqual((pendiente.rol, pendiente.tipo), ('ENCARGADO', 'DEPOSITO'))
        self.assertIsNone(svc.solicitar_inspeccion(self.deposito))
        self.assertEqual(self.aviso.call_count, 1)

    def test_comando_dry_run_no_crea_nada(self):
        self.en_efectivo()
        call_command('solicitar_inspecciones_deposito', '--dry-run')
        self.assertFalse(InspeccionDeposito.objects.exists())
        call_command('solicitar_inspecciones_deposito')
        self.assertEqual(InspeccionDeposito.objects.count(), 1)


@patch('comercial.services_openpay.requests.post', return_value=OK)
class DevolucionSinReporteTest(Base):
    def vencer(self, inspeccion):
        InspeccionDeposito.objects.filter(pk=inspeccion.pk).update(vence_en=timezone.now() - timedelta(minutes=1))

    def test_antes_del_plazo_no_se_devuelve_nada(self, post):
        self.con_tarjeta()
        self.inspeccion()
        self.assertEqual(svc.devolver_sin_reporte(), [])
        post.assert_not_called()
        self.assertEqual(self.movimientos(), set())

    def test_vencido_con_tarjeta_reembolsa_una_sola_vez(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.vencer(inspeccion)
        self.assertEqual(len(svc.devolver_sin_reporte()), 1)
        self.assertEqual(svc.devolver_sin_reporte(), [])  # segunda corrida
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs['json']['amount'], 1160.0)
        self.assertEqual(self.movimientos(), {('DEVOLUCION', Decimal('1160.00'))})
        inspeccion.refresh_from_db()
        self.assertEqual((inspeccion.estado, inspeccion.resolucion), ('LIQUIDADA', 'SIN_REPORTE'))

    def test_dos_corridas_a_la_vez_no_reembolsan_dos_veces(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.assertIsNotNone(svc._liquidar(inspeccion.pk, retencion=Decimal('0'), resolucion='SIN_REPORTE'))
        self.assertIsNone(svc._liquidar(inspeccion.pk, retencion=Decimal('0'), resolucion='SIN_REPORTE'))
        self.assertEqual(post.call_count, 1)

    def test_sin_aplicar_solo_lista(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.vencer(inspeccion)
        self.assertEqual(len(svc.devolver_sin_reporte(aplicar=False)), 1)
        post.assert_not_called()
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'SOLICITADA')

    def test_comando_simula_por_defecto_y_aplica_con_flag(self, post):
        self.con_tarjeta()
        self.vencer(self.inspeccion())
        call_command('devolver_depositos_sin_reporte')
        post.assert_not_called()
        call_command('devolver_depositos_sin_reporte', '--aplicar')
        self.assertEqual(post.call_count, 1)

    def test_no_en_tarjeta_no_registra_una_devolucion_que_nadie_hizo(self, post):
        self.en_efectivo()
        inspeccion = self.inspeccion()
        self.vencer(inspeccion)
        svc.devolver_sin_reporte()
        post.assert_not_called()
        self.assertEqual(self.movimientos(), set())
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'MANUAL')
        pendiente = Pendiente.objects.get(clave_idempotencia=f'inspeccion:{inspeccion.pk}:manual')
        self.assertEqual((pendiente.rol, pendiente.severidad), ('DIRECCION', 'URGENTE'))

    def test_mixto_tarjeta_y_efectivo_tambien_es_manual(self, post):
        self.con_tarjeta('500.00')
        self.en_efectivo('660.00')
        inspeccion = self.inspeccion()
        self.vencer(inspeccion)
        svc.devolver_sin_reporte()
        post.assert_not_called()
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'MANUAL')

    def test_openpay_rechaza_el_reembolso_queda_manual_sin_movimientos(self, post):
        post.return_value = MagicMock(status_code=400, json=lambda: {'description': 'fondos insuficientes'})
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.vencer(inspeccion)
        svc.devolver_sin_reporte()
        self.assertEqual(self.movimientos(), set())
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'MANUAL')
        self.assertIn('Openpay rechazó', inspeccion.nota)

    def test_ya_liquidado_por_otra_via_solo_se_cierra(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.vencer(inspeccion)
        MovimientoDeposito.objects.create(
            deposito=self.deposito, tipo='DEVOLUCION', monto=Decimal('1160.00'), metodo='TRANSFERENCIA',
            fecha=timezone.localdate())
        svc.devolver_sin_reporte()
        post.assert_not_called()
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'LIQUIDADA')
        self.assertEqual(MovimientoDeposito.objects.filter(tipo='DEVOLUCION').count(), 1)

    def test_con_daños_reportados_no_se_devuelve_solo(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion(estado='DANOS', monto_danos=Decimal('300.00'))
        self.vencer(inspeccion)
        self.assertEqual(svc.devolver_sin_reporte(), [])
        post.assert_not_called()


@patch('comercial.services_openpay.requests.post', return_value=OK)
class RespuestasTest(Base):
    def test_sin_danos_por_boton_del_encargado_devuelve(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        atendido, respuesta = svc.atender(ENCARGADO, f'insp:{inspeccion.pk}:ok')
        self.assertTrue(atendido)
        self.assertIn('sin daños', respuesta)
        self.assertEqual(self.movimientos(), {('DEVOLUCION', Decimal('1160.00'))})
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.resolucion, 'SIN_DANOS')
        self.assertEqual(Pendiente.objects.filter(estado='ABIERTO').count(), 0)

    def test_sin_danos_por_texto(self, post):
        self.con_tarjeta()
        self.inspeccion()
        _, respuesta = svc.atender(ENCARGADO, ' Sin daños ')
        self.assertIn('Se devolvió', respuesta)
        self.assertEqual(post.call_count, 1)

    def test_boton_o_texto_de_un_numero_ajeno_no_hace_nada(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.assertEqual(svc.atender(AJENO, f'insp:{inspeccion.pk}:ok'), (True, None))  # se descarta
        self.assertEqual(svc.atender(AJENO, 'sin daños'), (False, None))  # sigue su camino (agente)
        self.assertEqual(svc.atender(AJENO, 'daños 100 cot 1 vidrio'), (False, None))
        post.assert_not_called()
        self.assertEqual(self.movimientos(), set())

    def test_repetir_el_boton_no_devuelve_dos_veces(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        svc.atender(ENCARGADO, f'insp:{inspeccion.pk}:ok')
        _, respuesta = svc.atender(ENCARGADO, f'insp:{inspeccion.pk}:ok')
        self.assertIn('ya no está', respuesta)
        self.assertEqual(post.call_count, 1)

    def test_sin_danos_con_efectivo_queda_manual(self, post):
        self.en_efectivo()
        inspeccion = self.inspeccion()
        _, respuesta = svc.atender(ENCARGADO, f'insp:{inspeccion.pk}:ok')
        self.assertIn('Dirección lo atenderá', respuesta)
        self.assertEqual(self.movimientos(), set())
        post.assert_not_called()

    def test_texto_de_daños_se_interpreta(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        _, respuesta = svc.atender(ENCARGADO, 'Daños $1,000.50 cot %d vidrio roto en el baño' % self.cot.pk)
        inspeccion.refresh_from_db()
        self.assertEqual((inspeccion.estado, inspeccion.monto_danos), ('DANOS', Decimal('1000.50')))
        self.assertEqual(inspeccion.desglose, 'vidrio roto en el baño')
        self.assertIn('Falta al menos una foto', respuesta)
        self.assertEqual(self.movimientos(), set())

    def test_daños_sin_descripcion_o_con_monto_invalido_se_rechazan(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.assertIn('Describe los daños', svc.atender(ENCARGADO, 'daños 300')[1])
        self.assertIn('mayor a cero', svc.atender(ENCARGADO, 'daños 0 vidrio roto')[1])
        self.assertIn('excede', svc.atender(ENCARGADO, 'daños 5000 vidrio roto')[1])
        inspeccion.refresh_from_db()
        self.assertEqual((inspeccion.estado, inspeccion.monto_danos), ('SOLICITADA', None))

    def test_con_varias_abiertas_pide_el_folio(self, post):
        self.con_tarjeta()
        self.inspeccion()
        otra = _cotizacion()
        Cotizacion.objects.filter(pk=otra.pk).update(fecha_evento=timezone.localdate() - timedelta(days=1))
        InspeccionDeposito.objects.create(
            deposito=asegurar_deposito(otra, Decimal('1160.00')), vence_en=timezone.now() + timedelta(hours=48))
        self.assertIn('varias inspecciones', svc.atender(ENCARGADO, 'sin daños')[1])
        self.assertFalse(MovimientoDeposito.objects.exclude(tipo='RECEPCION').exists())


@override_settings(STORAGES=STORAGES_TEST)
@patch('comercial.services_openpay.requests.post', return_value=OK)
class RetencionTest(Base):
    def foto(self, pie=''):
        with patch.object(svc, 'descargar_media_wa', return_value=(b'\xff\xd8', 'image/jpeg', '.jpg')):
            return svc.recibir_foto(ENCARGADO, 'media-1', pie)

    def reportar(self, monto='300', descripcion='vidrio roto'):
        return svc.atender(ENCARGADO, f'daños {monto} {descripcion}')[1]

    @override_settings(ERP_DEPOSITO_TOPE_RETENCION='500')
    def test_dentro_del_tope_se_retiene_y_se_devuelve_el_resto(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.foto()
        respuesta = self.reportar()
        self.assertIn('retuve $300.00', respuesta)
        self.assertEqual(self.movimientos(), {('RETENCION_DANOS', Decimal('300.00')), ('DEVOLUCION', Decimal('860.00'))})
        self.assertEqual(post.call_args.kwargs['json']['amount'], 860.0)
        inspeccion.refresh_from_db()
        self.assertEqual((inspeccion.estado, inspeccion.resolucion), ('LIQUIDADA', 'RETENCION_ENCARGADO'))
        retencion = MovimientoDeposito.objects.get(tipo='RETENCION_DANOS')
        self.assertIn('1 foto(s)', retencion.desglose)
        self.assertIn('vidrio roto', retencion.desglose)
        self.assertEqual(self.aviso.call_args.args[0], f'52{DIRECCION}')  # Dirección se entera

    @override_settings(ERP_DEPOSITO_TOPE_RETENCION='500')
    def test_la_foto_puede_llegar_despues_del_monto(self, post):
        self.con_tarjeta()
        self.inspeccion()
        self.assertIn('Falta al menos una foto', self.reportar())
        self.assertEqual(self.movimientos(), set())
        self.assertIn('retuve $300.00', self.foto())

    @override_settings(ERP_DEPOSITO_TOPE_RETENCION='500')
    def test_sin_foto_no_se_retiene_nada(self, post):
        self.con_tarjeta()
        self.inspeccion()
        self.reportar()
        self.assertEqual(self.movimientos(), set())
        post.assert_not_called()

    @override_settings(ERP_DEPOSITO_TOPE_RETENCION='500')
    def test_justo_en_el_tope_se_aplica_y_un_peso_mas_pide_a_direccion(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.foto()
        self.assertIn('mandé el reporte', self.reportar('500.01'))
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'POR_APROBAR')
        post.assert_not_called()

    def test_sin_tope_configurado_toda_retencion_pasa_por_direccion(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.foto()
        respuesta = self.reportar('1')
        self.assertIn('mandé el reporte', respuesta)
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'POR_APROBAR')
        self.assertEqual(self.movimientos(), set())
        destino = self.aviso.call_args.args[0]
        self.assertEqual(destino, f'52{DIRECCION}')
        botones = self.aviso.call_args.kwargs['botones']
        self.assertEqual(botones, [(f'insp:{inspeccion.pk}:apr', 'Aprobar'), (f'insp:{inspeccion.pk}:rech', 'Devolver todo')])
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia=f'inspeccion:{inspeccion.pk}:aprobar').exists())

    def _por_aprobar(self):
        self.con_tarjeta()
        inspeccion = self.inspeccion()
        self.foto()
        self.reportar('700')
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'POR_APROBAR')
        return inspeccion

    def test_aprobar_desde_direccion_retiene(self, post):
        inspeccion = self._por_aprobar()
        atendido, respuesta = svc.atender(DIRECCION, f'insp:{inspeccion.pk}:apr')
        self.assertIn('retuve $700.00', respuesta)
        self.assertEqual(self.movimientos(), {('RETENCION_DANOS', Decimal('700.00')), ('DEVOLUCION', Decimal('460.00'))})
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.resolucion, 'RETENCION_APROBADA')
        self.assertEqual(Pendiente.objects.filter(estado='ABIERTO').count(), 0)

    def test_aprobar_dos_veces_no_duplica(self, post):
        inspeccion = self._por_aprobar()
        svc.atender(DIRECCION, f'insp:{inspeccion.pk}:apr')
        self.assertEqual(svc.atender(DIRECCION, f'insp:{inspeccion.pk}:apr'), (True, None))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(MovimientoDeposito.objects.filter(tipo='RETENCION_DANOS').count(), 1)

    def test_el_encargado_no_puede_aprobar_ni_rechazar(self, post):
        inspeccion = self._por_aprobar()
        self.assertEqual(svc.atender(ENCARGADO, f'insp:{inspeccion.pk}:apr'), (True, None))
        self.assertEqual(svc.atender(AJENO, f'insp:{inspeccion.pk}:rech'), (True, None))
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'POR_APROBAR')
        self.assertEqual(self.movimientos(), set())
        post.assert_not_called()

    def test_devolver_todo_desde_direccion(self, post):
        inspeccion = self._por_aprobar()
        _, respuesta = svc.atender(DIRECCION, f'insp:{inspeccion.pk}:rech')
        self.assertIn('completo', respuesta)
        self.assertEqual(self.movimientos(), {('DEVOLUCION', Decimal('1160.00'))})
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.resolucion, 'RETENCION_RECHAZADA')

    def test_aprobar_con_efectivo_queda_manual(self, post):
        self.en_efectivo()
        inspeccion = self.inspeccion()
        self.foto()
        self.reportar('700')
        svc.atender(DIRECCION, f'insp:{inspeccion.pk}:apr')
        inspeccion.refresh_from_db()
        # 460 por devolver en efectivo/transferencia: no se registra como hecho.
        self.assertEqual(inspeccion.estado, 'MANUAL')
        self.assertEqual(self.movimientos(), set())

    def test_foto_de_un_numero_ajeno_o_sin_inspeccion_no_se_guarda(self, post):
        self.con_tarjeta()
        with patch.object(svc, 'descargar_media_wa') as descargar:
            self.assertIsNone(svc.recibir_foto(AJENO, 'm', ''))
            self.assertIn('ninguna inspección', svc.recibir_foto(ENCARGADO, 'm', ''))
        descargar.assert_not_called()
        self.assertFalse(FotoInspeccion.objects.exists())

    def test_foto_guarda_evidencia_y_detiene_la_devolucion_automatica(self, post):
        self.con_tarjeta()
        inspeccion = self.inspeccion(vence_en=timezone.now() - timedelta(minutes=1))
        respuesta = self.foto()
        self.assertIn('Falta el monto', respuesta)
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, 'DANOS')
        self.assertEqual(inspeccion.fotos.count(), 1)
        self.assertEqual(svc.devolver_sin_reporte(), [])
        post.assert_not_called()

    def test_foto_invalida_se_explica(self, post):
        self.inspeccion()
        with patch.object(svc, 'descargar_media_wa', side_effect=ValueError('Solo puedo recibir fotos (JPG, PNG) o PDF.')):
            self.assertIn('Solo puedo recibir fotos', svc.recibir_foto(ENCARGADO, 'm', ''))
        self.assertFalse(FotoInspeccion.objects.exists())


PENDIENTE_ENC = '9992220000'
PENDIENTE_DIR = '9993330000'


@override_settings(STORAGES=STORAGES_TEST)
@patch('comercial.services_openpay.requests.post', return_value=OK)
class NumeroPendienteDeAceptoTest(Base):
    """Un número que aún no contesta «ACEPTO» no mueve dinero (ni libera ni retiene)."""

    def setUp(self):
        super().setUp()
        Responsable.objects.filter(rol='ENCARGADO').update(telefono_pendiente=PENDIENTE_ENC)
        Responsable.objects.filter(rol='DIRECCION').update(telefono_pendiente=PENDIENTE_DIR)
        self.con_tarjeta()

    def sin_efecto(self, post, inspeccion, estado):
        post.assert_not_called()
        self.assertEqual(self.movimientos(), set())
        inspeccion.refresh_from_db()
        self.assertEqual(inspeccion.estado, estado)

    def test_sin_danos_y_boton_ok_de_encargado_pendiente(self, post):
        inspeccion = self.inspeccion()
        self.assertEqual(svc.atender(PENDIENTE_ENC, 'sin daños'), (False, None))
        self.assertEqual(svc.atender(PENDIENTE_ENC, f'insp:{inspeccion.pk}:ok'), (True, None))
        self.assertEqual(svc.atender(PENDIENTE_DIR, f'insp:{inspeccion.pk}:ok'), (True, None))
        self.sin_efecto(post, inspeccion, 'SOLICITADA')

    def test_reporte_de_daños_y_fotos_de_pendientes(self, post):
        inspeccion = self.inspeccion()
        for tel in (PENDIENTE_ENC, PENDIENTE_DIR):
            self.assertEqual(svc.atender(tel, 'daños 300 vidrio roto'), (False, None))
            with patch.object(svc, 'descargar_media_wa') as descargar:
                self.assertIsNone(svc.recibir_foto(tel, 'm1', ''))
            descargar.assert_not_called()
        self.assertFalse(FotoInspeccion.objects.exists())
        self.sin_efecto(post, inspeccion, 'SOLICITADA')

    @override_settings(ERP_DEPOSITO_TOPE_RETENCION='500')
    def test_direccion_pendiente_no_aprueba_ni_rechaza(self, post):
        inspeccion = self.inspeccion(estado='POR_APROBAR', monto_danos=Decimal('700.00'), desglose='vidrio roto')
        self.assertEqual(svc.atender(PENDIENTE_DIR, f'insp:{inspeccion.pk}:apr'), (True, None))
        self.assertEqual(svc.atender(PENDIENTE_DIR, f'insp:{inspeccion.pk}:rech'), (True, None))
        self.assertEqual(svc.atender(PENDIENTE_ENC, f'insp:{inspeccion.pk}:apr'), (True, None))
        self.sin_efecto(post, inspeccion, 'POR_APROBAR')

    def test_los_numeros_activos_siguen_funcionando(self, post):
        inspeccion = self.inspeccion()
        self.assertEqual(svc.rol_activo(ENCARGADO), 'ENCARGADO')
        self.assertEqual(svc.rol_activo(DIRECCION), 'DIRECCION')
        self.assertIsNone(svc.rol_activo(PENDIENTE_ENC))
        self.assertIn('sin daños', svc.atender(ENCARGADO, f'insp:{inspeccion.pk}:ok')[1])


class AtoradasTest(Base):
    @patch('comercial.services_openpay.requests.post', return_value=OK)
    def test_avisa_reportes_incompletos_y_liquidaciones_detenidas(self, post):
        incompleta = self.inspeccion(estado='DANOS')
        detenida = InspeccionDeposito.objects.create(
            deposito=asegurar_deposito(_cotizacion(), Decimal('1160.00')), estado='PROCESANDO',
            vence_en=timezone.now())
        viejo = timezone.now() - timedelta(hours=49)
        InspeccionDeposito.objects.filter(pk__in=[incompleta.pk, detenida.pk]).update(updated_at=viejo)
        self.assertEqual(svc.revisar_atoradas(), 2)
        self.assertEqual(svc.revisar_atoradas(), 2)  # idempotente: la clave evita duplicar el pendiente
        self.assertEqual(Pendiente.objects.filter(tipo='DEPOSITO', severidad='URGENTE').count(), 2)

    def test_no_avisa_lo_reciente(self):
        self.inspeccion(estado='DANOS')
        self.assertEqual(svc.revisar_atoradas(), 0)


class TopeTest(TestCase):
    def test_valores_invalidos_o_negativos_valen_cero(self):
        for valor, esperado in (('0', 0), ('', 0), ('abc', 0), ('-5', 0), ('250.5', Decimal('250.50')), (300, 300)):
            with override_settings(ERP_DEPOSITO_TOPE_RETENCION=valor):
                self.assertEqual(svc.tope_retencion(), Decimal(str(esperado)).quantize(Decimal('0.01')), valor)
