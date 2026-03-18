# Migration to update regimen_fiscal choices with SAT catalog

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0002_cargar_catalogo_sat'),
    ]

    operations = [
        # Ampliar el campo para aceptar las claves SAT (ej: "626")
        migrations.AlterField(
            model_name='unidadnegocio',
            name='regimen_fiscal',
            field=models.CharField(
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
                default='612',
                max_length=3,
                verbose_name='Régimen fiscal SAT'
            ),
        ),
    ]