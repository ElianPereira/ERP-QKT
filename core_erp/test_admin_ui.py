"""Tests de los componentes visuales del admin (Issue #322)."""
from decimal import Decimal

from django.test import SimpleTestCase

from core_erp import admin_ui as ui


class BadgeTest(SimpleTestCase):
    def test_usa_clase_del_tono_y_no_estilos_en_linea(self):
        html = str(ui.badge('Aplicada', ui.EXITO))
        self.assertIn('qkt-badge--exito', html)
        self.assertNotIn('style=', html)

    def test_tono_desconocido_cae_a_neutro(self):
        self.assertIn('qkt-badge--neutro', str(ui.badge('X', 'morado')))

    def test_categoria_quita_el_punto(self):
        self.assertIn('qkt-badge--categoria', str(ui.badge('Evento', ui.INFO, categoria=True)))

    def test_escapa_el_texto(self):
        html = str(ui.badge('<script>alert(1)</script>'))
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_badge_por_valor_usa_el_mapa(self):
        html = str(ui.badge_por_valor('CANCELADA', {'CANCELADA': ui.ERROR}, 'Cancelada'))
        self.assertIn('qkt-badge--error', html)
        self.assertIn('qkt-badge--neutro', str(ui.badge_por_valor('OTRO', {}, 'Otro')))


class MontoTest(SimpleTestCase):
    def test_formato_con_miles_y_dos_decimales(self):
        self.assertIn('$32,480.00', str(ui.monto(Decimal('32480'))))

    def test_redondeo_half_up_sin_pasar_por_float(self):
        # 0.125 en float es 0.125 exacto, pero 2.675 no: con float daría 2.67
        self.assertIn('$2.68', str(ui.monto(Decimal('2.675'))))

    def test_negativo_y_tono(self):
        html = str(ui.monto(Decimal('-150.5'), tono=ui.ERROR))
        self.assertIn('-$150.50', html)
        self.assertIn('qkt-num--error', html)

    def test_none_es_vacio_alineado_como_cifra(self):
        html = str(ui.monto(None))
        self.assertIn('qkt-vacio', html)
        self.assertIn('qkt-num', html)


class AvanceTest(SimpleTestCase):
    def test_se_acota_entre_0_y_100(self):
        self.assertIn('width:100%', str(ui.avance(150)))
        self.assertIn('width:0%', str(ui.avance(-5)))

    def test_tono_por_avance(self):
        self.assertIn('qkt-avance--exito', str(ui.avance(100)))
        self.assertIn('qkt-avance--alerta', str(ui.avance(50)))


class BotonesTest(SimpleTestCase):
    def test_boton_icono_lleva_titulo_accesible(self):
        html = str(ui.boton_icono('/x/', 'file-pdf', 'Ver PDF', nueva_pestana=True))
        self.assertIn('aria-label="Ver PDF"', html)
        self.assertIn('target="_blank" rel="noopener"', html)

    def test_confirmacion_va_en_data_atributo_escapado(self):
        html = str(ui.boton('Enviar', '/x/', confirmar='¿Enviar "ya"?'))
        self.assertIn('data-qkt-confirmar="¿Enviar &quot;ya&quot;?"', html)
        self.assertNotIn('onclick', html)

    def test_menu_con_copiar_y_separador(self):
        html = str(ui.menu_acciones([
            {'texto': 'Lista', 'url': '/lista/'},
            {'separador': True},
            {'texto': 'Copiar', 'copiar': 'https://x/?a=1&b=2'},
        ]))
        self.assertIn('<details class="qkt-menu">', html)
        self.assertIn('qkt-menu__sep', html)
        self.assertIn('data-qkt-copiar="https://x/?a=1&amp;b=2"', html)

    def test_menu_vacio_deja_hueco(self):
        self.assertIn('qkt-btn--hueco', str(ui.menu_acciones([])))

    def test_acciones_ignora_partes_vacias(self):
        html = str(ui.acciones(ui.hueco_icono(), None, ''))
        self.assertEqual(html.count('qkt-btn--hueco'), 1)
