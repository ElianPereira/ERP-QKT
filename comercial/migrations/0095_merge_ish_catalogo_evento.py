# Fusiona las dos ramas de migraciones que quedaron abiertas al mergear el
# mismo día el PR #289 (retiro del catálogo cerrado de Eventos) y el #288
# (ISH del hospedaje directo): las dos partieron de `0092` sin conocerse, así
# que `comercial` quedó con dos hojas y `migrate` se niega a correr por
# completo ("Conflicting migrations detected; multiple leaf nodes"). Eso
# rompe el arranque del contenedor, porque el CMD del Dockerfile es
# `manage.py migrate --noinput && ...`: sin esta fusión el deploy no levanta.
#
# No lleva operaciones, y no las necesita: las dos ramas tocan modelos
# distintos —`Producto` y el catálogo de Eventos por un lado, `Cotizacion`
# por el otro— y ninguna redefine lo que hace la otra.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0094_retirar_catalogo_evento"),
        ("comercial", "0094_tasa_ish_congelada"),
    ]

    operations = []
