"""
Rol de un producto dentro del cotizador público.

Un `Producto` con `rol_cotizador` es la **línea base** de un servicio: la que el
cotizador agrega solo al elegir Evento o Pasadía (el arrendamiento de la quinta),
sin que el cliente marque nada. Vive aparte de `views_cotizador` porque lo usan
también la migración 0072 —que siembra los roles del catálogo ya capturado— y sus
tests.
"""
import unicodedata

# Nombre (normalizado, sin acentos) → rol, para el catálogo capturado a mano.
# Es una red de seguridad de una sola pasada: una vez marcado el rol en el admin,
# el nombre deja de importar.
ROLES_POR_NOMBRE = (
    ('paquete esencial', 'BASE_EVENTO'),
    ('paquete pasadia', 'BASE_PASADIA'),
    ('hora extra de arrendamiento', 'HORA_EXTRA'),
)


def normalizar(texto):
    """Minúsculas y sin acentos, para comparar nombres capturados a mano.

    `nombre__icontains` se traduce a un LIKE: insensible a mayúsculas pero
    **sensible a acentos** tanto en SQLite como en PostgreSQL. Por eso buscar
    'Pasadia' nunca encontraba 'Paquete Pasadía QKT'.
    """
    if not texto:
        return ''
    descompuesto = unicodedata.normalize('NFKD', str(texto))
    return ''.join(c for c in descompuesto if not unicodedata.combining(c)).casefold().strip()


def sembrar_roles(modelo_producto):
    """Marca `rol_cotizador` en los productos que ya existen, por su nombre.

    Idempotente y conservador: si un rol ya está asignado no se toca, y solo se
    marca el primer producto que case con cada nombre. Recibe el modelo por
    parámetro para poder llamarse desde una migración con el modelo histórico.
    """
    marcados = []
    for parcial, rol in ROLES_POR_NOMBRE:
        if modelo_producto.objects.filter(rol_cotizador=rol).exists():
            continue
        for producto in modelo_producto.objects.order_by('id'):
            if parcial in normalizar(producto.nombre):
                producto.rol_cotizador = rol
                producto.save(update_fields=['rol_cotizador'])
                marcados.append((producto.nombre, rol))
                break
    return marcados
