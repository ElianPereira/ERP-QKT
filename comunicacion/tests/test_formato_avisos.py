"""Todo aviso al equipo pasa por `Aviso` (Issue #450): nunca un texto suelto."""
import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from comunicacion.formato_wa import Aviso, bloques

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


class BloquesClienteTest(SimpleTestCase):
    def test_parrafos_datos_enlace_y_pregunta_en_su_orden(self):
        texto = bloques('Hola', 'Segundo párrafo', datos=('a', 'b', 'c'), enlace='https://x.test', pregunta='¿Listo?')
        self.assertEqual(texto, 'Hola\n\nSegundo párrafo\n\n• a\n• b\n• c\n\nhttps://x.test\n\n¿Listo?')

    def test_con_menos_de_tres_datos_no_hay_viñetas(self):
        self.assertEqual(bloques('Hola', datos=('a', 'b')), 'Hola\n\na\nb')

    def test_lo_vacio_se_omite(self):
        self.assertEqual(bloques('Hola', '', datos=('', None), enlace='', pregunta=''), 'Hola')

    def test_mensajes_fijos_sin_parrafos_largos_y_enlaces_solos(self):
        from comunicacion import services_agente, services_bajas, services_encuesta, services_responsables
        for texto in (services_agente.AVISO_INICIAL, services_agente.AVISO_ESPERA, services_agente.MENSAJE_HUMANO,
                      services_agente.MENSAJE_CONSENTIMIENTO, services_bajas.CONFIRMACION_BAJA,
                      services_responsables.CONFIRMACION_ACEPTO, services_encuesta.GRACIAS_MALA):
            for parrafo in texto.split('\n\n'):
                self.assertLessEqual(len(parrafo), 280, parrafo)
                if 'https://' in parrafo:
                    for renglon in parrafo.split('\n'):
                        if 'https://' in renglon and not renglon.startswith('• '):
                            self.assertTrue(renglon.startswith('https://'), renglon)
