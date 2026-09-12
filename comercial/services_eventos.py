"""Composición de las líneas de una cotización de Evento.

Traduce la selección del cliente (paquete, mobiliario, licor, taquiza, extras)
a la misma tupla `(producto, cantidad, descripcion)` que ya consume
`_lineas_cotizador`, para que `cotizador_enviar` cree los `ItemCotizacion`
reales y `api_total_cotizador` exhiba el total con exactamente las mismas
líneas. Una sola definición de qué se cobra, igual que en el resto del
cotizador.

Ningún precio se calcula aquí: cada línea sale del `Producto` asignado en el
admin, y el IVA lo aplica `Cotizacion.calcular_totales()` una sola vez sobre el
subtotal completo.
"""

import logging
from decimal import Decimal

from django.db.models import Prefetch

from core_erp import impuestos

from .models import CatalogoEvento, CatalogoEventoProducto
from .reglas_eventos import MODALIDAD_ARRENDAMIENTO, MODALIDAD_PAQUETE

logger = logging.getLogger(__name__)

# Nombres de concepto genéricos, reutilizados entre Esencial y QKT: la línea
# dice qué servicio es, no de qué paquete vino. El desglose de qué productos lo
# componen queda visible para el cliente, pero el concepto no cambia de nombre
# según el paquete que haya elegido.
CONCEPTO_MOBILIARIO = "Mobiliario"
CONCEPTO_LICOR = "Licor"
CONCEPTO_TAQUIZA = "Taquiza"


def _id_valido(valor):
    return str(valor).isdigit() if valor is not None else False


def resolver_seleccion(datos):
    """Convierte los ids que manda el navegador en objetos del catálogo.

    Un id inexistente, inactivo o de otro tipo se resuelve a `None` en vez de
    reventar: la combinación resultante la juzga
    `ConfiguracionEventoCotizacion.clean()`, que es la fuente única de las
    reglas y da un mensaje de negocio en vez de un 500.
    """
    modalidad = str(datos.get('modalidad') or '').upper()
    if modalidad not in (MODALIDAD_ARRENDAMIENTO, MODALIDAD_PAQUETE):
        modalidad = MODALIDAD_ARRENDAMIENTO

    def _uno(tipo, clave):
        # El filtro por `tipo` no es cosmético: las cinco selecciones salen de la
        # misma tabla, así que un id del tipo equivocado (a mano en el POST)
        # entraría como si fuera válido. Se resuelve a None y la combinación la
        # juzga `clean()`.
        valor = datos.get(clave)
        if not _id_valido(valor):
            return None
        return CatalogoEvento.objects.filter(
            id=int(valor), tipo=tipo, activo=True).first()

    extras_ids = [int(x) for x in (datos.get('extras_evento_ids') or []) if _id_valido(x)]

    return {
        'modalidad': modalidad,
        'paquete': _uno(CatalogoEvento.TIPO_PAQUETE, 'paquete_evento_id'),
        'tipo_mobiliario': _uno(CatalogoEvento.TIPO_MOBILIARIO, 'mobiliario_id'),
        'incluir_licores': bool(datos.get('incluir_licores')),
        'nivel_licor': _uno(CatalogoEvento.TIPO_LICOR, 'nivel_licor_id'),
        'combo_taquiza': _uno(CatalogoEvento.TIPO_TAQUIZA, 'combo_taquiza_id'),
        'extras': list(CatalogoEvento.objects.filter(
            id__in=extras_ids, tipo=CatalogoEvento.TIPO_EXTRA, activo=True)),
    }


def _lineas_de(asignaciones, num_personas, etiqueta):
    """Una línea por producto asignado, con el concepto genérico al frente."""
    lineas = []
    for asignacion in asignaciones:
        cantidad = asignacion.cantidad_para(num_personas)
        if cantidad <= 0:
            continue
        # Un extra suele llamarse igual que su único producto ("Brincolín
        # 4x4"), y repetirlo a los dos lados del guion se lee como un error.
        nombre = asignacion.producto.nombre
        descripcion = nombre if etiqueta == nombre else f"{etiqueta} — {nombre}"
        lineas.append((asignacion.producto, cantidad, descripcion))
    return lineas


def _activas(relacion):
    return relacion.filter(activo=True).select_related('producto').order_by('orden', 'id')


def lineas_evento(*, modalidad, paquete, tipo_mobiliario, incluir_licores, nivel_licor,
                  combo_taquiza, extras, num_personas):
    """Líneas propias del paquete, sin el arrendamiento base ni las horas extra.

    Esas dos las agrega `_lineas_cotizador` desde `Producto.rol_cotizador`
    (`BASE_EVENTO` / `HORA_EXTRA`), porque las comparten las dos modalidades y
    ya eran fuente única antes de este módulo.
    """
    if modalidad != MODALIDAD_PAQUETE or not paquete:
        return []

    lineas = []

    # Lo que el paquete incluye sin que el cliente lo elija (refrescos, servicio
    # de mesa). Cada fila trae su propio concepto capturado en el admin; si se
    # dejó vacío se cae al nombre del paquete, que es mejor que una línea que
    # empieza con un guion suelto.
    for asignacion in _activas(paquete.productos):
        cantidad = asignacion.cantidad_para(num_personas)
        if cantidad > 0:
            concepto = asignacion.concepto or paquete.nombre
            lineas.append((asignacion.producto, cantidad,
                           f"{concepto} — {asignacion.producto.nombre}"))

    if tipo_mobiliario:
        lineas += _lineas_de(_activas(tipo_mobiliario.productos), num_personas,
                             f"{CONCEPTO_MOBILIARIO} {tipo_mobiliario.nombre}")

    if incluir_licores and nivel_licor:
        lineas += _lineas_de(_activas(nivel_licor.productos), num_personas,
                             f"{CONCEPTO_LICOR} {nivel_licor.nombre}")

    if combo_taquiza:
        lineas += _lineas_de(_activas(combo_taquiza.productos), num_personas,
                             f"{CONCEPTO_TAQUIZA} {combo_taquiza.nombre}")

    # Un extra ya no es una asignación en sí mismo: es una opción con sus
    # productos, igual que el resto. Normalmente uno solo, pero nada impide un
    # extra compuesto (carrito + operador) sin cambiar el schema.
    for extra in extras:
        lineas += _lineas_de(_activas(extra.productos), num_personas, extra.nombre)

    if not lineas:
        # El catálogo existe pero nadie le asignó productos todavía: la
        # cotización saldría con la sola línea de arrendamiento, cobrando de
        # menos sin que nadie lo note. Mismo criterio que `_producto_por_rol`.
        logger.warning(
            "Cotizador de eventos: el paquete '%s' no produjo ninguna línea "
            "(¿sin productos asignados en el admin?).", paquete.codigo,
        )

    return lineas


def _base_de(asignaciones, num_personas):
    return sum(
        (Decimal(str(a.producto.sugerencia_precio())) * a.cantidad_para(num_personas)
         for a in asignaciones),
        Decimal('0.00'),
    )


def _opcion(obj, num_personas, asignaciones):
    """Una opción del catálogo tal como la consume el frontend.

    `precio` lleva IVA incluido (LFPC art. 7 BIS) y es **indicativo por
    opción**: el importe que se cobra sale de `api_total_cotizador`, que
    convierte una sola vez sobre el subtotal completo. Sumar estos precios en
    el navegador daría diferencias de centavos contra ese total.
    """
    return {
        'id': obj.id,
        'codigo': obj.codigo,
        'nombre': obj.nombre,
        'descripcion': obj.descripcion_corta,
        'precio': str(impuestos.con_iva(_base_de(asignaciones, num_personas))),
        'sin_configurar': not asignaciones,
    }


def _prefetch_productos_activos(queryset):
    """Adjunta los productos activos de cada opción en un solo query extra.

    Sin esto, iterar el queryset y leer `obj.productos` dentro del loop dispara
    un SELECT por opción (N+1): con el catálogo creciendo (más tipos de
    mobiliario, niveles de licor, combos, extras) `catalogo_para_cotizador` es
    la API pública que arma el navegador en cada paso del cotizador
    (`api_catalogo_eventos`, hasta 60 veces/min por `@rate_limit`), así que el
    costo escala con el tamaño del catálogo en vez de quedarse fijo.
    `to_attr` deja la lista ya filtrada/ordenada en el objeto en vez de un
    QuerySet cacheado, para no arriesgar un segundo hit si algo vuelve a
    tocar `.productos` en lugar del atributo.
    """
    return queryset.prefetch_related(Prefetch(
        'productos',
        queryset=CatalogoEventoProducto.objects.filter(activo=True)
            .select_related('producto').order_by('orden', 'id'),
        to_attr='_productos_activos',
    ))


def catalogo_para_cotizador(num_personas):
    """Catálogo completo de opciones cerradas, ya valorizado para ese aforo.

    Una opción sin productos asignados viaja con `sin_configurar: True` para que
    el frontend no la ofrezca: el cliente nunca debe poder elegir algo que
    cobraría $0.00 (mismo criterio que el toggle de Pasadía Premium, que se
    oculta mientras su producto no exista).
    """
    def _opciones_de(tipo):
        salida = []
        qs = _prefetch_productos_activos(
            CatalogoEvento.objects.filter(tipo=tipo, activo=True)
        ).order_by('orden', 'nombre')
        for obj in qs:
            salida.append(_opcion(obj, num_personas, obj._productos_activos))
        return salida

    paquetes = []
    qs_paquetes = _prefetch_productos_activos(
        CatalogoEvento.objects.filter(tipo=CatalogoEvento.TIPO_PAQUETE, activo=True)
    ).order_by('orden', 'nombre')
    for paq in qs_paquetes:
        datos = _opcion(paq, num_personas, paq._productos_activos)
        # Un paquete SIN productos incluidos es legítimo (Esencial solo lleva
        # mobiliario, que se elige aparte), así que aquí no significa "sin
        # configurar" como en el resto de las opciones.
        datos['sin_configurar'] = False
        datos.update({
            'requiere_mobiliario': paq.requiere_mobiliario,
            'permite_licores_opcional': paq.permite_licores_opcional,
            'requiere_taquiza': paq.requiere_taquiza,
            'permite_extras': paq.permite_extras,
        })
        paquetes.append(datos)

    extras = []
    qs_extras = _prefetch_productos_activos(
        CatalogoEvento.objects.filter(tipo=CatalogoEvento.TIPO_EXTRA, activo=True)
    ).order_by('orden', 'nombre')
    for extra in qs_extras:
        datos = _opcion(extra, num_personas, extra._productos_activos)
        datos['capacidad_maxima_simultanea'] = extra.capacidad_maxima_simultanea
        extras.append(datos)

    return {
        'paquetes': paquetes,
        'mobiliario': _opciones_de(CatalogoEvento.TIPO_MOBILIARIO),
        'niveles_licor': _opciones_de(CatalogoEvento.TIPO_LICOR),
        'combos_taquiza': _opciones_de(CatalogoEvento.TIPO_TAQUIZA),
        'extras': extras,
        'imagen_zonas_restringidas': _imagen_zonas_restringidas(),
    }


def _imagen_zonas_restringidas():
    """Plano de las áreas que NO entran en el arrendamiento, o `None`.

    El cotizador lo enseña justo antes de confirmar. Devuelve `None` mientras
    el propietario no haya subido ninguna, y el frontend se salta el bloque —
    vale más no mostrar nada que mostrar un hueco roto.
    """
    from .models import ImagenLanding

    imagen = (ImagenLanding.objects
              .filter(seccion='ZONAS_RESTRINGIDAS', activo=True)
              .order_by('orden', 'id')
              .first())
    if not imagen or not imagen.imagen:
        return None
    return {'url': imagen.imagen.url, 'alt': imagen.alt_text or 'Zonas restringidas de la Quinta'}
