from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0065_pago_comision_tpv"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="pago",
            options={
                "verbose_name": "Pago Aprobado",
                "verbose_name_plural": "Pagos Aprobados",
            },
        ),
    ]
