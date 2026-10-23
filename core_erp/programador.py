"""
Programador único (Issue #423): la tabla de tareas y el cálculo de sus ranuras.

Un solo cron de Railway (cada 5 min) corre `manage.py programador`, que lee
`TAREAS`, calcula la última ranura vencida de cada una y la corre una sola vez
(ver `operaciones/management/commands/programador.py`). Todo horario está en
America/Merida y una ranura es siempre un `datetime` con zona.

Los comandos con `--aplicar` lo conservan aquí: sin él solo simulan.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Tuple
from zoneinfo import ZoneInfo

ZONA = ZoneInfo('America/Merida')

LUNES, MARTES, MIERCOLES, JUEVES, VIERNES, SABADO, DOMINGO = range(7)


def _hora(texto: str) -> time:
    hh, mm = texto.split(':')
    return time(int(hh), int(mm))


def _local(ahora: datetime) -> datetime:
    return ahora.astimezone(ZONA)


def _a_ranura(dia, hora: time) -> datetime:
    return datetime.combine(dia, hora, tzinfo=ZONA)


@dataclass(frozen=True)
class cada:
    """Cada N minutos contados desde la medianoche local (N divide a 1,440)."""
    minutos: int

    def ultima_ranura(self, ahora: datetime) -> datetime:
        local = _local(ahora)
        medianoche = local.replace(hour=0, minute=0, second=0, microsecond=0)
        transcurridos = int((local - medianoche).total_seconds() // 60)
        return medianoche + timedelta(minutes=transcurridos - transcurridos % self.minutos)

    def __str__(self):
        return f'cada {self.minutos} min'


@dataclass(frozen=True)
class diario:
    hora: str

    def ultima_ranura(self, ahora: datetime) -> datetime:
        local = _local(ahora)
        ranura = _a_ranura(local.date(), _hora(self.hora))
        return ranura if ranura <= local else _a_ranura(local.date() - timedelta(days=1), _hora(self.hora))

    def __str__(self):
        return f'diario {self.hora}'


@dataclass(frozen=True)
class semanal:
    dia: int  # 0 = lunes … 6 = domingo
    hora: str

    def ultima_ranura(self, ahora: datetime) -> datetime:
        local = _local(ahora)
        atras = (local.weekday() - self.dia) % 7
        ranura = _a_ranura(local.date() - timedelta(days=atras), _hora(self.hora))
        return ranura if ranura <= local else ranura - timedelta(days=7)

    def __str__(self):
        return f'semanal {("lun", "mar", "mié", "jue", "vie", "sáb", "dom")[self.dia]} {self.hora}'


@dataclass(frozen=True)
class mensual:
    dia: int  # día del mes; si el mes es más corto, el último día
    hora: str

    def _en(self, anio: int, mes: int) -> datetime:
        return _a_ranura(datetime(anio, mes, min(self.dia, monthrange(anio, mes)[1])).date(), _hora(self.hora))

    def ultima_ranura(self, ahora: datetime) -> datetime:
        local = _local(ahora)
        ranura = self._en(local.year, local.month)
        if ranura <= local:
            return ranura
        anio, mes = (local.year - 1, 12) if local.month == 1 else (local.year, local.month - 1)
        return self._en(anio, mes)

    def __str__(self):
        return f'mensual día {self.dia} {self.hora}'


@dataclass(frozen=True)
class Tarea:
    nombre: str
    comando: str
    horario: object
    tolerancia: timedelta
    args: Tuple[str, ...] = ()


MIN = timedelta(minutes=1)
HORA = timedelta(hours=1)

# Horarios en hora de Mérida. La tolerancia es cuánto después de su ranura se
# sigue corriendo una tarea atrasada y cuánto tarda el vigilante en avisar si
# no corrió; las diarias dan margen de horas, las mensuales de un día.
TAREAS = (
    Tarea('cerrar_cotizaciones', 'cerrar_cotizaciones', diario('00:10'), 6 * HORA),
    Tarea('enviar_recordatorios', 'enviar_recordatorios', diario('18:00'), 3 * HORA),
    Tarea('enviar_guias', 'enviar_guias', diario('09:00'), 6 * HORA),
    Tarea('enviar_recordatorios_contador', 'enviar_recordatorios_contador', diario('18:00'), 6 * HORA),
    Tarea('procesar_tareas_operativas', 'procesar_tareas_operativas', cada(15), 45 * MIN),
    Tarea('avisar_vencimientos', 'avisar_vencimientos', diario('07:05'), 6 * HORA, ('--aplicar',)),
    Tarea('sincronizar_sat', 'sincronizar_sat', cada(60), 3 * HORA),
    Tarea('sincronizar_comisiones_openpay', 'sincronizar_comisiones_openpay', mensual(2, '08:45'), 24 * HORA,
          ('--aplicar',)),
    Tarea('purgar_conversaciones_whatsapp', 'purgar_conversaciones_whatsapp', diario('03:00'), 12 * HORA,
          ('--aplicar',)),
    Tarea('enviar_seguimientos', 'enviar_seguimientos', diario('10:52'), 6 * HORA),
    Tarea('resumir_preguntas_agente', 'resumir_preguntas_agente', semanal(LUNES, '08:52'), 24 * HORA,
          ('--aplicar',)),
    Tarea('enviar_recordatorios_visitas', 'enviar_recordatorios_visitas', diario('18:00'), 3 * HORA),
    Tarea('revisar_pendientes', 'revisar_pendientes', cada(15), 45 * MIN),
    Tarea('enviar_resumen_diario', 'enviar_resumen_diario', diario('08:00'), 6 * HORA),
    Tarea('enviar_resumen_semanal', 'enviar_resumen_semanal', semanal(LUNES, '08:30'), 24 * HORA),
    Tarea('revisar_desviacion', 'revisar_desviacion', diario('09:30'), 6 * HORA),
    Tarea('enviar_nomina_semanal', 'enviar_nomina_semanal', semanal(VIERNES, '12:00'), 24 * HORA),
    Tarea('preguntar_cfdi_contador', 'preguntar_cfdi_contador', diario('10:00'), 6 * HORA),
)

POR_NOMBRE = {t.nombre: t for t in TAREAS}
