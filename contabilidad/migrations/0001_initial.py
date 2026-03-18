# Generated manually for contabilidad module

from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='CuentaContable',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo_sat', models.CharField(help_text='Código agrupador SAT (ej: 102.01)', max_length=20, unique=True, verbose_name='Código SAT')),
                ('nombre', models.CharField(max_length=200, verbose_name='Nombre de la cuenta')),
                ('tipo', models.CharField(choices=[('ACTIVO', '1 - Activo'), ('PASIVO', '2 - Pasivo'), ('CAPITAL', '3 - Capital'), ('INGRESO', '4 - Ingresos'), ('COSTO', '5 - Costos'), ('GASTO', '6 - Gastos'), ('ORDEN', '8 - Cuentas de orden')], max_length=10, verbose_name='Tipo')),
                ('naturaleza', models.CharField(choices=[('D', 'Deudora'), ('A', 'Acreedora')], help_text='D=Deudora (aumenta con cargo), A=Acreedora (aumenta con abono)', max_length=1, verbose_name='Naturaleza')),
                ('nivel', models.PositiveSmallIntegerField(default=1, help_text='1=Rubro, 2=Grupo, 3=Cuenta, 4+=Subcuenta', verbose_name='Nivel jerárquico')),
                ('permite_movimientos', models.BooleanField(default=True, help_text='False para cuentas de acumulación (solo totalizan subcuentas)', verbose_name='¿Permite movimientos?')),
                ('activa', models.BooleanField(default=True, verbose_name='Activa')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('padre', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='subcuentas', to='contabilidad.cuentacontable', verbose_name='Cuenta padre')),
            ],
            options={
                'verbose_name': 'Cuenta contable',
                'verbose_name_plural': 'Catálogo de cuentas',
                'ordering': ['codigo_sat'],
            },
        ),
        migrations.CreateModel(
            name='UnidadNegocio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('clave', models.CharField(help_text='Identificador corto (ej: QUINTA, AIRBNB)', max_length=20, unique=True, verbose_name='Clave')),
                ('nombre', models.CharField(max_length=100, verbose_name='Nombre')),
                ('descripcion', models.TextField(blank=True, verbose_name='Descripción')),
                ('regimen_fiscal', models.CharField(choices=[('EMPRESARIAL', 'Actividad Empresarial'), ('PLATAFORMAS', 'Plataformas Tecnológicas'), ('MIXTO', 'Mixto')], default='EMPRESARIAL', max_length=20, verbose_name='Régimen fiscal')),
                ('activa', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Unidad de negocio',
                'verbose_name_plural': 'Unidades de negocio',
                'ordering': ['clave'],
            },
        ),
        migrations.CreateModel(
            name='CuentaBancaria',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(help_text='Ej: BBVA Principal, Santander Nómina', max_length=100, verbose_name='Nombre descriptivo')),
                ('banco', models.CharField(max_length=50, verbose_name='Banco')),
                ('numero_cuenta', models.CharField(blank=True, max_length=20, verbose_name='Número de cuenta')),
                ('clabe', models.CharField(max_length=18, unique=True, verbose_name='CLABE interbancaria')),
                ('saldo_inicial', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Saldo al momento de dar de alta la cuenta', max_digits=14, verbose_name='Saldo inicial')),
                ('fecha_saldo_inicial', models.DateField(blank=True, null=True, verbose_name='Fecha del saldo inicial')),
                ('activa', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('cuenta_contable', models.OneToOneField(blank=True, help_text='Debe ser una subcuenta de Bancos (102.xx)', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='cuenta_bancaria', to='contabilidad.cuentacontable', verbose_name='Cuenta contable')),
                ('unidad_negocio', models.ForeignKey(blank=True, help_text='Si la cuenta es exclusiva de una unidad', null=True, on_delete=django.db.models.deletion.SET_NULL, to='contabilidad.unidadnegocio', verbose_name='Unidad de negocio')),
            ],
            options={
                'verbose_name': 'Cuenta bancaria',
                'verbose_name_plural': 'Cuentas bancarias',
                'ordering': ['banco', 'nombre'],
            },
        ),
        migrations.CreateModel(
            name='Poliza',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('I', 'Ingreso'), ('E', 'Egreso'), ('D', 'Diario')], max_length=1, verbose_name='Tipo de póliza')),
                ('folio', models.PositiveIntegerField(verbose_name='Folio')),
                ('fecha', models.DateField(verbose_name='Fecha de póliza')),
                ('concepto', models.CharField(max_length=300, verbose_name='Concepto')),
                ('estado', models.CharField(choices=[('BORRADOR', 'Borrador'), ('APLICADA', 'Aplicada'), ('CANCELADA', 'Cancelada')], default='BORRADOR', max_length=10, verbose_name='Estado')),
                ('origen', models.CharField(choices=[('MANUAL', 'Captura manual'), ('PAGO_CLIENTE', 'Pago de cliente'), ('PAGO_AIRBNB', 'Pago Airbnb'), ('COMPRA', 'Compra/Gasto'), ('NOMINA', 'Nómina'), ('AJUSTE', 'Ajuste contable')], default='MANUAL', max_length=20, verbose_name='Origen')),
                ('object_id', models.PositiveIntegerField(blank=True, null=True, verbose_name='ID del documento origen')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('fecha_cancelacion', models.DateTimeField(blank=True, null=True)),
                ('motivo_cancelacion', models.TextField(blank=True, verbose_name='Motivo de cancelación')),
                ('cancelada_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='polizas_canceladas', to=settings.AUTH_USER_MODEL, verbose_name='Cancelada por')),
                ('content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='contenttypes.contenttype', verbose_name='Tipo de documento origen')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='polizas_creadas', to=settings.AUTH_USER_MODEL, verbose_name='Creado por')),
                ('unidad_negocio', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='contabilidad.unidadnegocio', verbose_name='Unidad de negocio')),
            ],
            options={
                'verbose_name': 'Póliza contable',
                'verbose_name_plural': 'Pólizas contables',
                'ordering': ['-fecha', '-folio'],
            },
        ),
        migrations.CreateModel(
            name='MovimientoContable',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('concepto', models.CharField(blank=True, help_text='Detalle adicional (opcional si es igual al de la póliza)', max_length=200, verbose_name='Concepto del movimiento')),
                ('debe', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14, verbose_name='Debe (Cargo)')),
                ('haber', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14, verbose_name='Haber (Abono)')),
                ('referencia', models.CharField(blank=True, help_text='Número de cheque, transferencia, factura, etc.', max_length=100, verbose_name='Referencia')),
                ('cuenta', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='movimientos', to='contabilidad.cuentacontable', verbose_name='Cuenta contable')),
                ('poliza', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='movimientos', to='contabilidad.poliza', verbose_name='Póliza')),
            ],
            options={
                'verbose_name': 'Movimiento contable',
                'verbose_name_plural': 'Movimientos contables',
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='ConciliacionBancaria',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mes', models.PositiveSmallIntegerField(verbose_name='Mes')),
                ('anio', models.PositiveSmallIntegerField(verbose_name='Año')),
                ('saldo_segun_banco', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14, verbose_name='Saldo según estado de cuenta')),
                ('saldo_segun_libros', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14, verbose_name='Saldo según libros')),
                ('cargos_banco_no_registrados', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Comisiones, intereses cobrados, etc.', max_digits=14, verbose_name='Cargos del banco no registrados')),
                ('abonos_banco_no_registrados', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Intereses ganados, depósitos no identificados', max_digits=14, verbose_name='Abonos del banco no registrados')),
                ('cargos_empresa_no_cobrados', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Cheques girados pendientes de cobro', max_digits=14, verbose_name='Cheques expedidos no cobrados')),
                ('abonos_empresa_no_abonados', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Depósitos registrados pendientes de acreditación', max_digits=14, verbose_name='Depósitos en tránsito')),
                ('diferencia', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14, verbose_name='Diferencia final')),
                ('estado', models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('EN_PROCESO', 'En proceso'), ('CONCILIADA', 'Conciliada')], default='PENDIENTE', max_length=15)),
                ('notas', models.TextField(blank=True, verbose_name='Notas')),
                ('fecha_conciliacion', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('conciliada_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='Conciliada por')),
                ('cuenta_bancaria', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='conciliaciones', to='contabilidad.cuentabancaria', verbose_name='Cuenta bancaria')),
            ],
            options={
                'verbose_name': 'Conciliación bancaria',
                'verbose_name_plural': 'Conciliaciones bancarias',
                'ordering': ['-anio', '-mes'],
                'unique_together': {('cuenta_bancaria', 'mes', 'anio')},
            },
        ),
        migrations.CreateModel(
            name='ConfiguracionContable',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operacion', models.CharField(choices=[('PAGO_CLIENTE_EFECTIVO', 'Pago cliente - Efectivo'), ('PAGO_CLIENTE_TRANSFERENCIA', 'Pago cliente - Transferencia'), ('PAGO_CLIENTE_TARJETA', 'Pago cliente - Tarjeta'), ('INGRESO_EVENTOS', 'Ingreso por eventos'), ('IVA_TRASLADADO', 'IVA trasladado'), ('ANTICIPO_CLIENTES', 'Anticipo de clientes'), ('INGRESO_AIRBNB', 'Ingreso Airbnb'), ('RETENCION_ISR_AIRBNB', 'Retención ISR Airbnb'), ('RETENCION_IVA_AIRBNB', 'Retención IVA Airbnb'), ('IMPUESTO_HOSPEDAJE', 'Impuesto al hospedaje'), ('COMISION_AIRBNB', 'Comisión Airbnb'), ('PROVEEDORES', 'Proveedores'), ('IVA_ACREDITABLE', 'IVA acreditable'), ('GASTOS_GENERALES', 'Gastos generales'), ('GASTOS_BEBIDAS', 'Gastos bebidas'), ('GASTOS_NOMINA_EXT', 'Gastos nómina externa'), ('SUELDOS_SALARIOS', 'Sueldos y salarios'), ('IMSS_PATRONAL', 'IMSS patronal'), ('BANCO_PRINCIPAL', 'Banco principal'), ('BANCO_SECUNDARIO', 'Banco secundario'), ('CAJA', 'Caja')], max_length=30, unique=True, verbose_name='Tipo de operación')),
                ('descripcion', models.CharField(blank=True, max_length=200, verbose_name='Descripción')),
                ('activa', models.BooleanField(default=True)),
                ('cuenta', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='contabilidad.cuentacontable', verbose_name='Cuenta contable')),
            ],
            options={
                'verbose_name': 'Configuración contable',
                'verbose_name_plural': 'Configuración contable',
                'ordering': ['operacion'],
            },
        ),
        migrations.AddIndex(
            model_name='cuentacontable',
            index=models.Index(fields=['codigo_sat'], name='contabilida_codigo__a1b2c3_idx'),
        ),
        migrations.AddIndex(
            model_name='cuentacontable',
            index=models.Index(fields=['tipo', 'activa'], name='contabilida_tipo_ac_d4e5f6_idx'),
        ),
        migrations.AddIndex(
            model_name='poliza',
            index=models.Index(fields=['fecha', 'tipo'], name='contabilida_fecha_t_g7h8i9_idx'),
        ),
        migrations.AddIndex(
            model_name='poliza',
            index=models.Index(fields=['estado'], name='contabilida_estado_j0k1l2_idx'),
        ),
        migrations.AddIndex(
            model_name='poliza',
            index=models.Index(fields=['unidad_negocio', 'fecha'], name='contabilida_unidad__m3n4o5_idx'),
        ),
        migrations.AddIndex(
            model_name='movimientocontable',
            index=models.Index(fields=['cuenta', 'poliza'], name='contabilida_cuenta__p6q7r8_idx'),
        ),
    ]