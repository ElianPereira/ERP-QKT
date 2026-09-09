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

from core_erp import impuestos

from .models import (
    ComboTaquiza,
    ExtraEvento,
    NivelLicor,
    PaqueteEvento,
    TipoMobiliario,
)
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

    def _uno(modelo, clave):
        valor = datos.get(clave)
        if not _id_valido(valor):
            return None
        return modelo.objects.filter(id=int(valor), activo=True).first()

    extras_ids = [int(x) for x in (datos.get('extras_evento_ids') or []) if _id_valido(x)]

    return {
        'modalidad': modalidad,
        'paquete': _uno(PaqueteEvento, 'paquete_evento_id'),
        'tipo_mobiliario': _uno(TipoMobiliario, 'mobiliario_id'),
        'incluir_licores': bool(datos.get('incluir_licores')),
        'nivel_licor': _uno(NivelLicor, 'nivel_licor_id'),
        'combo_taquiza': _uno(ComboTaquiza, 'combo_taquiza_id'),
        'extras': list(ExtraEvento.objects.filter(id__in=extras_ids, activo=True)),
    }


def _lineas_de(asignaciones, num_personas, etiqueta):
    """Una línea por producto asignado, con el concepto genérico al frente."""
    lineas = []
    for asignacion in asignaciones:
        cantidad = asignacion.cantidad_para(num_personas)
        if cantidad <= 0:
            continue
        lineas.append((
            asignacion.producto,
            cantidad,
            f"{etiqueta} — {asignacion.producto.nombre}",
        ))
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
    # de mesa). Cada fila trae su propio concepto capturado en el admin.
    for asignacion in _activas(paquete.productos_incluidos):
        cantidad = asignacion.cantidad_para(num_personas)
        if cantidad > 0:
            lineas.append((asignacion.producto, cantidad,
                           f"{asignacion.concepto} — {asignacion.producto.nombre}"))

    if tipo_mobiliario:
        lineas += _lineas_de(_activas(tipo_mobiliario.productos), num_personas,
                             f"{CONCEPTO_MOBILIARIO} {tipo_mobiliario.nombre}")

    if incluir_licores and nivel_licor:
        lineas += _lineas_de(_activas(nivel_licor.productos), num_personas,
                             f"{CONCEPTO_LICOR} {nivel_licor.nombre}")

    if combo_taquiza:
        lineas += _lineas_de(_activas(combo_taquiza.productos), num_personas,
                             f"{CONCEPTO_TAQUIZA} {combo_taquiza.nombre}")

    for extra in extras:
        cantidad = extra.cantidad_para(num_personas)
        if cantidad > 0:
            lineas.append((extra.producto, cantidad, extra.nombre))

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


def catalogo_para_cotizador(num_personas):
    """Catálogo completo de opciones cerradas, ya valorizado para ese aforo.

    Una opción sin productos asignados viaja con `sin_configurar: True` para que
    el frontend no la ofrezca: el cliente nunca debe poder elegir algo que
    cobraría $0.00 (mismo criterio que el toggle de Pasadía Premium, que se
    oculta mientras su producto no exista).
    """
    def _lista(queryset, relacion):
        salida = []
        for obj in queryset.filter(activo=True).order_by('orden', 'nombre'):
            salida.append(_opcion(obj, num_personas, list(_activas(getattr(obj, relacion)))))
        return salida

    paquetes = []
    for paq in PaqueteEvento.objects.filter(activo=True).order_by('orden', 'nombre'):
        datos = _opcion(paq, num_personas, list(_activas(paq.productos_incluidos)))
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
    for extra in ExtraEvento.objects.filter(activo=True).select_related(
            'producto').order_by('orden', 'nombre'):
        base = Decimal(str(extra.producto.sugerencia_precio())) * extra.cantidad_para(num_personas)
        extras.append({
            'id': extra.id,
            'codigo': extra.codigo,
            'nombre': extra.nombre,
            'descripcion': extra.descripcion_corta,
            'precio': str(impuestos.con_iva(base)),
            'capacidad_maxima_simultanea': extra.capacidad_maxima_simultanea,
            'sin_configurar': False,
        })

    return {
        'paquetes': paquetes,
        'mobiliario': _lista(TipoMobiliario.objects, 'productos'),
        'niveles_licor': _lista(NivelLicor.objects, 'productos'),
        'combos_taquiza': _lista(ComboTaquiza.objects, 'productos'),
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
