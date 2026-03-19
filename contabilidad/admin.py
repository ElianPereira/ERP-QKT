"""
Admin del Módulo de Contabilidad
================================
Sistema de Diseño QKT v2.0
"""
from decimal import Decimal
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Sum

from .models import (
    CuentaContable, UnidadNegocio, CuentaBancaria,
    Poliza, MovimientoContable, ConciliacionBancaria,
    ConfiguracionContable
)


class MovimientoContableInline(admin.TabularInline):
    model = MovimientoContable
    extra = 2
    fields = ['cuenta', 'concepto', 'debe', 'haber', 'referencia']
    autocomplete_fields = ['cuenta']


class SubcuentaInline(admin.TabularInline):
    model = CuentaContable
    fk_name = 'padre'
    extra = 0
    fields = ['codigo_sat', 'nombre', 'naturaleza', 'permite_movimientos', 'activa']
    readonly_fields = ['codigo_sat', 'nombre']
    show_change_link = True
    verbose_name = "Subcuenta"
    verbose_name_plural = "Subcuentas"
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ['codigo_sat', 'nombre', 'tipo_display', 'naturaleza_display', 'nivel', 'permite_movimientos', 'activa']
    list_filter = ['tipo', 'naturaleza', 'nivel', 'activa', 'permite_movimientos']
    search_fields = ['codigo_sat', 'nombre']
    ordering = ['codigo_sat']
    list_per_page = 50
    autocomplete_fields = ['padre']
    inlines = [SubcuentaInline]
    
    @admin.display(description="Tipo", ordering="tipo")
    def tipo_display(self, obj):
        colores = {
            'ACTIVO': '#3498db',
            'PASIVO': '#9b59b6',
            'CAPITAL': '#2E7D32',
            'INGRESO': '#27ae60',
            'COSTO': '#e67e22',
            'GASTO': '#e74c3c',
            'ORDEN': '#95a5a6',
        }
        color = colores.get(obj.tipo, '#95a5a6')
        return mark_safe(
            '<span style="background:{}; color:#fff; padding:4px 10px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>'.format(
                color, obj.tipo
            )
        )
    
    @admin.display(description="Nat.", ordering="naturaleza")
    def naturaleza_display(self, obj):
        if obj.naturaleza == 'D':
            return mark_safe('<span style="color:#3498db; font-weight:600;">D</span>')
        return mark_safe('<span style="color:#9b59b6; font-weight:600;">A</span>')


@admin.register(UnidadNegocio)
class UnidadNegocioAdmin(admin.ModelAdmin):
    list_display = ['clave', 'nombre', 'regimen_display', 'activa']
    list_filter = ['regimen_fiscal', 'activa']
    search_fields = ['clave', 'nombre']
    
    @admin.display(description="Régimen Fiscal")
    def regimen_display(self, obj):
        texto = obj.get_regimen_fiscal_display()
        if len(texto) > 50:
            texto = texto[:50] + '...'
        return mark_safe(
            '<span style="color:#d4d1c8; font-size:12px;">{}</span>'.format(texto)
        )


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'banco', 'clabe_display', 'cuenta_contable', 'saldo_display', 'activa']
    list_filter = ['banco', 'activa']
    search_fields = ['nombre', 'banco', 'clabe']
    
    @admin.display(description="CLABE")
    def clabe_display(self, obj):
        if obj.clabe:
            return mark_safe(
                '<span style="color:#95a5a6;">****{}</span>'.format(obj.clabe[-4:])
            )
        return "-"
    
    @admin.display(description="Saldo")
    def saldo_display(self, obj):
        saldo = obj.saldo_actual
        if saldo >= 0:
            color = '#27ae60'
        else:
            color = '#e74c3c'
        return mark_safe(
            '<span style="color:{}; font-weight:600;">${:,.2f}</span>'.format(color, float(saldo))
        )


@admin.register(Poliza)
class PolizaAdmin(admin.ModelAdmin):
    list_display = ['folio_display', 'tipo_display', 'fecha', 'concepto_display', 'unidad_negocio', 'total_display', 'estado_display']
    list_filter = ['tipo', 'estado', 'unidad_negocio', 'origen', 'fecha']
    search_fields = ['folio', 'concepto']
    date_hierarchy = 'fecha'
    ordering = ['-fecha', '-folio']
    readonly_fields = ['created_by', 'created_at', 'cancelada_por', 'fecha_cancelacion']
    inlines = [MovimientoContableInline]
    
    fieldsets = (
        ('Datos de la Póliza', {
            'fields': ('tipo', 'fecha', 'concepto', 'unidad_negocio')
        }),
        ('Estado', {
            'fields': ('estado', 'origen')
        }),
        ('Cancelación', {
            'fields': ('motivo_cancelacion', 'cancelada_por', 'fecha_cancelacion'),
            'classes': ('collapse',)
        }),
        ('Auditoría', {
            'fields': ('created_by', 'created_at'),
            'classes': ('collapse',)
        }),
    )
    
    @admin.display(description="Folio", ordering="folio")
    def folio_display(self, obj):
        return mark_safe(
            '<span style="color:#4CAF50; font-weight:700;">{}-{}</span>'.format(
                obj.tipo, str(obj.folio).zfill(4)
            )
        )
    
    @admin.display(description="Tipo", ordering="tipo")
    def tipo_display(self, obj):
        colores = {
            'I': '#27ae60',
            'E': '#e74c3c',
            'D': '#3498db'
        }
        color = colores.get(obj.tipo, '#95a5a6')
        return mark_safe(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>'.format(
                color, obj.get_tipo_display()
            )
        )
    
    @admin.display(description="Concepto")
    def concepto_display(self, obj):
        concepto = obj.concepto
        if len(concepto) > 45:
            concepto = concepto[:45] + "..."
        return concepto
    
    @admin.display(description="Total")
    def total_display(self, obj):
        total = obj.total_debe
        cuadra = obj.esta_cuadrada
        if cuadra:
            return mark_safe(
                '<span style="color:#27ae60; font-weight:600;">${:,.2f}</span>'.format(float(total))
            )
        return mark_safe(
            '<span style="color:#e74c3c; font-weight:600;">${:,.2f} !</span>'.format(float(total))
        )
    
    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        colores = {
            'BORRADOR': '#e67e22',
            'APLICADA': '#27ae60',
            'CANCELADA': '#95a5a6'
        }
        color = colores.get(obj.estado, '#95a5a6')
        return mark_safe(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>'.format(
                color, obj.get_estado_display()
            )
        )
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.folio = Poliza.siguiente_folio(obj.tipo, obj.fecha)
        super().save_model(request, obj, form, change)
    
    actions = ['aplicar_polizas', 'cancelar_polizas']
    
    @admin.action(description="Aplicar pólizas seleccionadas")
    def aplicar_polizas(self, request, queryset):
        aplicadas = 0
        errores = []
        for poliza in queryset.filter(estado='BORRADOR'):
            if poliza.esta_cuadrada:
                poliza.aplicar(request.user)
                aplicadas += 1
            else:
                errores.append("{}-{}".format(poliza.tipo, poliza.folio))
        
        if aplicadas:
            self.message_user(request, "{} póliza(s) aplicada(s)".format(aplicadas))
        if errores:
            self.message_user(request, "No cuadran: {}".format(', '.join(errores)), level='ERROR')
    
    @admin.action(description="Cancelar pólizas seleccionadas")
    def cancelar_polizas(self, request, queryset):
        from django.utils import timezone
        canceladas = queryset.exclude(estado='CANCELADA').update(
            estado='CANCELADA',
            cancelada_por=request.user,
            fecha_cancelacion=timezone.now(),
            motivo_cancelacion='Cancelación masiva desde admin'
        )
        self.message_user(request, "{} póliza(s) cancelada(s)".format(canceladas))


@admin.register(ConciliacionBancaria)
class ConciliacionBancariaAdmin(admin.ModelAdmin):
    list_display = ['cuenta_bancaria', 'periodo_display', 'saldo_segun_banco', 'saldo_segun_libros', 'diferencia_display', 'estado_display']
    list_filter = ['estado', 'cuenta_bancaria', 'anio']
    ordering = ['-anio', '-mes']
    
    @admin.display(description="Período")
    def periodo_display(self, obj):
        meses = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 
                 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
        return mark_safe(
            '<span style="color:#F5C518; font-weight:600;">{} {}</span>'.format(
                meses[obj.mes], obj.anio
            )
        )
    
    @admin.display(description="Diferencia")
    def diferencia_display(self, obj):
        if abs(obj.diferencia) < Decimal('0.01'):
            return mark_safe(
                '<span style="color:#27ae60; font-weight:600;">$0.00</span>'
            )
        return mark_safe(
            '<span style="color:#e74c3c; font-weight:600;">${:,.2f}</span>'.format(
                float(obj.diferencia)
            )
        )
    
    @admin.display(description="Estado")
    def estado_display(self, obj):
        colores = {
            'PENDIENTE': '#e67e22',
            'EN_PROCESO': '#3498db',
            'CONCILIADA': '#27ae60'
        }
        color = colores.get(obj.estado, '#95a5a6')
        return mark_safe(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>'.format(
                color, obj.get_estado_display()
            )
        )


@admin.register(ConfiguracionContable)
class ConfiguracionContableAdmin(admin.ModelAdmin):
    list_display = ['operacion_display', 'cuenta_display', 'descripcion', 'activa']
    list_filter = ['activa']
    search_fields = ['operacion', 'cuenta__codigo_sat', 'descripcion']
    autocomplete_fields = ['cuenta']
    
    @admin.display(description="Operación", ordering="operacion")
    def operacion_display(self, obj):
        return mark_safe(
            '<span style="color:#4CAF50; font-weight:600;">{}</span>'.format(
                obj.get_operacion_display()
            )
        )
    
    @admin.display(description="Cuenta")
    def cuenta_display(self, obj):
        nombre = obj.cuenta.nombre
        if len(nombre) > 30:
            nombre = nombre[:30] + '...'
        return mark_safe(
            '<span style="color:#d4d1c8;">{} - {}</span>'.format(
                obj.cuenta.codigo_sat, nombre
            )
        )


@admin.register(MovimientoContable)
class MovimientoContableAdmin(admin.ModelAdmin):
    list_display = ['poliza_display', 'cuenta', 'concepto', 'debe_display', 'haber_display', 'referencia']
    list_filter = ['poliza__tipo', 'poliza__estado', 'cuenta__tipo']
    search_fields = ['cuenta__codigo_sat', 'cuenta__nombre', 'concepto', 'poliza__concepto']
    autocomplete_fields = ['cuenta']
    
    @admin.display(description="Póliza")
    def poliza_display(self, obj):
        return mark_safe(
            '<a href="/admin/contabilidad/poliza/{}/change/" '
            'style="color:#4CAF50; font-weight:600;">{}-{}</a>'.format(
                obj.poliza.pk, obj.poliza.tipo, str(obj.poliza.folio).zfill(4)
            )
        )
    
    @admin.display(description="Debe")
    def debe_display(self, obj):
        if obj.debe > 0:
            return mark_safe(
                '<span style="color:#3498db; font-weight:600;">${:,.2f}</span>'.format(
                    float(obj.debe)
                )
            )
        return mark_safe('<span style="color:#95a5a6;">-</span>')
    
    @admin.display(description="Haber")
    def haber_display(self, obj):
        if obj.haber > 0:
            return mark_safe(
                '<span style="color:#9b59b6; font-weight:600;">${:,.2f}</span>'.format(
                    float(obj.haber)
                )
            )
        return mark_safe('<span style="color:#95a5a6;">-</span>')
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False