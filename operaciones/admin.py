from django.contrib import admin
from django.db import models as db_models

from comercial.widgets import TimeSlotWidget

from .models import ItemChecklist, PlantillaChecklist, TareaProgramada

# Jazzmin convierte los fieldsets nombrados de PlantillaChecklistAdmin en
# pestañas — el selector de fecha del admin (DateTimeShortcuts.js) posiciona
# su caja sumando offsetLeft/offsetTop de los padres, cálculo que no
# contempla pestañas ni transform, y la deja anclada fuera de la pantalla o
# bloqueada (mismo bug ya resuelto en comercial/airbnb/reportes). El fix
# vive en static/js/tabs_fix.js, sin tocar el JS de Django. Este admin no
# tiene ningún DateField hoy, pero se deja cableado por si se agrega uno.
MEDIA_CONFIG = {
    'css': {'all': ('css/admin_fix.css', 'css/mobile_fix_v4.css')},
    'js': ('js/tabs_fix.js',),
}


class ItemChecklistInline(admin.TabularInline):
    model = ItemChecklist
    extra = 1
    fields = ['orden', 'texto']
    ordering = ['orden']


@admin.register(PlantillaChecklist)
class PlantillaChecklistAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'responsable_default', 'cadencia', 'activa']
    list_filter = ['tipo', 'cadencia', 'activa']
    search_fields = ['nombre', 'encabezado']
    inlines = [ItemChecklistInline]
    # Mismo widget que usa Cotizacion para hora_inicio/hora_fin — un
    # dropdown buscable con franjas de 15 min, en vez del popover nativo de
    # Django ("Elija una hora", 5 atajos fijos) que además arrastra el bug
    # de posicionamiento del <dialog> nativo en pantallas con pestañas.
    formfield_overrides = {
        db_models.TimeField: {'widget': TimeSlotWidget},
    }
    fieldsets = [
        (None, {'fields': ['nombre', 'tipo', 'encabezado', 'responsable_default', 'activa']}),
        ('Turnover', {
            'fields': ['duracion_estimada_horas'],
            'description': "Solo aplica a plantillas de preparación (turnover).",
        }),
        ('Mantenimiento recurrente', {
            'fields': ['cadencia', 'dia_semana', 'dia_mes', 'hora_limite_default'],
            'description': "Solo aplica a plantillas de tipo Mantenimiento recurrente.",
        }),
    ]

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


@admin.register(TareaProgramada)
class TareaProgramadaAdmin(admin.ModelAdmin):
    list_display = [
        'fecha', 'plantilla', 'responsable', 'hora_entrada', 'hora_limite',
        'requiere_tiempo_extra', 'estado_operativo', 'estado_resumen_propietario',
    ]
    list_filter = ['plantilla__tipo', 'responsable', 'estado_operativo', 'requiere_tiempo_extra']
    date_hierarchy = 'fecha'
    autocomplete_fields = ['cotizacion', 'responsable']
    readonly_fields = [
        'plantilla', 'cotizacion', 'fecha', 'hora_entrada', 'hora_limite',
        'requiere_tiempo_extra', 'estado_aviso_horario', 'estado_operativo',
        'estado_resumen_propietario', 'created_at',
    ]

    def has_add_permission(self, request):
        # Se generan solo (signal + cron); capturarlas a mano rompería la
        # idempotencia con la que las genera operaciones.services.
        return False

    # Es el tablero de supervisión (qué se le mandó a quién): todo de solo
    # lectura salvo `responsable`, para permitir el override manual antes de
    # que salga el envío.
