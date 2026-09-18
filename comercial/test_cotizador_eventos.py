"""
Tests del rediseño de Evento en el cotizador público (Issue #287).

Dos caminos únicos: "Elige un paquete" (`Producto(es_paquete=True)` con
`ProductoComponente` reales) y "Arma tu propio evento" (catálogo abierto por
categoría, mismo mecanismo que Pasadía/Hospedaje). El catálogo cerrado
`CatalogoEvento`/`ConfiguracionEventoCotizacion` quedó retirado por completo
— ver `comercial/models.py` y `comercial/views_cotizador.py`.

Ejecutar: python manage.py test comercial.test_cotizador_eventos --verbosity=2
"""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Producto, ProductoComponente
from comercial.reglas_eventos import MAX_PERSONAS_EVENTO
from comercial.views_cotizador import _agregar_item, _lineas_cotizador
from comunicacion.tests.utils import RespuestaFalsa, limpiar_cache_emisor, wa_settings


def _payload(**extra):
    return {
        'nombre': 'Ana Ruiz',
        'telefono': '5215555550001',
        'email': 'ana@example.com',
        'servicio': 'EVENTO',
        'fecha': (timezone.localdate() + timedelta(days=60)).strftime('%Y-%m-%d'),
        'personas': '80',
        'acepta_legales': True,
        **extra,
    }


def _crear_paquete(nombre='Paquete Premium QKT', precio_fijo='6000.00', **extra):
    campos = {
        'visible_cotizador': True, 'cotizador_evento': True, 'es_paquete': True,
    }
    campos.update(extra)
    return Producto.objects.create(
        nombre=nombre, precio_venta_fijo=Decimal(precio_fijo), **campos,
    )


class TopeAforoTest(TestCase):
    """Tope duro de 150 personas, sin ruta alterna (Issue #287)."""

    def setUp(self):
        cache.clear()

    def test_mas_de_150_personas_se_rechaza_sin_crear_nada(self):
        respuesta = self.client.post(
            reverse('cotizador_enviar'),
            data=json.dumps(_payload(personas='151')),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('150', respuesta.json()['errores'][0])
        self.assertFalse(Cotizacion.objects.exists())

    def test_150_personas_exactas_se_acepta(self):
        _crear_paquete()
        respuesta = self.client.post(
            reverse('cotizador_enviar'),
            data=json.dumps(_payload(personas='150')),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 200)

    def test_api_total_acota_el_aforo_exhibido_al_tope(self):
        datos = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'EVENTO', 'personas': '999',
        }).json()
        self.assertEqual(datos['personas'], MAX_PERSONAS_EVENTO)


class RedondeoAforoTest(TestCase):
    """El aforo cotizado sube al siguiente bloque de 10 (mínimo 20)."""

    def setUp(self):
        cache.clear()

    def test_un_aforo_intermedio_sube_al_siguiente_bloque_de_diez(self):
        _crear_paquete()
        self.client.post(
            reverse('cotizador_enviar'),
            data=json.dumps(_payload(personas='57')),
            content_type='application/json',
        )
        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.num_personas, 60)

    def test_un_aforo_menor_a_veinte_sube_al_minimo(self):
        _crear_paquete()
        self.client.post(
            reverse('cotizador_enviar'),
            data=json.dumps(_payload(personas='5')),
            content_type='application/json',
        )
        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.num_personas, 20)


class PaqueteEsProductoRealTest(TestCase):
    """El paquete es un Producto(es_paquete=True) con ProductoComponente reales,
    nunca un precio suelto — así el guard de `Producto.clean()` protege el
    margen automáticamente, sin lógica nueva en el cotizador."""

    def setUp(self):
        cache.clear()

    def test_un_paquete_no_puede_costar_menos_que_sus_componentes(self):
        silla = Producto.objects.create(nombre='Silla Tiffany', precio_venta_fijo=Decimal('25.00'))
        mesa = Producto.objects.create(nombre='Mesa redonda', precio_venta_fijo=Decimal('150.00'))
        paquete = Producto.objects.create(
            nombre='Paquete Rústico', es_paquete=True, precio_venta_fijo=Decimal('100.00'),
        )
        ProductoComponente.objects.create(producto_padre=paquete, producto_hijo=silla, cantidad=Decimal('10'))
        ProductoComponente.objects.create(producto_padre=paquete, producto_hijo=mesa, cantidad=Decimal('1'))
        # 10 × 25.00 + 1 × 150.00 = 400.00, muy por encima del precio fijo (100.00).
        with self.assertRaises(ValidationError):
            paquete.full_clean()

    def test_un_paquete_con_precio_suficiente_pasa_la_validacion(self):
        silla = Producto.objects.create(nombre='Silla Tiffany', precio_venta_fijo=Decimal('25.00'))
        paquete = Producto.objects.create(
            nombre='Paquete Rústico', es_paquete=True, precio_venta_fijo=Decimal('300.00'),
        )
        ProductoComponente.objects.create(producto_padre=paquete, producto_hijo=silla, cantidad=Decimal('10'))
        paquete.full_clean()  # no lanza: 300.00 > 10 × 25.00


class ApiPaquetesEventoTest(TestCase):
    """GET /api/cotizador/paquetes-evento/ — el grid de 'Elige un paquete'."""

    def setUp(self):
        cache.clear()

    def test_solo_lista_paquetes_visibles_para_evento(self):
        visible = _crear_paquete(nombre='Visible')
        _crear_paquete(nombre='Otro servicio', cotizador_evento=False)
        _crear_paquete(nombre='No visible', visible_cotizador=False)
        Producto.objects.create(
            nombre='No es paquete', precio_venta_fijo=Decimal('500.00'),
            visible_cotizador=True, cotizador_evento=True, es_paquete=False,
        )
        datos = self.client.get(reverse('api_paquetes_evento')).json()
        nombres = [p['nombre'] for p in datos['paquetes']]
        self.assertEqual(nombres, [visible.nombre])

    def test_el_precio_incluye_iva(self):
        _crear_paquete(nombre='Premium', precio_fijo='6000.00')
        datos = self.client.get(reverse('api_paquetes_evento')).json()
        # 6000.00 base × 1.16 = 6960.00
        self.assertEqual(datos['paquetes'][0]['precio'], '6960.00')

    def test_expone_el_tope_de_aforo(self):
        datos = self.client.get(reverse('api_paquetes_evento')).json()
        self.assertEqual(datos['max_personas'], MAX_PERSONAS_EVENTO)


class LineasEventoConPaqueteTest(TestCase):
    """`_lineas_cotizador` con un paquete elegido: solo el paquete, nunca
    duplicado con la línea base ni con el catálogo abierto."""

    def setUp(self):
        cache.clear()

    def test_paquete_elegido_es_la_unica_linea(self):
        paquete = _crear_paquete()
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=paquete.id, extras_ids=[],
            num_personas=80, horas_evento=6,
        )
        self.assertEqual([prod for prod, _, _ in lineas], [paquete])

    def test_un_paquete_mandado_tambien_en_extras_ids_no_se_duplica(self):
        # Defensa en profundidad: nada impide que el cliente mande el mismo
        # id como paquete_id y dentro de extras_ids.
        paquete = _crear_paquete()
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=paquete.id, extras_ids=[paquete.id],
            num_personas=80, horas_evento=6,
        )
        self.assertEqual([prod for prod, _, _ in lineas], [paquete])

    def test_un_paquete_inexistente_o_invisible_cae_al_camino_abierto(self):
        base = Producto.objects.create(
            nombre='Paquete Esencial QKT', precio_venta_fijo=Decimal('4000.00'),
            visible_cotizador=True, cotizador_evento=True, rol_cotizador='BASE_EVENTO',
        )
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=999999, extras_ids=[],
            num_personas=80, horas_evento=6,
        )
        self.assertEqual([prod for prod, _, _ in lineas], [base])


@wa_settings()
class EnvioPaqueteEventoTest(TestCase):
    """De punta a punta: el paquete elegido crea la cotización con ese único item."""

    def setUp(self):
        limpiar_cache_emisor()
        cache.clear()

    def _enviar(self, **extra):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value='5215555550003'):
            return self.client.post(
                reverse('cotizador_enviar'),
                data=json.dumps(_payload(**extra)),
                content_type='application/json',
            )

    def test_crea_la_cotizacion_con_el_paquete_elegido(self):
        paquete = _crear_paquete()
        respuesta = self._enviar(paquete_id=paquete.id)
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        items = list(cotizacion.items.all())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].producto, paquete)

    def test_el_total_exhibido_coincide_con_el_que_se_cobra(self):
        # Invariante del art. 7 BIS de la LFPC: lo anunciado y lo cobrado son
        # lo mismo — ver la propia _lineas_cotizador().
        paquete = _crear_paquete()
        exhibido = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'EVENTO', 'personas': '80', 'paquete': paquete.id,
        }).json()

        self._enviar(paquete_id=paquete.id)
        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(Decimal(exhibido['total']), cotizacion.precio_final)

    def test_personalizado_sin_paquete_usa_el_camino_abierto(self):
        base = Producto.objects.create(
            nombre='Paquete Esencial QKT', precio_venta_fijo=Decimal('4000.00'),
            visible_cotizador=True, cotizador_evento=True, rol_cotizador='BASE_EVENTO',
        )
        extra = Producto.objects.create(
            nombre='Carrito de bolis', precio_venta_fijo=Decimal('500.00'),
            visible_cotizador=True, cotizador_evento=True, grupo_cotizador='EXTRAS',
        )
        respuesta = self._enviar(extras_ids=[extra.id])
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        nombres = {item.producto.nombre for item in cotizacion.items.all()}
        self.assertEqual(nombres, {base.nombre, extra.nombre})


class CosteoPorBloqueDeDiezTest(TestCase):
    """`cantidad_por_persona` + `factor_personas=10`: costeo por bloque de 10,
    no por persona — mecanismo del catálogo abierto, compartido con
    Pasadía/Hospedaje, ahora usado también en 'Arma tu propio evento'."""

    def setUp(self):
        cache.clear()

    def test_80_personas_cobra_8_unidades(self):
        mesero = Producto.objects.create(
            nombre='Mesero de servicio', precio_venta_fijo=Decimal('300.00'),
            visible_cotizador=True, cotizador_evento=True, grupo_cotizador='SERVICIOS',
            cantidad_por_persona=True, factor_personas=10,
        )
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[mesero.id],
            num_personas=80, horas_evento=6,
        )
        cantidades = {prod.nombre: qty for prod, qty, _ in lineas}
        self.assertEqual(cantidades['Mesero de servicio'], 8)

    def test_una_fraccion_sobrante_redondea_hacia_arriba(self):
        # 81 / 10 = 8.1 → 9, no 8: una fracción de bloque sigue siendo un
        # bloque completo a cobrar.
        mesero = Producto.objects.create(
            nombre='Mesero de servicio', precio_venta_fijo=Decimal('300.00'),
            visible_cotizador=True, cotizador_evento=True, grupo_cotizador='SERVICIOS',
            cantidad_por_persona=True, factor_personas=10,
        )
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[mesero.id],
            num_personas=81, horas_evento=6,
        )
        cantidades = {prod.nombre: qty for prod, qty, _ in lineas}
        self.assertEqual(cantidades['Mesero de servicio'], 9)


class GrupoExclusionTest(TestCase):
    """Mobiliario (u otra categoría con `grupo_exclusion`) rechaza dos
    productos del mismo grupo en la misma cotización — candado ya existente
    de `ItemCotizacion.clean()`, corre en cada `_agregar_item` porque
    `ItemCotizacion.save()` llama `full_clean()`."""

    def setUp(self):
        cache.clear()

    def test_dos_productos_del_mismo_grupo_de_exclusion_se_rechazan(self):
        cliente = Cliente.objects.create(nombre='Ana Ruiz', telefono='9995550001')
        cotizacion = Cotizacion.objects.create(
            cliente=cliente, tipo_servicio='EVENTO',
            fecha_evento=timezone.localdate() + timedelta(days=60), num_personas=80,
        )
        mesa_redonda = Producto.objects.create(
            nombre='Mesa redonda', precio_venta_fijo=Decimal('100.00'),
            grupo_cotizador='MOBILIARIO', grupo_exclusion='MESAS',
        )
        mesa_rectangular = Producto.objects.create(
            nombre='Mesa rectangular', precio_venta_fijo=Decimal('100.00'),
            grupo_cotizador='MOBILIARIO', grupo_exclusion='MESAS',
        )
        _agregar_item(cotizacion, mesa_redonda, 1)
        with self.assertRaises(ValidationError):
            _agregar_item(cotizacion, mesa_rectangular, 1)


class ImagenZonasRestringidasTest(TestCase):
    """El plano de zonas restringidas solo se expone para Evento."""

    def setUp(self):
        cache.clear()

    def test_es_none_para_otro_servicio(self):
        datos = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'PASADIA', 'personas': '10',
        }).json()
        self.assertIsNone(datos['imagen_zonas_restringidas'])

    def test_sin_plano_cargado_da_none_para_evento(self):
        datos = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'EVENTO', 'personas': '80',
        }).json()
        self.assertIsNone(datos['imagen_zonas_restringidas'])
