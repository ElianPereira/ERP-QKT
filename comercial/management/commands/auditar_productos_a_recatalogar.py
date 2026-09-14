"""
Solo lectura — no modifica nada. Corre en Railway para decidir, con datos
reales, cuáles de los productos que hoy dependen de la receta vieja
(Insumo/SubProducto) son seguros de desactivar (`visible_cotizador=False`)
sin afectar Evento/Pasadía/Hospedaje ni perder trazabilidad de cotizaciones
ya reales.

No tiene relación con Arrendamiento — es el otro pendiente de la sesión:
el intento de borrar en bloque estos productos desde el admin lo bloqueó
`ProductoComponente.producto_hijo` (PROTECT), porque siguen siendo
componente de los paquetes "Paquete Básico/Premium/Lujo 50/100 Personas".

Uso en Railway:

    python manage.py auditar_productos_a_recatalogar

Para cada producto de la lista reporta: en qué servicios sigue apareciendo
(evento/pasadía/hospedaje/arrendamiento), si sigue visible, si es
componente de algún paquete (lo que bloquea un DELETE), y cuántas
cotizaciones —y de esas, cuántas ya no son un simple borrador— lo usaron
alguna vez. Esa última columna es la señal real de riesgo: un producto sin
ningún uso fuera de BORRADOR se puede desactivar (o incluso borrar) sin
perder ningún historial de verdad.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from comercial.models import Cotizacion, ItemCotizacion, Producto
from comercial.roles_cotizador import normalizar

# Nombres tal como aparecían en la pantalla de "eliminar múltiples objetos"
# que se bloqueó — normalizados (sin acentos) para no fallar por un typo o
# una tilde, mismo criterio que ya usa `roles_cotizador.normalizar`.
NOMBRES_A_REVISAR = (
    'mobiliario basico',
    'paquete esencial qkt',
    'refrescos y mezcladores',
    'taquiza y botanas',
    'paquete tiffany de mobilairio',
    'paquete tiffany de mobiliario',
    'platillo gourmet y botanas',
    'licores nacionales',
    'habitacion otoch para anfitriones pernocta',
    'paquete crossback de mobiliario',
    "habitacion ka'an para anfitriones pernocta",
    'cerveza nacional',
    'hora extra de arrendamiento de la quinta',
    'paquete basico 50 personas',
    'paquete basico 100 personas',
    'paquete premium 50 personas',
    'paquete premium 100 personas',
    'paquete lujo para 50 personas',
    'paquete lujo para 100 personas',
)

ESTADOS_SIN_COBRO_REAL = ('BORRADOR', 'CANCELADA', 'EXPIRADA')

# Comillas rectas y curvas (' ' ' `) — un nombre capturado a mano en el admin
# puede traer cualquiera de las cuatro sin que se note a simple vista (p. ej.
# "Ka'an" con apóstrofe curvo vs. el recto de esta lista), y `roles_cotizador
# .normalizar()` solo quita acentos, no esto. Se quitan aparte, solo para
# esta comparación — no se toca `normalizar()`, que otros módulos comparten.
_COMILLAS = str.maketrans('', '', "'’‘`")


def _clave(texto):
    return normalizar(texto).translate(_COMILLAS)


class Command(BaseCommand):
    help = "Audita, sin modificar nada, el riesgo real de desactivar/borrar cada producto de la lista."

    def handle(self, *args, **opciones):
        objetivos = {_clave(n) for n in NOMBRES_A_REVISAR}
        productos = [p for p in Producto.objects.all().order_by('nombre')
                     if _clave(p.nombre) in objetivos]

        encontrados = {_clave(p.nombre) for p in productos}
        faltantes = objetivos - encontrados
        if faltantes:
            self.stdout.write(self.style.WARNING(
                f'{len(faltantes)} nombre(s) de la lista no se encontraron en el catálogo '
                '(puede ser normal si ya se resolvió a mano o el nombre cambió):'
            ))
            for f in sorted(faltantes):
                self.stdout.write(f'   - {f}')
            self.stdout.write('')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'{len(productos)} producto(s) de la lista encontrados en el catálogo'
        ))

        for p in productos:
            self._reportar(p)

    def _reportar(self, p):
        servicios = ', '.join(s for s, activo in (
            ('Evento', p.cotizador_evento),
            ('Pasadía', p.cotizador_pasadia),
            ('Hospedaje', p.cotizador_hospedaje),
            ('Arrendamiento', p.cotizador_arrendamiento),
        ) if activo) or '(ninguno)'

        items = ItemCotizacion.objects.filter(producto=p)
        total_usos = items.count()
        usos_reales = items.exclude(
            cotizacion__estado__in=ESTADOS_SIN_COBRO_REAL,
        ).count()

        paquetes_que_lo_incluyen = list(
            p.incluido_en_paquetes.values_list('producto_padre__nombre', flat=True)
        )

        precio = (Decimal(str(p.precio_venta_fijo)) if p.precio_venta_fijo
                  else Decimal(str(p.sugerencia_precio())))

        self.stdout.write('')
        self.stdout.write(self.style.WARNING(p.nombre) if usos_reales else self.style.SUCCESS(p.nombre))
        self.stdout.write(f'   visible_cotizador={p.visible_cotizador}  es_paquete={p.es_paquete}  precio={precio:,.2f}')
        self.stdout.write(f'   activo en: {servicios}')
        self.stdout.write(
            f'   usado en {total_usos} cotización(es) en total, de las cuales {usos_reales} '
            'NO son borrador/cancelada/expirada (uso real con cobro de por medio)'
        )
        if paquetes_que_lo_incluyen:
            self.stdout.write(
                '   es componente de (esto bloquea un DELETE, no un desactivar): '
                + ', '.join(paquetes_que_lo_incluyen)
            )
        if p.es_paquete:
            n_hijos = p.productos_incluidos.count()
            self.stdout.write(f'   como paquete, incluye {n_hijos} producto(s)')

        if usos_reales == 0 and not p.es_paquete:
            self.stdout.write(self.style.SUCCESS(
                '   -> sin uso real: desactivar (o incluso borrar, quitando antes el vínculo '
                'del paquete que lo protege) es seguro.'
            ))
        elif usos_reales == 0 and p.es_paquete:
            self.stdout.write(self.style.SUCCESS(
                '   -> sin uso real: desactivar es seguro (borrarlo de una vez libera a sus '
                'componentes de la protección, si a su vez no se usan en otro lado).'
            ))
        else:
            self.stdout.write(self.style.ERROR(
                '   -> tiene uso real: NO borrar (perdería la trazabilidad de esas '
                'cotizaciones). Si ya no se vende, desactivar (visible_cotizador=False) en '
                'vez de eliminar.'
            ))
