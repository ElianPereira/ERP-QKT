"""
Tests del flujo de cotización con el catálogo cerrado de Eventos.

Cubre la composición de líneas (`services_eventos.lineas_evento`), su
integración en `_lineas_cotizador`, el envío real del formulario público y
las dos APIs que alimentan al navegador.

El invariante que más importa aquí: el total que `api_total_cotizador` le
exhibe al cliente tiene que ser exactamente el `precio_final` de la
`Cotizacion` que después crea `cotizador_enviar` con la misma selección. Si
se separaran, el cotizador estaría anunciando un precio y cobrando otro
(art. 7 BIS de la LFPC).

Ejecutar: python manage.py test comercial.test_cotizador_eventos_flujo --verbosity=2
"""

import base64
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from comercial.models import (
    CatalogoEvento,
    CatalogoEventoProducto,
    ConfiguracionEventoCotizacion,
    Cotizacion,
    ImagenLanding,
    Producto,
)
from comercial.reglas_eventos import MODALIDAD_ARRENDAMIENTO, MODALIDAD_PAQUETE
from comercial.services_eventos import lineas_evento, resolver_seleccion
from comercial.views_cotizador import _lineas_cotizador
from comunicacion.tests.utils import RespuestaFalsa, limpiar_cache_emisor, wa_settings

# PNG 1x1 real: el ImageField valida la imagen, no basta con bytes cualquiera.
_PNG_MINIMO = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
)


def _producto(nombre, precio, **extra):
    return Producto.objects.create(
        nombre=nombre, precio_venta_fijo=Decimal(precio), **extra,
    )


def _catalogo_eventos():
    """Catálogo mínimo pero completo: los dos paquetes con todo asignado."""
    base = _producto('Arrendamiento de la Quinta', '10000.00',
                     visible_cotizador=True, cotizador_evento=True,
                     rol_cotizador='BASE_EVENTO')
    hora_extra = _producto('Hora Extra de Arrendamiento', '1000.00',
                           visible_cotizador=True, cotizador_evento=True,
                           rol_cotizador='HORA_EXTRA')

    esencial = CatalogoEvento.objects.create(
        tipo=CatalogoEvento.TIPO_PAQUETE,
        codigo='esencial', nombre='Esencial', orden=1,
        requiere_mobiliario=True, permite_licores_opcional=False,
        requiere_taquiza=False, permite_extras=False,
    )
    qkt = CatalogoEvento.objects.create(
        tipo=CatalogoEvento.TIPO_PAQUETE,
        codigo='qkt', nombre='QKT', orden=2,
        requiere_mobiliario=True, permite_licores_opcional=True,
        requiere_taquiza=True, permite_extras=True,
    )

    # QKT incluye refrescos y servicio de mesa sin que el cliente los elija.
    refresco = _producto('Refresco 2L', '30.00')
    mesero = _producto('Mesero por evento', '600.00')
    CatalogoEventoProducto.objects.create(
        opcion=qkt, producto=refresco, concepto='Refrescos',
        cantidad_por_persona=Decimal('1'), orden=1,
    )
    CatalogoEventoProducto.objects.create(
        opcion=qkt, producto=mesero, concepto='Servicio de mesa',
        cantidad_por_persona=Decimal('0.05'), orden=2,  # 1 cada 20 invitados
    )

    mobiliario = CatalogoEvento.objects.create(
        tipo=CatalogoEvento.TIPO_MOBILIARIO, codigo='rustico', nombre='Rústico')
    silla = _producto('Silla Tiffany', '25.00')
    CatalogoEventoProducto.objects.create(
        opcion=mobiliario, producto=silla, cantidad_por_persona=Decimal('1'),
    )

    nivel = CatalogoEvento.objects.create(
        tipo=CatalogoEvento.TIPO_LICOR, codigo='nacional', nombre='Nacional')
    botella = _producto('Botella nacional', '400.00')
    CatalogoEventoProducto.objects.create(
        opcion=nivel, producto=botella, cantidad_por_persona=Decimal('0.1'),
    )

    combo = CatalogoEvento.objects.create(
        tipo=CatalogoEvento.TIPO_TAQUIZA, codigo='combo_1', nombre='Pastor y Asado')
    pastor = _producto('Taco de pastor', '12.00')
    CatalogoEventoProducto.objects.create(
        opcion=combo, producto=pastor, cantidad_por_persona=Decimal('5'),
    )

    bolis = _producto('Carrito de bolis', '40.00')
    extra = CatalogoEvento.objects.create(
        tipo=CatalogoEvento.TIPO_EXTRA, codigo='carrito_bolis', nombre='Carrito de bolis')
    CatalogoEventoProducto.objects.create(
        opcion=extra, producto=bolis, cantidad_por_persona=Decimal('1'),
    )

    return {
        'base': base, 'hora_extra': hora_extra,
        'esencial': esencial, 'qkt': qkt,
        'refresco': refresco, 'mesero': mesero,
        'mobiliario': mobiliario, 'silla': silla,
        'nivel': nivel, 'botella': botella,
        'combo': combo, 'pastor': pastor,
        'extra': extra, 'bolis': bolis,
    }


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


class LineasEventoTest(TestCase):
    """Qué productos y qué cantidades produce cada selección."""

    def setUp(self):
        cache.clear()
        self.cat = _catalogo_eventos()

    def _seleccion(self, **extra):
        base = {
            'modalidad': MODALIDAD_PAQUETE,
            'paquete': self.cat['qkt'],
            'tipo_mobiliario': self.cat['mobiliario'],
            'incluir_licores': False,
            'niveles_licor': [],
            'combo_taquiza': self.cat['combo'],
            'extras': [],
        }
        base.update(extra)
        return base

    def _por_producto(self, lineas):
        return {prod.nombre: cantidad for prod, cantidad, _ in lineas}

    def test_arrendamiento_no_agrega_ninguna_linea_de_paquete(self):
        lineas = lineas_evento(
            modalidad=MODALIDAD_ARRENDAMIENTO, paquete=None, tipo_mobiliario=None,
            incluir_licores=False, niveles_licor=[], combo_taquiza=None,
            extras=[], num_personas=80,
        )
        self.assertEqual(lineas, [])

    def test_qkt_compone_incluidos_mobiliario_y_taquiza(self):
        cantidades = self._por_producto(lineas_evento(num_personas=80, **self._seleccion()))
        self.assertEqual(cantidades['Refresco 2L'], Decimal('80'))
        self.assertEqual(cantidades['Mesero por evento'], Decimal('4'))  # 80 / 20
        self.assertEqual(cantidades['Silla Tiffany'], Decimal('80'))
        self.assertEqual(cantidades['Taco de pastor'], Decimal('400'))  # 5 por persona
        self.assertNotIn('Botella nacional', cantidades)

    def test_los_licores_solo_entran_si_el_cliente_los_activo(self):
        sin = self._por_producto(lineas_evento(num_personas=80, **self._seleccion()))
        self.assertNotIn('Botella nacional', sin)

        con = self._por_producto(lineas_evento(num_personas=80, **self._seleccion(
            incluir_licores=True, niveles_licor=[self.cat['nivel']],
        )))
        self.assertEqual(con['Botella nacional'], Decimal('8'))  # 0.1 × 80

    def test_un_nivel_de_licor_sin_activar_el_toggle_no_se_cobra(self):
        # El modelo ya rechaza esa combinación; aquí se comprueba que aunque
        # llegara, la línea no se compone.
        cantidades = self._por_producto(lineas_evento(num_personas=80, **self._seleccion(
            incluir_licores=False, niveles_licor=[self.cat['nivel']],
        )))
        self.assertNotIn('Botella nacional', cantidades)

    def test_varios_niveles_de_licor_se_suman_sin_ser_excluyentes(self):
        # Cerveza + Nacional + Premium: el cliente puede combinarlos.
        premium = CatalogoEvento.objects.create(
            tipo=CatalogoEvento.TIPO_LICOR, codigo='premium', nombre='Premium')
        botella_premium = _producto('Botella premium', '700.00')
        CatalogoEventoProducto.objects.create(
            opcion=premium, producto=botella_premium, cantidad_por_persona=Decimal('0.1'),
        )
        cantidades = self._por_producto(lineas_evento(num_personas=80, **self._seleccion(
            incluir_licores=True, niveles_licor=[self.cat['nivel'], premium],
        )))
        self.assertEqual(cantidades['Botella nacional'], Decimal('8'))
        self.assertEqual(cantidades['Botella premium'], Decimal('8'))

    def test_esencial_solo_agrega_su_mobiliario(self):
        cantidades = self._por_producto(lineas_evento(num_personas=80, **self._seleccion(
            paquete=self.cat['esencial'], combo_taquiza=None,
        )))
        self.assertEqual(list(cantidades), ['Silla Tiffany'])

    def test_los_extras_se_suman_con_su_propia_cantidad(self):
        cantidades = self._por_producto(lineas_evento(num_personas=80, **self._seleccion(
            extras=[self.cat['extra']],
        )))
        self.assertEqual(cantidades['Carrito de bolis'], Decimal('80'))

    def test_los_conceptos_son_genericos_y_no_llevan_el_nombre_del_paquete(self):
        descripciones = [desc for _, _, desc in lineas_evento(
            num_personas=80, **self._seleccion(
                incluir_licores=True, niveles_licor=[self.cat['nivel']],
            ))]
        self.assertIn('Mobiliario Rústico — Silla Tiffany', descripciones)
        self.assertIn('Licor Nacional — Botella nacional', descripciones)
        self.assertIn('Taquiza Pastor y Asado — Taco de pastor', descripciones)
        self.assertFalse(any('QKT' in d for d in descripciones))

    def test_una_opcion_sin_productos_asignados_no_rompe_ni_inventa_lineas(self):
        vacio = CatalogoEvento.objects.create(
            tipo=CatalogoEvento.TIPO_MOBILIARIO, codigo='vacio', nombre='Sin configurar')
        cantidades = self._por_producto(lineas_evento(num_personas=80, **self._seleccion(
            tipo_mobiliario=vacio,
        )))
        self.assertNotIn('Silla Tiffany', cantidades)
        self.assertIn('Taco de pastor', cantidades)

    def test_una_asignacion_desactivada_deja_de_cobrarse(self):
        self.cat['mobiliario'].productos.update(activo=False)
        cantidades = self._por_producto(lineas_evento(num_personas=80, **self._seleccion()))
        self.assertNotIn('Silla Tiffany', cantidades)


class LineasCotizadorConCatalogoTest(TestCase):
    """El catálogo cerrado convive con el camino de siempre sin pisarlo."""

    def setUp(self):
        cache.clear()
        self.cat = _catalogo_eventos()
        self.extra_libre = _producto('Extra viejo del catálogo abierto', '500.00',
                                     visible_cotizador=True, cotizador_evento=True)

    def _seleccion(self, **extra):
        datos = {
            'modalidad': MODALIDAD_PAQUETE,
            'paquete_evento_id': self.cat['qkt'].id,
            'mobiliario_id': self.cat['mobiliario'].id,
            'combo_taquiza_id': self.cat['combo'].id,
        }
        datos.update(extra)
        return resolver_seleccion(datos)

    def _nombres(self, lineas):
        return [prod.nombre for prod, _, _ in lineas]

    def test_el_arrendamiento_base_va_en_las_dos_modalidades(self):
        solo_arrendamiento = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[],
            num_personas=80, horas_evento=6,
            seleccion_evento=resolver_seleccion({'modalidad': MODALIDAD_ARRENDAMIENTO}),
        )
        self.assertEqual(self._nombres(solo_arrendamiento), ['Arrendamiento de la Quinta'])

        con_paquete = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[],
            num_personas=80, horas_evento=6, seleccion_evento=self._seleccion(),
        )
        self.assertEqual(self._nombres(con_paquete)[0], 'Arrendamiento de la Quinta')
        self.assertIn('Silla Tiffany', self._nombres(con_paquete))

    def test_las_horas_extra_siguen_cobrandose_igual(self):
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[],
            num_personas=80, horas_evento=8, seleccion_evento=self._seleccion(),
        )
        horas = [cant for prod, cant, _ in lineas if prod.nombre == 'Hora Extra de Arrendamiento']
        self.assertEqual(horas, [2])

    def test_el_catalogo_abierto_se_ignora_cuando_manda_el_cerrado(self):
        # Admitir las dos vías a la vez dejaría cobrar el mismo concepto dos
        # veces: un `extras_ids` heredado no debe colarse.
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[self.extra_libre.id],
            num_personas=80, horas_evento=6, seleccion_evento=self._seleccion(),
        )
        self.assertNotIn('Extra viejo del catálogo abierto', self._nombres(lineas))

    def test_sin_seleccion_el_camino_de_siempre_queda_intacto(self):
        # Regresión: mientras el frontend nuevo no exista, Evento sigue
        # comportándose exactamente como antes de esta etapa.
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[self.extra_libre.id],
            num_personas=80, horas_evento=6,
        )
        self.assertEqual(
            self._nombres(lineas),
            ['Arrendamiento de la Quinta', 'Extra viejo del catálogo abierto'],
        )

    def test_la_pasadia_no_se_ve_afectada_por_una_seleccion_de_evento(self):
        pasadia = _producto('Paquete Pasadía QKT', '1500.00',
                            visible_cotizador=True, cotizador_pasadia=True,
                            rol_cotizador='BASE_PASADIA_BASICO')
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=15, horas_evento=8, seleccion_evento=self._seleccion(),
        )
        self.assertEqual(self._nombres(lineas), [pasadia.nombre])


@wa_settings()
class EnvioConCatalogoEventosTest(TestCase):
    """De punta a punta: el POST público crea la cotización y su configuración."""

    def setUp(self):
        limpiar_cache_emisor()
        cache.clear()
        self.cat = _catalogo_eventos()

    def _enviar(self, **extra):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value='5215555550003'):
            return self.client.post(
                reverse('cotizador_enviar'),
                data=json.dumps(_payload(**extra)),
                content_type='application/json',
            )

    def _seleccion_qkt(self, **extra):
        datos = {
            'modalidad': MODALIDAD_PAQUETE,
            'paquete_evento_id': self.cat['qkt'].id,
            'mobiliario_id': self.cat['mobiliario'].id,
            'combo_taquiza_id': self.cat['combo'].id,
        }
        datos.update(extra)
        return datos

    def test_crea_la_cotizacion_con_su_configuracion(self):
        respuesta = self._enviar(**self._seleccion_qkt())
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        config = ConfiguracionEventoCotizacion.objects.get(cotizacion=cotizacion)
        self.assertEqual(config.modalidad, MODALIDAD_PAQUETE)
        self.assertEqual(config.paquete, self.cat['qkt'])
        self.assertEqual(config.tipo_mobiliario, self.cat['mobiliario'])
        self.assertEqual(config.combo_taquiza, self.cat['combo'])
        self.assertFalse(config.incluir_licores)

        nombres = {item.producto.nombre for item in cotizacion.items.all()}
        self.assertIn('Arrendamiento de la Quinta', nombres)
        self.assertIn('Silla Tiffany', nombres)
        self.assertIn('Taco de pastor', nombres)

    def test_los_extras_elegidos_quedan_guardados(self):
        self._enviar(**self._seleccion_qkt(extras_evento_ids=[self.cat['extra'].id]))
        config = ConfiguracionEventoCotizacion.objects.latest('id')
        self.assertEqual(list(config.extras.all()), [self.cat['extra']])

    def test_las_bebidas_elegidas_quedan_guardadas_y_no_son_excluyentes(self):
        premium = CatalogoEvento.objects.create(
            tipo=CatalogoEvento.TIPO_LICOR, codigo='premium', nombre='Premium')
        respuesta = self._enviar(**self._seleccion_qkt(
            incluir_licores=True,
            niveles_licor_ids=[self.cat['nivel'].id, premium.id],
        ))
        self.assertEqual(respuesta.status_code, 200)
        config = ConfiguracionEventoCotizacion.objects.latest('id')
        self.assertTrue(config.incluir_licores)
        self.assertEqual(
            set(config.niveles_licor.values_list('id', flat=True)),
            {self.cat['nivel'].id, premium.id},
        )

    def test_mas_del_tope_de_personas_se_rechaza_sin_ruta_alterna(self):
        respuesta = self._enviar(personas='151', **self._seleccion_qkt())
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('150', respuesta.json()['errores'][0])
        self.assertFalse(ConfiguracionEventoCotizacion.objects.exists())

    def test_un_aforo_intermedio_sube_al_siguiente_tramo_de_diez(self):
        self._enviar(personas='57', **self._seleccion_qkt())
        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.num_personas, 60)

    def test_esencial_con_taquiza_se_rechaza_sin_crear_nada(self):
        respuesta = self._enviar(
            modalidad=MODALIDAD_PAQUETE,
            paquete_evento_id=self.cat['esencial'].id,
            mobiliario_id=self.cat['mobiliario'].id,
            combo_taquiza_id=self.cat['combo'].id,
        )
        self.assertEqual(respuesta.status_code, 400)
        # Ni cotización huérfana ni configuración a medias.
        self.assertFalse(Cotizacion.objects.exists())
        self.assertFalse(ConfiguracionEventoCotizacion.objects.exists())

    def test_licores_activados_sin_nivel_se_rechaza(self):
        respuesta = self._enviar(**self._seleccion_qkt(incluir_licores=True))
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Cotizacion.objects.exists())

    def test_qkt_sin_taquiza_se_rechaza(self):
        respuesta = self._enviar(
            modalidad=MODALIDAD_PAQUETE,
            paquete_evento_id=self.cat['qkt'].id,
            mobiliario_id=self.cat['mobiliario'].id,
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Cotizacion.objects.exists())

    def test_solo_arrendamiento_cobra_unicamente_el_espacio(self):
        respuesta = self._enviar(modalidad=MODALIDAD_ARRENDAMIENTO, personas='12')
        self.assertEqual(respuesta.status_code, 200)
        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.num_personas, 12)  # sin redondeo a decenas
        self.assertEqual(
            [item.producto.nombre for item in cotizacion.items.all()],
            ['Arrendamiento de la Quinta'],
        )

    def test_el_total_exhibido_coincide_con_el_que_se_cobra(self):
        # El invariante del art. 7 BIS: lo anunciado y lo cobrado son lo mismo.
        seleccion = self._seleccion_qkt(extras_evento_ids=[self.cat['extra'].id])
        exhibido = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'EVENTO', 'personas': '80', 'horas': '6',
            'modalidad': MODALIDAD_PAQUETE,
            'paquete_evento': self.cat['qkt'].id,
            'mobiliario': self.cat['mobiliario'].id,
            'combo_taquiza': self.cat['combo'].id,
            'extras_evento': str(self.cat['extra'].id),
        }).json()

        self._enviar(**seleccion)
        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(Decimal(exhibido['total']), cotizacion.precio_final)


class ApiTotalEventosTest(TestCase):
    """El total del servidor con el catálogo cerrado."""

    def setUp(self):
        cache.clear()
        self.cat = _catalogo_eventos()

    def _total(self, **params):
        base = {'servicio': 'EVENTO', 'personas': '80', 'horas': '6',
                'modalidad': MODALIDAD_PAQUETE}
        base.update(params)
        return self.client.get(reverse('api_total_cotizador'), base).json()

    def test_devuelve_el_aforo_realmente_cotizado_no_el_escrito(self):
        datos = self._total(personas='57', paquete_evento=self.cat['qkt'].id,
                            mobiliario=self.cat['mobiliario'].id,
                            combo_taquiza=self.cat['combo'].id)
        self.assertEqual(datos['personas'], 60)

    def test_el_aforo_nunca_se_exhibe_por_encima_del_tope(self):
        datos = self._total(personas='500', paquete_evento=self.cat['qkt'].id,
                            mobiliario=self.cat['mobiliario'].id,
                            combo_taquiza=self.cat['combo'].id)
        self.assertEqual(datos['personas'], 150)

    def test_los_conceptos_exhibidos_son_los_que_se_van_a_cobrar(self):
        datos = self._total(paquete_evento=self.cat['qkt'].id,
                            mobiliario=self.cat['mobiliario'].id,
                            combo_taquiza=self.cat['combo'].id)
        self.assertIn('Mobiliario Rústico — Silla Tiffany', datos['conceptos'])
        self.assertIn('Servicio de mesa — Mesero por evento', datos['conceptos'])

    def test_el_total_lleva_iva_incluido_una_sola_vez(self):
        # Solo arrendamiento: base 10,000 → 11,600 con IVA. Si el IVA se
        # aplicara por línea y luego otra vez al total, no cuadraría.
        datos = self._total(personas='50', modalidad=MODALIDAD_ARRENDAMIENTO)
        self.assertEqual(datos['total'], '11600.00')
        self.assertEqual(datos['leyenda'], 'Precios en MXN, IVA incluido')


class ApiCatalogoEventosTest(TestCase):
    """Lo que el navegador recibe para pintar las opciones."""

    def setUp(self):
        cache.clear()
        self.cat = _catalogo_eventos()

    def _catalogo(self, **params):
        return self.client.get(reverse('api_catalogo_eventos'), params).json()

    def test_lista_las_opciones_activas_con_precio_con_iva(self):
        datos = self._catalogo(personas=80)
        self.assertEqual([p['codigo'] for p in datos['paquetes']], ['esencial', 'qkt'])
        mobiliario = datos['mobiliario'][0]
        # 80 sillas × $25.00 = $2,000.00 + IVA = $2,320.00
        self.assertEqual(mobiliario['precio'], '2320.00')

    def test_el_precio_escala_con_el_aforo(self):
        cincuenta = self._catalogo(personas=50)['mobiliario'][0]['precio']
        cien = self._catalogo(personas=100)['mobiliario'][0]['precio']
        self.assertEqual(cincuenta, '1450.00')
        self.assertEqual(cien, '2900.00')

    def test_una_opcion_sin_productos_se_marca_para_no_ofrecerla(self):
        CatalogoEvento.objects.create(tipo=CatalogoEvento.TIPO_MOBILIARIO,
            codigo='vacio', nombre='Sin configurar', orden=9)
        opciones = {m['codigo']: m['sin_configurar'] for m in self._catalogo(personas=80)['mobiliario']}
        self.assertFalse(opciones['rustico'])
        self.assertTrue(opciones['vacio'])

    def test_un_paquete_sin_incluidos_no_se_marca_como_sin_configurar(self):
        # Esencial legítimamente no incluye nada automático: su contenido es
        # el mobiliario, que se elige aparte.
        paquetes = {p['codigo']: p['sin_configurar'] for p in self._catalogo(personas=80)['paquetes']}
        self.assertFalse(paquetes['esencial'])

    def test_cada_paquete_declara_que_pasos_muestra(self):
        paquetes = {p['codigo']: p for p in self._catalogo(personas=80)['paquetes']}
        self.assertFalse(paquetes['esencial']['requiere_taquiza'])
        self.assertFalse(paquetes['esencial']['permite_extras'])
        self.assertTrue(paquetes['qkt']['requiere_taquiza'])
        self.assertTrue(paquetes['qkt']['permite_licores_opcional'])

    def test_una_opcion_desactivada_no_se_ofrece(self):
        self.cat['nivel'].activo = False
        self.cat['nivel'].save()
        self.assertEqual(self._catalogo(personas=80)['niveles_licor'], [])

    def test_expone_los_aforos_cotizables_del_paquete(self):
        datos = self._catalogo(personas=80)
        self.assertEqual(datos['aforos_paquete'],
                         [50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150])
        self.assertEqual(datos['max_personas'], 150)

    def test_un_aforo_fuera_de_rango_se_acota_en_vez_de_reventar(self):
        self.assertEqual(self._catalogo(personas='abc')['personas'], 50)
        self.assertEqual(self._catalogo(personas=999)['personas'], 150)

    def test_sin_plano_de_zonas_cargado_no_se_ofrece_ninguno(self):
        # El frontend se salta el bloque; vale más no mostrar nada que un hueco.
        self.assertIsNone(self._catalogo(personas=80)['imagen_zonas_restringidas'])

    @override_settings(STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    })
    def test_el_plano_de_zonas_cargado_llega_al_cotizador(self):
        ImagenLanding.objects.create(
            seccion='ZONAS_RESTRINGIDAS',
            imagen=SimpleUploadedFile('zonas.png', _PNG_MINIMO, content_type='image/png'),
            alt_text='Áreas no incluidas',
        )
        datos = self._catalogo(personas=80)['imagen_zonas_restringidas']
        self.assertIn('zonas', datos['url'])
        self.assertEqual(datos['alt'], 'Áreas no incluidas')

    @override_settings(STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    })
    def test_un_plano_desactivado_deja_de_mostrarse(self):
        ImagenLanding.objects.create(
            seccion='ZONAS_RESTRINGIDAS', activo=False,
            imagen=SimpleUploadedFile('zonas.png', _PNG_MINIMO, content_type='image/png'),
        )
        self.assertIsNone(self._catalogo(personas=80)['imagen_zonas_restringidas'])
