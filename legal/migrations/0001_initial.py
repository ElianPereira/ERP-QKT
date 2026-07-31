import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('comercial', '0067_itemcotizacion_precio_unitario_sin_iva'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentoLegal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('AVISO_PRIVACIDAD', 'Aviso de Privacidad'), ('AVISO_SIMPLIFICADO', 'Aviso de Privacidad Simplificado'), ('TERMINOS', 'Términos y Condiciones'), ('POLITICA_CANCELACION', 'Política de Cancelación y Reembolso'), ('REGLAMENTO', 'Reglamento Interno')], db_index=True, max_length=32)),
                ('version', models.CharField(help_text="Ej. '2.0'", max_length=16)),
                ('titulo', models.CharField(max_length=200)),
                ('contenido_md', models.TextField(help_text='Contenido en Markdown. Inmutable una vez guardado.')),
                ('hash_contenido', models.CharField(db_index=True, editable=False, max_length=64)),
                ('vigente_desde', models.DateField()),
                ('vigente', models.BooleanField(db_index=True, default=False)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('creado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='documentos_legales_creados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Documento legal',
                'verbose_name_plural': 'Documentos legales',
                'ordering': ['tipo', '-vigente_desde'],
            },
        ),
        migrations.CreateModel(
            name='Finalidad',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('clave', models.SlugField(max_length=40, unique=True)),
                ('nombre', models.CharField(max_length=200)),
                ('descripcion', models.TextField(blank=True)),
                ('requiere_consentimiento', models.BooleanField(default=True)),
                ('orden', models.PositiveSmallIntegerField(default=0)),
                ('activa', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Finalidad del tratamiento',
                'verbose_name_plural': 'Finalidades del tratamiento',
                'ordering': ['orden', 'clave'],
            },
        ),
        migrations.CreateModel(
            name='SolicitudARCO',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('folio', models.CharField(editable=False, max_length=24, unique=True)),
                ('tipo', models.CharField(choices=[('ACCESO', 'Acceso'), ('RECTIFICACION', 'Rectificación'), ('CANCELACION', 'Cancelación'), ('OPOSICION', 'Oposición'), ('REVOCACION', 'Revocación del consentimiento')], max_length=16)),
                ('titular_nombre', models.CharField(max_length=200)),
                ('correo', models.EmailField(max_length=254)),
                ('telefono', models.CharField(blank=True, max_length=20)),
                ('descripcion', models.TextField()),
                ('identificacion', models.FileField(blank=True, upload_to='arco/identificaciones/')),
                ('estado', models.CharField(choices=[('RECIBIDA', 'Recibida'), ('EN_TRAMITE', 'En trámite'), ('PREVENCION', 'Prevención (información faltante)'), ('PROCEDENTE', 'Procedente'), ('IMPROCEDENTE', 'Improcedente')], default='RECIBIDA', max_length=16)),
                ('recibida_en', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('fecha_limite', models.DateField(editable=False)),
                ('respondida_en', models.DateTimeField(blank=True, null=True)),
                ('respuesta', models.TextField(blank=True)),
                ('atendida_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='solicitudes_arco', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Solicitud ARCO',
                'verbose_name_plural': 'Solicitudes ARCO',
                'ordering': ['-recibida_en'],
            },
        ),
        migrations.CreateModel(
            name='AceptacionLegal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('correo', models.EmailField(help_text='Correo declarado al momento de aceptar.', max_length=254)),
                ('snapshot_documentos', models.JSONField(default=list, help_text="[{'tipo':..., 'version':..., 'hash':...}] congelado al aceptar.")),
                ('finalidades_aceptadas', models.JSONField(default=list, help_text='Claves de Finalidad consentidas.')),
                ('finalidades_rechazadas', models.JSONField(default=list)),
                ('origen', models.CharField(choices=[('FORM_COTIZACION', 'Formulario público de cotización'), ('PORTAL_CLIENTE', 'Portal de clientes'), ('CHECKOUT', 'Checkout de pago'), ('CONTRATO', 'Firma de contrato'), ('ADMIN', 'Captura administrativa')], max_length=24)),
                ('ip', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True)),
                ('aceptado_en', models.DateTimeField(db_index=True, default=django.utils.timezone.now, editable=False)),
                ('cliente', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='aceptaciones_legales', to='comercial.cliente')),
                ('documentos', models.ManyToManyField(related_name='aceptaciones', to='legal.documentolegal')),
            ],
            options={
                'verbose_name': 'Aceptación legal',
                'verbose_name_plural': 'Aceptaciones legales',
                'ordering': ['-aceptado_en'],
            },
        ),
        migrations.AddIndex(
            model_name='documentolegal',
            index=models.Index(fields=['tipo', 'vigente'], name='legal_docum_tipo_vig_idx'),
        ),
        migrations.AddConstraint(
            model_name='documentolegal',
            constraint=models.UniqueConstraint(fields=('tipo', 'version'), name='uniq_doc_tipo_version'),
        ),
        migrations.AddConstraint(
            model_name='documentolegal',
            constraint=models.UniqueConstraint(condition=models.Q(('vigente', True)), fields=('tipo',), name='uniq_doc_vigente_por_tipo'),
        ),
        migrations.AddIndex(
            model_name='solicitudarco',
            index=models.Index(fields=['estado', 'fecha_limite'], name='legal_solic_est_lim_idx'),
        ),
        migrations.AddIndex(
            model_name='aceptacionlegal',
            index=models.Index(fields=['cliente', '-aceptado_en'], name='legal_acept_cli_fec_idx'),
        ),
        migrations.AddIndex(
            model_name='aceptacionlegal',
            index=models.Index(fields=['correo', '-aceptado_en'], name='legal_acept_cor_fec_idx'),
        ),
    ]
