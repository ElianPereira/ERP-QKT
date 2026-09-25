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
import re

from django import forms

from core_erp import impuestos
from facturacion.choices import RegimenFiscal, regimen_valido_para_persona

TIPO_EVENTO_CHOICES = [(v, v) for v in (
    'Boda', 'XV Años', 'Graduación', 'Cumpleaños', 'Bautizo',
    'Aniversario', 'Evento Corporativo', 'Otro',
)]

SERVICIO_CHOICES = [(v, v) for v in ('EVENTO', 'PASADIA', 'HOSPEDAJE')]

COMO_NOS_ENCONTRO_CHOICES = [(v, v) for v in (
    'Facebook', 'Instagram', 'TikTok', 'Google', 'Recomendación',
    'WhatsApp', 'Visité la quinta', 'Otro',
)]


class CotizadorEnviarForm(forms.Form):
    nombre = forms.CharField(max_length=200, required=False)
    telefono = forms.CharField(max_length=30, required=False)
    email = forms.CharField(max_length=254, required=False)
    # Acotado a los códigos reales de Cotizacion.TIPO_SERVICIO_CHOICES: el valor
    # se guarda tal cual en `Cotizacion.tipo_servicio` (max_length=15), y de él
    # dependen el mínimo a pagar en el portal y los descuentos por servicio.
    servicio = forms.ChoiceField(choices=SERVICIO_CHOICES, required=False)
    fecha = forms.CharField(max_length=10, required=False)
    personas = forms.CharField(max_length=10, required=False)
    # Solo HOSPEDAJE; parseo laxo igual que `personas`, la validación real de
    # rango vive en clean() y en views_cotizador.py.
    noches = forms.CharField(max_length=5, required=False)
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
    regimen_fiscal = forms.ChoiceField(choices=RegimenFiscal.choices, required=False)

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

        if cleaned.get('servicio') == 'HOSPEDAJE':
            try:
                if int(cleaned.get('noches', '').strip() or 0) < 1:
                    errores.append("Indica cuántas noches te vas a quedar.")
            except ValueError:
                errores.append("Indica cuántas noches te vas a quedar.")

        if cleaned.get('requiere_factura'):
            errores.extend(self._errores_fiscales(cleaned))

        if not cleaned.get('acepta_legales'):
            errores.append(
                "Debes aceptar el Aviso de Privacidad y los Términos y Condiciones."
            )

        if errores:
            raise forms.ValidationError(errores)
        return cleaned

    @staticmethod
    def _errores_fiscales(cleaned):
        """CFDI 4.0 exige RFC, nombre, C.P. y régimen del receptor tal como
        vienen en su constancia; sin alguno de ellos el signal de facturación
        caía en silencio a "Público en General" o armaba un CFDI que el PAC
        rechaza (p. ej. régimen de persona física en un RFC de empresa)."""
        errores = []
        rfc = (cleaned.get('rfc') or '').strip().upper()
        tipo_persona = impuestos.tipo_persona_por_rfc(rfc)
        if not tipo_persona or not re.fullmatch(r'[A-ZÑ&0-9]+', rfc):
            errores.append("Ingresa un RFC válido (12 o 13 caracteres).")
        if not (cleaned.get('razon_social') or '').strip():
            errores.append("La razón social es requerida para facturar.")
        if not re.fullmatch(r'\d{5}', (cleaned.get('cp_fiscal') or '').strip()):
            errores.append("El código postal fiscal debe tener 5 dígitos.")
        regimen = cleaned.get('regimen_fiscal')
        if not regimen:
            errores.append("Selecciona tu régimen fiscal.")
        elif tipo_persona and not regimen_valido_para_persona(regimen, tipo_persona):
            persona = 'moral (RFC de 12 caracteres)' if tipo_persona == 'MORAL' else 'física (RFC de 13 caracteres)'
            errores.append(f"El régimen fiscal elegido no corresponde a una persona {persona}.")
        return errores
