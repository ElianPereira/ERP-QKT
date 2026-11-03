"""
Única puerta de lectura del entorno (Issue #424).

Regla de nombres: ``ORIGEN_SERVICIO_DATO[_DISTINTIVO]`` (ver
``docs/variables_entorno.md``). Todo valor del entorno entra por ``env()``;
nada fuera de este archivo llama a ``decouple.config`` ni toca ``os.environ``
(lo vigila ``core_erp/test_variables.py``).

Los nombres anteriores al corte ya no se leen: el respaldo ``legado=`` se retiró
el 2026-10-24.
"""
from decouple import config


def env(nombre, **kwargs):
    """Lee ``nombre`` con ``default``/``cast`` como ``decouple.config``; sin
    ``default`` y sin la variable, lanza ``UndefinedValueError``."""
    return config(nombre, **kwargs)
