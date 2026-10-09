"""
Formato de horas visibles al cliente — fuente única.

Todo horario que un cliente vea (cotizador, portal, contrato PDF,
notificaciones) se muestra en 12 h con a.m./p.m. explícito, nunca en 24 h a
secas ("14:00"): pedido directo del propietario para evitar confusiones.
No usa `strftime('%I:%M %p')` porque depende del locale del sistema (puede
salir "AM"/"PM" en mayúsculas sin puntos, o en otro idioma); esta versión es
determinista sin importar el locale del servidor.
"""
from datetime import datetime

from django.utils import timezone


def formato_hora_ampm(hora):
    """`datetime.time` → "2:00 p.m." / "10:00 a.m."; cadena vacía si no hay hora.

    Un `datetime` con zona (como sale de la base) se pasa antes a la hora local:
    si no, un documento mostraría la hora UTC.
    """
    if not hora:
        return ''
    if isinstance(hora, datetime) and timezone.is_aware(hora):
        hora = timezone.localtime(hora)
    h12 = hora.hour % 12 or 12
    sufijo = 'a.m.' if hora.hour < 12 else 'p.m.'
    return f"{h12}:{hora.minute:02d} {sufijo}"
