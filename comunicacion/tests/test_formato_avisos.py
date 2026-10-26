"""Todo aviso al equipo pasa por `Aviso` (Issue #450): nunca un texto suelto."""
import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from comunicacion.formato_wa import Aviso

LLAMADAS = {'_enviar_aviso'}
TEXTO_SUELTO = (ast.Constant, ast.JoinedStr, ast.BinOp)


def _nombre(llamada):
    func = llamada.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')


class NingunAvisoPasaTextoSueltoTest(SimpleTestCase):
    def test_enviar_aviso_solo_recibe_un_aviso(self):
        encontradas, sueltas = 0, []
        for ruta in Path(settings.BASE_DIR).rglob('*.py'):
            partes = ruta.parts
            if 'migrations' in partes or 'tests' in partes or ruta.name.startswith('test'):
                continue
            arbol = ast.parse(ruta.read_text(encoding='utf-8'))
            for nodo in ast.walk(arbol):
                if isinstance(nodo, ast.Call) and _nombre(nodo) in LLAMADAS and len(nodo.args) >= 2:
                    encontradas += 1
                    if isinstance(nodo.args[1], TEXTO_SUELTO):
                        sueltas.append(f'{ruta.name}:{nodo.lineno}')
        self.assertGreater(encontradas, 10)
        self.assertEqual(sueltas, [])

    def test_los_parametros_de_meta_son_validos(self):
        aviso = Aviso(titulo='Resumen\ndel día', categoria='Resumen diario', urgente=True,
                      lineas=['uno\n\ndos', 'tres    cuatro\t' * 40] * 8, cierre='fin\ny más', enlace='https://x.test/a')
        for parametro in aviso.parametros():
            self.assertNotIn('\n', parametro)
            self.assertNotIn('\t', parametro)
            self.assertNotIn('     ', parametro)
            self.assertLessEqual(len(parametro), 900)
