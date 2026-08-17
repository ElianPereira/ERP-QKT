"""
Tests del backlog de seguridad (Issue #190), orden 48 (SEC-BIZ-002):
`confirmar_accion_destructiva` envuelve una acción de admin con una página
de confirmación intermedia — un solo POST directo (sin pasar por esa
página) no dispara el efecto.

Ejecutar: python manage.py test core_erp.test_admin_utils --verbosity=2
"""
from unittest.mock import MagicMock

from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase

from core_erp.admin_utils import confirmar_accion_destructiva


class ConfirmarAccionDestructivaTest(TestCase):
    def _admin_falso(self):
        admin_falso = MagicMock()
        admin_falso.admin_site.each_context.return_value = {}
        admin_falso.model._meta = 'opts-falso'
        return admin_falso

    def _request(self, post_data):
        request = RequestFactory().post('/admin/app/modelo/', post_data)
        request.user = MagicMock()
        return request

    def test_sin_confirmar_no_ejecuta_y_renderiza_la_pagina_de_confirmacion(self):
        ejecutada = []

        @confirmar_accion_destructiva("¿Seguro?")
        def accion(self, request, queryset):
            ejecutada.append(True)

        admin_falso = self._admin_falso()
        request = self._request({'action': 'accion', '_selected_action': ['1']})

        respuesta = accion(admin_falso, request, queryset=[])

        self.assertEqual(ejecutada, [])
        self.assertIsInstance(respuesta, TemplateResponse)
        self.assertEqual(respuesta.template_name, 'admin/confirmar_accion_destructiva.html')
        self.assertEqual(respuesta.context_data['mensaje'], "¿Seguro?")
        self.assertEqual(respuesta.context_data['action_name'], 'accion')

    def test_con_confirmar_si_ejecuta_la_accion_original(self):
        ejecutada = []

        @confirmar_accion_destructiva("¿Seguro?")
        def accion(self, request, queryset):
            ejecutada.append(queryset)
            return 'resultado'

        admin_falso = self._admin_falso()
        request = self._request({'action': 'accion', 'confirmar': 'si'})

        resultado = accion(admin_falso, request, queryset=['obj-1'])

        self.assertEqual(ejecutada, [['obj-1']])
        self.assertEqual(resultado, 'resultado')

    def test_un_post_directo_sin_pasar_por_la_confirmacion_no_tiene_efecto(self):
        """El escenario del backlog: una sesión secuestrada scripteando un
        único POST directo al changelist, sin el campo `confirmar`."""
        ejecutada = []

        @confirmar_accion_destructiva("¿Seguro?")
        def borrar_todo(self, request, queryset):
            ejecutada.append(True)

        admin_falso = self._admin_falso()
        request = self._request({'action': 'borrar_todo', '_selected_action': ['1', '2']})

        borrar_todo(admin_falso, request, queryset=[])

        self.assertEqual(ejecutada, [], "un solo POST sin 'confirmar=si' no debe ejecutar la acción")
