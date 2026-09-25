"""
Filtros de negocio de las listas del admin de `comercial` (Issue #322).

Cada filtro responde una pregunta de la operación diaria ("¿qué eventos
vienen?", "¿qué compras no tienen CFDI?") en vez de exponer un campo tal
cual. Los títulos van en español de negocio porque la barra los muestra como
"Título: valor".
"""
from calendar import monthrange
from datetime import timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Case, F, IntegerField, Q, When
from django.utils import timezone

from .models import Cotizacion

# Mismo umbral que la Cartera CxC (comercial/views.py::ver_cartera_cxc): por
# debajo de 50 centavos se considera liquidada.
TOLERANCIA_SALDO = Decimal('0.50')
ESTADOS_POR_COBRAR = ('COTIZADA', 'CONFIRMADA', 'EJECUTADA')


class FechaEventoFilter(admin.SimpleListFilter):
    """El filtro de fecha de Django solo mira hacia atrás ("Últimos 7 días");
    en eventos lo que importa es lo que viene."""
    title = 'Fecha del evento'
    parameter_name = 'cuando'

    def lookups(self, request, model_admin):
        return (
            ('prox7', 'Próximos 7 días'),
            ('prox30', 'Próximos 30 días'),
            ('mes', 'Este mes'),
            ('pasados', 'Pasados'),
        )

    def queryset(self, request, queryset):
        hoy = timezone.localdate()
        if self.value() == 'prox7':
            return queryset.filter(fecha_evento__range=(hoy, hoy + timedelta(days=7)))
        if self.value() == 'prox30':
            return queryset.filter(fecha_evento__range=(hoy, hoy + timedelta(days=30)))
        if self.value() == 'mes':
            fin = hoy.replace(day=monthrange(hoy.year, hoy.month)[1])
            return queryset.filter(fecha_evento__range=(hoy.replace(day=1), fin))
        if self.value() == 'pasados':
            return queryset.filter(fecha_evento__lt=hoy)
        return queryset


class PagoCotizacionFilter(admin.SimpleListFilter):
    """Situación de cobro. "Con saldo" usa los mismos estados y tolerancia
    que la Cartera CxC para que ambas pantallas cuenten lo mismo."""
    title = 'Pago'
    parameter_name = 'pago'

    def lookups(self, request, model_admin):
        return (
            ('saldo', 'Con saldo pendiente'),
            ('pagada', 'Pagada'),
            ('sin', 'Sin pagos'),
        )

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        qs = Cotizacion.anotar_pagado_neto(queryset)
        if self.value() == 'saldo':
            return qs.filter(estado__in=ESTADOS_POR_COBRAR,
                             precio_final__gt=F('pagado_neto') + TOLERANCIA_SALDO)
        if self.value() == 'pagada':
            return qs.filter(precio_final__lte=F('pagado_neto') + TOLERANCIA_SALDO)
        if self.value() == 'sin':
            return qs.filter(precio_final__gt=0, pagado_neto__lte=0)
        return queryset


class IdentificacionFilter(admin.SimpleListFilter):
    title = 'Identificación'
    parameter_name = 'ine'

    def lookups(self, request, model_admin):
        return (
            ('pendiente', 'Pendiente de revisar'),
            ('revisada', 'Revisada'),
            ('sin', 'Sin subir'),
        )

    def queryset(self, request, queryset):
        sin_archivo = Q(identificacion_oficial='') | Q(identificacion_oficial__isnull=True)
        if self.value() == 'pendiente':
            return queryset.exclude(sin_archivo).filter(identificacion_revisada=False)
        if self.value() == 'revisada':
            return queryset.exclude(sin_archivo).filter(identificacion_revisada=True)
        if self.value() == 'sin':
            return queryset.filter(sin_archivo)
        return queryset


CAMPOS_BARRA = ('incluye_refrescos', 'incluye_cerveza', 'incluye_licor_nacional',
                'incluye_licor_premium', 'incluye_cocteleria_basica', 'incluye_cocteleria_premium')


def nivel_paquete(n_componentes):
    """Nivel de barra por número de componentes marcados. Fuente única de la
    columna "Paquete" y de su filtro."""
    if n_componentes == 0:
        return 'Sin barra'
    if n_componentes <= 2:
        return 'Básico'
    if n_componentes <= 4:
        return 'Plus'
    return 'Premium'


class PaqueteBarraFilter(admin.SimpleListFilter):
    title = 'Paquete de barra'
    parameter_name = 'barra'
    RANGOS = {'sin': (0, 0), 'basico': (1, 2), 'plus': (3, 4), 'premium': (5, 6)}

    def lookups(self, request, model_admin):
        return (('sin', 'Sin barra'), ('basico', 'Básico'), ('plus', 'Plus'), ('premium', 'Premium'))

    def queryset(self, request, queryset):
        rango = self.RANGOS.get(self.value())
        if not rango:
            return queryset
        suma = sum(
            (Case(When(**{c: True}, then=1), default=0, output_field=IntegerField()) for c in CAMPOS_BARRA[1:]),
            Case(When(**{CAMPOS_BARRA[0]: True}, then=1), default=0, output_field=IntegerField()),
        )
        return queryset.annotate(_n_barra=suma).filter(_n_barra__range=rango)


class FacturacionPagoFilter(admin.SimpleListFilter):
    """¿El pago ya generó su solicitud de factura (o la pidió)?"""
    title = 'Facturación'
    parameter_name = 'factura'

    def lookups(self, request, model_admin):
        return (('solicitada', 'Solicitada'), ('sin', 'Sin solicitud'))

    def queryset(self, request, queryset):
        if self.value() == 'solicitada':
            return queryset.filter(solicitudes_factura__isnull=False).distinct()
        if self.value() == 'sin':
            return queryset.filter(solicitudes_factura__isnull=True)
        return queryset


class EstadoCotizacionDelPagoFilter(admin.SimpleListFilter):
    title = 'Estado de la cotización'
    parameter_name = 'estado_cot'

    def lookups(self, request, model_admin):
        return Cotizacion.ESTADOS

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(cotizacion__estado=self.value())
        return queryset


class ConComisionTPVFilter(admin.SimpleListFilter):
    title = 'Comisión de terminal'
    parameter_name = 'tpv'

    def lookups(self, request, model_admin):
        return (('si', 'Con comisión'), ('no', 'Sin comisión'))

    def queryset(self, request, queryset):
        if self.value() == 'si':
            return queryset.filter(comision_tpv__gt=0)
        if self.value() == 'no':
            return queryset.filter(Q(comision_tpv__isnull=True) | Q(comision_tpv=0))
        return queryset


class CfdiCompraFilter(admin.SimpleListFilter):
    title = 'CFDI'
    parameter_name = 'cfdi'

    def lookups(self, request, model_admin):
        return (('con', 'Con factura (UUID)'), ('sin', 'Sin factura'))

    def queryset(self, request, queryset):
        sin_uuid = Q(uuid__isnull=True) | Q(uuid='')
        if self.value() == 'con':
            return queryset.exclude(sin_uuid)
        if self.value() == 'sin':
            return queryset.filter(sin_uuid)
        return queryset


class PolizaCompraFilter(admin.SimpleListFilter):
    """Detecta compras cuya póliza quedó en borrador (le faltaba unidad o
    cuenta de pago) o que no tienen ninguna."""
    title = 'Contabilidad'
    parameter_name = 'poliza'

    def lookups(self, request, model_admin):
        return (('aplicada', 'Póliza aplicada'), ('borrador', 'Póliza en borrador'), ('sin', 'Sin póliza'))

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        from contabilidad.models import Poliza

        ct = ContentType.objects.get_for_model(queryset.model)
        polizas = Poliza.objects.filter(content_type=ct).exclude(estado='CANCELADA')
        aplicadas = polizas.filter(estado='APLICADA').values('object_id')
        if self.value() == 'aplicada':
            return queryset.filter(pk__in=aplicadas)
        if self.value() == 'borrador':
            return queryset.filter(pk__in=polizas.filter(estado='BORRADOR').values('object_id')).exclude(pk__in=aplicadas)
        if self.value() == 'sin':
            return queryset.exclude(pk__in=polizas.values('object_id'))
        return queryset


class ServicioProductoFilter(admin.SimpleListFilter):
    """Junta los interruptores `cotizador_*` del producto en un solo filtro."""
    title = 'Servicio'
    parameter_name = 'servicio'
    CAMPOS = {'evento': 'cotizador_evento', 'pasadia': 'cotizador_pasadia', 'hospedaje': 'cotizador_hospedaje'}

    def lookups(self, request, model_admin):
        return (('evento', 'Evento'), ('pasadia', 'Pasadía'), ('hospedaje', 'Hospedaje'))

    def queryset(self, request, queryset):
        campo = self.CAMPOS.get(self.value())
        if campo:
            return queryset.filter(**{campo: True})
        return queryset
