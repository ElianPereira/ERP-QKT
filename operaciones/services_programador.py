"""
Lógica del programador único (Issue #423). El comando `programador` y el aviso
de salud del admin son capas delgadas sobre estas funciones.

Una vuelta, por cada tarea de `core_erp.programador.TAREAS`:
1. calcula su última ranura vencida;
2. si cae dentro de la tolerancia y nadie la tomó, la reserva con un
   `EjecucionCron` EN_CURSO (el `UniqueConstraint` decide quién gana si dos
   procesos corren a la vez) y la corre capturando su salida;
3. al final de la vuelta, el vigilante abre un pendiente URGENTE por cada
   tarea cuya ranura más reciente falló o no corrió a tiempo.
"""
import io
import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Min
from django.utils import timezone

from core_erp.programador import TAREAS, ZONA
from operaciones.models import EjecucionCron

logger = logging.getLogger(__name__)

MAX_SALIDA = 5000
MAX_ERROR = 2000
# Sin vuelta registrada en tanto tiempo, el programador mismo está caído: la
# tarea más frecuente corre cada 15 min, así que 30 min son dos vueltas perdidas.
UMBRAL_SALUD = timedelta(minutes=30)

_BEARER = re.compile(r'(?i)(bearer\s+)[^\s"\',;]+')
_SECRETO = re.compile(
    r'(?i)((?:token|secret|password|passwd|api[_-]?key|authorization)\w*[\s"\']*[=:]\s*)["\']?[^\s"\',;]+')


def sin_secretos(texto: str, limite: int = MAX_SALIDA) -> str:
    """Enmascara valores que parecen credenciales y trunca (conserva el final,
    que es donde sale el resumen o el error)."""
    texto = _SECRETO.sub(lambda m: m.group(1) + '***', _BEARER.sub(r'\1***', texto or ''))
    return texto if len(texto) <= limite else '…' + texto[-(limite - 1):]


@dataclass
class Resultado:
    tarea: str
    ranura: object
    accion: str  # 'corrio' | 'error' | 'tomada' | 'fuera_de_tolerancia' | 'simulada' | 'ya_corrida'


def _tareas(solo: Optional[str]):
    if not solo:
        return TAREAS
    elegidas = [t for t in TAREAS if t.nombre == solo]
    if not elegidas:
        raise ValueError(f'Tarea desconocida: {solo}')
    return elegidas


def _correr(tarea, ejecucion) -> str:
    t0 = time.monotonic()
    salida = io.StringIO()
    estado, error = 'EXITO', ''
    try:
        call_command(tarea.comando, *tarea.args, stdout=salida, stderr=salida)
    except (Exception, SystemExit) as exc:  # una tarea nunca detiene a las demás
        logger.exception('Programador: falló «%s»', tarea.nombre)
        estado, error = 'ERROR', sin_secretos(f'{type(exc).__name__}: {exc}', MAX_ERROR)
    ejecucion.estado = estado
    ejecucion.fin = ejecucion.inicio + timedelta(seconds=time.monotonic() - t0)
    ejecucion.salida = sin_secretos(salida.getvalue())
    ejecucion.error = error
    ejecucion.save(update_fields=['estado', 'fin', 'salida', 'error'])
    return estado


def correr_vuelta(ahora=None, solo: Optional[str] = None, simular: bool = False):
    """Una vuelta del programador. Devuelve la lista de `Resultado`."""
    ahora = ahora or timezone.now()
    resultados = []
    for tarea in _tareas(solo):
        try:
            ranura = tarea.horario.ultima_ranura(ahora)
            if ahora - ranura > tarea.tolerancia:
                resultados.append(Resultado(tarea.nombre, ranura, 'fuera_de_tolerancia'))
                continue
            if simular:
                ya = EjecucionCron.objects.filter(tarea=tarea.nombre, programada_para=ranura).exists()
                resultados.append(Resultado(tarea.nombre, ranura, 'ya_corrida' if ya else 'simulada'))
                continue
            try:
                with transaction.atomic():
                    ejecucion = EjecucionCron.objects.create(
                        tarea=tarea.nombre, programada_para=ranura, inicio=ahora)
            except IntegrityError:
                resultados.append(Resultado(tarea.nombre, ranura, 'tomada'))
                continue
            estado = _correr(tarea, ejecucion)
            resultados.append(Resultado(tarea.nombre, ranura, 'corrio' if estado == 'EXITO' else 'error'))
        except Exception:
            logger.exception('Programador: no se pudo procesar «%s»', tarea.nombre)
    if not simular:
        vigilar(ahora, solo)
    return resultados


def vigilar(ahora=None, solo: Optional[str] = None) -> int:
    """Pendiente URGENTE para Dirección por cada tarea cuya ranura más reciente
    terminó en ERROR, o sigue sin EXITO pasada su tolerancia (no corrió o se
    quedó EN_CURSO). Idempotente por tarea y ranura. Devuelve cuántas revisó
    con problema. Las ranuras anteriores a la primera corrida del programador no
    cuentan: antes de existir no podía correrlas."""
    from comunicacion.services_pendientes import registrar_pendiente

    ahora = ahora or timezone.now()
    primera = EjecucionCron.objects.aggregate(m=Min('inicio'))['m']
    if primera is None:
        return 0
    con_problema = 0
    for tarea in _tareas(solo):
        try:
            ranura = tarea.horario.ultima_ranura(ahora)
            ejecucion = EjecucionCron.objects.filter(tarea=tarea.nombre, programada_para=ranura).first()
            if ejecucion is None and ranura < primera:
                continue
            if ejecucion is not None and ejecucion.estado == 'EXITO':
                continue
            if ejecucion is not None and ejecucion.estado == 'ERROR':
                motivo = f'falló. {ejecucion.error}'.strip()
            elif ahora - ranura > tarea.tolerancia:
                motivo = ('no corrió dentro de su tolerancia' if ejecucion is None
                          else 'sigue en curso pasada su tolerancia')
            else:
                continue
            con_problema += 1
            local = ranura.astimezone(ZONA)
            registrar_pendiente(
                titulo=f'Tarea programada «{tarea.nombre}» {motivo.split(".")[0]} ({local:%d/%m %H:%M})',
                rol='DIRECCION', tipo='CRON', severidad='URGENTE', detalle=motivo[:500],
                clave_idempotencia=f'cron:{tarea.nombre}:{local:%Y%m%dT%H%M}')
        except Exception:
            logger.exception('Programador: el vigilante no pudo revisar «%s»', tarea.nombre)
    return con_problema


def ultima_vuelta():
    """Inicio de la ejecución más reciente, o None si el programador nunca corrió."""
    return EjecucionCron.objects.order_by('-inicio').values_list('inicio', flat=True).first()


def salud_programador(ahora=None):
    """None si todo está bien (o el programador aún no entra en uso); si no,
    la última vuelta (datetime) que quedó vieja."""
    ahora = ahora or timezone.now()
    ultima = ultima_vuelta()
    if ultima is not None and ahora - ultima > UMBRAL_SALUD:
        return ultima
    return None


def texto_ultima_vuelta() -> str:
    ultima = ultima_vuelta()
    if ultima is None:
        return 'Programador: sin vueltas registradas'
    return f'Programador: última vuelta {timezone.localtime(ultima):%H:%M}'
