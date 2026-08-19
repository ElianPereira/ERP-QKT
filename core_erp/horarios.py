"""
Formato de horas visibles al cliente — fuente única.

Todo horario que un cliente vea (cotizador, portal, contrato PDF,
notificaciones) se muestra en 12 h con a.m./p.m. explícito, nunca en 24 h a
secas ("14:00"): pedido directo del propietario para evitar confusiones.
No usa `strftime('%I:%M %p')` porque depende del locale del sistema (puede
salir "AM"/"PM" en mayúsculas sin puntos, o en otro idioma); esta versión es
determinista sin importar el locale del servidor.
"""


def formato_hora_ampm(hora):
    """`datetime.time` → "2:00 p.m." / "10:00 a.m."; cadena vacía si no hay hora."""
    if not hora:
        return ''
    h12 = hora.hour % 12 or 12
    sufijo = 'a.m.' if hora.hour < 12 else 'p.m.'
    return f"{h12}:{hora.minute:02d} {sufijo}"
