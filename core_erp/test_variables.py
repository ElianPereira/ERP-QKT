"""Variables de entorno (Issue #424): helper `env()`, test guardián de nombres y
arranque con los nombres nuevos (los anteriores ya no se leen).

Los valores de abajo son marcadores de prueba, nunca credenciales.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

from decouple import UndefinedValueError
from django.test import SimpleTestCase

from core_erp.variables import env

BASE_DIR = Path(__file__).resolve().parent.parent
CATALOGO = BASE_DIR / 'docs' / 'variables_entorno.md'
REGLA = re.compile(r'^(DJANGO|META|ANTHROPIC|OPENPAY|CLOUDFLARE|BREVO|JIBBLE|ERP)_[A-Z0-9_]+$')
EXENTAS = {'DATABASE_URL', 'PORT'}  # además de RAILWAY_*
IGNORAR = {'.git', '.venv', 'venv', 'node_modules', 'staticfiles', 'graphify-out', '__pycache__', 'migrations'}


def _exenta(nombre):
    return nombre in EXENTAS or nombre.startswith('RAILWAY_')


def _es_prueba(ruta: Path):
    return ruta.name.startswith('test') or 'tests' in ruta.parts


def _archivos_py():
    for raiz, dirs, archivos in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORAR and not d.startswith('.')]
        for nombre in archivos:
            if nombre.endswith('.py'):
                yield Path(raiz) / nombre


def catalogo():
    """Nombres de la primera columna del catálogo (`| `NOMBRE` | …`)."""
    nombres = set()
    for linea in CATALOGO.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^\| `([A-Z0-9_]+)` \|', linea)
        if m:
            nombres.add(m.group(1))
    return nombres


def llamadas_env(fuente):
    """Nombres de cada `env('NOMBRE', …)` del código."""
    return [nodo.args[0].value for nodo in ast.walk(ast.parse(fuente))
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id == 'env'
            and nodo.args and isinstance(nodo.args[0], ast.Constant)]


def lecturas_prohibidas(fuente):
    """Lecturas directas del entorno (decouple.config u os.environ/os.getenv) salvo
    DJANGO_SETTINGS_MODULE."""
    malas = []
    arbol = ast.parse(fuente)
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module == 'decouple' and any(a.name == 'config' for a in nodo.names):
            malas.append('from decouple import config')
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id == 'config':
            malas.append('config(')
        es_environ = (isinstance(nodo, ast.Attribute) and isinstance(nodo.value, ast.Name)
                      and nodo.value.id == 'os' and nodo.attr in ('environ', 'getenv', 'putenv'))
        if es_environ:
            malas.append(f'os.{nodo.attr}')
    # Quita los usos legítimos: os.environ.setdefault('DJANGO_SETTINGS_MODULE', …)
    for nodo in ast.walk(arbol):
        if (isinstance(nodo, ast.Call) and nodo.args and isinstance(nodo.args[0], ast.Constant)
                and nodo.args[0].value == 'DJANGO_SETTINGS_MODULE' and 'os.environ' in malas):
            malas.remove('os.environ')
    return malas


def problemas_de_nombres(fuente, catalogado):
    problemas = []
    for nombre in llamadas_env(fuente):
        if _exenta(nombre):
            continue
        if not REGLA.match(nombre):
            problemas.append(f'{nombre}: no cumple ORIGEN_SERVICIO_DATO')
        elif nombre not in catalogado:
            problemas.append(f'{nombre}: falta en docs/variables_entorno.md')
    return problemas


class EnvHelperTest(SimpleTestCase):
    def test_lee_la_variable(self):
        with mock.patch.dict(os.environ, {'ERP_PRUEBA_X': 'nuevo'}):
            self.assertEqual(env('ERP_PRUEBA_X', default=''), 'nuevo')

    def test_sin_variable_aplica_el_default_con_su_cast(self):
        self.assertEqual(env('ERP_PRUEBA_NO_EXISTE', default='7', cast=int), 7)
        self.assertIs(env('ERP_PRUEBA_NO_EXISTE', default=False, cast=bool), False)

    def test_sin_default_y_sin_variable_falla_como_decouple(self):
        with self.assertRaises(UndefinedValueError):
            env('ERP_PRUEBA_NO_EXISTE')

    def test_ya_no_acepta_respaldo_legado(self):
        with self.assertRaises(TypeError):
            env('ERP_PRUEBA_X', legado='PRUEBA_X', default='')


class GuardianTest(SimpleTestCase):
    def test_falla_con_un_nombre_que_no_cumple_la_regla(self):
        self.assertEqual(problemas_de_nombres("X = env('MI_VARIABLE', default='')", catalogo()),
                         ['MI_VARIABLE: no cumple ORIGEN_SERVICIO_DATO'])

    def test_falla_con_un_nombre_bien_formado_que_no_esta_en_el_catalogo(self):
        self.assertEqual(problemas_de_nombres("X = env('ERP_PRUEBA_SIN_ALTA', default='')", catalogo()),
                         ['ERP_PRUEBA_SIN_ALTA: falta en docs/variables_entorno.md'])

    def test_acepta_catalogadas_y_exentas(self):
        fuente = "a = env('ERP_PORTAL_URL', default='')\nb = env('RAILWAY_GIT_COMMIT_SHA', default='')\nc = env('PORT', default='')"
        self.assertEqual(problemas_de_nombres(fuente, catalogo()), [])

    def test_detecta_config_y_os_environ_fuera_del_helper(self):
        self.assertTrue(lecturas_prohibidas("from decouple import config\nx = config('A')"))
        self.assertTrue(lecturas_prohibidas("import os\nx = os.environ.get('A')"))
        self.assertTrue(lecturas_prohibidas("import os\nx = os.getenv('A')"))
        self.assertEqual(lecturas_prohibidas("import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'x')"), [])
        self.assertEqual(lecturas_prohibidas("import dj_database_url\nx = dj_database_url.config()"), [])

    def test_todo_el_codigo_cumple(self):
        nombres = catalogo()
        problemas = []
        for ruta in _archivos_py():
            fuente = ruta.read_text(encoding='utf-8')
            rel = ruta.relative_to(BASE_DIR)
            if _es_prueba(rel):
                continue  # las pruebas del helper usan nombres de mentira
            problemas += [f'{rel}: {p}' for p in problemas_de_nombres(fuente, nombres)]
            if rel != Path('core_erp/variables.py'):
                problemas += [f'{rel}: lectura directa del entorno ({p}); usa core_erp.variables.env()'
                              for p in lecturas_prohibidas(fuente)]
        self.assertEqual(problemas, [])

    def test_catalogo_y_settings_dicen_lo_mismo(self):
        """Cada variable de `env()` está en el catálogo, y a la inversa."""
        en_codigo = {n for n in llamadas_env((BASE_DIR / 'core_erp' / 'settings.py').read_text(encoding='utf-8'))
                     if not _exenta(n)}
        self.assertEqual(en_codigo, {n for n in catalogo() if not _exenta(n)})

    def test_el_catalogo_no_lleva_valores(self):
        for linea in CATALOGO.read_text(encoding='utf-8').splitlines():
            self.assertNotRegex(linea, r'(?i)(sk-|xox|AKIA|-----BEGIN|\bey[A-Za-z0-9]{20,})', linea)


def _arranque(entorno, extra=''):
    """Importa core_erp.settings en un proceso limpio con SOLO `entorno` y devuelve (valores, stderr)."""
    codigo = (
        "import json, core_erp.settings as s\n"
        "print(json.dumps({'a': {k: getattr(s, k) for k in KEYS}, 'storages': [s.STORAGES['privado']['OPTIONS']['bucket_name'],"
        " s.STORAGES['default']['OPTIONS']['bucket_name']], 'anymail': s.ANYMAIL['BREVO_API_KEY']}, default=str))\n"
    ).replace('KEYS', repr(LLAVES))
    base = {'PATH': os.environ.get('PATH', ''), 'HOME': os.environ.get('HOME', ''),
            'PYTHONPATH': str(BASE_DIR)}
    res = subprocess.run([sys.executable, '-c', codigo], cwd=BASE_DIR, env={**base, **entorno},  # noqa: S603 — argv fijo
                         capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(res.stderr)
    return json.loads(res.stdout.strip().splitlines()[-1]), res.stderr


LLAVES = [
    'SECRET_KEY', 'DEBUG', 'ALLOWED_HOSTS', 'DEFAULT_FROM_EMAIL', 'SERVER_EMAIL', 'SESSION_COOKIE_AGE',
    'META_WA_KOOXI_TOKEN', 'META_WA_KOOXI_ID', 'META_WA_PLANTILLA_PAGO', 'ANTHROPIC_API_KEY_KOOXI',
    'OPENPAY_PAGOS_ID', 'OPENPAY_PAGOS_MODO', 'OPENPAY_WEBHOOK_SECRET', 'ERP_FISCAL_TASA_ISH',
    'ERP_KOOXI_MAX_RESPUESTAS_DIA', 'ERP_KOOXI_NUMEROS_PRUEBA', 'ERP_EMAIL_REMITENTE_PAGOS',
    'ERP_SAT_FIEL_CIFRADO_SECRET', 'ERP_PORTAL_URL', 'ERP_CSP_PUBLICO_ACTIVO', 'JIBBLE_NOMINA_ID',
    'ERP_NOMINA_WEBHOOK_TOKEN',
]
PARES = {  # nombre nuevo → (nombre anterior, valor de prueba)
    'DJANGO_SECRET_KEY': ('SECRET_KEY', 'sk-prueba-0001'), 'DJANGO_DEBUG': ('DEBUG', 'True'),
    'DJANGO_ALLOWED_HOSTS': ('ALLOWED_HOSTS', 'uno.test,dos.test'),
    'ERP_EMAIL_REMITENTE': ('DEFAULT_FROM_EMAIL', 'base@prueba.test'),
    'ERP_EMAIL_REMITENTE_PAGOS': ('EMAIL_FROM_PAGOS', 'pagos@prueba.test'),
    'ERP_SESION_INACTIVIDAD_SEGUNDOS': ('SESSION_IDLE_TIMEOUT', '600'),
    'META_WA_KOOXI_TOKEN': ('WA_CLOUD_API_TOKEN', 'marcador-token'), 'META_WA_KOOXI_ID': ('WA_PHONE_NUMBER_ID', 'marcador-id'),
    'META_WA_PLANTILLA_PAGO': ('WA_TEMPLATE_PAGO', 'plantilla_pago'),
    'ANTHROPIC_API_KEY_KOOXI': ('ANTHROPIC_API_KEY', 'marcador-anthropic'),
    'OPENPAY_PAGOS_ID': ('OPENPAY_MERCHANT_ID', 'marcador-merchant'), 'OPENPAY_PAGOS_MODO': ('OPENPAY_MODE', 'production'),
    'OPENPAY_WEBHOOK_SECRET': ('OPENPAY_WEBHOOK_PASSWORD', 'marcador-webhook'),
    'ERP_FISCAL_TASA_ISH': ('TASA_ISH', '0.045'), 'ERP_KOOXI_MAX_RESPUESTAS_DIA': ('WA_AGENTE_MAX_RESPUESTAS_DIA', '77'),
    'ERP_KOOXI_NUMEROS_PRUEBA': ('WA_AGENTE_NUMEROS_PRUEBA', '5211,5222'),
    'ERP_SAT_FIEL_CIFRADO_SECRET': ('SAT_FIEL_ENCRYPTION_KEY', 'marcador-sat'), 'ERP_PORTAL_URL': ('PORTAL_URL', 'https://portal.prueba.test'),
    'ERP_CSP_PUBLICO_ACTIVO': ('PUBLIC_CSP_ENABLED', 'False'), 'JIBBLE_NOMINA_ID': ('JIBBLE_CLIENT_ID', 'marcador-jibble'),
    'ERP_NOMINA_WEBHOOK_TOKEN': ('NOMINA_CRON_TOKEN', 'marcador-nomina'),
    'CLOUDFLARE_R2_PRIVADO_BUCKET': ('CLOUDFLARE_R2_PRIVATE_BUCKET_NAME', 'bucket-privado'),
    'CLOUDFLARE_R2_PUBLICO_BUCKET': ('CLOUDFLARE_R2_BUCKET_NAME', 'bucket-publico'),
    'BREVO_EMAIL_API_KEY': ('BREVO_API_KEY', 'marcador-brevo'),
}


class ArranqueConNombresTest(SimpleTestCase):
    """Con los nombres nuevos todo se lee; los anteriores ya no tienen efecto."""

    def test_con_los_nombres_nuevos_todo_funciona(self):
        valores, _ = _arranque({nuevo: v for nuevo, (_, v) in PARES.items()})
        a = valores['a']
        self.assertEqual(a['SECRET_KEY'], 'sk-prueba-0001')
        self.assertIs(a['DEBUG'], True)
        self.assertEqual(a['ALLOWED_HOSTS'], ['uno.test', 'dos.test'])
        self.assertEqual(a['DEFAULT_FROM_EMAIL'], 'base@prueba.test')
        self.assertEqual(a['SERVER_EMAIL'], 'base@prueba.test')
        self.assertEqual(a['ERP_EMAIL_REMITENTE_PAGOS'], 'pagos@prueba.test')
        self.assertEqual(a['SESSION_COOKIE_AGE'], 600)
        self.assertEqual(a['META_WA_KOOXI_TOKEN'], 'marcador-token')
        self.assertEqual(a['META_WA_KOOXI_ID'], 'marcador-id')
        self.assertEqual(a['META_WA_PLANTILLA_PAGO'], 'plantilla_pago')
        self.assertEqual(a['ANTHROPIC_API_KEY_KOOXI'], 'marcador-anthropic')
        self.assertEqual(a['OPENPAY_PAGOS_ID'], 'marcador-merchant')
        self.assertEqual(a['OPENPAY_PAGOS_MODO'], 'production')
        self.assertEqual(a['OPENPAY_WEBHOOK_SECRET'], 'marcador-webhook')
        self.assertEqual(a['ERP_FISCAL_TASA_ISH'], '0.045')
        self.assertEqual(a['ERP_KOOXI_MAX_RESPUESTAS_DIA'], 77)
        self.assertEqual(a['ERP_KOOXI_NUMEROS_PRUEBA'], ['5211', '5222'])
        self.assertEqual(a['ERP_SAT_FIEL_CIFRADO_SECRET'], 'marcador-sat')
        self.assertEqual(a['ERP_PORTAL_URL'], 'https://portal.prueba.test')
        self.assertIs(a['ERP_CSP_PUBLICO_ACTIVO'], False)
        self.assertEqual(a['JIBBLE_NOMINA_ID'], 'marcador-jibble')
        self.assertEqual(a['ERP_NOMINA_WEBHOOK_TOKEN'], 'marcador-nomina')
        self.assertEqual(valores['storages'], ['bucket-privado', 'bucket-publico'])
        self.assertEqual(valores['anymail'], 'marcador-brevo')

    def test_los_nombres_anteriores_ya_no_se_leen(self):
        entorno = {'DJANGO_SECRET_KEY': 'sk-prueba-0001'}
        entorno.update({viejo: v for nuevo, (viejo, v) in PARES.items() if nuevo != 'DJANGO_SECRET_KEY'})
        valores, _ = _arranque(entorno)
        a = valores['a']
        self.assertNotEqual(a['META_WA_KOOXI_TOKEN'], 'marcador-token')
        self.assertNotEqual(a['OPENPAY_PAGOS_ID'], 'marcador-merchant')
        self.assertNotEqual(a['ERP_PORTAL_URL'], 'https://portal.prueba.test')
