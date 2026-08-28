"""Tests del módulo de operaciones (Issue #257)."""
from datetime import date, time
from unittest.mock import patch

from django.test import TestCase

from comercial.models import Cliente, Cotizacion
from comunicacion.tests.utils import TEL_NEGOCIO, RespuestaFalsa, wa_settings
from nomina.models import Empleado

from .constantes import HORA_ENTRADA_NORMAL, calcular_horario_turnover
from .models import ItemChecklist, PlantillaChecklist, TareaProgramada
from .services import (
    construir_bloques_checklist,
    construir_texto_aviso_horario,
    construir_texto_resumen_propietario,
    enviar_aviso_horario,
    enviar_mensaje_operativo,
    enviar_resumen_propietario,
    generar_tarea_turnover,
    generar_tareas_mantenimiento,
    procesar_pendientes,
)

TEL_COLABORADOR = '525555550009'


class CalcularHorarioTurnoverTest(TestCase):
    def test_hora_limite_dentro_del_turno_no_requiere_ajuste(self):
        entrada, extra = calcular_horario_turnover(time(13, 0), 2.0)
        self.assertEqual(entrada, HORA_ENTRADA_NORMAL)
        self.assertFalse(extra)

    def test_hora_limite_temprana_adelanta_la_entrada(self):
        # Hospedaje entra a las 14:00; si el límite fuera 9:00, con 2h de
        # preparación tocaría entrar a las 7:00, antes de la normal.
        entrada, extra = calcular_horario_turnover(time(9, 0), 2.0)
        self.assertEqual(entrada, time(7, 0))
        self.assertTrue(extra)

    def test_hora_limite_despues_del_turno_marca_tiempo_extra_sin_adelantar_entrada(self):
        entrada, extra = calcular_horario_turnover(time(15, 0), 2.0)
        self.assertEqual(entrada, HORA_ENTRADA_NORMAL)
        self.assertTrue(extra)


class GenerarTareaTurnoverTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Cliente Test')
        self.empleado = Empleado.objects.create(nombre='Juan', telefono=TEL_COLABORADOR)
        self.plantilla = PlantillaChecklist.objects.create(
            nombre='Preparación — Hospedaje', tipo='TURNOVER_HOSPEDAJE',
            responsable_default=self.empleado, duracion_estimada_horas=2,
        )
        ItemChecklist.objects.create(plantilla=self.plantilla, orden=1, texto='Tender habitaciones')

    def _cotizacion(self, **extra):
        datos = dict(
            cliente=self.cliente, nombre_evento='H', tipo_servicio='HOSPEDAJE',
            fecha_evento=date(2026, 9, 10), fecha_salida=date(2026, 9, 13),
            hora_inicio=time(14, 0), estado='CONFIRMADA',
        )
        datos.update(extra)
        return Cotizacion.objects.create(**datos)

    def test_genera_tarea_con_responsable_y_horario_heredados_de_la_plantilla(self):
        cot = self._cotizacion()
        tarea = generar_tarea_turnover(cot)
        self.assertIsNotNone(tarea)
        self.assertEqual(tarea.responsable, self.empleado)
        self.assertEqual(tarea.hora_limite, time(14, 0))
        self.assertEqual(tarea.fecha, cot.fecha_evento)

    def test_es_idempotente(self):
        cot = self._cotizacion()
        generar_tarea_turnover(cot)
        generar_tarea_turnover(cot)
        self.assertEqual(TareaProgramada.objects.filter(cotizacion=cot).count(), 1)

    def test_sin_plantilla_activa_no_genera_nada(self):
        self.plantilla.activa = False
        self.plantilla.save()
        cot = self._cotizacion()
        self.assertIsNone(generar_tarea_turnover(cot))
        self.assertFalse(TareaProgramada.objects.filter(cotizacion=cot).exists())

    def test_sin_hora_inicio_no_genera_nada(self):
        cot = self._cotizacion(hora_inicio=None)
        self.assertIsNone(generar_tarea_turnover(cot))

    def test_arrendamiento_sin_plantilla_por_tipo_no_genera_nada(self):
        cot = self._cotizacion(tipo_servicio='ARRENDAMIENTO', hora_inicio=time(10, 0))
        self.assertIsNone(generar_tarea_turnover(cot))


class GenerarTareasMantenimientoTest(TestCase):
    def setUp(self):
        self.empleado = Empleado.objects.create(nombre='María', telefono=TEL_COLABORADOR)

    def test_cadencia_diaria_genera_todos_los_dias(self):
        PlantillaChecklist.objects.create(
            nombre='Revisión diaria', tipo='MANTENIMIENTO_RECURRENTE',
            cadencia='DIARIA', responsable_default=self.empleado, activa=True,
        )
        self.assertEqual(generar_tareas_mantenimiento(date(2026, 9, 10)), 1)
        self.assertEqual(generar_tareas_mantenimiento(date(2026, 9, 11)), 1)

    def test_cadencia_semanal_solo_el_dia_configurado(self):
        # 2026-09-10 es jueves (weekday()==3).
        PlantillaChecklist.objects.create(
            nombre='Revisión semanal', tipo='MANTENIMIENTO_RECURRENTE',
            cadencia='SEMANAL', dia_semana=3, responsable_default=self.empleado, activa=True,
        )
        self.assertEqual(generar_tareas_mantenimiento(date(2026, 9, 10)), 1)
        self.assertEqual(generar_tareas_mantenimiento(date(2026, 9, 11)), 0)

    def test_es_idempotente(self):
        plantilla = PlantillaChecklist.objects.create(
            nombre='Revisión diaria', tipo='MANTENIMIENTO_RECURRENTE',
            cadencia='DIARIA', responsable_default=self.empleado, activa=True,
        )
        generar_tareas_mantenimiento(date(2026, 9, 10))
        generar_tareas_mantenimiento(date(2026, 9, 10))
        self.assertEqual(TareaProgramada.objects.filter(plantilla=plantilla).count(), 1)

    def test_plantilla_inactiva_no_genera(self):
        PlantillaChecklist.objects.create(
            nombre='Revisión diaria', tipo='MANTENIMIENTO_RECURRENTE',
            cadencia='DIARIA', responsable_default=self.empleado, activa=False,
        )
        self.assertEqual(generar_tareas_mantenimiento(date(2026, 9, 10)), 0)


class ConstruirMensajesTest(TestCase):
    def setUp(self):
        self.empleado = Empleado.objects.create(nombre='Juan', telefono=TEL_COLABORADOR)
        self.plantilla = PlantillaChecklist.objects.create(
            nombre='Preparación', tipo='TURNOVER_EVENTO', encabezado='Preparación — Evento',
        )
        self.tarea = TareaProgramada.objects.create(
            plantilla=self.plantilla, responsable=self.empleado,
            fecha=date(2026, 8, 27), hora_entrada=time(6, 0), hora_limite=time(13, 0),
            requiere_tiempo_extra=True,
        )

    def test_un_solo_bloque_si_hay_pocas_tareas(self):
        for i in range(1, 4):
            ItemChecklist.objects.create(plantilla=self.plantilla, orden=i, texto=f"Tarea {i}")
        bloques = construir_bloques_checklist(self.tarea)
        self.assertEqual(len(bloques), 1)
        self.assertIn('Jueves 27 de agosto', bloques[0])
        self.assertIn('1:00 p.m.', bloques[0])
        self.assertIn('tiempo extra', bloques[0])
        self.assertIn('1. Tarea 1', bloques[0])

    def test_mas_de_ocho_tareas_se_parte_en_varios_mensajes(self):
        for i in range(1, 11):
            ItemChecklist.objects.create(plantilla=self.plantilla, orden=i, texto=f"Tarea {i}")
        bloques = construir_bloques_checklist(self.tarea)
        self.assertEqual(len(bloques), 2)
        self.assertIn('(1/2)', bloques[0])
        self.assertIn('(2/2)', bloques[1])
        # La hora límite solo va en el primer bloque.
        self.assertIn('Termina antes de', bloques[0])
        self.assertNotIn('Termina antes de', bloques[1])

    def test_aviso_horario_incluye_motivo_y_hora(self):
        texto = construir_texto_aviso_horario(self.tarea)
        self.assertIn('6:00 a.m.', texto)
        self.assertIn('Preparación — Evento', texto)

    def test_resumen_propietario_agrupa_por_persona(self):
        ItemChecklist.objects.create(plantilla=self.plantilla, orden=1, texto='Tarea 1')
        texto = construir_texto_resumen_propietario([self.tarea])
        self.assertIn('👤 Juan', texto)
        self.assertIn('1:00 p.m.', texto)


@wa_settings()
class EnviarMensajesTest(TestCase):
    def setUp(self):
        patcher = patch('comunicacion.services.numero_emisor_wa', return_value='')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.empleado = Empleado.objects.create(nombre='Juan', telefono=TEL_COLABORADOR)
        self.plantilla = PlantillaChecklist.objects.create(
            nombre='Preparación', tipo='TURNOVER_EVENTO',
        )
        ItemChecklist.objects.create(plantilla=self.plantilla, orden=1, texto='Tarea 1')
        self.tarea = TareaProgramada.objects.create(
            plantilla=self.plantilla, responsable=self.empleado,
            fecha=date(2026, 8, 27), hora_entrada=time(6, 0), hora_limite=time(13, 0),
            requiere_tiempo_extra=True,
        )

    def test_envio_exitoso_marca_enviado(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()):
            estado = enviar_mensaje_operativo(self.tarea)
        self.tarea.refresh_from_db()
        self.assertEqual(estado, 'ENVIADO')
        self.assertEqual(self.tarea.estado_operativo, 'ENVIADO')

    def test_reintento_tras_fallo_no_duplica_una_vez_enviado(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa(status_code=500)):
            enviar_mensaje_operativo(self.tarea)
        self.tarea.refresh_from_db()
        self.assertEqual(self.tarea.estado_operativo, 'FALLIDO')

        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()):
            estado = enviar_mensaje_operativo(self.tarea)
        self.assertEqual(estado, 'ENVIADO')
        # Reintentar una tarea ya ENVIADA no vuelve a llamar a la API.
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post_otra_vez:
            enviar_mensaje_operativo(self.tarea)
        post_otra_vez.assert_not_called()

    def test_sin_telefono_de_responsable_marca_fallido_sin_llamar_a_meta(self):
        self.tarea.responsable = None
        self.tarea.save()
        with patch('comunicacion.services.requests.post') as post:
            estado = enviar_mensaje_operativo(self.tarea)
        post.assert_not_called()
        self.assertEqual(estado, 'FALLIDO')

    def test_aviso_horario_no_aplica_si_no_requiere_tiempo_extra(self):
        self.tarea.requiere_tiempo_extra = False
        self.tarea.save()
        self.assertEqual(enviar_aviso_horario(self.tarea), 'NO_APLICA')

    def test_resumen_propietario_va_al_numero_del_negocio(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post:
            estado = enviar_resumen_propietario(self.tarea.fecha)
        self.assertEqual(estado, 'ENVIADO')
        self.assertEqual(post.call_args.kwargs['json']['to'], TEL_NEGOCIO)


class ProcesarPendientesTest(TestCase):
    def setUp(self):
        patcher = patch('comunicacion.services.numero_emisor_wa', return_value='')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.empleado = Empleado.objects.create(nombre='Juan', telefono=TEL_COLABORADOR)
        self.plantilla = PlantillaChecklist.objects.create(nombre='Preparación', tipo='TURNOVER_EVENTO')
        ItemChecklist.objects.create(plantilla=self.plantilla, orden=1, texto='Tarea 1')
        self.tarea = TareaProgramada.objects.create(
            plantilla=self.plantilla, responsable=self.empleado,
            fecha=date(2026, 8, 27), hora_entrada=time(8, 0), hora_limite=time(13, 0),
        )

    @wa_settings()
    def test_no_envia_el_checklist_antes_de_su_horario(self):
        # A las 4:00 a.m. el resumen al propietario (corte 8:00 p.m. del día
        # anterior) ya está vencido y sí se manda; lo que NO debe salir
        # todavía es el checklist operativo (entrada 8:00 a.m. - 2h = 6:00).
        ahora = timezone_naive(date(2026, 8, 27), time(4, 0))
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()):
            procesar_pendientes(ahora)
        self.tarea.refresh_from_db()
        self.assertEqual(self.tarea.estado_operativo, 'PENDIENTE')

    @wa_settings()
    def test_envia_el_checklist_dos_horas_antes_de_la_entrada(self):
        ahora = timezone_naive(date(2026, 8, 27), time(6, 0))
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()):
            contadores = procesar_pendientes(ahora)
        self.tarea.refresh_from_db()
        self.assertEqual(self.tarea.estado_operativo, 'ENVIADO')
        self.assertEqual(contadores['operativo'], 1)


def timezone_naive(fecha, hora):
    from datetime import datetime
    return datetime.combine(fecha, hora)


@wa_settings()
class SignalConfirmacionCotizacionTest(TestCase):
    """Confirmar una Cotizacion genera sola la tarea de preparación — sin
    tocar el admin de operaciones (ver Issue #257 § generación automática)."""

    def setUp(self):
        patcher = patch('comunicacion.services.numero_emisor_wa', return_value='')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cliente = Cliente.objects.create(nombre='Cliente Test')
        self.empleado = Empleado.objects.create(nombre='Juan', telefono=TEL_COLABORADOR)
        self.plantilla = PlantillaChecklist.objects.create(
            nombre='Preparación — Hospedaje', tipo='TURNOVER_HOSPEDAJE',
            responsable_default=self.empleado, duracion_estimada_horas=2,
        )
        ItemChecklist.objects.create(plantilla=self.plantilla, orden=1, texto='Tender habitaciones')

    def test_confirmar_genera_la_tarea_de_preparacion(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()):
            with self.captureOnCommitCallbacks(execute=True):
                Cotizacion.objects.create(
                    cliente=self.cliente, nombre_evento='H', tipo_servicio='HOSPEDAJE',
                    fecha_evento=date(2026, 9, 10), fecha_salida=date(2026, 9, 13),
                    hora_inicio=time(14, 0), estado='CONFIRMADA',
                )
        tarea = TareaProgramada.objects.get(plantilla=self.plantilla)
        self.assertEqual(tarea.responsable, self.empleado)

    def test_volver_a_guardar_confirmada_no_duplica(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()):
            with self.captureOnCommitCallbacks(execute=True):
                cot = Cotizacion.objects.create(
                    cliente=self.cliente, nombre_evento='H', tipo_servicio='HOSPEDAJE',
                    fecha_evento=date(2026, 9, 10), fecha_salida=date(2026, 9, 13),
                    hora_inicio=time(14, 0), estado='CONFIRMADA',
                )
            with self.captureOnCommitCallbacks(execute=True):
                cot.save()
        self.assertEqual(TareaProgramada.objects.filter(cotizacion=cot).count(), 1)

    def test_borrador_no_genera_tarea(self):
        with self.captureOnCommitCallbacks(execute=True):
            Cotizacion.objects.create(
                cliente=self.cliente, nombre_evento='H', tipo_servicio='HOSPEDAJE',
                fecha_evento=date(2026, 9, 10), fecha_salida=date(2026, 9, 13),
                hora_inicio=time(14, 0), estado='BORRADOR',
            )
        self.assertFalse(TareaProgramada.objects.exists())
