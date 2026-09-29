"""Reglas del menú lateral del admin (Jazzmin).

Todo modelo registrado que aparece en el menú lleva un nombre de una sola
palabra (`verbose_name_plural`) e ícono propio en `JAZZMIN_SETTINGS['icons']`.
Sin esto, un apartado nuevo sale con un nombre largo y el ícono genérico.
"""
from django.conf import settings
from django.contrib import admin
from django.test import SimpleTestCase


def _modelos_en_menu():
    jazzmin = settings.JAZZMIN_SETTINGS
    ocultos = {m.lower() for m in jazzmin.get('hide_models', [])}
    apps_ocultas = {a.lower() for a in jazzmin.get('hide_apps', [])}
    for modelo in admin.site._registry:
        clave = f'{modelo._meta.app_label}.{modelo._meta.model_name}'
        if clave in ocultos or modelo._meta.app_label in apps_ocultas:
            continue
        yield clave, str(modelo._meta.verbose_name_plural)


class MenuAdminTest(SimpleTestCase):
    def test_cada_apartado_tiene_icono_propio(self):
        iconos = {k.lower() for k in settings.JAZZMIN_SETTINGS['icons']}
        faltan = [clave for clave, _ in _modelos_en_menu() if clave not in iconos]
        self.assertEqual(faltan, [], "Agrega su ícono en JAZZMIN_SETTINGS['icons'] (core_erp/settings.py).")

    def test_cada_apartado_tiene_nombre_de_una_palabra(self):
        largos = [f'{clave}: «{nombre}»' for clave, nombre in _modelos_en_menu() if len(nombre.split()) > 1]
        self.assertEqual(largos, [], "verbose_name_plural del menú debe ser una sola palabra.")
