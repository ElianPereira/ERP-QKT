"""
Solo lectura — no modifica nada. Corre en Railway para medir el alcance real
de dejar de depender de Insumo/SubProducto como fuente del costo de un
Producto.

Uso en Railway:

    python manage.py auditar_costeo_por_receta

Contexto: el propietario confirmó que en producción SÍ hay datos reales en
Insumo/SubProducto, y que hay Productos SIMPLE (es_paquete=False) enlazados
a ellos vía ComponenteProducto — a diferencia de la copia local de esta
sesión, que estaba vacía. Antes de escribir la migración que retira ese
enlace hace falta ver, contra los datos reales, cuántos Productos quedarían
sin precio si se les quita el costeo por receta (esos son los que habría
que recapturar con "Precio de venta fijo", a mano o en un paso aparte) y
cuántos ya tienen un precio fijo capturado en paralelo (esos se desvinculan
solos, sin pedirle nada al propietario).
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from comercial.models import Insumo, Producto, SubProducto


class Command(BaseCommand):
    help = "Audita, sin modificar nada, qué depende hoy de Insumo/SubProducto."

    def handle(self, *args, **opciones):
        self._insumos()
        self.stdout.write('')
        self._subproductos()
        self.stdout.write('')
        self._productos_simples_con_receta()
        self.stdout.write('')
        self._productos_paquete_de_productos()

    def _insumos(self):
        qs = Insumo.objects.all().order_by('nombre')
        self.stdout.write(self.style.MIGRATE_HEADING(f'1. Insumo — {qs.count()} registro(s)'))
        for i in qs:
            self.stdout.write(
                f'   {i.nombre[:40]:<40} costo={i.costo_unitario:>10,.2f}  '
                f'presentación={i.presentacion or "—"}'
            )

    def _subproductos(self):
        qs = SubProducto.objects.all().order_by('nombre')
        self.stdout.write(self.style.MIGRATE_HEADING(f'2. SubProducto — {qs.count()} registro(s)'))
        for s in qs:
            n_insumos = s.receta.count()
            self.stdout.write(
                f'   {s.nombre[:40]:<40} costo={Decimal(str(s.costo_insumos())):>10,.2f}  '
                f'insumos en su receta={n_insumos}'
            )

    def _productos_simples_con_receta(self):
        qs = (Producto.objects.filter(es_paquete=False)
              .prefetch_related('componentes')
              .order_by('nombre'))
        con_receta = [p for p in qs if p.componentes.exists()]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'3. Producto SIMPLE enlazado a SubProducto — {len(con_receta)} de {qs.count()} producto(s) simples'
        ))
        if not con_receta:
            self.stdout.write(self.style.SUCCESS('   Ninguno — no hay nada que recapturar.'))
            return

        ya_tiene_fijo = [p for p in con_receta if p.precio_venta_fijo and p.precio_venta_fijo > 0]
        sin_fijo = [p for p in con_receta if not (p.precio_venta_fijo and p.precio_venta_fijo > 0)]

        self.stdout.write(self.style.SUCCESS(
            f'   {len(ya_tiene_fijo)} ya tiene(n) "Precio de venta fijo" capturado — '
            'se desvinculan solos, sin pedirte nada:'
        ))
        for p in ya_tiene_fijo:
            self.stdout.write(f'     - {p.nombre} (fijo={p.precio_venta_fijo})')

        self.stdout.write(self.style.WARNING(
            f'   {len(sin_fijo)} NO tiene(n) precio fijo — hoy su precio sale de sumar sus '
            'subproductos (costo actual, más margen). Habría que decidir para cada uno si se le '
            'captura un precio fijo (recomendado: usar el costo calculado como punto de partida) '
            'o si se recrea desde cero, según lo que confirmes:'
        ))
        for p in sin_fijo:
            costo = Decimal(str(p.calcular_costo()))
            precio_sugerido = Decimal(str(p.sugerencia_precio()))
            self.stdout.write(
                f'     - {p.nombre[:40]:<40} costo={costo:>10,.2f}  '
                f'precio sugerido hoy (costo×margen)={precio_sugerido:>10,.2f}  '
                f'visible_cotizador={p.visible_cotizador}'
            )

    def _productos_paquete_de_productos(self):
        qs = (Producto.objects.filter(es_paquete=True)
              .prefetch_related('productos_incluidos')
              .order_by('nombre'))
        con_hijos = [p for p in qs if p.productos_incluidos.exists()]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'4. Producto PAQUETE (de otros Productos) — {len(con_hijos)} registro(s), fuera de este cambio'
        ))
        self.stdout.write(
            '   Estos NO dependen de Insumo/SubProducto, dependen de otros Productos '
            '(ComponenteProducto→Producto) — se quedan igual, no forman parte de este retiro.'
        )
        for p in con_hijos:
            self.stdout.write(f'     - {p.nombre} ({p.productos_incluidos.count()} producto(s) incluido(s))')
