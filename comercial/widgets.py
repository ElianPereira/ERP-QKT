"""
Widgets de formulario reutilizables para el admin de comercial.
"""
from django import forms
from django.utils.safestring import mark_safe


class TimeSlotWidget(forms.TimeInput):
    """
    Selector de hora con lista de franjas horarias buscable (patrón usado
    por Google Calendar / Airbnb), pensado para funcionar igual en mouse,
    teclado y pantallas táctiles.

    Sustituye al AdminTimeWidget por defecto de Django, cuyo popover
    "Elija una hora" solo ofrece 5 atajos fijos (Ahora/Medianoche/6 a.m./
    Mediodía/6 p.m.) y no tiene equivalente táctil utilizable en móvil.
    """

    def __init__(self, attrs=None):
        base_attrs = {
            'class': 'time-slot-input',
            'autocomplete': 'off',
            'inputmode': 'numeric',
            'placeholder': 'HH:MM',
        }
        if attrs:
            base_attrs.update(attrs)
        super().__init__(attrs=base_attrs, format='%H:%M')

    def render(self, name, value, attrs=None, renderer=None):
        input_html = super().render(name, value, attrs, renderer)
        return mark_safe(
            '<div class="time-slot-field">'
            + input_html
            + '<button type="button" class="time-slot-toggle" aria-label="Elegir hora" tabindex="-1">'
              '<span aria-hidden="true">&#128337;</span></button>'
              '<div class="time-slot-dropdown" role="listbox" aria-label="Horarios disponibles"></div>'
              '</div>'
        )

    class Media:
        css = {'all': ('css/time_slot_picker.css',)}
        js = ('js/time_slot_picker.js',)
