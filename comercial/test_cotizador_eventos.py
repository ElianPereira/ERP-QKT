"""
Tests de la capa de asignación del cotizador de Eventos.

Cubre las dos mitades que el catálogo nuevo tiene que garantizar:
  1. La aritmética de cantidades (`reglas_eventos`), que decide cuántas
     unidades de cada producto entran según el aforo.
  2. Las reglas de combinación (`ConfiguracionEventoCotizacion.clean`), que
     rechazan cualquier selección que el cotizador no debería haber armado —
     también cuando la captura viene del admin y no del formulario público.

Ejecutar: python manage.py test comercial.test_cotizador_eventos --verbosity=2
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from comercial.models import (
    CatalogoEvento,
    CatalogoEventoProducto,
    Cliente,
    ConfiguracionEventoCotizacion,
    Cotizacion,
    Producto,
)
from comercial.reglas_eventos import (
    MAX_PERSONAS_EVENTO,
    MODALIDAD_ARRENDAMIENTO,
    MODALIDAD_PAQUETE,
    personas_validas_paquete,
    redondear_personas_paquete,
    resolver_cantidad,
)


class ReglasAforoTest(TestCase):
    """El aforo cotizable y el redondeo al tramo de paquete."""

    def test_los_unicos_aforos_de_paquete_son_de_50_a_150_en_pasos_de_10(self):
        self.assertEqual(personas_validas_paquete(),
                         [50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150])

    def test_49_personas_sube_al_minimo_de_paquete(self):
        self.assertEqual(redondear_personas_paquete(49), 50)

    def test_50_personas_se_queda_en_50(self):
        self.assertEqual(redondear_personas_paquete(50), 50)

    def test_55_personas_sube_a_60_no_baja_a_50(self):
        # Hacia abajo dejaría 5 invitados sin mobiliario.
        self.assertEqual(redondear_personas_paquete(55), 60)

    def test_51_personas_ya_sube_al_siguiente_tramo(self):
        self.assertEqual(redondear_personas_paquete(51), 60)

    def test_150_personas_se_queda_en_el_tope(self):
        self.assertEqual(redondear_personas_paquete(150), MAX_PERSONAS_EVENTO)

    def test_el_redondeo_nunca_pasa_del_tope_duro(self):
        # 151 no es cotizable; la validación lo rechaza aparte. El redondeo,
        # por si acaso, tampoco puede inventar un tramo de 160.
        self.assertEqual(redondear_personas_paquete(151), MAX_PERSONAS_EVENTO)


class ResolverCantidadTest(TestCase):
    """Cuántas unidades entran de cada producto asignado."""

    def test_cantidad_fija_ignora_el_aforo(self):
        self.assertEqual(
            resolver_cantidad(cantidad_por_persona=None, cantidad_fija=Decimal('1'),
                              num_personas=100),
            Decimal('1'),
        )

    def test_cantidad_por_persona_multiplica_por_el_aforo(self):
        self.assertEqual(
            resolver_cantidad(cantidad_por_persona=Decimal('8'), cantidad_fija=None,
                              num_personas=60),
            Decimal('480'),
        )

    def test_un_mesero_cada_20_invitados_da_5_meseros_para_100(self):
        self.assertEqual(
            resolver_cantidad(cantidad_por_persona=Decimal('0.05'), cantidad_fija=None,
                              num_personas=100),
            Decimal('5'),
        )

    def test_una_fraccion_sobrante_redondea_hacia_arriba_no_hacia_el_par(self):
        # 0.05 × 70 = 3.5 meseros → 4. Quedarse en 3 deja invitados sin servicio;
        # ROUND_HALF_UP daría 4 aquí pero ROUND_HALF_EVEN daría 4 también, así
        # que el caso que de verdad los separa es 3.2 → 4 (abajo).
        self.assertEqual(
            resolver_cantidad(cantidad_por_persona=Decimal('0.05'), cantidad_fija=None,
                              num_personas=70),
            Decimal('4'),
        )

    def test_cualquier_fraccion_por_pequena_que_sea_sube_una_unidad_entera(self):
        # 0.04 × 80 = 3.2 → 4, no 3: media silla no existe.
        self.assertEqual(
            resolver_cantidad(cantidad_por_persona=Decimal('0.04'), cantidad_fija=None,
                              num_personas=80),
            Decimal('4'),
        )


class AsignacionProductoTest(TestCase):
    """Una asignación lleva exactamente una de las dos cantidades."""

    @classmethod
    def setUpTestData(cls):
        cls.producto = Producto.objects.create(nombre='Silla Tiffany',
                                               precio_venta_fijo=Decimal('25.00'))
        cls.mobiliario = CatalogoEvento.objects.create(tipo=CatalogoEvento.TIPO_MOBILIARIO, codigo='rustico', nombre='Rústico')

    def test_rechaza_una_asignacion_sin_ninguna_cantidad(self):
        fila = CatalogoEventoProducto(opcion=self.mobiliario, producto=self.producto)
        with self.assertRaises(ValidationError):
            fila.full_clean()

    def test_rechaza_una_asignacion_con_las_dos_cantidades(self):
        fila = CatalogoEventoProducto(
            opcion=self.mobiliario, producto=self.producto,
            cantidad_por_persona=Decimal('1'), cantidad_fija=Decimal('10'),
        )
        with self.assertRaises(ValidationError):
            fila.full_clean()

    def test_rechaza_una_cantidad_en_cero(self):
        fila = CatalogoEventoProducto(
            opcion=self.mobiliario, producto=self.producto,
            cantidad_por_persona=Decimal('0'),
        )
        with self.assertRaises(ValidationError):
            fila.full_clean()

    def test_acepta_solo_cantidad_por_persona(self):
        fila = CatalogoEventoProducto(
            opcion=self.mobiliario, producto=self.producto,
            cantidad_por_persona=Decimal('1'),
        )
        fila.full_clean()  # no lanza

    def test_un_extra_usa_la_misma_validacion_de_cantidad(self):
        # Un extra ya no es cabecera + asignación en la misma fila: es una
        # opción normal con sus productos, así que hereda la validación por el
        # mismo camino que el resto.
        extra = CatalogoEvento.objects.create(
            tipo=CatalogoEvento.TIPO_EXTRA, codigo='brincolin', nombre='Brincolín')
        fila = CatalogoEventoProducto(opcion=extra, producto=self.producto)
        with self.assertRaises(ValidationError):
            fila.full_clean()

        fila.cantidad_fija = Decimal('1')
        fila.full_clean()  # no lanza

    def test_las_banderas_de_paquete_no_se_pueden_poner_en_otro_tipo(self):
        # El precio de haber fusionado los cinco modelos: hay campos que solo
        # aplican a un tipo, y el modelo lo dice en vez de dejarlos ambiguos.
        opcion = CatalogoEvento(tipo=CatalogoEvento.TIPO_LICOR, codigo='x', nombre='X',
                                requiere_taquiza=True)
        with self.assertRaises(ValidationError):
            opcion.full_clean()

    def test_la_capacidad_simultanea_solo_aplica_a_un_extra(self):
        opcion = CatalogoEvento(tipo=CatalogoEvento.TIPO_MOBILIARIO, codigo='y', nombre='Y',
                                capacidad_maxima_simultanea=5)
        with self.assertRaises(ValidationError):
            opcion.full_clean()

    def test_borrar_un_tier_no_puede_arrastrar_el_producto_del_catalogo(self):
        # PROTECT: el precio de venta vive en Producto y lo comparten otras
        # cotizaciones ya cobradas.
        CatalogoEventoProducto.objects.create(
            opcion=self.mobiliario, producto=self.producto,
            cantidad_por_persona=Decimal('1'),
        )
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.producto.delete()


class ConfiguracionEventoValidacionTest(TestCase):
    """Las combinaciones que el backend tiene que rechazar, venga de donde venga."""

    @classmethod
    def setUpTestData(cls):
        cls.cliente = Cliente.objects.create(nombre='Ana Ruiz', telefono='9995550001')
        cls.esencial = CatalogoEvento.objects.create(
            tipo=CatalogoEvento.TIPO_PAQUETE, codigo='esencial', nombre='Esencial',
            requiere_mobiliario=True, permite_licores_opcional=False,
            requiere_taquiza=False, permite_extras=False,
        )
        cls.qkt = CatalogoEvento.objects.create(
            tipo=CatalogoEvento.TIPO_PAQUETE, codigo='qkt', nombre='QKT',
            requiere_mobiliario=True, permite_licores_opcional=True,
            requiere_taquiza=True, permite_extras=True,
        )
        cls.mobiliario = CatalogoEvento.objects.create(tipo=CatalogoEvento.TIPO_MOBILIARIO, codigo='rustico', nombre='Rústico')
        cls.nivel = CatalogoEvento.objects.create(tipo=CatalogoEvento.TIPO_LICOR, codigo='nacional', nombre='Nacional')
        cls.combo = CatalogoEvento.objects.create(tipo=CatalogoEvento.TIPO_TAQUIZA, codigo='combo_1', nombre='Pastor y Asado')
        producto_bolis = Producto.objects.create(nombre='Carrito de bolis',
                                                 precio_venta_fijo=Decimal('40.00'))
        cls.extra = CatalogoEvento.objects.create(
            tipo=CatalogoEvento.TIPO_EXTRA, codigo='carrito_bolis',
            nombre='Carrito de bolis',
        )
        CatalogoEventoProducto.objects.create(
            opcion=cls.extra, producto=producto_bolis, cantidad_por_persona=Decimal('1'),
        )

    def _cotizacion(self, personas):
        return Cotizacion.objects.create(
            cliente=self.cliente, tipo_servicio='EVENTO',
            nombre_evento='Boda de prueba',
            fecha_evento=timezone.localdate() + timedelta(days=60),
            num_personas=personas,
        )

    def _config(self, personas, **campos):
        campos.setdefault('modalidad', MODALIDAD_PAQUETE)
        return ConfiguracionEventoCotizacion(cotizacion=self._cotizacion(personas), **campos)

    def _paquete_completo(self, personas, **extra):
        base = {'paquete': self.qkt, 'tipo_mobiliario': self.mobiliario,
                'combo_taquiza': self.combo}
        base.update(extra)
        return self._config(personas, **base)

    # ── Aforo ──────────────────────────────────────────────────────────────
    def test_paquete_con_49_personas_es_rechazado(self):
        with self.assertRaises(ValidationError):
            self._paquete_completo(49).full_clean()

    def test_paquete_con_50_personas_es_aceptado(self):
        self._paquete_completo(50).full_clean()

    def test_paquete_con_55_personas_es_rechazado_por_no_ser_multiplo_de_10(self):
        # El redondeo lo hace el cotizador ANTES de llegar aquí; si un aforo sin
        # redondear llega al modelo es que alguien se saltó ese paso.
        with self.assertRaises(ValidationError):
            self._paquete_completo(55).full_clean()

    def test_paquete_con_150_personas_es_aceptado(self):
        self._paquete_completo(150).full_clean()

    def test_paquete_con_151_personas_es_rechazado(self):
        with self.assertRaises(ValidationError):
            self._paquete_completo(151).full_clean()

    def test_arrendamiento_con_150_personas_es_aceptado(self):
        self._config(150, modalidad=MODALIDAD_ARRENDAMIENTO).full_clean()

    def test_arrendamiento_con_151_personas_es_rechazado(self):
        with self.assertRaises(ValidationError):
            self._config(151, modalidad=MODALIDAD_ARRENDAMIENTO).full_clean()

    def test_arrendamiento_sin_minimo_acepta_1_persona(self):
        self._config(1, modalidad=MODALIDAD_ARRENDAMIENTO).full_clean()

    def test_arrendamiento_con_0_personas_es_rechazado(self):
        with self.assertRaises(ValidationError):
            self._config(0, modalidad=MODALIDAD_ARRENDAMIENTO).full_clean()

    # ── Licores ────────────────────────────────────────────────────────────
    # `niveles_licor` es M2M, igual que `extras`: `clean()`/`full_clean()` no
    # puede leerlo antes del primer `save()`, así que se valida aparte con
    # `validar_niveles_licor()` — mismo patrón que ya usan las pruebas de
    # `validar_extras` más abajo.
    def test_licores_activados_sin_nivel_es_rechazado(self):
        config = self._paquete_completo(80, incluir_licores=True)
        config.full_clean()
        config.save()
        with self.assertRaises(ValidationError) as ctx:
            config.validar_niveles_licor([])
        self.assertIn('niveles_licor', ctx.exception.message_dict)

    def test_nivel_de_licor_sin_activar_licores_es_rechazado(self):
        config = self._paquete_completo(80, incluir_licores=False)
        config.full_clean()
        config.save()
        with self.assertRaises(ValidationError) as ctx:
            config.validar_niveles_licor([self.nivel])
        self.assertIn('niveles_licor', ctx.exception.message_dict)

    def test_licores_activados_con_nivel_es_aceptado(self):
        config = self._paquete_completo(80, incluir_licores=True)
        config.full_clean()
        config.save()
        config.validar_niveles_licor([self.nivel])  # no lanza

    def test_licores_activados_con_varios_niveles_es_aceptado(self):
        # Cerveza + Nacional + Premium: no son excluyentes entre sí.
        otro_nivel = CatalogoEvento.objects.create(
            tipo=CatalogoEvento.TIPO_LICOR, codigo='premium', nombre='Premium')
        config = self._paquete_completo(80, incluir_licores=True)
        config.full_clean()
        config.save()
        config.validar_niveles_licor([self.nivel, otro_nivel])  # no lanza

    def test_un_extra_ajeno_en_niveles_licor_es_rechazado(self):
        # Mismo motivo que `validar_extras`: el M2M acepta cualquier fila del
        # catálogo, así que el tipo se verifica a mano.
        config = self._paquete_completo(80, incluir_licores=True)
        config.full_clean()
        config.save()
        with self.assertRaises(ValidationError) as ctx:
            config.validar_niveles_licor([self.extra])
        self.assertIn('niveles_licor', ctx.exception.message_dict)

    # ── Esencial no ofrece lo del QKT ──────────────────────────────────────
    def test_esencial_con_licores_es_rechazado(self):
        config = self._config(80, paquete=self.esencial, tipo_mobiliario=self.mobiliario,
                              incluir_licores=True)
        with self.assertRaises(ValidationError) as ctx:
            config.full_clean()
        self.assertIn('incluir_licores', ctx.exception.message_dict)

    def test_esencial_con_taquiza_es_rechazado(self):
        config = self._config(80, paquete=self.esencial, tipo_mobiliario=self.mobiliario,
                              combo_taquiza=self.combo)
        with self.assertRaises(ValidationError) as ctx:
            config.full_clean()
        self.assertIn('combo_taquiza', ctx.exception.message_dict)

    def test_esencial_con_extras_es_rechazado(self):
        config = self._config(80, paquete=self.esencial, tipo_mobiliario=self.mobiliario)
        config.full_clean()
        config.save()
        with self.assertRaises(ValidationError) as ctx:
            config.validar_extras([self.extra])
        self.assertIn('extras', ctx.exception.message_dict)

    def test_qkt_con_extras_es_aceptado(self):
        config = self._paquete_completo(80)
        config.full_clean()
        config.save()
        config.validar_extras([self.extra])  # no lanza

    def test_esencial_sin_nada_extra_es_aceptado(self):
        self._config(80, paquete=self.esencial, tipo_mobiliario=self.mobiliario).full_clean()

    # ── Obligatorios de cada paquete ───────────────────────────────────────
    def test_paquete_sin_mobiliario_es_rechazado(self):
        config = self._config(80, paquete=self.qkt, combo_taquiza=self.combo)
        with self.assertRaises(ValidationError) as ctx:
            config.full_clean()
        self.assertIn('tipo_mobiliario', ctx.exception.message_dict)

    def test_qkt_sin_taquiza_es_rechazado(self):
        config = self._config(80, paquete=self.qkt, tipo_mobiliario=self.mobiliario)
        with self.assertRaises(ValidationError) as ctx:
            config.full_clean()
        self.assertIn('combo_taquiza', ctx.exception.message_dict)

    def test_modalidad_paquete_sin_paquete_es_rechazada(self):
        config = self._config(80)
        with self.assertRaises(ValidationError) as ctx:
            config.full_clean()
        self.assertIn('paquete', ctx.exception.message_dict)

    # ── Arrendamiento no arrastra nada del paquete ─────────────────────────
    def test_arrendamiento_con_paquete_es_rechazado(self):
        config = self._config(80, modalidad=MODALIDAD_ARRENDAMIENTO, paquete=self.qkt)
        with self.assertRaises(ValidationError) as ctx:
            config.full_clean()
        self.assertIn('paquete', ctx.exception.message_dict)

    def test_arrendamiento_con_mobiliario_es_rechazado(self):
        config = self._config(80, modalidad=MODALIDAD_ARRENDAMIENTO,
                              tipo_mobiliario=self.mobiliario)
        with self.assertRaises(ValidationError) as ctx:
            config.full_clean()
        self.assertIn('tipo_mobiliario', ctx.exception.message_dict)

    def test_arrendamiento_con_taquiza_es_rechazado(self):
        config = self._config(80, modalidad=MODALIDAD_ARRENDAMIENTO, combo_taquiza=self.combo)
        with self.assertRaises(ValidationError) as ctx:
            config.full_clean()
        self.assertIn('combo_taquiza', ctx.exception.message_dict)

    def test_arrendamiento_con_extras_es_rechazado(self):
        config = self._config(80, modalidad=MODALIDAD_ARRENDAMIENTO)
        config.full_clean()
        config.save()
        with self.assertRaises(ValidationError):
            config.validar_extras([self.extra])


class AislamientoPorTipoServicioTest(TestCase):
    """Una configuración de Evento no aparece en las consultas de otro servicio."""

    def test_la_configuracion_solo_cuelga_de_cotizaciones_de_evento(self):
        cliente = Cliente.objects.create(nombre='Ana Ruiz', telefono='9995550001')
        fecha = timezone.localdate() + timedelta(days=60)
        evento = Cotizacion.objects.create(
            cliente=cliente, tipo_servicio='EVENTO', nombre_evento='Boda',
            fecha_evento=fecha, num_personas=80,
        )
        pasadia = Cotizacion.objects.create(
            cliente=cliente, tipo_servicio='PASADIA', nombre_evento='Pasadía',
            fecha_evento=fecha + timedelta(days=1), num_personas=20,
        )
        paquete = CatalogoEvento.objects.create(tipo=CatalogoEvento.TIPO_PAQUETE, codigo='qkt', nombre='QKT',
                                               requiere_taquiza=False)
        mobiliario = CatalogoEvento.objects.create(tipo=CatalogoEvento.TIPO_MOBILIARIO, codigo='rustico', nombre='Rústico')
        ConfiguracionEventoCotizacion.objects.create(
            cotizacion=evento, modalidad=MODALIDAD_PAQUETE,
            paquete=paquete, tipo_mobiliario=mobiliario,
        )

        self.assertEqual(
            ConfiguracionEventoCotizacion.objects.filter(
                cotizacion__tipo_servicio='EVENTO').count(),
            1,
        )
        self.assertEqual(
            ConfiguracionEventoCotizacion.objects.filter(
                cotizacion__tipo_servicio='PASADIA').count(),
            0,
        )
        self.assertIsNone(getattr(pasadia, 'config_evento', None))


class ComboTaquizaTest(TestCase):
    """El combo es cerrado: sus proteínas son filas hijas, no dos FK fijas."""

    def test_un_combo_admite_dos_proteinas_con_su_propia_cantidad(self):
        pastor = Producto.objects.create(nombre='Taco de pastor',
                                         precio_venta_fijo=Decimal('12.00'))
        asado = Producto.objects.create(nombre='Taco de asado',
                                        precio_venta_fijo=Decimal('15.00'))
        combo = CatalogoEvento.objects.create(tipo=CatalogoEvento.TIPO_TAQUIZA, codigo='combo_1', nombre='Pastor y Asado')
        CatalogoEventoProducto.objects.create(opcion=combo, producto=pastor,
                                            cantidad_por_persona=Decimal('5'))
        CatalogoEventoProducto.objects.create(opcion=combo, producto=asado,
                                            cantidad_por_persona=Decimal('3'))

        cantidades = {p.producto.nombre: p.cantidad_para(80) for p in combo.productos.all()}
        self.assertEqual(cantidades['Taco de pastor'], Decimal('400'))
        self.assertEqual(cantidades['Taco de asado'], Decimal('240'))
