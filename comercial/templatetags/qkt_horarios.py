from django import template

from core_erp.horarios import formato_hora_ampm

register = template.Library()

register.filter('hora_ampm', formato_hora_ampm)
