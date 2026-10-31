"""Acciones en Meta (fase 5): solo APROBADA ejecuta, ±20 %, tope diario, interruptor, revalidación, idempotencia,
reasignación con fallo, permisos y secretos. Meta siempre mockeado."""
import logging
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from comunicacion.models import Pendiente
from core_erp.test_utils import login_superuser_con_totp
from marketing.models import AccionAnuncio, Anuncio, Campana, Decision
from marketing.services import acciones
from marketing.services.proveedores import CampanaExterna, EstadoObjeto, ProveedorError

User = get_user_model()
TOKEN = 'tok-escritura-secreto'


class FalsoMeta:
    """Proveedor de prueba: guarda un estado por id y registra las escrituras."""

    def __init__(self, escribe=True):
        self.escribe, self.estados, self.llamadas, self.falla_en = escribe, {}, [], None

    def puede_escribir(self):
        return self.escribe

    def leer_estado(self, id_externo, con_presupuesto=False):
        return self.estados[str(id_externo)]

    def listar_campanas(self):
        for k, e in self.estados.items():
            if e.nivel_presupuesto == 'CAMPANA':
                yield CampanaExterna(k, k, e.estado, presupuesto_diario=e.presupuesto_diario)

    def _hacer(self, nombre, id_externo, valor=None):
        self.llamadas.append((nombre, str(id_externo), valor))
        if self.falla_en == len(self.llamadas):
            raise ProveedorError(f'Meta respondió con error: token {TOKEN} inválido')

    def pausar(self, i):
        self._hacer('pausar', i)

    def reanudar(self, i):
        self._hacer('reanudar', i)

    def ajustar_presupuesto(self, i, v):
        self._hacer('ajustar', i, Decimal(v))


def vivo(estado='ACTIVE', presupuesto=None):
    return EstadoObjeto('x', estado, Decimal(presupuesto) if presupuesto is not None else None,
                        'CAMPANA' if presupuesto is not None else '')


class Base(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('dir', 'd@x.com', 'x')
        self.meta = FalsoMeta()
        p = patch('marketing.services.acciones.proveedor_meta', return_value=self.meta)
        p.start()
        self.addCleanup(p.stop)

    def campana(self, ext, presupuesto='100.00', estado='ACTIVE'):
        c = Campana.objects.create(id_externo=ext, nombre=f'Camp {ext}', estado=estado,
                                   presupuesto_diario=Decimal(presupuesto))
        self.meta.estados[ext] = vivo(estado, presupuesto)
        return c

    def decision(self, tipo, accion, estado='APROBADA', dias=5):
        return Decision.objects.create(
            regla='R', objeto_clave=f'o{Decision.objects.count()}', tipo_accion=tipo, titulo='Prueba', accion_propuesta=accion,
            estado=estado, expira_en=timezone.now() + timedelta(days=dias))

    def ajuste(self, c, nuevo, **kw):
        return self.decision(Decision.AJUSTAR, {'tipo': 'AJUSTAR_PRESUPUESTO', 'campana_id': c.pk,
                                                'presupuesto_actual': str(c.presupuesto_diario),
                                                'presupuesto_nuevo': nuevo}, **kw)

    def reasignar(self, desde, hacia, monto='20.00'):
        m = Decimal(monto)
        return self.decision(Decision.REASIGNAR, {
            'tipo': 'REASIGNAR', 'desde_campana_id': desde.pk, 'hacia_campana_id': hacia.pk, 'monto_diario': monto,
            'desde_nuevo': str(desde.presupuesto_diario - m), 'hacia_nuevo': str(hacia.presupuesto_diario + m)})


@override_settings(ERP_MARKETING_ACCIONES_ACTIVAS=True, META_ADS_TOKEN_ESCRITURA=TOKEN,
                   ERP_MARKETING_PRESUPUESTO_MAX_DIARIO='1000')
class EjecucionRealTest(Base):
    def test_ajuste_aprobado_se_ejecuta_y_actualiza_lo_local(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        hecho, _ = acciones.ejecutar(d.pk, self.admin)
        self.assertTrue(hecho)
        self.assertEqual(self.meta.llamadas, [('ajustar', 'C1', Decimal('110.00'))])
        d.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual((d.estado, c.presupuesto_diario), ('EJECUTADA', Decimal('110.00')))
        self.assertIsNotNone(d.ejecutado_en)
        fila = d.acciones.get()
        self.assertEqual((fila.modo, fila.estado, fila.valor_anterior, fila.valor_nuevo, fila.created_by),
                         ('REAL', 'OK', Decimal('100.00'), Decimal('110.00'), self.admin))

    def test_solo_aprobada_ejecuta(self):
        c = self.campana('C1')
        for estado in ('PENDIENTE', 'RECHAZADA', 'POSPUESTA', 'EJECUTADA', 'FALLIDA', 'EXPIRADA'):
            d = self.ajuste(c, '110.00', estado=estado)
            self.assertFalse(acciones.ejecutar(d.pk, self.admin)[0], estado)
        self.assertEqual(self.meta.llamadas, [])
        self.assertEqual(AccionAnuncio.objects.count(), 0)

    def test_expirada_no_ejecuta(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00', dias=-1)
        self.assertFalse(acciones.ejecutar(d.pk, self.admin)[0])
        self.assertEqual(self.meta.llamadas, [])

    def test_informativa_no_ejecuta(self):
        d = self.decision(Decision.INFORMATIVA, {'tipo': 'INFORMATIVA'})
        self.assertFalse(acciones.ejecutar(d.pk, self.admin)[0])
        self.assertEqual(self.meta.llamadas, [])

    def test_solo_superusuario(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.assertFalse(acciones.ejecutar(d.pk, staff)[0])
        self.assertEqual(self.meta.llamadas, [])

    def test_sin_credencial_de_escritura_no_hace_nada(self):
        self.meta.escribe = False
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        self.assertFalse(hecho)
        self.assertIn('credencial', mensaje)
        self.assertEqual((self.meta.llamadas, AccionAnuncio.objects.count()), ([], 0))

    def test_limite_de_20_por_ciento_recorta_y_nunca_excede(self):
        c = self.campana('C1', '100.03')
        d = self.decision(Decision.AJUSTAR, {'campana_id': c.pk, 'presupuesto_actual': '100.03',
                                             'presupuesto_nuevo': '500.00'})
        self.assertTrue(acciones.ejecutar(d.pk, self.admin)[0])
        enviado = self.meta.llamadas[0][2]
        self.assertEqual(enviado, Decimal('120.03'))  # 120.036 redondeado hacia adentro
        self.assertLessEqual(enviado, Decimal('100.03') * Decimal('1.2'))

    def test_limite_a_la_baja(self):
        c = self.campana('C1', '100.00')
        d = self.decision(Decision.AJUSTAR, {'campana_id': c.pk, 'presupuesto_actual': '100.00',
                                             'presupuesto_nuevo': '10.00'})
        acciones.ejecutar(d.pk, self.admin)
        self.assertEqual(self.meta.llamadas[0][2], Decimal('80.00'))

    def test_recortar_funcion_pura(self):
        self.assertEqual(acciones.recortar(Decimal('100.01'), Decimal('999')), Decimal('120.01'))
        self.assertEqual(acciones.recortar(Decimal('100.01'), Decimal('1')), Decimal('80.01'))
        self.assertEqual(acciones.recortar(Decimal('100'), Decimal('105')), Decimal('105.00'))

    @override_settings(ERP_MARKETING_PRESUPUESTO_MAX_DIARIO='205')
    def test_tope_diario_bloquea_subir(self):
        c = self.campana('C1', '100.00')
        self.campana('C2', '100.00')
        d = self.ajuste(c, '110.00')  # total quedaría en 210 > 205
        hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        self.assertFalse(hecho)
        self.assertIn('tope', mensaje)
        self.assertEqual(self.meta.llamadas, [])
        d.refresh_from_db()
        self.assertEqual(d.estado, 'APROBADA')  # reintentable si Dirección sube el tope
        self.assertEqual(d.acciones.get().estado, 'FALLIDA')

    @override_settings(ERP_MARKETING_PRESUPUESTO_MAX_DIARIO='210')
    def test_tope_diario_permite_justo_el_limite(self):
        c = self.campana('C1', '100.00')
        self.campana('C2', '100.00')
        self.assertTrue(acciones.ejecutar(self.ajuste(c, '110.00').pk, self.admin)[0])

    @override_settings(ERP_MARKETING_PRESUPUESTO_MAX_DIARIO='')
    def test_sin_tope_definido_no_se_sube_pero_si_se_baja(self):
        c = self.campana('C1', '100.00')
        self.assertFalse(acciones.ejecutar(self.ajuste(c, '110.00').pk, self.admin)[0])
        self.assertEqual(self.meta.llamadas, [])
        self.assertTrue(acciones.ejecutar(self.ajuste(c, '90.00').pk, self.admin)[0])
        self.assertEqual(self.meta.llamadas[0][2], Decimal('90.00'))

    def test_revalidacion_detecta_cambio_de_presupuesto(self):
        c = self.campana('C1', '100.00')
        d = self.ajuste(c, '110.00')
        self.meta.estados['C1'] = vivo('ACTIVE', '150.00')  # alguien lo cambió en Meta
        hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        self.assertFalse(hecho)
        self.assertIn('cambió', mensaje)
        self.assertEqual(self.meta.llamadas, [])
        d.refresh_from_db()
        self.assertEqual(d.estado, 'FALLIDA')

    def test_presupuesto_en_conjuntos_se_rechaza(self):
        c = self.campana('C1', '100.00')
        self.meta.estados['C1'] = EstadoObjeto('C1', 'ACTIVE', None, 'CONJUNTO')
        hecho, mensaje = acciones.ejecutar(self.ajuste(c, '110.00').pk, self.admin)
        self.assertFalse(hecho)
        self.assertIn('conjuntos', mensaje)
        self.assertEqual(self.meta.llamadas, [])

    def test_error_de_lectura_no_envia_y_se_puede_reintentar(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        del self.meta.estados['C1']  # KeyError no es ProveedorError: simula lectura con error propio
        self.meta.leer_estado = lambda *a, **k: (_ for _ in ()).throw(ProveedorError('sin red'))
        self.assertFalse(acciones.ejecutar(d.pk, self.admin)[0])
        d.refresh_from_db()
        self.assertEqual((d.estado, self.meta.llamadas), ('APROBADA', []))

    def test_doble_clic_no_ejecuta_dos_veces(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        self.assertTrue(acciones.ejecutar(d.pk, self.admin)[0])
        self.assertFalse(acciones.ejecutar(d.pk, self.admin)[0])
        self.assertEqual(len(self.meta.llamadas), 1)

    def test_ejecucion_en_curso_bloquea_el_segundo_clic(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        AccionAnuncio.objects.create(decision=d, tipo='AJUSTAR_PRESUPUESTO', objeto_tipo='CAMPANA',
                                     objeto_id_externo='C1', modo='REAL', estado='EJECUTANDO')
        hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        self.assertFalse(hecho)
        self.assertIn('ejecutando', mensaje)
        self.assertEqual(self.meta.llamadas, [])

    def test_reintento_tras_caida_no_repite_y_pide_verificar(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        fila = AccionAnuncio.objects.create(decision=d, tipo='AJUSTAR_PRESUPUESTO', objeto_tipo='CAMPANA',
                                            objeto_id_externo='C1', modo='REAL', estado='EJECUTANDO')
        AccionAnuncio.objects.filter(pk=fila.pk).update(updated_at=timezone.now() - timedelta(minutes=30))
        hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        self.assertFalse(hecho)
        self.assertIn('verifica en Meta', mensaje)
        self.assertEqual(self.meta.llamadas, [])
        d.refresh_from_db()
        fila.refresh_from_db()
        self.assertEqual((d.estado, fila.estado), ('FALLIDA', 'FALLIDA'))
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia=f'mkt_accion_fallida:{d.pk}').exists())
        # y un nuevo intento tampoco repite
        self.assertFalse(acciones.ejecutar(d.pk, self.admin)[0])
        self.assertEqual(self.meta.llamadas, [])

    def test_pausar_anuncio_y_ya_pausado(self):
        c = self.campana('C1')
        a = Anuncio.objects.create(campana=c, id_externo='A1', nombre='Anuncio', estado='ACTIVE')
        self.meta.estados['A1'] = vivo('ACTIVE')
        d = self.decision(Decision.PAUSAR, {'nivel': 'anuncio', 'anuncio_id': a.pk, 'anuncio_externo': 'A1'})
        self.assertTrue(acciones.ejecutar(d.pk, self.admin)[0])
        self.assertEqual(self.meta.llamadas, [('pausar', 'A1', None)])
        a.refresh_from_db()
        self.assertEqual(a.estado, 'PAUSED')
        # ya pausado en Meta: no se envía nada
        self.meta.estados['A1'] = vivo('PAUSED')
        d2 = self.decision(Decision.PAUSAR, {'nivel': 'anuncio', 'anuncio_id': a.pk, 'anuncio_externo': 'A1'})
        self.assertTrue(acciones.ejecutar(d2.pk, self.admin)[0])
        self.assertEqual(len(self.meta.llamadas), 1)

    def test_fallo_al_enviar_deja_fallida_sin_secretos(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        self.meta.falla_en = 1
        with self.assertLogs('marketing.services.acciones', level=logging.WARNING) as logs:
            hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        self.assertFalse(hecho)
        d.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual((d.estado, c.presupuesto_diario), ('FALLIDA', Decimal('100.00')))
        textos = [mensaje, d.resultado, d.acciones.get().respuesta, *logs.output,
                  *Pendiente.objects.values_list('detalle', flat=True)]
        for texto in textos:
            self.assertNotIn(TOKEN, texto)

    def test_reasignar_ok_mueve_sin_cambiar_el_total(self):
        a, b = self.campana('CA', '100.00'), self.campana('CB', '100.00')
        d = self.reasignar(a, b)
        self.assertTrue(acciones.ejecutar(d.pk, self.admin)[0])
        self.assertEqual(self.meta.llamadas, [('ajustar', 'CA', Decimal('80.00')), ('ajustar', 'CB', Decimal('120.00'))])
        self.assertEqual(d.acciones.count(), 2)

    def test_reasignar_recorta_el_monto_al_20_por_ciento(self):
        a, b = self.campana('CA', '100.00'), self.campana('CB', '100.00')
        d = self.reasignar(a, b, monto='50.00')
        self.assertTrue(acciones.ejecutar(d.pk, self.admin)[0])
        self.assertEqual([c[2] for c in self.meta.llamadas], [Decimal('80.00'), Decimal('120.00')])

    def test_reasignar_falla_el_segundo_y_revierte_el_primero(self):
        a, b = self.campana('CA', '100.00'), self.campana('CB', '100.00')
        d = self.reasignar(a, b)
        self.meta.falla_en = 2
        hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        self.assertFalse(hecho)
        self.assertEqual(self.meta.llamadas, [('ajustar', 'CA', Decimal('80.00')), ('ajustar', 'CB', Decimal('120.00')),
                                              ('ajustar', 'CA', Decimal('100.00'))])
        a.refresh_from_db()
        d.refresh_from_db()
        self.assertEqual((d.estado, a.presupuesto_diario), ('FALLIDA', Decimal('100.00')))
        self.assertTrue(d.acciones.filter(reversion=True, estado='OK').exists())
        self.assertNotIn(TOKEN, mensaje + d.resultado)

    def test_reasignar_falla_el_segundo_y_la_reversion_tambien(self):
        a, b = self.campana('CA', '100.00'), self.campana('CB', '100.00')
        d = self.reasignar(a, b)
        self.meta.falla_en = 2
        original = self.meta.ajustar_presupuesto

        def ajustar(i, v):
            if len(self.meta.llamadas) == 2:  # la reversión
                self.meta.llamadas.append(('ajustar', i, v))
                raise ProveedorError('sigue caído')
            original(i, v)
        self.meta.ajustar_presupuesto = ajustar
        hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        self.assertFalse(hecho)
        self.assertIn('también falló', mensaje)
        self.assertTrue(d.acciones.filter(reversion=True, estado='FALLIDA').exists())
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia=f'mkt_accion_fallida:{d.pk}',
                                                 severidad='URGENTE').exists())

    def test_bitacora_inmutable(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        acciones.ejecutar(d.pk, self.admin)
        fila = d.acciones.get()
        fila.respuesta = 'editada'
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            fila.save()
        with self.assertRaises(ValidationError):
            fila.delete()


@override_settings(ERP_MARKETING_ACCIONES_ACTIVAS=False, META_ADS_TOKEN_ESCRITURA='', ERP_MARKETING_PRESUPUESTO_MAX_DIARIO='1000')
class SimulacionTest(Base):
    def test_interruptor_apagado_simula_sin_tocar_meta(self):
        c = self.campana('C1')
        d = self.ajuste(c, '110.00')
        with patch('requests.post') as post, patch('requests.get') as get:
            hecho, mensaje = acciones.ejecutar(d.pk, self.admin)
        post.assert_not_called()
        get.assert_not_called()
        self.assertTrue(hecho)
        self.assertIn('Simulación', mensaje)
        self.assertEqual(self.meta.llamadas, [])
        d.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual((d.estado, c.presupuesto_diario, d.ejecutado_en), ('APROBADA', Decimal('100.00'), None))
        fila = d.acciones.get()
        self.assertEqual((fila.modo, fila.estado, fila.valor_nuevo), ('SIMULACION', 'OK', Decimal('110.00')))

    def test_simulacion_aplica_los_mismos_topes(self):
        c = self.campana('C1')
        d = self.decision(Decision.AJUSTAR, {'campana_id': c.pk, 'presupuesto_actual': '100.00',
                                             'presupuesto_nuevo': '300.00'})
        acciones.ejecutar(d.pk, self.admin)
        self.assertEqual(d.acciones.get().valor_nuevo, Decimal('120.00'))

    def test_simulacion_tambien_exige_aprobada_y_superusuario(self):
        c = self.campana('C1')
        self.assertFalse(acciones.ejecutar(self.ajuste(c, '110.00', estado='PENDIENTE').pk, self.admin)[0])
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.assertFalse(acciones.ejecutar(self.ajuste(c, '110.00').pk, staff)[0])
        self.assertEqual(AccionAnuncio.objects.count(), 0)


class VistaTest(Base):
    def setUp(self):
        super().setUp()
        self.c = self.campana('C1')
        self.dec = self.ajuste(self.c, '110.00')

    def url(self):
        return reverse('marketing:ejecutar', args=[self.dec.pk])

    def test_tarjeta_muestra_boton_simular_y_aviso(self):
        login_superuser_con_totp(self.client, self.admin)
        r = self.client.get(reverse('marketing:tablero'))
        self.assertContains(r, self.url())
        self.assertContains(r, 'Simular')
        self.assertContains(r, 'Modo simulación')
        self.assertContains(r, '$100.00')
        self.assertNotContains(r, 'style=')

    @override_settings(ERP_MARKETING_ACCIONES_ACTIVAS=True)
    def test_modo_real_dice_ejecutar_en_meta(self):
        login_superuser_con_totp(self.client, self.admin)
        r = self.client.get(reverse('marketing:tablero'))
        self.assertContains(r, 'Ejecutar en Meta')

    def test_post_simula(self):
        login_superuser_con_totp(self.client, self.admin)
        self.assertEqual(self.client.post(self.url()).status_code, 302)
        self.assertEqual(self.dec.acciones.get().modo, 'SIMULACION')

    def test_get_no_ejecuta(self):
        login_superuser_con_totp(self.client, self.admin)
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertEqual(AccionAnuncio.objects.count(), 0)

    def test_exige_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        login_superuser_con_totp(cliente, self.admin)
        self.assertEqual(cliente.post(self.url()).status_code, 403)
        self.assertEqual(AccionAnuncio.objects.count(), 0)

    def test_staff_y_anonimo_no_ejecutan(self):
        self.assertEqual(self.client.post(self.url()).status_code, 302)
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url()).status_code, 403)
        self.assertEqual(AccionAnuncio.objects.count(), 0)

    def test_admin_acciones_solo_lectura(self):
        login_superuser_con_totp(self.client, self.admin)
        self.client.post(self.url())
        self.assertEqual(self.client.get(reverse('admin:marketing_accionanuncio_changelist')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:marketing_accionanuncio_add')).status_code, 403)
