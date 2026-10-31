"""Pixel de Meta con banner de cookies propio (Issue #468, fase 6)."""
from django import template
from django.conf import settings

register = template.Library()


@register.inclusion_tag('marketing/pixel_meta.html')
def pixel_meta():
    """Banner de cookies + Pixel. No renderiza nada sin `META_PIXEL_ID` (solo dígitos)."""
    pixel_id = (settings.META_PIXEL_ID or '').strip()
    return {'pixel_id': pixel_id if pixel_id.isdigit() else ''}
