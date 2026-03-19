# Migration to update SolicitudFactura and add ConfiguracionContador

from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('facturacion', '0001_initial'),  # Ajusta según tu última migración
        ('comercial', '0026_add_solicitar_factura'),  # Para la FK a Pago
    ]

    operations = [
        # ─── ConfiguracionContador ────────────────────────────
        migrations.CreateModel(
            name='ConfiguracionContador',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=200, verbose_name='Nombre del Contador')),
                ('email', models.EmailField(max_length=254, verbose_name='Email')),
                ('telefono_whatsapp', models.CharField(help_text='Con código de país, ej: 529991234567', max_length=15, verbose_name='WhatsApp')),
                ('notas', models.TextField(blank=True, verbose_name='Notas / Instrucciones')),
                ('activo', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Configuración del Contador',
                'verbose_name_plural': 'Configuración del Contador',
            },
        ),
        
        # ─── Nuevos campos en SolicitudFactura ────────────────
        migrations.AddField(
            model_name='solicitudfactura',
            name='pago',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='solicitudes_factura',
                to='comercial.pago',
                verbose_name='Pago Origen'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='estado',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de Enviar'),
                    ('ENVIADA', 'Enviada al Contador'),
                    ('FACTURADA', 'Factura Recibida'),
                    ('CANCELADA', 'Cancelada')
                ],
                default='PENDIENTE',
                max_length=15,
                verbose_name='Estado'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='rfc',
            field=models.CharField(default='', max_length=13, verbose_name='RFC'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='razon_social',
            field=models.CharField(default='', max_length=300, verbose_name='Razón Social'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='codigo_postal',
            field=models.CharField(default='', max_length=5, verbose_name='C.P. Fiscal'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='regimen_fiscal',
            field=models.CharField(
                choices=[
                    ('601', 'General de Ley Personas Morales'),
                    ('603', 'Personas Morales con Fines no Lucrativos'),
                    ('605', 'Sueldos y Salarios e Ingresos Asimilados a Salarios'),
                    ('606', 'Arrendamiento'),
                    ('607', 'Régimen de Enajenación o Adquisición de Bienes'),
                    ('608', 'Demás ingresos'),
                    ('610', 'Residentes en el Extranjero sin Establecimiento Permanente en México'),
                    ('611', 'Ingresos por Dividendos (socios y accionistas)'),
                    ('612', 'Personas Físicas con Actividades Empresariales y Profesionales'),
                    ('614', 'Ingresos por intereses'),
                    ('615', 'Régimen de los ingresos por obtención de premios'),
                    ('616', 'Sin obligaciones fiscales'),
                    ('620', 'Sociedades Cooperativas de Producción que optan por diferir sus ingresos'),
                    ('621', 'Incorporación Fiscal'),
                    ('622', 'Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras'),
                    ('623', 'Opcional para Grupos de Sociedades'),
                    ('624', 'Coordinados'),
                    ('625', 'Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas'),
                    ('626', 'Régimen Simplificado de Confianza'),
                ],
                default='616',
                max_length=3,
                verbose_name='Régimen Fiscal'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='uso_cfdi',
            field=models.CharField(
                choices=[
                    ('G01', 'Adquisición de mercancías'),
                    ('G02', 'Devoluciones, descuentos o bonificaciones'),
                    ('G03', 'Gastos en general'),
                    ('I01', 'Construcciones'),
                    ('I02', 'Mobilario y equipo de oficina por inversiones'),
                    ('I03', 'Equipo de transporte'),
                    ('I04', 'Equipo de computo y accesorios'),
                    ('I05', 'Dados, troqueles, moldes, matrices y herramental'),
                    ('I06', 'Comunicaciones telefónicas'),
                    ('I07', 'Comunicaciones satelitales'),
                    ('I08', 'Otra maquinaria y equipo'),
                    ('D01', 'Honorarios médicos, dentales y gastos hospitalarios'),
                    ('D02', 'Gastos médicos por incapacidad o discapacidad'),
                    ('D03', 'Gastos funerales'),
                    ('D04', 'Donativos'),
                    ('D05', 'Intereses reales efectivamente pagados por créditos hipotecarios'),
                    ('D06', 'Aportaciones voluntarias al SAR'),
                    ('D07', 'Primas por seguros de gastos médicos'),
                    ('D08', 'Gastos de transportación escolar obligatoria'),
                    ('D09', 'Depósitos en cuentas para el ahorro, primas que tengan como base planes de pensiones'),
                    ('D10', 'Pagos por servicios educativos (colegiaturas)'),
                    ('S01', 'Sin efectos fiscales'),
                    ('CP01', 'Pagos'),
                    ('CN01', 'Nómina'),
                ],
                default='G03',
                max_length=4,
                verbose_name='Uso CFDI'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='fecha_pago',
            field=models.DateField(blank=True, null=True, verbose_name='Fecha de Pago'),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='enviada_por',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='solicitudes_enviadas',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Enviada por'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='fecha_envio',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Fecha de Envío'),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='metodo_envio',
            field=models.CharField(
                blank=True,
                choices=[('EMAIL', 'Email'), ('WHATSAPP', 'WhatsApp')],
                max_length=20,
                verbose_name='Método de Envío'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='archivo_zip',
            field=models.FileField(
                blank=True, null=True,
                upload_to='facturas_zip/',
                verbose_name='ZIP con PDF y XML',
                help_text='Archivo ZIP con la factura (PDF + XML)'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='archivo_xml',
            field=models.FileField(
                blank=True, null=True,
                upload_to='facturas_xml/',
                verbose_name='Factura XML'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='uuid_factura',
            field=models.CharField(
                blank=True,
                max_length=36,
                verbose_name='UUID/Folio Fiscal',
                help_text='Se extrae automáticamente del XML'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='fecha_factura',
            field=models.DateField(blank=True, null=True, verbose_name='Fecha de Factura'),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='notas',
            field=models.TextField(blank=True, verbose_name='Notas / Observaciones'),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='created_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='solicitudes_creadas',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Creada por'
            ),
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='solicitudfactura',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        
        # ─── Índices ──────────────────────────────────────────
        migrations.AddIndex(
            model_name='solicitudfactura',
            index=models.Index(fields=['estado', '-fecha_solicitud'], name='facturacion_estado_fecha_idx'),
        ),
        migrations.AddIndex(
            model_name='solicitudfactura',
            index=models.Index(fields=['cliente', '-fecha_solicitud'], name='facturacion_cliente_fecha_idx'),
        ),
    ]