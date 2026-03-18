"""
Admin del Módulo de Contabilidad
================================
Siguiendo Sistema de Diseño QKT v2.0
Paleta: Verde (#2E7D32) + Amarillo (#F5C518)
"""
from decimal import Decimal
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum

from .models import (
    CuentaContable, UnidadNegocio, CuentaBancaria,
    Poliza, MovimientoContable, ConciliacionBancaria,
    ConfiguracionContable
)


# ============================================================
# ESTILOS QKT v2.0
# ============================================================
# Verde primario: #2E7D32 (hover: #388E3C, texto: #4CAF50)
# Amarillo marca: #F5C518
# Success: #27ae60 | Warning: #e67e22 | Danger: #e74c3c
# Info: #3498db | Neutral: #95a5a6 | Teal: #1abc9c
# Badge radius: 12px
# ============================================================


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
    list_display = ['codigo_sat', 'nombre', 'tipo_badge', 'naturaleza_badge', 'nivel', 'permite_movimientos', 'activa']
    list_filter = ['tipo', 'naturaleza', 'nivel', 'activa', 'permite_movimientos']
    search_fields = ['codigo_sat', 'nombre']
    ordering = ['codigo_sat']
    list_per_page = 50
    autocomplete_fields = ['padre']
    inlines = [SubcuentaInline]
    
    @admin.display(description="Tipo", ordering="tipo")
    def tipo_badge(self, obj):
        # Colores alineados al sistema QKT
        colores = {
            'ACTIVO': '#3498db',      # Info - azul
            'PASIVO': '#9b59b6',      # Morado
            'CAPITAL': '#2E7D32',     # Verde QKT primario
            'INGRESO': '#27ae60',     # Success
            'COSTO': '#e67e22',       # Warning
            'GASTO': '#e74c3c',       # Danger
            'ORDEN': '#95a5a6',       # Neutral
        }
        color = colores.get(obj.tipo, '#95a5a6')
        return format_html(
            '<span style="background:{}; color:#fff; padding:4px 10px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>',
            color,
            obj.tipo
        )
    
    @admin.display(description="Nat.", ordering="naturaleza")
    def naturaleza_badge(self, obj):
        if obj.naturaleza == 'D':
            return format_html(
                '<span style="color:#3498db; font-weight:600;">↑ D</span>'
            )
        return format_html(
            '<span style="color:#9b59b6; font-weight:600;">↓ A</span>'
        )


@admin.register(UnidadNegocio)
class UnidadNegocioAdmin(admin.ModelAdmin):
    list_display = ['clave', 'nombre', 'regimen_fiscal_badge', 'activa']
    list_filter = ['regimen_fiscal', 'activa']
    search_fields = ['clave', 'nombre']
    
    @admin.display(description="Régimen Fiscal")
    def regimen_fiscal_badge(self, obj):
        # Mostrar código + nombre corto
        texto = obj.get_regimen_fiscal_display()
        return format_html(
            '<span style="color:#d4d1c8; font-size:12px;">{}</span>',
            texto[:50] + '...' if len(texto) > 50 else texto
        )


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'banco', 'clabe_oculta', 'cuenta_contable', 'saldo_badge', 'activa']
    list_filter = ['banco', 'activa']
    search_fields = ['nombre', 'banco', 'clabe']
    
    @admin.display(description="CLABE")
    def clabe_oculta(self, obj):
        if obj.clabe:
            return format_html(
                '<span style="color:#95a5a6;">****{}</span>',
                obj.clabe[-4:]
            )
        return "-"
    
    @admin.display(description="Saldo")
    def saldo_badge(self, obj):
        saldo = obj.saldo_actual
        if saldo >= 0:
            color = '#27ae60'  # Success
        else:
            color = '#e74c3c'  # Danger
        return format_html(
            '<span style="color:{}; font-weight:600;">${:,.2f}</span>',
            color,
            saldo
        )


@admin.register(Poliza)
class PolizaAdmin(admin.ModelAdmin):
    list_display = ['folio_badge', 'tipo_badge', 'fecha', 'concepto_corto', 'unidad_negocio', 'total_badge', 'estado_badge']
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
    def folio_badge(self, obj):
        return format_html(
            '<span style="color:#4CAF50; font-weight:700;">{}-{:04d}</span>',
            obj.tipo,
            obj.folio
        )
    
    @admin.display(description="Tipo", ordering="tipo")
    def tipo_badge(self, obj):
        colores = {
            'I': '#27ae60',   # Ingreso = Success (verde)
            'E': '#e74c3c',   # Egreso = Danger (rojo)
            'D': '#3498db'    # Diario = Info (azul)
        }
        color = colores.get(obj.tipo, '#95a5a6')
        return format_html(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>',
            color,
            obj.get_tipo_display()
        )
    
    @admin.display(description="Concepto")
    def concepto_corto(self, obj):
        if len(obj.concepto) > 45:
            return obj.concepto[:45] + "..."
        return obj.concepto
    
    @admin.display(description="Total")
    def total_badge(self, obj):
        total = obj.total_debe
        cuadra = obj.esta_cuadrada
        if cuadra:
            return format_html(
                '<span style="color:#27ae60; font-weight:600;">${:,.2f} ✓</span>',
                total
            )
        return format_html(
            '<span style="color:#e74c3c; font-weight:600;">${:,.2f} ✗</span>',
            total
        )
    
    @admin.display(description="Estado", ordering="estado")
    def estado_badge(self, obj):
        colores = {
            'BORRADOR': '#e67e22',   # Warning (naranja)
            'APLICADA': '#27ae60',   # Success (verde)
            'CANCELADA': '#95a5a6'   # Neutral (gris)
        }
        color = colores.get(obj.estado, '#95a5a6')
        return format_html(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>',
            color,
            obj.get_estado_display()
        )
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.folio = Poliza.siguiente_folio(obj.tipo, obj.fecha)
        super().save_model(request, obj, form, change)
    
    actions = ['aplicar_polizas', 'cancelar_polizas']
    
    @admin.action(description="✓ Aplicar pólizas seleccionadas")
    def aplicar_polizas(self, request, queryset):
        aplicadas = 0
        errores = []
        for poliza in queryset.filter(estado='BORRADOR'):
            if poliza.esta_cuadrada:
                poliza.aplicar(request.user)
                aplicadas += 1
            else:
                errores.append(f"{poliza.tipo}-{poliza.folio}")
        
        if aplicadas:
            self.message_user(request, f"✓ {aplicadas} póliza(s) aplicada(s)")
        if errores:
            self.message_user(request, f"✗ No cuadran: {', '.join(errores)}", level='ERROR')
    
    @admin.action(description="✗ Cancelar pólizas seleccionadas")
    def cancelar_polizas(self, request, queryset):
        from django.utils import timezone
        canceladas = queryset.exclude(estado='CANCELADA').update(
            estado='CANCELADA',
            cancelada_por=request.user,
            fecha_cancelacion=timezone.now(),
            motivo_cancelacion='Cancelación masiva desde admin'
        )
        self.message_user(request, f"✓ {canceladas} póliza(s) cancelada(s)")


@admin.register(ConciliacionBancaria)
class ConciliacionBancariaAdmin(admin.ModelAdmin):
    list_display = ['cuenta_bancaria', 'periodo_badge', 'saldo_segun_banco', 'saldo_segun_libros', 'diferencia_badge', 'estado_badge']
    list_filter = ['estado', 'cuenta_bancaria', 'anio']
    ordering = ['-anio', '-mes']
    
    @admin.display(description="Período")
    def periodo_badge(self, obj):
        meses = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 
                 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
        return format_html(
            '<span style="color:#F5C518; font-weight:600;">{} {}</span>',
            meses[obj.mes],
            obj.anio
        )
    
    @admin.display(description="Diferencia")
    def diferencia_badge(self, obj):
        if abs(obj.diferencia) < Decimal('0.01'):
            return format_html(
                '<span style="color:#27ae60; font-weight:600;">$0.00 ✓</span>'
            )
        return format_html(
            '<span style="color:#e74c3c; font-weight:600;">${:,.2f}</span>',
            obj.diferencia
        )
    
    @admin.display(description="Estado")
    def estado_badge(self, obj):
        colores = {
            'PENDIENTE': '#e67e22',    # Warning
            'EN_PROCESO': '#3498db',   # Info
            'CONCILIADA': '#27ae60'    # Success
        }
        color = colores.get(obj.estado, '#95a5a6')
        return format_html(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>',
            color,
            obj.get_estado_display()
        )


@admin.register(ConfiguracionContable)
class ConfiguracionContableAdmin(admin.ModelAdmin):
    list_display = ['operacion_badge', 'cuenta_badge', 'descripcion', 'activa']
    list_filter = ['activa']
    search_fields = ['operacion', 'cuenta__codigo_sat', 'descripcion']
    autocomplete_fields = ['cuenta']
    
    @admin.display(description="Operación", ordering="operacion")
    def operacion_badge(self, obj):
        return format_html(
            '<span style="color:#4CAF50; font-weight:600;">{}</span>',
            obj.get_operacion_display()
        )
    
    @admin.display(description="Cuenta")
    def cuenta_badge(self, obj):
        return format_html(
            '<span style="color:#d4d1c8;">{} - {}</span>',
            obj.cuenta.codigo_sat,
            obj.cuenta.nombre[:30]
        )


@admin.register(MovimientoContable)
class MovimientoContableAdmin(admin.ModelAdmin):
    list_display = ['poliza_link', 'cuenta', 'concepto', 'debe_badge', 'haber_badge', 'referencia']
    list_filter = ['poliza__tipo', 'poliza__estado', 'cuenta__tipo']
    search_fields = ['cuenta__codigo_sat', 'cuenta__nombre', 'concepto', 'poliza__concepto']
    autocomplete_fields = ['cuenta']
    
    @admin.display(description="Póliza")
    def poliza_link(self, obj):
        return format_html(
            '<a href="/admin/contabilidad/poliza/{}/change/" '
            'style="color:#4CAF50; font-weight:600;">{}-{:04d}</a>',
            obj.poliza.pk,
            obj.poliza.tipo,
            obj.poliza.folio
        )
    
    @admin.display(description="Debe")
    def debe_badge(self, obj):
        if obj.debe > 0:
            return format_html(
                '<span style="color:#3498db; font-weight:600;">${:,.2f}</span>',
                obj.debe
            )
        return format_html('<span style="color:#95a5a6;">-</span>')
    
    @admin.display(description="Haber")
    def haber_badge(self, obj):
        if obj.haber > 0:
            return format_html(
                '<span style="color:#9b59b6; font-weight:600;">${:,.2f}</span>',
                obj.haber
            )
        return format_html('<span style="color:#95a5a6;">-</span>')
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False