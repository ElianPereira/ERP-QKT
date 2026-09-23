"""Borra las tablas de la app Airbnb (Issue #311, fase 2). Los datos ya los
borró `contabilidad.0020_retiro_airbnb_datos`, de la que depende."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('airbnb', '0007_alter_pagoairbnb_codigo_confirmacion_and_more'),
        ('contabilidad', '0020_retiro_airbnb_datos'),
    ]

    operations = [
        migrations.DeleteModel(name='DepositoConciliado'),
        migrations.DeleteModel(name='ConflictoCalendario'),
        migrations.DeleteModel(name='PagoAirbnb'),
        migrations.DeleteModel(name='ReservaAirbnb'),
        migrations.DeleteModel(name='AnuncioAirbnb'),
    ]
