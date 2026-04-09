"""Tests del módulo de traducción de errores del SAT."""
from django.test import SimpleTestCase

from facturacion.services.sat_errors import (
    extraer_codigo_sat,
    traducir_error,
    SAT_ERRORS,
)


class ExtraerCodigoTests(SimpleTestCase):
    def test_extrae_codigo_simple(self):
        self.assertEqual(
            extraer_codigo_sat("CFDI40147: RFC no válido"),
            "CFDI40147",
        )

    def test_codigo_en_medio_del_mensaje(self):
        self.assertEqual(
            extraer_codigo_sat("Error CFDI33118 al emitir"),
            "CFDI33118",
        )

    def test_sin_codigo_devuelve_none(self):
        self.assertIsNone(extraer_codigo_sat("Error desconocido"))

    def test_mensaje_vacio_devuelve_none(self):
        self.assertIsNone(extraer_codigo_sat(""))
        self.assertIsNone(extraer_codigo_sat(None))


class TraducirErrorTests(SimpleTestCase):
    def test_codigo_conocido_incluye_sugerencia(self):
        mensaje = traducir_error("CFDI40147: RFC inválido")
        self.assertIn("CFDI40147", mensaje)
        self.assertIn(
            SAT_ERRORS["CFDI40147"].sugerencia.split(".")[0],
            mensaje,
        )

    def test_codigo_desconocido_se_marca(self):
        mensaje = traducir_error("CFDI99999: algo raro")
        self.assertIn("[CFDI99999]", mensaje)

    def test_sin_codigo_devuelve_mensaje_original(self):
        self.assertEqual(
            traducir_error("Error genérico sin código"),
            "Error genérico sin código",
        )

    def test_vacio(self):
        self.assertIn("desconocido", traducir_error("").lower())
        self.assertIn("desconocido", traducir_error(None).lower())
