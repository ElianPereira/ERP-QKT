"""
Modelos del Módulo de Contabilidad
==================================
Sistema de contabilidad con partida doble, catálogo SAT 2024,
múltiples unidades de negocio y conciliación bancaria.

ERP Quinta Ko'ox Tanil
"""
from decimal import Decimal
from django.db import models
from django.db.models import Sum, Q
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone


# ==========================================
# CATÁLOGO DE REGÍMENES FISCALES SAT
# ==========================================

REGIMEN_FISCAL_SAT_CHOICES = [
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
]


# ==========================================
# 1. CATÁLOGO DE CUENTAS (SAT 2024)
# ==========================================

class CuentaContable(models.Model):
    """
    Catálogo de cuentas contables basado en el código agrupador SAT 2024.
    Estructura jerárquica: 1 dígito = rubro, 2 = grupo, 3+ = cuenta/subcuenta.
    """
    NATURALEZA_CHOICES = [
        ('D', 'Deudora'),
        ('A', 'Acreedora'),
    ]
    
    TIPO_CHOICES = [
        ('ACTIVO', '1 - Activo'),
        ('PASIVO', '2 - Pasivo'),
        ('CAPITAL', '3 - Capital'),
        ('INGRESO', '4 - Ingresos'),
        ('COSTO', '5 - Costos'),
        ('GASTO', '6 - Gastos'),
        ('ORDEN', '8 - Cuentas de orden'),
    ]
    
    codigo_sat = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Código SAT",
        help_text="Código agrupador SAT (ej: 102.01)"
    )
    nombre = models.CharField(max_length=200, verbose_name="Nombre de la cuenta")
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, verbose_name="Tipo")
    naturaleza = models.CharField(
        max_length=1,
        choices=NATURALEZA_CHOICES,
        verbose_name="Naturaleza",
        help_text="D=Deudora (aumenta con cargo), A=Acreedora (aumenta con abono)"
    )
    nivel = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Nivel jerárquico",
        help_text="1=Rubro, 2=Grupo, 3=Cuenta, 4+=Subcuenta"
    )
    padre = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subcuentas',
        verbose_name="Cuenta padre"
    )
    permite_movimientos = models.BooleanField(
        default=True,
        verbose_name="¿Permite movimientos?",
        help_text="False para cuentas de acumulación (solo totalizan subcuentas)"
    )
    activa = models.BooleanField(default=True, verbose_name="Activa")
    
    # Metadatos
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Cuenta contable"
        verbose_name_plural = "Catálogo de cuentas"
        ordering = ['codigo_sat']
        indexes = [
            models.Index(fields=['codigo_sat']),
            models.Index(fields=['tipo', 'activa']),
        ]
    
    def __str__(self):
        return f"{self.codigo_sat} - {self.nombre}"
    
    def clean(self):
        if self.padre and self.padre == self:
            raise ValidationError("Una cuenta no puede ser su propio padre.")
    
    @property
    def saldo_actual(self):
        """Calcula el saldo actual de la cuenta (debe - haber o viceversa según naturaleza)."""
        movimientos = self.movimientos.filter(
            poliza__estado='APLICADA'
        ).aggregate(
            total_debe=Sum('debe'),
            total_haber=Sum('haber')
        )
        debe = movimientos['total_debe'] or Decimal('0.00')
        haber = movimientos['total_haber'] or Decimal('0.00')
        
        if self.naturaleza == 'D':
            return debe - haber
        else:
            return haber - debe


# ==========================================
# 2. UNIDADES DE NEGOCIO (CENTROS DE COSTO)
# ==========================================

class UnidadNegocio(models.Model):
    """
    Permite separar la contabilidad por línea de negocio.
    Útil para reportes de rentabilidad por segmento.
    """
    clave = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Clave",
        help_text="Identificador corto (ej: QUINTA, AIRBNB)"
    )
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    descripcion = models.TextField(blank=True, verbose_name="Descripción")
    regimen_fiscal = models.CharField(
        max_length=3,
        choices=REGIMEN_FISCAL_SAT_CHOICES,
        default='612',
        verbose_name="Régimen fiscal SAT"
    )
    activa = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Unidad de negocio"
        verbose_name_plural = "Unidades de negocio"
        ordering = ['clave']
    
    def __str__(self):
        return f"{self.clave} - {self.nombre}"


# ==========================================
# 3. CUENTAS BANCARIAS
# ==========================================

class CuentaBancaria(models.Model):
    """
    Cuentas bancarias de la empresa para control de saldos y conciliación.
    Cada cuenta bancaria se liga a una cuenta contable de Bancos (102.xx).
    """
    nombre = models.CharField(
        max_length=100,
        verbose_name="Nombre descriptivo",
        help_text="Ej: BBVA Principal, Santander Nómina"
    )
    banco = models.CharField(max_length=50, verbose_name="Banco")
    numero_cuenta = models.CharField(max_length=20, blank=True, verbose_name="Número de cuenta")
    clabe = models.CharField(
        max_length=18,
        unique=True,
        verbose_name="CLABE interbancaria"
    )
    cuenta_contable = models.OneToOneField(
        CuentaContable,
        on_delete=models.PROTECT,
        related_name='cuenta_bancaria',
        verbose_name="Cuenta contable",
        help_text="Debe ser una subcuenta de Bancos (102.xx)",
        null=True,
        blank=True
    )
    unidad_negocio = models.ForeignKey(
        UnidadNegocio,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Unidad de negocio",
        help_text="Si la cuenta es exclusiva de una unidad"
    )
    saldo_inicial = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Saldo inicial",
        help_text="Saldo al momento de dar de alta la cuenta"
    )
    fecha_saldo_inicial = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha del saldo inicial"
    )
    activa = models.BooleanField(default=True)
    
    # Metadatos
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Cuenta bancaria"
        verbose_name_plural = "Cuentas bancarias"
        ordering = ['banco', 'nombre']
    
    def __str__(self):
        return f"{self.banco} - {self.nombre}"
    
    @property
    def saldo_actual(self):
        """Calcula saldo actual: inicial + movimientos contables."""
        if not self.cuenta_contable:
            return self.saldo_inicial
        return self.saldo_inicial + self.cuenta_contable.saldo_actual


# ==========================================
# 4. PÓLIZAS CONTABLES
# ==========================================

class Poliza(models.Model):
    """
    Encabezado de póliza contable.
    Agrupa movimientos que deben cuadrar (suma debe = suma haber).
    """
    TIPO_CHOICES = [
        ('I', 'Ingreso'),
        ('E', 'Egreso'),
        ('D', 'Diario'),
    ]
    
    ESTADO_CHOICES = [
        ('BORRADOR', 'Borrador'),
        ('APLICADA', 'Aplicada'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    ORIGEN_CHOICES = [
        ('MANUAL', 'Captura manual'),
        ('PAGO_CLIENTE', 'Pago de cliente'),
        ('PAGO_AIRBNB', 'Pago Airbnb'),
        ('COMPRA', 'Compra/Gasto'),
        ('NOMINA', 'Nómina'),
        ('AJUSTE', 'Ajuste contable'),
    ]
    
    tipo = models.CharField(
        max_length=1,
        choices=TIPO_CHOICES,
        verbose_name="Tipo de póliza"
    )
    folio = models.PositiveIntegerField(verbose_name="Folio")
    fecha = models.DateField(verbose_name="Fecha de póliza")
    concepto = models.CharField(max_length=300, verbose_name="Concepto")
    
    unidad_negocio = models.ForeignKey(
        UnidadNegocio,
        on_delete=models.PROTECT,
        verbose_name="Unidad de negocio"
    )
    
    estado = models.CharField(
        max_length=10,
        choices=ESTADO_CHOICES,
        default='BORRADOR',
        verbose_name="Estado"
    )
    origen = models.CharField(
        max_length=20,
        choices=ORIGEN_CHOICES,
        default='MANUAL',
        verbose_name="Origen"
    )
    
    # Referencias opcionales a documentos origen
    content_type = models.ForeignKey(
        'contenttypes.ContentType',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Tipo de documento origen"
    )
    object_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="ID del documento origen"
    )
    
    # Auditoría
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='polizas_creadas',
        verbose_name="Creado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelada_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='polizas_canceladas',
        verbose_name="Cancelada por"
    )
    fecha_cancelacion = models.DateTimeField(null=True, blank=True)
    motivo_cancelacion = models.TextField(blank=True, verbose_name="Motivo de cancelación")
    
    class Meta:
        verbose_name = "Póliza contable"
        verbose_name_plural = "Pólizas contables"
        ordering = ['-fecha', '-folio']
        indexes = [
            models.Index(fields=['fecha', 'tipo']),
            models.Index(fields=['estado']),
            models.Index(fields=['unidad_negocio', 'fecha']),
        ]
    
    def __str__(self):
        return f"{self.get_tipo_display()}-{self.folio} | {self.fecha} | {self.concepto[:50]}"
    
    @property
    def total_debe(self):
        return self.movimientos.aggregate(t=Sum('debe'))['t'] or Decimal('0.00')
    
    @property
    def total_haber(self):
        return self.movimientos.aggregate(t=Sum('haber'))['t'] or Decimal('0.00')
    
    @property
    def esta_cuadrada(self):
        """Verifica que la póliza cuadre (debe = haber)."""
        return abs(self.total_debe - self.total_haber) < Decimal('0.01')
    
    def clean(self):
        if self.estado == 'APLICADA' and not self.esta_cuadrada:
            raise ValidationError(
                f"La póliza no cuadra. Debe: ${self.total_debe}, Haber: ${self.total_haber}"
            )
    
    def aplicar(self, usuario):
        """Aplica la póliza (la hace definitiva)."""
        if not self.esta_cuadrada:
            raise ValidationError("No se puede aplicar una póliza que no cuadra.")
        if self.estado != 'BORRADOR':
            raise ValidationError("Solo se pueden aplicar pólizas en borrador.")
        self.estado = 'APLICADA'
        self.save()
    
    def cancelar(self, usuario, motivo):
        """Cancela la póliza con motivo y usuario."""
        if self.estado == 'CANCELADA':
            raise ValidationError("La póliza ya está cancelada.")
        self.estado = 'CANCELADA'
        self.cancelada_por = usuario
        self.fecha_cancelacion = timezone.now()
        self.motivo_cancelacion = motivo
        self.save()
    
    @classmethod
    def siguiente_folio(cls, tipo, fecha):
        """Obtiene el siguiente folio disponible para el tipo y mes."""
        ultimo = cls.objects.filter(
            tipo=tipo,
            fecha__year=fecha.year,
            fecha__month=fecha.month
        ).aggregate(max_folio=models.Max('folio'))['max_folio']
        return (ultimo or 0) + 1


# ==========================================
# 5. MOVIMIENTOS CONTABLES (LÍNEAS DE PÓLIZA)
# ==========================================

class MovimientoContable(models.Model):
    """
    Línea individual de una póliza.
    Cada movimiento afecta una cuenta con cargo (debe) o abono (haber).
    """
    poliza = models.ForeignKey(
        Poliza,
        on_delete=models.CASCADE,
        related_name='movimientos',
        verbose_name="Póliza"
    )
    cuenta = models.ForeignKey(
        CuentaContable,
        on_delete=models.PROTECT,
        related_name='movimientos',
        verbose_name="Cuenta contable"
    )
    concepto = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Concepto del movimiento",
        help_text="Detalle adicional (opcional si es igual al de la póliza)"
    )
    debe = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Debe (Cargo)"
    )
    haber = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Haber (Abono)"
    )
    referencia = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Referencia",
        help_text="Número de cheque, transferencia, factura, etc."
    )
    
    class Meta:
        verbose_name = "Movimiento contable"
        verbose_name_plural = "Movimientos contables"
        ordering = ['id']
        indexes = [
            models.Index(fields=['cuenta', 'poliza']),
        ]
    
    def __str__(self):
        tipo = "Cargo" if self.debe > 0 else "Abono"
        monto = self.debe if self.debe > 0 else self.haber
        return f"{tipo} ${monto} → {self.cuenta.codigo_sat}"
    
    def clean(self):
        if self.debe > 0 and self.haber > 0:
            raise ValidationError("Un movimiento no puede tener cargo y abono simultáneamente.")
        if self.debe == 0 and self.haber == 0:
            raise ValidationError("El movimiento debe tener un monto en debe o haber.")
        if not self.cuenta.permite_movimientos:
            raise ValidationError(f"La cuenta {self.cuenta} no permite movimientos directos.")


# ==========================================
# 6. CONCILIACIÓN BANCARIA
# ==========================================

class ConciliacionBancaria(models.Model):
    """
    Conciliación mensual entre el saldo del banco y el saldo en libros.
    """
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('EN_PROCESO', 'En proceso'),
        ('CONCILIADA', 'Conciliada'),
    ]
    
    cuenta_bancaria = models.ForeignKey(
        CuentaBancaria,
        on_delete=models.PROTECT,
        related_name='conciliaciones',
        verbose_name="Cuenta bancaria"
    )
    mes = models.PositiveSmallIntegerField(verbose_name="Mes")
    anio = models.PositiveSmallIntegerField(verbose_name="Año")
    
    saldo_segun_banco = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Saldo según estado de cuenta"
    )
    saldo_segun_libros = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Saldo según libros"
    )
    
    # Partidas en conciliación
    cargos_banco_no_registrados = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Cargos del banco no registrados",
        help_text="Comisiones, intereses cobrados, etc."
    )
    abonos_banco_no_registrados = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Abonos del banco no registrados",
        help_text="Intereses ganados, depósitos no identificados"
    )
    cargos_empresa_no_cobrados = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Cheques expedidos no cobrados",
        help_text="Cheques girados pendientes de cobro"
    )
    abonos_empresa_no_abonados = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Depósitos en tránsito",
        help_text="Depósitos registrados pendientes de acreditación"
    )
    
    diferencia = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Diferencia final"
    )
    estado = models.CharField(
        max_length=15,
        choices=ESTADO_CHOICES,
        default='PENDIENTE'
    )
    notas = models.TextField(blank=True, verbose_name="Notas")
    
    # Auditoría
    conciliada_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Conciliada por"
    )
    fecha_conciliacion = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Conciliación bancaria"
        verbose_name_plural = "Conciliaciones bancarias"
        unique_together = ['cuenta_bancaria', 'mes', 'anio']
        ordering = ['-anio', '-mes']
    
    def __str__(self):
        return f"{self.cuenta_bancaria} - {self.mes:02d}/{self.anio}"
    
    def calcular_diferencia(self):
        """
        Calcula la diferencia entre saldo banco y saldo libros ajustado.
        Si es 0, la conciliación cuadra.
        """
        saldo_libros_ajustado = (
            self.saldo_segun_libros
            - self.cargos_banco_no_registrados
            + self.abonos_banco_no_registrados
        )
        saldo_banco_ajustado = (
            self.saldo_segun_banco
            - self.abonos_empresa_no_abonados
            + self.cargos_empresa_no_cobrados
        )
        self.diferencia = saldo_libros_ajustado - saldo_banco_ajustado
        return self.diferencia
    
    def save(self, *args, **kwargs):
        self.calcular_diferencia()
        super().save(*args, **kwargs)


# ==========================================
# 7. CONFIGURACIÓN DE CUENTAS POR MÓDULO
# ==========================================

class ConfiguracionContable(models.Model):
    """
    Mapeo de cuentas contables por defecto para cada tipo de operación.
    Permite configurar qué cuentas usar en las pólizas automáticas.
    """
    OPERACION_CHOICES = [
        # Comercial
        ('PAGO_CLIENTE_EFECTIVO', 'Pago cliente - Efectivo'),
        ('PAGO_CLIENTE_TRANSFERENCIA', 'Pago cliente - Transferencia'),
        ('PAGO_CLIENTE_TARJETA', 'Pago cliente - Tarjeta'),
        ('INGRESO_EVENTOS', 'Ingreso por eventos'),
        ('IVA_TRASLADADO', 'IVA trasladado'),
        ('ANTICIPO_CLIENTES', 'Anticipo de clientes'),
        
        # Airbnb
        ('INGRESO_AIRBNB', 'Ingreso Airbnb'),
        ('RETENCION_ISR_AIRBNB', 'Retención ISR Airbnb'),
        ('RETENCION_IVA_AIRBNB', 'Retención IVA Airbnb'),
        ('IMPUESTO_HOSPEDAJE', 'Impuesto al hospedaje'),
        ('COMISION_AIRBNB', 'Comisión Airbnb'),
        
        # Compras/Gastos
        ('PROVEEDORES', 'Proveedores'),
        ('IVA_ACREDITABLE', 'IVA acreditable'),
        ('GASTOS_GENERALES', 'Gastos generales'),
        ('GASTOS_BEBIDAS', 'Gastos bebidas'),
        ('GASTOS_NOMINA_EXT', 'Gastos nómina externa'),
        
        # Nómina
        ('SUELDOS_SALARIOS', 'Sueldos y salarios'),
        ('IMSS_PATRONAL', 'IMSS patronal'),
        
        # Bancos
        ('BANCO_PRINCIPAL', 'Banco principal'),
        ('BANCO_SECUNDARIO', 'Banco secundario'),
        ('CAJA', 'Caja'),
    ]
    
    operacion = models.CharField(
        max_length=30,
        choices=OPERACION_CHOICES,
        unique=True,
        verbose_name="Tipo de operación"
    )
    cuenta = models.ForeignKey(
        CuentaContable,
        on_delete=models.PROTECT,
        verbose_name="Cuenta contable"
    )
    descripcion = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Descripción"
    )
    activa = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Configuración contable"
        verbose_name_plural = "Configuración contable"
        ordering = ['operacion']
    
    def __str__(self):
        return f"{self.get_operacion_display()} → {self.cuenta.codigo_sat}"
    
    @classmethod
    def obtener_cuenta(cls, operacion):
        """Obtiene la cuenta configurada para una operación."""
        try:
            config = cls.objects.get(operacion=operacion, activa=True)
            return config.cuenta
        except cls.DoesNotExist:
            return None