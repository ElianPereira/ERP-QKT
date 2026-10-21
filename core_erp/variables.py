"""
Única puerta de lectura del entorno (Issue #424).

Regla de nombres: ``ORIGEN_SERVICIO_DATO[_DISTINTIVO]`` (ver
``docs/variables_entorno.md``). Todo valor del entorno entra por ``env()``;
nada fuera de este archivo llama a ``decouple.config`` ni toca ``os.environ``
(lo vigila ``core_erp/test_variables.py``).

``legado=`` es el respaldo del corte de nombres: si la variable nueva no está
definida y la vieja sí, se usa la vieja y se deja un aviso en el log (solo el
nombre, jamás el valor). Se quita 2 semanas después del corte en Railway.
"""
import logging

from decouple import UndefinedValueError, config

logger = logging.getLogger(__name__)


def env(nombre, legado=None, **kwargs):
    """Lee ``nombre``; si no existe y hay ``legado``, lee el nombre viejo; si
    tampoco, aplica ``default`` (con ``cast``, como ``decouple.config``).
    Sin ``default`` y sin ninguna de las dos, lanza ``UndefinedValueError``."""
    cast = kwargs.get('cast')
    opciones = {'cast': cast} if cast is not None else {}
    try:
        return config(nombre, **opciones)
    except UndefinedValueError:
        pass
    if legado:
        try:
            valor = config(legado, **opciones)
        except UndefinedValueError:
            pass
        else:
            logger.warning('Variable de entorno: usando nombre legado %s (nuevo: %s)', legado, nombre)
            return valor
    if 'default' not in kwargs:
        raise UndefinedValueError(f'{nombre} not found. Declare it as envvar or define a default value.')
    return config(nombre, **kwargs)
