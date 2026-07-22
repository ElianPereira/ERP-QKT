from django.db import migrations, models


class Migration(migrations.Migration):
    """Amplía RegimenFiscal a los 23 códigos del catálogo SAT c_RegimenFiscal
    (antes solo tenía 19; le faltaban 609/628/629/630). Ver también
    facturacion/migrations/0004_alter_solicitudfactura_regimen_fiscal.py.
    Solo cambia metadata de choices, no el esquema ni los datos existentes.
    """

    dependencies = [
        ("comercial", "0058_compra_proveedor_catalogo"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cliente",
            name="regimen_fiscal",
            field=models.CharField(
                max_length=3,
                blank=True,
                null=True,
                default='616',
                choices=[
                    ('601', '601 - General de Ley Personas Morales'),
                    ('603', '603 - Personas Morales con Fines no Lucrativos'),
                    ('605', '605 - Sueldos y Salarios e Ingresos Asimilados a Salarios'),
                    ('606', '606 - Arrendamiento'),
                    ('607', '607 - Régimen de Enajenación o Adquisición de Bienes'),
                    ('608', '608 - Demás ingresos'),
                    ('609', '609 - Consolidación'),
                    ('610', '610 - Residentes en el Extranjero sin Establecimiento Permanente en México'),
                    ('611', '611 - Ingresos por Dividendos (socios y accionistas)'),
                    ('612', '612 - Personas Físicas con Actividades Empresariales y Profesionales'),
                    ('614', '614 - Ingresos por intereses'),
                    ('615', '615 - Régimen de los ingresos por obtención de premios'),
                    ('616', '616 - Sin obligaciones fiscales'),
                    ('620', '620 - Sociedades Cooperativas de Producción que optan por diferir sus ingresos'),
                    ('621', '621 - Incorporación Fiscal'),
                    ('622', '622 - Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras'),
                    ('623', '623 - Opcional para Grupos de Sociedades'),
                    ('624', '624 - Coordinados'),
                    ('625', '625 - Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas'),
                    ('626', '626 - Régimen Simplificado de Confianza'),
                    ('628', '628 - Hidrocarburos'),
                    ('629', '629 - De los Regímenes Fiscales Preferentes y de las Empresas Multinacionales'),
                    ('630', '630 - Enajenación de acciones en bolsa de valores'),
                ],
            ),
        ),
    ]
