"""Órdenes de Dirección por WhatsApp, mecánica común y bloqueo de fechas (Issue #433)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from comercial import services_ordenes_wa as ordenes
from comercial.models import AccionWhatsApp, BloqueoFecha, Cliente, Cotizacion, ItemCotizacion
from comunicacion.models import Responsable

TEL_DIR = '525555550012'
TEL_ENC = '525555550011'
TEL_RESPALDO = '525555550099'


def responsable(rol, telefono, **kw):
    return Responsable.objects.create(rol=rol, nombre=f'{rol} test', telefono_wa=telefono, **kw)


def cotizacion(dias=60, estado='BORRADOR', precio='10000.00'):
    cliente = Cliente.objects.create(nombre='Cliente Orden', telefono='5555550001')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento', incluye_refrescos=False,
        fecha_evento=timezone.localdate() + timedelta(days=dias))
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1,
                                  precio_unitario=Decimal(precio))
    Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
    cot.refresh_from_db()
    return cot


def token_de(resultado, decision='ok'):
    return next(b for b, _ in resultado.botones if b.endswith(f':{decision}'))


@override_settings(ERP_WA_NEGOCIO_NUMERO=TEL_RESPALDO)
class DireccionActivaTest(TestCase):
    def test_solo_el_telefono_activo_de_direccion(self):
        responsable('DIRECCION', TEL_DIR)
        responsable('ENCARGADO', TEL_ENC)
        self.assertTrue(ordenes.direccion_activa(TEL_DIR))
        self.assertTrue(ordenes.direccion_activa('+52 1 55 5555 0012'.replace(' ', '')))
        for no in (TEL_ENC, TEL_RESPALDO, '525555550000', ''):
            self.assertFalse(ordenes.direccion_activa(no), no)

    def test_sin_responsable_el_respaldo_no_cuenta(self):
        self.assertFalse(ordenes.direccion_activa(TEL_RESPALDO))

    def test_numero_pendiente_de_acepto_no_cuenta(self):
        responsable('DIRECCION', TEL_DIR, telefono_pendiente='525555550077')
        self.assertFalse(ordenes.direccion_activa('525555550077'))
        self.assertTrue(ordenes.direccion_activa(TEL_DIR))

    def test_responsable_inactivo_o_suplente_fuera_de_rango_no_cuenta(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Baja', telefono_wa=TEL_DIR, activo=False)
        self.assertFalse(ordenes.direccion_activa(TEL_DIR))
        hoy = timezone.localdate()
        Responsable.objects.create(rol='DIRECCION', nombre='Suplente', telefono_wa='525555550055',
                                   vigente_desde=hoy + timedelta(days=5))
        self.assertFalse(ordenes.direccion_activa('525555550055'))


class BloqueoPorWhatsAppTest(TestCase):
    def setUp(self):
        responsable('DIRECCION', TEL_DIR)
        responsable('ENCARGADO', TEL_ENC)
        self.desde = timezone.localdate() + timedelta(days=40)
        self.hasta = self.desde + timedelta(days=2)

    def _proponer(self, telefono=TEL_DIR, **kw):
        datos = dict(desde=self.desde.isoformat(), hasta=self.hasta.isoformat(), motivo='MANTENIMIENTO')
        datos.update(kw)
        return ordenes.proponer_bloqueo(telefono, **datos)

    def test_otro_telefono_no_obtiene_propuesta_ni_deja_rastro(self):
        self.assertIsNone(self._proponer(TEL_ENC))
        self.assertIsNone(self._proponer('525555550000'))
        self.assertEqual((AccionWhatsApp.objects.count(), BloqueoFecha.objects.count()), (0, 0))

    def test_propuesta_no_bloquea_hasta_confirmar(self):
        r = self._proponer()
        self.assertIn('Bloquear del', r.texto)
        self.assertIn('3 días', r.texto)
        self.assertIn('mantenimiento', r.texto)
        self.assertEqual(BloqueoFecha.objects.count(), 0)
        a = AccionWhatsApp.objects.get()
        self.assertEqual((a.estado, a.telefono, a.rol, a.tipo), ('PROPUESTA', TEL_DIR, 'DIRECCION', 'BLOQUEO_FECHAS'))
        token = token_de(r).split(':')[1]
        self.assertNotIn(token, a.token_hash)   # nunca en claro
        self.assertEqual([t for _, t in r.botones], ['Confirmar', 'Cancelar'])

    def test_confirmar_crea_el_bloqueo_una_sola_vez(self):
        r = self._proponer(notas='Pintura de la alberca')
        ok = ordenes.responder_boton(TEL_DIR, token_de(r))
        self.assertTrue(ok.ejecutada)
        b = BloqueoFecha.objects.get()
        self.assertEqual((b.fecha_inicio, b.fecha_fin, b.motivo, b.activo), (self.desde, self.hasta, 'MANTENIMIENTO', True))
        self.assertIn('WhatsApp', b.notas)
        self.assertIn('Pintura de la alberca', b.notas)
        a = AccionWhatsApp.objects.get()
        self.assertEqual((a.estado, a.bloqueo_id), ('CONFIRMADA', b.pk))
        otra = ordenes.responder_boton(TEL_DIR, token_de(r))
        self.assertFalse(otra.ejecutada)
        self.assertEqual(otra.texto, ordenes.YA_REGISTRADA)
        self.assertEqual(BloqueoFecha.objects.count(), 1)

    def test_el_bloqueo_cierra_la_fecha_en_disponibilidad(self):
        from comercial.disponibilidad import verificar_disponibilidad_rango
        r = self._proponer()
        ordenes.responder_boton(TEL_DIR, token_de(r))
        ok = verificar_disponibilidad_rango(self.desde, self.desde + timedelta(days=1))
        self.assertFalse(ok[0])

    def test_boton_desde_otro_telefono_no_hace_nada(self):
        r = self._proponer()
        self.assertIsNone(ordenes.responder_boton(TEL_ENC, token_de(r)))
        self.assertEqual((BloqueoFecha.objects.count(), AccionWhatsApp.objects.get().estado), (0, 'PROPUESTA'))

    def test_token_atado_al_telefono_que_propuso(self):
        r = self._proponer()
        AccionWhatsApp.objects.update(telefono='525555550123')
        self.assertEqual(ordenes.responder_boton(TEL_DIR, token_de(r)).texto, ordenes.BOTON_INVALIDO)
        self.assertEqual(BloqueoFecha.objects.count(), 0)

    def test_token_falso_o_de_otro_formato(self):
        self._proponer()
        falso = 'ord:' + 'A' * 32 + ':ok'
        self.assertEqual(ordenes.responder_boton(TEL_DIR, falso).texto, ordenes.BOTON_INVALIDO)
        self.assertIsNone(ordenes.responder_boton(TEL_DIR, 'ord:corto:ok'))
        self.assertIsNone(ordenes.responder_boton(TEL_DIR, 'confirmar'))

    def test_vencido_a_los_10_minutos(self):
        r = self._proponer()
        AccionWhatsApp.objects.update(vence_en=timezone.now() - timedelta(seconds=1))
        self.assertEqual(ordenes.responder_boton(TEL_DIR, token_de(r)).texto, ordenes.BOTON_VENCIDO)
        self.assertEqual((AccionWhatsApp.objects.get().estado, BloqueoFecha.objects.count()), ('VENCIDA', 0))
        self.assertEqual(ordenes.responder_boton(TEL_DIR, token_de(r)).texto, ordenes.BOTON_VENCIDO)

    def test_vigencia_de_10_minutos(self):
        self._proponer()
        a = AccionWhatsApp.objects.get()
        self.assertAlmostEqual((a.vence_en - a.created_at).total_seconds(), 600, delta=2)

    def test_cancelar_no_crea_nada_y_el_confirmar_posterior_tampoco(self):
        r = self._proponer()
        self.assertEqual(ordenes.responder_boton(TEL_DIR, token_de(r, 'no')).texto, ordenes.CANCELADA)
        self.assertEqual(AccionWhatsApp.objects.get().estado, 'CANCELADA')
        ordenes.responder_boton(TEL_DIR, token_de(r, 'ok'))
        self.assertEqual(BloqueoFecha.objects.count(), 0)

    def test_propuesta_identica_anula_la_anterior(self):
        r1 = self._proponer()
        r2 = self._proponer()
        self.assertEqual(sorted(AccionWhatsApp.objects.values_list('estado', flat=True)), ['PROPUESTA', 'VENCIDA'])
        ordenes.responder_boton(TEL_DIR, token_de(r1))
        self.assertEqual(BloqueoFecha.objects.count(), 0)
        self.assertTrue(ordenes.responder_boton(TEL_DIR, token_de(r2)).ejecutada)

    def test_no_se_bloquea_encima_de_una_confirmada(self):
        cot = cotizacion(estado='CONFIRMADA')
        cot.fecha_evento = self.desde + timedelta(days=1)
        cot.save(update_fields=['fecha_evento'])
        r = self._proponer()
        self.assertIsNone(r.botones)
        self.assertIn(f'COT-{cot.pk:03d}', r.texto)
        a = AccionWhatsApp.objects.get()
        self.assertEqual(a.estado, 'RECHAZADA')
        self.assertEqual(BloqueoFecha.objects.count(), 0)

    def test_se_revalida_al_confirmar_si_en_el_intermedio_se_confirmo_una_reservacion(self):
        r = self._proponer()
        cot = cotizacion(estado='CONFIRMADA')
        Cotizacion.objects.filter(pk=cot.pk).update(fecha_evento=self.desde + timedelta(days=1))
        res = ordenes.responder_boton(TEL_DIR, token_de(r))
        self.assertFalse(res.ejecutada)
        self.assertIn('No se hizo ningún cambio', res.texto)
        self.assertEqual((BloqueoFecha.objects.count(), AccionWhatsApp.objects.get().estado), (0, 'FALLIDA'))

    def test_avisa_las_abiertas_al_proponer_y_al_confirmar(self):
        cot = cotizacion(estado='COTIZADA')
        Cotizacion.objects.filter(pk=cot.pk).update(fecha_evento=self.desde)
        r = self._proponer()
        self.assertIn(f'COT-{cot.pk:03d}', r.texto)
        ok = ordenes.responder_boton(TEL_DIR, token_de(r))
        self.assertIn(f'COT-{cot.pk:03d}', ok.texto)
        self.assertIn('Avísales', ok.texto)

    def test_rechazos_con_motivo_y_en_bitacora(self):
        hoy = timezone.localdate()
        casos = {
            'fechas ilegibles': dict(desde='pasado mañana', hasta='después', motivo='OTRO'),
            'invertidas': dict(desde=self.hasta.isoformat(), hasta=self.desde.isoformat()),
            'ya pasaron': dict(desde=(hoy - timedelta(days=9)).isoformat(), hasta=(hoy - timedelta(days=8)).isoformat()),
            'demasiado largo': dict(hasta=(self.desde + timedelta(days=120)).isoformat()),
            'motivo': dict(motivo='VACACIONES'),
        }
        for nombre, kw in casos.items():
            r = self._proponer(**kw)
            self.assertIsNone(r.botones, nombre)
        self.assertEqual(AccionWhatsApp.objects.filter(estado='RECHAZADA').count(), len(casos))
        self.assertEqual(BloqueoFecha.objects.count(), 0)

    def test_ya_bloqueadas_se_rechaza(self):
        ordenes.responder_boton(TEL_DIR, token_de(self._proponer()))
        r = self._proponer(desde=self.desde.isoformat(), hasta=self.desde.isoformat())
        self.assertIn('ya están bloqueadas', r.texto)
        self.assertEqual(BloqueoFecha.objects.count(), 1)
