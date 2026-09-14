"""
Solo lectura — no modifica nada. Corre en Railway para saber qué productos
son EXCLUSIVOS de Arrendamiento (línea de negocio que se está dando de baja)
y cuáles se comparten con Evento/Pasadía/Hospedaje.

Uso en Railway:

    python manage.py auditar_productos_arrendamiento

Un producto "exclusivo" (cotizador_arrendamiento=True y los otros tres
flags en False) se puede desactivar sin afectar ningún otro servicio. Un
producto "compartido" (ej. un DJ o una taquiza que también sirve para
Eventos) solo debe perder el flag de Arrendamiento, no desaparecer del
todo.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from comercial.models import Producto


class Command(BaseCommand):
    help = "Audita, sin modificar nada, qué productos son exclusivos de Arrendamiento."

    def handle(self, *args, **opciones):
        qs = Producto.objects.filter(cotizador_arrendamiento=True).order_by('nombre')
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Producto con cotizador_arrendamiento=True — {qs.count()} registro(s)'
        ))
        if not qs:
            self.stdout.write(self.style.SUCCESS('   Ninguno.'))
            return

        exclusivos, compartidos = [], []
        for p in qs:
            otro_servicio = p.cotizador_evento or p.cotizador_pasadia or p.cotizador_hospedaje
            (compartidos if otro_servicio else exclusivos).append(p)

        self.stdout.write(self.style.WARNING(
            f'\n{len(exclusivos)} EXCLUSIVO(S) de Arrendamiento — candidatos a desactivar '
            '(visible_cotizador=False):'
        ))
        for p in exclusivos:
            precio = Decimal(str(p.precio_venta_fijo)) if p.precio_venta_fijo else Decimal(str(p.sugerencia_precio()))
            self.stdout.write(
                f'   - {p.nombre[:44]:<44} precio={precio:>10,.2f}  visible_cotizador={p.visible_cotizador}'
            )

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(compartidos)} COMPARTIDO(S) con otro servicio — solo se les quita el flag de '
            'Arrendamiento, siguen visibles para lo demás:'
        ))
        for p in compartidos:
            servicios = ', '.join(s for s, activo in (
                ('Evento', p.cotizador_evento),
                ('Pasadía', p.cotizador_pasadia),
                ('Hospedaje', p.cotizador_hospedaje),
            ) if activo)
            self.stdout.write(f'   - {p.nombre[:44]:<44} también en: {servicios}')
