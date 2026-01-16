from django import forms

class CalculadoraForm(forms.Form):
    TIPO_EVENTO = [
        ('boda', 'Boda / XV Años (Consumo Alto)'),
        ('casual', 'Fiesta Casual / Cumpleaños'),
        ('empresarial', 'Evento Empresarial (Consumo Moderado)'),
    ]
    
    CLIMA = [
        ('calor', 'Día / Calor (Mérida 🔥)'),
        ('fresco', 'Noche / Aire Acondicionado'),
    ]

    invitados = forms.IntegerField(
        label="Número de Invitados", 
        min_value=1, 
        initial=100, 
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    horas = forms.IntegerField(
        label="Duración (Horas)", 
        min_value=1, 
        initial=5, 
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    tipo_evento = forms.ChoiceField(
        choices=TIPO_EVENTO, 
        initial='boda', 
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    clima = forms.ChoiceField(
        choices=CLIMA, 
        initial='calor', 
        label="Clima / Horario", 
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    # Checkboxes
    calcular_cerveza = forms.BooleanField(
        required=False, 
        initial=True, 
        label="Incluir Cerveza"
    )
    calcular_destilados = forms.BooleanField(
        required=False, 
        initial=True, 
        label="Incluir Destilados (Whisky, Tequila, etc.)"
    )