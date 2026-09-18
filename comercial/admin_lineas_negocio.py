"""3 submódulos de Productos por línea de negocio (Eventos/Pasadía/Hospedaje).

Reorganización puramente visual (pedido del propietario, Memoria 2026-09-17):
`ProductoEventos`/`ProductoPasadia`/`ProductoHospedaje` son proxies de
`Producto` — misma tabla, mismos datos, cero migración de esquema real. Cada
uno filtra el changelist a los productos de su línea y, al dar de alta uno
desde aquí, precarga el flag correspondiente para no tener que marcarlo a
mano.

El `Producto` "plano" sigue existiendo y registrado (autocompletado, popups
"+" desde otros formularios), solo se oculta del menú (`hide_models` en
settings.py).

Arrendamiento de mobiliario a otras ubicaciones ya no se ofrece (Memoria
2026-09-17): el mobiliario que hoy se subarrenda es solo un costo dentro de
Eventos (categoría "Mobiliario" del catálogo abierto, ver Issue #287), no una
línea de negocio propia — por eso no hay un cuarto submódulo aquí.
`cotizador_arrendamiento` sigue en el modelo solo por compatibilidad con
cotizaciones históricas.
"""

from django.contrib import admin
from django.db.models import Q

from .admin import ProductoAdmin
from .models import ProductoEventos, ProductoHospedaje, ProductoPasadia

# Un producto entra a la línea por el flag de "extra normal" (cotizador_X) O
# por tener un rol propio de esa línea (base del servicio, habitación,
# persona extra) — varios productos con rol se capturan SIN el flag marcado
# (ver Memoria Hospedaje 2026-08-19 y Pasadía Básico/Premium 2026-09-04), así
# que filtrar solo por el flag los dejaría fuera del submódulo.
_FILTROS = {
    ProductoEventos: Q(cotizador_evento=True) | Q(rol_cotizador__in=['BASE_EVENTO', 'HORA_EXTRA']),
    ProductoPasadia: Q(cotizador_pasadia=True) | Q(rol_cotizador__in=[
        'BASE_PASADIA', 'BASE_PASADIA_BASICO', 'BASE_PASADIA_PREMIUM', 'PERSONA_EXTRA_PASADIA',
    ]),
    ProductoHospedaje: Q(cotizador_hospedaje=True) | Q(rol_cotizador__in=[
        'HABITACION_HOSPEDAJE', 'PERSONA_EXTRA_HOSPEDAJE',
    ]),
}

_FLAG_ALTA = {
    ProductoEventos: 'cotizador_evento',
    ProductoPasadia: 'cotizador_pasadia',
    ProductoHospedaje: 'cotizador_hospedaje',
}


class _ProductoPorLineaAdmin(ProductoAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(_FILTROS[self.model]).distinct()

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial[_FLAG_ALTA[self.model]] = True
        return initial


@admin.register(ProductoEventos)
class ProductoEventosAdmin(_ProductoPorLineaAdmin):
    pass


@admin.register(ProductoPasadia)
class ProductoPasadiaAdmin(_ProductoPorLineaAdmin):
    pass


@admin.register(ProductoHospedaje)
class ProductoHospedajeAdmin(_ProductoPorLineaAdmin):
    pass
