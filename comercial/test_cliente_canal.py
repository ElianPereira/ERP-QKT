"""Tests para get_or_create_cliente_desde_canal."""
from django.test import TestCase

from comercial.models import Cliente
from comercial.services import get_or_create_cliente_desde_canal


class GetOrCreateClienteDesdeCanalTest(TestCase):
    def test_telefono_vacio_crea_nuevo_y_no_matchea_fantasma(self):
        """Cliente fantasma 'PUBLICO EN GENERAL' con teléfono vacío NO debe matchearse."""
        Cliente.objects.create(nombre='PUBLICO EN GENERAL', telefono='', origen='Otro')

        cliente, created = get_or_create_cliente_desde_canal(
            telefono_raw='',
            nombre_raw='Juan Pérez',
            origen='WhatsApp',
        )
        self.assertTrue(created)
        self.assertEqual(cliente.nombre, 'JUAN PÉREZ')
        self.assertEqual(Cliente.objects.count(), 2)

    def test_telefono_invalido_corto_crea_nuevo(self):
        """Teléfono con menos de 10 dígitos no debe matchear y debe crear nuevo."""
        Cliente.objects.create(nombre='PUBLICO EN GENERAL', telefono='123', origen='Otro')
        cliente, created = get_or_create_cliente_desde_canal(
            telefono_raw='123',
            nombre_raw='Maria',
            origen='Web',
        )
        self.assertTrue(created)
        self.assertEqual(Cliente.objects.count(), 2)

    def test_telefono_valido_nuevo_crea_cliente(self):
        cliente, created = get_or_create_cliente_desde_canal(
            telefono_raw='9991234567',
            nombre_raw='Pedro López',
            origen='WhatsApp',
        )
        self.assertTrue(created)
        self.assertEqual(cliente.telefono, '9991234567')
        self.assertEqual(cliente.nombre, 'PEDRO LÓPEZ')

    def test_telefono_valido_existente_con_nombre_real_no_sobrescribe(self):
        Cliente.objects.create(nombre='JUAN PÉREZ', telefono='9991234567')
        cliente, created = get_or_create_cliente_desde_canal(
            telefono_raw='9991234567',
            nombre_raw='Otro Nombre',
            origen='WhatsApp',
        )
        self.assertFalse(created)
        self.assertEqual(cliente.nombre, 'JUAN PÉREZ')

    def test_telefono_valido_existente_con_nombre_generico_publico_sobrescribe(self):
        Cliente.objects.create(nombre='PUBLICO EN GENERAL', telefono='9991234567')
        cliente, created = get_or_create_cliente_desde_canal(
            telefono_raw='9991234567',
            nombre_raw='Ana Martínez',
            origen='WhatsApp',
        )
        self.assertFalse(created)
        self.assertEqual(cliente.nombre, 'ANA MARTÍNEZ')

    def test_telefono_valido_existente_con_nombre_prospecto_sobrescribe(self):
        Cliente.objects.create(nombre='PROSPECTO WA (4567)', telefono='9991234567')
        cliente, created = get_or_create_cliente_desde_canal(
            telefono_raw='9991234567',
            nombre_raw='Luis Gómez',
            origen='WhatsApp',
        )
        self.assertFalse(created)
        self.assertEqual(cliente.nombre, 'LUIS GÓMEZ')

    def test_normalizacion_telefono_quita_caracteres(self):
        cliente, created = get_or_create_cliente_desde_canal(
            telefono_raw='+52 (999) 123-4567',
            nombre_raw='Test',
            origen='Web',
        )
        self.assertTrue(created)
        self.assertEqual(cliente.telefono, '5299912345671'[:11] if False else '529991234567')

    def test_segundo_request_mismo_telefono_no_duplica(self):
        get_or_create_cliente_desde_canal(
            telefono_raw='9991111111', nombre_raw='Primero', origen='WhatsApp'
        )
        cliente2, created2 = get_or_create_cliente_desde_canal(
            telefono_raw='9991111111', nombre_raw='Segundo', origen='WhatsApp'
        )
        self.assertFalse(created2)
        # Primero ya tenía nombre real, no se sobrescribe
        self.assertEqual(cliente2.nombre, 'PRIMERO')
        self.assertEqual(Cliente.objects.filter(telefono='9991111111').count(), 1)
