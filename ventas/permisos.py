"""Grupo `Vendedor` (Issue #505): quién es y qué no puede ver ni hacer."""
GRUPO_VENDEDOR = 'Vendedor'


def es_vendedor(usuario) -> bool:
    """Usuario del grupo Vendedor que no es Dirección. Un superusuario nunca queda limitado."""
    if not getattr(usuario, 'is_authenticated', False) or usuario.is_superuser:
        return False
    return usuario.groups.filter(name=GRUPO_VENDEDOR).exists()


def no_para_vendedor(vista):
    """Decorador: la vista financiera no se abre al grupo Vendedor aunque tenga el permiso de modelo
    (los reportes de ventas comparten `view_cotizacion`/`view_pago` con el trabajo diario)."""
    from functools import wraps

    from django.core.exceptions import PermissionDenied

    @wraps(vista)
    def envuelta(request, *args, **kwargs):
        if es_vendedor(request.user):
            raise PermissionDenied
        return vista(request, *args, **kwargs)
    return envuelta
