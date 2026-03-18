"""
Signals del Módulo de Contabilidad
==================================
Genera pólizas automáticamente cuando se crean registros en otros módulos.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings


# ==========================================
# FLAG PARA DESHABILITAR TEMPORALMENTE
# ==========================================

CONTABILIDAD_SIGNALS_ENABLED = getattr(settings, 'CONTABILIDAD_SIGNALS_ENABLED', True)


def get_system_user():
    """Obtiene o crea un usuario del sistema para operaciones automáticas."""
    from django.contrib.auth.models import User
    user, _ = User.objects.get_or_create(
        username='sistema_contable',
        defaults={
            'first_name': 'Sistema',
            'last_name': 'Contable',
            'is_active': False
        }
    )
    return user


# Los signals para Pago, PagoAirbnb, Compra y Nómina se pueden habilitar
# después de configurar las cuentas contables iniciales.
# Por ahora dejamos el archivo preparado pero sin signals activos
# para evitar errores durante la primera migración.

# Descomentar después de ejecutar: python manage.py cargar_catalogo_sat

# @receiver(post_save, sender='comercial.Pago')
# def contabilizar_pago_cliente(sender, instance, created, **kwargs):
#     pass

# @receiver(post_save, sender='airbnb.PagoAirbnb')  
# def contabilizar_pago_airbnb(sender, instance, created, **kwargs):
#     pass
