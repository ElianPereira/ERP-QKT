"""Utilidades compartidas para el admin de Django."""
from functools import wraps

from django.contrib.admin import helpers
from django.template.response import TemplateResponse


def confirmar_accion_destructiva(mensaje, template_name='admin/confirmar_accion_destructiva.html'):
    """
    Envuelve una acción de admin (`def accion(self, request, queryset)`) con
    una página de confirmación intermedia — mismo patrón que el
    `delete_selected` propio de Django: la selección viaja en campos ocultos
    (`_selected_action` por cada pk, más el nombre de la acción) y solo se
    ejecuta si el POST de vuelta trae `confirmar=si`. Sin eso, un solo POST
    directo a la URL del changelist (una sesión secuestrada scripteando la
    petición, sin pasar por el botón) no dispara el efecto — hace falta el
    segundo salto con el campo de confirmación.
    """
    def decorador(func):
        @wraps(func)
        def wrapper(self, request, queryset):
            if request.POST.get('confirmar') == 'si':
                return func(self, request, queryset)
            contexto = {
                **self.admin_site.each_context(request),
                'title': "¿Confirmar esta acción?",
                'mensaje': mensaje,
                'queryset': queryset,
                'opts': self.model._meta,
                'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
                'action_name': request.POST.get('action'),
            }
            return TemplateResponse(request, template_name, contexto)
        return wrapper
    return decorador
