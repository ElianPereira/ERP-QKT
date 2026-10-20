"""
Constantes del horario laboral de los colaboradores de mantenimiento/limpieza
(Issue #257) — fuente única para no repartir estos valores por el módulo.
"""
from datetime import datetime, time, timedelta

# Turno normal de los colaboradores: 8:00 a.m. - 2:00 p.m.
HORA_ENTRADA_NORMAL = time(8, 0)
HORA_SALIDA_NORMAL = time(14, 0)

# El mensaje operativo (checklist) sale esta cantidad de horas antes de la
# hora de entrada que le toque ese día al colaborador (normal o adelantada).
HORAS_ANTES_ENVIO_OPERATIVO = 2

def calcular_horario_turnover(hora_limite: time, duracion_estimada_horas) -> tuple:
    """
    Deriva (hora_entrada, requiere_tiempo_extra) para una tarea de turnover.

    La entrada es la normal (8:00 a.m.) salvo que la hora límite, menos la
    duración estimada de la preparación, caiga antes — en ese caso el
    colaborador tiene que entrar más temprano. `requiere_tiempo_extra` marca
    cualquiera de las dos formas de salirse del turno normal: entrar antes de
    las 8:00 a.m. o tener que terminar después de las 2:00 p.m.

    Aritmética de horas del día (sin cruzar medianoche): suficiente para este
    negocio, donde ningún servicio empieza de madrugada.
    """
    base = datetime(2000, 1, 1)
    dt_limite = datetime.combine(base, hora_limite)
    dt_entrada_deseada = dt_limite - timedelta(hours=float(duracion_estimada_horas))
    hora_entrada_deseada = dt_entrada_deseada.time()

    if hora_entrada_deseada < HORA_ENTRADA_NORMAL:
        hora_entrada = hora_entrada_deseada
    else:
        hora_entrada = HORA_ENTRADA_NORMAL

    requiere_tiempo_extra = hora_entrada < HORA_ENTRADA_NORMAL or hora_limite > HORA_SALIDA_NORMAL
    return hora_entrada, requiere_tiempo_extra
