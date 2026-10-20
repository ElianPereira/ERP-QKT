"""Programador único (Issue #423): horarios, ranuras, vigilante, salud y admin."""
import threading
from datetime import datetime, timedelta
from datetime import timezone as dt_tz
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, get_commands
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from comunicacion.models import Pendiente
from core_erp import context_processors
from core_erp.programador import TAREAS, ZONA, Tarea, cada, diario, mensual, semanal
from core_erp.test_utils import login_superuser_con_totp
from operaciones import services_programador as svc
from operaciones.models import EjecucionCron

MIN = timedelta(minutes=1)


def mx(*args):
    return datetime(*args, tzinfo=ZONA)


class HorariosTest(TestCase):
    def test_cada_15_minutos_alinea_a_la_medianoche_local(self):
        self.assertEqual(cada(15).ultima_ranura(mx(2026, 10, 20, 9, 37)), mx(2026, 10, 20, 9, 30))
        self.assertEqual(cada(15).ultima_ranura(mx(2026, 10, 20, 9, 30)), mx(2026, 10, 20, 9, 30))
        self.assertEqual(cada(60).ultima_ranura(mx(2026, 10, 20, 0, 5)), mx(2026, 10, 20, 0, 0))

    def test_diario_antes_y_despues_de_la_hora(self):
        self.assertEqual(diario('18:00').ultima_ranura(mx(2026, 10, 20, 18, 0)), mx(2026, 10, 20, 18, 0))
        self.assertEqual(diario('18:00').ultima_ranura(mx(2026, 10, 20, 17, 59)), mx(2026, 10, 19, 18, 0))

    def test_diario_acepta_instantes_en_utc(self):
        # 00:10 Mérida = 06:10 UTC.
        ahora = datetime(2026, 10, 20, 6, 15, tzinfo=dt_tz.utc)
        self.assertEqual(diario('00:10').ultima_ranura(ahora), mx(2026, 10, 20, 0, 10))

    def test_semanal_lunes_cruza_el_cambio_de_mes(self):
        h = semanal(0, '08:52')
        # lunes 2 de noviembre de 2026, antes de la hora: la ranura es el lunes anterior (octubre)
        self.assertEqual(h.ultima_ranura(mx(2026, 11, 2, 8, 0)), mx(2026, 10, 26, 8, 52))
        self.assertEqual(h.ultima_ranura(mx(2026, 11, 2, 8, 52)), mx(2026, 11, 2, 8, 52))
        # miércoles: el lunes de esa semana
        self.assertEqual(h.ultima_ranura(mx(2026, 11, 4, 12, 0)), mx(2026, 11, 2, 8, 52))
        # domingo: el lunes de seis días antes
        self.assertEqual(h.ultima_ranura(mx(2026, 11, 8, 23, 0)), mx(2026, 11, 2, 8, 52))

    def test_mensual_dia_2_cruza_mes_y_anio(self):
        h = mensual(2, '08:45')
        self.assertEqual(h.ultima_ranura(mx(2026, 11, 1, 12, 0)), mx(2026, 10, 2, 8, 45))
        self.assertEqual(h.ultima_ranura(mx(2026, 11, 2, 8, 44)), mx(2026, 10, 2, 8, 45))
        self.assertEqual(h.ultima_ranura(mx(2026, 11, 2, 8, 45)), mx(2026, 11, 2, 8, 45))
        self.assertEqual(h.ultima_ranura(mx(2027, 1, 1, 10, 0)), mx(2026, 12, 2, 8, 45))
        self.assertEqual(h.ultima_ranura(mx(2027, 1, 2, 9, 0)), mx(2027, 1, 2, 8, 45))

    def test_mensual_dia_31_se_ajusta_a_meses_cortos(self):
        h = mensual(31, '10:00')
        self.assertEqual(h.ultima_ranura(mx(2026, 3, 1, 0, 0)), mx(2026, 2, 28, 10, 0))

    def test_todas_las_tareas_apuntan_a_un_comando_que_existe(self):
        existentes = set(get_commands())
        faltan = [t.comando for t in TAREAS if t.comando not in existentes]
        self.assertEqual(faltan, [])
        nombres = [t.nombre for t in TAREAS]
        self.assertEqual(len(nombres), len(set(nombres)))

    def test_comandos_con_aplicar_lo_conservan(self):
        por = {t.nombre: t for t in TAREAS}
        for nombre in ('avisar_vencimientos', 'sincronizar_comisiones_openpay',
                       'purgar_conversaciones_whatsapp', 'resumir_preguntas_agente'):
            self.assertEqual(por[nombre].args, ('--aplicar',), nombre)


TAREA_X = Tarea('tarea_x', 'comando_x', diario('10:00'), 2 * 60 * MIN)
TAREA_Y = Tarea('tarea_y', 'comando_y', diario('10:00'), 2 * 60 * MIN, ('--aplicar',))


def _con_tareas(*tareas):
    return patch.multiple(svc, TAREAS=tareas)


@patch('comunicacion.services_pendientes._avisar_urgente')
class VueltaTest(TestCase):
    ahora = mx(2026, 10, 20, 10, 20)  # ranura del día: 10:00, 20 min de atraso

    def test_corre_la_ranura_una_sola_vez(self, _):
        with _con_tareas(TAREA_X, TAREA_Y), patch.object(svc, 'call_command') as cc:
            primera = svc.correr_vuelta(self.ahora)
            segunda = svc.correr_vuelta(self.ahora + 5 * MIN)
        self.assertEqual([r.accion for r in primera], ['corrio', 'corrio'])
        self.assertEqual([r.accion for r in segunda], ['tomada', 'tomada'])
        self.assertEqual(cc.call_count, 2)
        cc.assert_any_call('comando_y', '--aplicar', stdout=cc.call_args.kwargs['stdout'],
                           stderr=cc.call_args.kwargs['stderr'])
        e = EjecucionCron.objects.get(tarea='tarea_x')
        self.assertEqual((e.estado, e.programada_para), ('EXITO', mx(2026, 10, 20, 10, 0)))
        self.assertIsNotNone(e.fin)

    def test_ranura_ya_tomada_por_otro_proceso_no_se_corre(self, _):
        EjecucionCron.objects.create(tarea='tarea_x', programada_para=mx(2026, 10, 20, 10, 0),
                                     inicio=timezone.now())
        with _con_tareas(TAREA_X), patch.object(svc, 'call_command') as cc:
            res = svc.correr_vuelta(self.ahora)
        self.assertEqual(res[0].accion, 'tomada')
        cc.assert_not_called()

    def test_unique_constraint_impide_duplicar_la_ranura(self, _):
        EjecucionCron.objects.create(tarea='t', programada_para=mx(2026, 10, 20, 10, 0), inicio=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            EjecucionCron.objects.create(tarea='t', programada_para=mx(2026, 10, 20, 10, 0),
                                         inicio=timezone.now())

    def test_ranura_atrasada_dentro_de_tolerancia_corre_una_vez(self, _):
        with _con_tareas(TAREA_X), patch.object(svc, 'call_command') as cc:
            svc.correr_vuelta(mx(2026, 10, 20, 11, 55))
            svc.correr_vuelta(mx(2026, 10, 20, 12, 5))  # misma ranura, pasada la tolerancia
        self.assertEqual(cc.call_count, 1)

    def test_ranura_vieja_fuera_de_tolerancia_no_se_recupera(self, _):
        with _con_tareas(TAREA_X), patch.object(svc, 'call_command') as cc:
            res = svc.correr_vuelta(mx(2026, 10, 20, 15, 0))
        self.assertEqual(res[0].accion, 'fuera_de_tolerancia')
        cc.assert_not_called()
        self.assertFalse(EjecucionCron.objects.exists())

    def test_una_tarea_que_falla_queda_en_error_abre_pendiente_y_las_demas_corren(self, _):
        def llamar(nombre, *a, **kw):
            if nombre == 'comando_x':
                raise RuntimeError('boom token=abc123secreto')
            kw['stdout'].write('hecho')
        with _con_tareas(TAREA_X, TAREA_Y), patch.object(svc, 'call_command', side_effect=llamar):
            res = svc.correr_vuelta(self.ahora)
        self.assertEqual([r.accion for r in res], ['error', 'corrio'])
        x = EjecucionCron.objects.get(tarea='tarea_x')
        self.assertEqual(x.estado, 'ERROR')
        self.assertIn('RuntimeError', x.error)
        self.assertNotIn('abc123secreto', x.error)
        self.assertEqual(EjecucionCron.objects.get(tarea='tarea_y').estado, 'EXITO')
        p = Pendiente.objects.get()
        self.assertEqual((p.tipo, p.severidad, p.rol), ('CRON', 'URGENTE', 'DIRECCION'))
        self.assertEqual(p.clave_idempotencia, 'cron:tarea_x:20261020T1000')

    def test_error_no_duplica_el_pendiente_en_vueltas_siguientes(self, _):
        with _con_tareas(TAREA_X), patch.object(svc, 'call_command', side_effect=RuntimeError('x')):
            svc.correr_vuelta(self.ahora)
            svc.correr_vuelta(self.ahora + 5 * MIN)
            svc.correr_vuelta(self.ahora + 10 * MIN)
        self.assertEqual(Pendiente.objects.count(), 1)

    def test_tarea_que_no_corrio_dentro_de_su_tolerancia_abre_pendiente_sin_duplicar(self, _):
        # El programador ya estaba en uso (hay una corrida previa de otra tarea)
        # pero tarea_x no corrió su ranura de las 10:00.
        EjecucionCron.objects.create(tarea='otra', programada_para=mx(2026, 10, 19, 10, 0),
                                     inicio=mx(2026, 10, 19, 10, 0))
        ahora = mx(2026, 10, 20, 12, 30)
        with _con_tareas(TAREA_X):
            self.assertEqual(svc.vigilar(ahora), 1)
            self.assertEqual(svc.vigilar(ahora + 5 * MIN), 1)
        self.assertEqual(Pendiente.objects.filter(tipo='CRON').count(), 1)

    def test_sin_historial_el_vigilante_no_alerta_ranuras_previas_al_programador(self, _):
        with _con_tareas(TAREA_X):
            self.assertEqual(svc.vigilar(mx(2026, 10, 20, 15, 0)), 0)
        EjecucionCron.objects.create(tarea='otra', programada_para=mx(2026, 10, 20, 14, 0),
                                     inicio=mx(2026, 10, 20, 14, 0))
        with _con_tareas(TAREA_X):
            self.assertEqual(svc.vigilar(mx(2026, 10, 20, 15, 0)), 0)  # ranura 10:00 < primera corrida
        self.assertFalse(Pendiente.objects.exists())

    def test_tarea_en_curso_pasada_su_tolerancia_se_vigila(self, _):
        EjecucionCron.objects.create(tarea='tarea_x', programada_para=mx(2026, 10, 20, 10, 0),
                                     inicio=mx(2026, 10, 20, 10, 0))
        with _con_tareas(TAREA_X):
            self.assertEqual(svc.vigilar(mx(2026, 10, 20, 10, 30)), 0)
            self.assertEqual(svc.vigilar(mx(2026, 10, 20, 12, 30)), 1)

    def test_salida_truncada_y_sin_secretos(self, _):
        def llamar(nombre, *a, **kw):
            kw['stdout'].write('password=hunter2 ' + 'x' * 9000 + ' fin')
        with _con_tareas(TAREA_X), patch.object(svc, 'call_command', side_effect=llamar):
            svc.correr_vuelta(self.ahora)
        e = EjecucionCron.objects.get()
        self.assertLessEqual(len(e.salida), svc.MAX_SALIDA)
        self.assertTrue(e.salida.endswith('fin'))
        self.assertNotIn('hunter2', svc.sin_secretos('password=hunter2 y Bearer abc.def.ghi'))
        self.assertNotIn('abc.def.ghi', svc.sin_secretos('Authorization: Bearer abc.def.ghi'))

    def test_simular_no_ejecuta_ni_registra(self, _):
        with _con_tareas(TAREA_X), patch.object(svc, 'call_command') as cc:
            res = svc.correr_vuelta(self.ahora, simular=True)
        self.assertEqual(res[0].accion, 'simulada')
        cc.assert_not_called()
        self.assertFalse(EjecucionCron.objects.exists())
        self.assertFalse(Pendiente.objects.exists())

    def test_solo_limita_a_una_tarea_y_rechaza_desconocidas(self, _):
        with _con_tareas(TAREA_X, TAREA_Y), patch.object(svc, 'call_command'):
            res = svc.correr_vuelta(self.ahora, solo='tarea_y')
        self.assertEqual([r.tarea for r in res], ['tarea_y'])
        with _con_tareas(TAREA_X), self.assertRaises(ValueError):
            svc.correr_vuelta(self.ahora, solo='nada')


class ComandoProgramadorTest(TestCase):
    def test_simular_lista_lo_que_correria_sin_ejecutar(self):
        salida = StringIO()
        with patch.object(svc, 'call_command') as cc:
            call_command('programador', '--simular', stdout=salida)
        cc.assert_not_called()
        self.assertFalse(EjecucionCron.objects.exists())
        texto = salida.getvalue()
        self.assertIn('procesar_tareas_operativas', texto)  # cada 15 min: siempre dentro de tolerancia

    def test_tarea_desconocida_es_error_de_comando(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('programador', '--solo', 'no_existe', stdout=StringIO())


class ConcurrenciaTest(TransactionTestCase):
    """Dos procesos corriendo a la vez: el UniqueConstraint deja pasar a uno."""

    def test_dos_hilos_corren_la_ranura_una_sola_vez(self):
        if connection.vendor == 'sqlite':
            self.skipTest('SQLite en memoria no comparte la BD entre hilos')
        llamadas = []
        ahora = mx(2026, 10, 20, 10, 20)

        def vuelta():
            try:
                with _con_tareas(TAREA_X), patch.object(svc, 'call_command',
                                                         side_effect=lambda *a, **k: llamadas.append(a)), \
                        patch('comunicacion.services_pendientes._avisar_urgente'):
                    svc.correr_vuelta(ahora)
            finally:
                connection.close()

        hilos = [threading.Thread(target=vuelta) for _ in range(2)]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        self.assertEqual(len(llamadas), 1)


class SaludTest(TestCase):
    def _peticion(self, superusuario=True, ruta='/admin/'):
        peticion = RequestFactory().get(ruta)
        peticion.user = get_user_model().objects.create_user(
            f'u{get_user_model().objects.count()}', 'u@example.com', 'x' * 20, is_superuser=superusuario)
        return peticion

    def test_sin_corridas_no_hay_aviso(self):
        self.assertEqual(context_processors.salud_programador(self._peticion()), {})

    def test_aviso_solo_si_la_ultima_corrida_tiene_mas_de_30_minutos(self):
        EjecucionCron.objects.create(tarea='t', programada_para=timezone.now(),
                                     inicio=timezone.now() - timedelta(minutes=29))
        self.assertEqual(context_processors.salud_programador(self._peticion()), {})
        EjecucionCron.objects.update(inicio=timezone.now() - timedelta(minutes=31))
        self.assertIn('programador_ultima_vuelta', context_processors.salud_programador(self._peticion()))

    def test_el_aviso_no_sale_a_quien_no_es_superusuario_ni_fuera_del_admin(self):
        EjecucionCron.objects.create(tarea='t', programada_para=timezone.now(),
                                     inicio=timezone.now() - timedelta(hours=2))
        self.assertEqual(context_processors.salud_programador(self._peticion(superusuario=False)), {})
        self.assertEqual(context_processors.salud_programador(self._peticion(ruta='/cotizar/')), {})

    def test_el_admin_pinta_el_aviso_con_qkt_aviso(self):
        EjecucionCron.objects.create(tarea='t', programada_para=timezone.now(),
                                     inicio=timezone.now() - timedelta(hours=2))
        usuario = get_user_model().objects.create_superuser('dir', 'd@example.com', 'x' * 20)
        login_superuser_con_totp(self.client, usuario)
        html = self.client.get(reverse('admin:index')).content.decode()
        self.assertIn('qkt-aviso qkt-aviso--error', html)
        self.assertIn('El programador no está corriendo', html)

    def test_resumen_diario_incluye_la_ultima_vuelta(self):
        from comunicacion import services_pendientes as sp
        from comunicacion.models import Responsable
        Responsable.objects.create(rol='DIRECCION', nombre='Dirección', telefono_wa='525555550011')
        Pendiente.objects.create(titulo='Algo', rol='DIRECCION', tipo='ALERTA',
                                 vence_en=timezone.now() + timedelta(hours=5))
        inicio = timezone.now()
        EjecucionCron.objects.create(tarea='t', programada_para=inicio, inicio=inicio)
        textos = []
        with patch.object(sp, '_enviar_aviso', side_effect=lambda tel, texto, **k: textos.append(texto)):
            sp.enviar_resumen_diario()
        self.assertIn(f'Programador: última vuelta {timezone.localtime(inicio):%H:%M}', textos[0])


class AdminEjecucionesTest(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_superuser('dir', 'd@example.com', 'x' * 20)
        login_superuser_con_totp(self.client, usuario)
        self.e = EjecucionCron.objects.create(
            tarea='enviar_guias', programada_para=timezone.now(), inicio=timezone.now(),
            fin=timezone.now() + timedelta(seconds=75), estado='ERROR', error='Boom', salida='línea')

    def test_lista_filtros_y_detalle_de_solo_lectura(self):
        r = self.client.get(reverse('admin:operaciones_ejecucioncron_changelist'))
        self.assertContains(r, 'enviar_guias')
        self.assertContains(r, 'qkt-badge qkt-badge--error')
        self.assertContains(r, '1 min 15 s')
        self.assertContains(r, 'Ejecuciones')
        r = self.client.get(reverse('admin:operaciones_ejecucioncron_change', args=[self.e.pk]))
        self.assertContains(r, 'Boom')
        self.assertNotContains(r, 'name="_save"')

    def test_no_se_agrega_ni_se_borra(self):
        self.assertEqual(self.client.get(reverse('admin:operaciones_ejecucioncron_add')).status_code, 403)
        self.assertEqual(
            self.client.post(reverse('admin:operaciones_ejecucioncron_delete', args=[self.e.pk]),
                             {'post': 'yes'}).status_code, 403)
        self.assertTrue(EjecucionCron.objects.filter(pk=self.e.pk).exists())
