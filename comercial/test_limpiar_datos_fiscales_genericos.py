"""Tests para el comando limpiar_datos_fiscales_genericos."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from comercial.models import Cliente


class LimpiarDatosFiscalesGenericosTest(TestCase):
    def setUp(self):
        # Cliente con datos fiscales genéricos (RFC genérico)
        self.cliente_generico_rfc = Cliente.objects.create(
            nombre='JUAN PEREZ',
            telefono='9991111111',
            rfc='XAXX010101000',
            razon_social='PUBLICO EN GENERAL',
            codigo_postal_fiscal='97238',
            regimen_fiscal='616',
            uso_cfdi='S01',
            es_cliente_fiscal=True,
        )
        # Cliente con razón social genérica con acento
        self.cliente_acento = Cliente.objects.create(
            nombre='MARIA LOPEZ',
            telefono='9992222222',
            rfc='XAXX010101000',
            razon_social='PÚBLICO EN GENERAL',
            es_cliente_fiscal=True,
        )
        # Cliente con datos fiscales REALES (no debe tocarse)
        self.cliente_real = Cliente.objects.create(
            nombre='EMPRESA ABC',
            telefono='9993333333',
            rfc='ABC010101AAA',
            razon_social='EMPRESA ABC SA DE CV',
            codigo_postal_fiscal='97000',
            regimen_fiscal='601',
            uso_cfdi='G03',
            es_cliente_fiscal=True,
        )
        # Cliente sin datos fiscales (no debe tocarse, ya está limpio)
        self.cliente_vacio = Cliente.objects.create(
            nombre='PEDRO',
            telefono='9994444444',
        )

    def test_dry_run_no_modifica_nada(self):
        out = StringIO()
        call_command('limpiar_datos_fiscales_genericos', stdout=out)
        self.cliente_generico_rfc.refresh_from_db()
        self.assertEqual(self.cliente_generico_rfc.rfc, 'XAXX010101000')
        self.assertEqual(self.cliente_generico_rfc.razon_social, 'PUBLICO EN GENERAL')
        self.assertIn('DRY RUN', out.getvalue())
        self.assertIn('Encontrados 2', out.getvalue())

    def test_apply_limpia_solo_genericos(self):
        out = StringIO()
        call_command('limpiar_datos_fiscales_genericos', '--apply', stdout=out)

        # Genérico → vaciado
        self.cliente_generico_rfc.refresh_from_db()
        self.assertIsNone(self.cliente_generico_rfc.rfc)
        self.assertIsNone(self.cliente_generico_rfc.razon_social)
        self.assertIsNone(self.cliente_generico_rfc.codigo_postal_fiscal)
        self.assertIsNone(self.cliente_generico_rfc.regimen_fiscal)
        self.assertIsNone(self.cliente_generico_rfc.uso_cfdi)
        self.assertFalse(self.cliente_generico_rfc.es_cliente_fiscal)
        # El nombre del cliente NO se toca
        self.assertEqual(self.cliente_generico_rfc.nombre, 'JUAN PEREZ')

        # Genérico con acento → vaciado
        self.cliente_acento.refresh_from_db()
        self.assertIsNone(self.cliente_acento.rfc)
        self.assertIsNone(self.cliente_acento.razon_social)

        # Real → intacto
        self.cliente_real.refresh_from_db()
        self.assertEqual(self.cliente_real.rfc, 'ABC010101AAA')
        self.assertEqual(self.cliente_real.razon_social, 'EMPRESA ABC SA DE CV')
        self.assertTrue(self.cliente_real.es_cliente_fiscal)

        # Vacío → sigue vacío
        self.cliente_vacio.refresh_from_db()
        self.assertIsNone(self.cliente_vacio.rfc)

    def test_sin_genericos_no_hace_nada(self):
        # Borrar los genéricos
        self.cliente_generico_rfc.delete()
        self.cliente_acento.delete()

        out = StringIO()
        call_command('limpiar_datos_fiscales_genericos', '--apply', stdout=out)
        self.assertIn('Nada que hacer', out.getvalue())
