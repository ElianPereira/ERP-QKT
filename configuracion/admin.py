from django.contrib import admin

from .models import CategoriaGasto


@admin.register(CategoriaGasto)
class CategoriaGastoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "clave", "orden", "activa"]
    list_editable = ["orden", "activa"]
    list_filter = ["activa"]
    search_fields = ["clave", "nombre"]
    ordering = ["orden", "nombre"]
