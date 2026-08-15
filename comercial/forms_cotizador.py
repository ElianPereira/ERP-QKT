"""
Formulario del cotizador público (SEC-VAL-001, backlog de seguridad orden 24).

Reemplaza la validación manual dispersa en `views_cotizador.py::cotizador_enviar`.
Los campos que en el formulario público son un <select>/chips de opciones
fijas (tipo_evento, como_nos_encontro) quedan acotados a esas mismas
opciones: antes eran texto libre sin ninguna restricción y alimentaban
`nombre_evento` directamente.

Todos los campos son `required=False` a nivel de Field: la lógica de
"obligatorio" vive en `clean()`, igual que antes de este cambio, para
conservar exactamente los mismos mensajes que ya muestra el formulario
público (ninguno de estos textos está mal escrito por casualidad, hay un
test que compara el string exacto de la falta de consentimiento). Lo que el
Field añade es tipo, longitud máxima y choices cerradas donde antes
cualquier string pasaba sin límite.
"""
from django import forms

TIPO_EVENTO_CHOICES = [(v, v) for v in (
    'Boda', 'XV Años', 'Graduación', 'Cumpleaños', 'Bautizo',
    'Aniversario', 'Evento Corporativo', 'Otro',
)]

COMO_NOS_ENCONTRO_CHOICES = [(v, v) for v in (
    'Facebook', 'Instagram', 'TikTok', 'Google', 'Recomendación',
    'WhatsApp', 'Visité la quinta', 'Otro',
)]


class CotizadorEnviarForm(forms.Form):
    nombre = forms.CharField(max_length=200, required=False)
    telefono = forms.CharField(max_length=30, required=False)
    email = forms.CharField(max_length=254, required=False)
    servicio = forms.CharField(max_length=20, required=False)
    fecha = forms.CharField(max_length=10, required=False)
    personas = forms.CharField(max_length=10, required=False)
    hora_inicio = forms.CharField(max_length=10, required=False)
    hora_fin = forms.CharField(max_length=10, required=False)
    tipo_evento = forms.ChoiceField(choices=TIPO_EVENTO_CHOICES, required=False)
    notas = forms.CharField(max_length=300, required=False)
    como_nos_encontro = forms.ChoiceField(choices=COMO_NOS_ENCONTRO_CHOICES, required=False)
    acepta_legales = forms.BooleanField(required=False)
    requiere_factura = forms.BooleanField(required=False)
    rfc = forms.CharField(max_length=50, required=False)
    razon_social = forms.CharField(max_length=300, required=False)
    cp_fiscal = forms.CharField(max_length=20, required=False)

    def clean(self):
        cleaned = super().clean()
        errores = []

        if not cleaned.get('nombre', '').strip():
            errores.append("El nombre es requerido.")

        telefono_digitos = ''.join(filter(str.isdigit, cleaned.get('telefono', '') or ''))
        if len(telefono_digitos) < 10:
            errores.append("El teléfono debe tener al menos 10 dígitos.")

        if not cleaned.get('servicio', '').strip():
            errores.append("Selecciona un tipo de servicio.")

        if not cleaned.get('fecha', '').strip():
            errores.append("La fecha es requerida.")

        if not cleaned.get('acepta_legales'):
            errores.append(
                "Debes aceptar el Aviso de Privacidad y los Términos y Condiciones."
            )

        if errores:
            raise forms.ValidationError(errores)
        return cleaned
