from django import template

register = template.Library()


@register.filter
def get_item(diccionario, clave):
    return diccionario.get(clave, clave)
