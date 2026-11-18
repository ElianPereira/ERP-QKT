"""Comisiones del personal de ventas (Issue #505, PR 3).

Los montos y porcentajes nunca viven aquí: salen de `nomina.CondicionesPago` y se
sellan en cada registro. Porcentajes como fracción (0.05 = 5 %).
`MovimientoComision` es inmutable: se corrige con un movimiento contrario.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models

from core_erp.storages_qkt import storage_privado

CERO = Decimal('0.00')


def _usuario_fk(nombre):
    return models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                             on_delete=models.SET_NULL, related_name='+', verbose_name=nombre)


class ComisionContratacion(models.Model):
    """Comisión de una contratación (cotización confirmada con vendedor). Una por cotización."""

    cotizacion = models.OneToOneField('comercial.Cotizacion', on_delete=models.PROTECT,
                                      related_name='comision_contratacion', verbose_name='Cotización')
    vendedor = models.ForeignKey('nomina.Empleado', on_delete=models.PROTECT, related_name='comisiones',
                                 verbose_name='Vendedor')
    condiciones = models.ForeignKey('nomina.CondicionesPago', on_delete=models.PROTECT, related_name='+',
                                    verbose_name='Condiciones')
    numero_en_periodo = models.PositiveIntegerField(verbose_name='Contratación n.º del año')
    anio = models.PositiveSmallIntegerField(db_index=True, verbose_name='Año')
    monto_fijo = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Monto fijo sellado')
    porcentaje_extras = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0'),
                                            verbose_name='Porcentaje sobre extras sellado')
    precio_final = models.DecimalField(max_digits=12, decimal_places=2, default=CERO,
                                       verbose_name='Precio de la venta sellado',
                                       help_text='Con él se calcula lo devengado; tras la baja ya no cambia.')
    base_extras = models.DecimalField(max_digits=12, decimal_places=2, default=CERO,
                                      verbose_name='Base de extras (sin IVA)')
    monto_extras = models.DecimalField(max_digits=10, decimal_places=2, default=CERO,
                                       verbose_name='Comisión sobre extras')
    total = models.DecimalField(max_digits=10, decimal_places=2, default=CERO, verbose_name='Comisión total')
    devengado = models.DecimalField(max_digits=10, decimal_places=2, default=CERO,
                                    verbose_name='Devengado acumulado')
    confirmada_en = models.DateTimeField(verbose_name='Confirmada')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creada')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizada')
    created_by = _usuario_fk('Creada por')

    class Meta:
        verbose_name = 'Comisión'
        verbose_name_plural = 'Comisiones'
        ordering = ['-confirmada_en']
        # Solo habilita el enlace «Mis comisiones» del menú; el candado real es resolver al Empleado
        # desde `request.user` en el servidor (ventas/views.py).
        permissions = [('ver_mis_comisiones', 'Puede ver su propia pantalla «Mis comisiones»')]
        indexes = [models.Index(fields=['vendedor', 'anio'], name='ventas_comision_vendedor_anio')]

    def __str__(self):
        return f'Comisión COT-{self.cotizacion_id} · {self.vendedor}'

    @property
    def por_devengar(self):
        return self.total - self.devengado


class MovimientoQuerySet(models.QuerySet):
    """Solo se permite ligar un movimiento a su corte; todo lo demás es inmutable."""

    def update(self, **campos):
        if set(campos) - {'corte', 'corte_id'}:
            raise PermissionDenied('Un movimiento de comisión no se edita: corrígelo con otro contrario.')
        return super().update(**campos)

    def delete(self):
        raise PermissionDenied('Un movimiento de comisión no se borra.')


class MovimientoComision(models.Model):
    TIPOS = [('VISITA', 'Visita'), ('CONTRATACION', 'Contratación'), ('AJUSTE', 'Ajuste')]

    vendedor = models.ForeignKey('nomina.Empleado', on_delete=models.PROTECT, related_name='movimientos_comision',
                                 verbose_name='Vendedor')
    tipo = models.CharField(max_length=14, choices=TIPOS, db_index=True, verbose_name='Tipo')
    monto = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Monto',
                                help_text='Positivo devenga; negativo revierte.')
    fecha = models.DateField(db_index=True, verbose_name='Fecha')
    comision = models.ForeignKey(ComisionContratacion, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name='movimientos', verbose_name='Comisión')
    visita = models.ForeignKey('comunicacion.Visita', null=True, blank=True, on_delete=models.PROTECT,
                               related_name='movimientos_comision', verbose_name='Visita')
    pago = models.ForeignKey('comercial.Pago', null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='movimientos_comision', verbose_name='Pago que lo originó')
    corte = models.ForeignKey('ventas.CorteComision', null=True, blank=True, on_delete=models.PROTECT,
                              related_name='movimientos', verbose_name='Corte')
    absorcion = models.BooleanField(default=False, verbose_name='Absorbido por la Quinta',
                                    help_text='Cancela saldo en contra sin pagarse: es costo de la Quinta.')
    detalle = models.CharField(max_length=300, blank=True, verbose_name='Detalle')
    clave_idempotencia = models.CharField(max_length=80, unique=True, verbose_name='Clave')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    created_by = _usuario_fk('Creado por')

    objects = MovimientoQuerySet.as_manager()

    class Meta:
        verbose_name = 'Movimiento'
        verbose_name_plural = 'Movimientos'
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f'{self.get_tipo_display()} {self.monto} · {self.vendedor}'

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionDenied('Un movimiento de comisión no se edita: corrígelo con otro contrario.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied('Un movimiento de comisión no se borra.')


class CorteComision(models.Model):
    """Corte de pago de un vendedor (PR 4). Declarado desde el PR 3 para que `MovimientoComision.corte`
    ya exista; la lógica de cortes vive en `services_cortes`."""

    TIPOS = [('VISITAS', 'Visitas'), ('COMISIONES', 'Comisiones')]
    ESTADOS = [('BORRADOR', 'Borrador'), ('APROBADO', 'Aprobado'), ('PAGADO', 'Pagado'),
               ('CANCELADO', 'Cancelado')]
    METODOS = [('EFECTIVO', 'Efectivo'), ('TRANSFERENCIA', 'Transferencia')]

    vendedor = models.ForeignKey('nomina.Empleado', on_delete=models.PROTECT, related_name='cortes_comision',
                                 verbose_name='Vendedor')
    tipo = models.CharField(max_length=12, choices=TIPOS, db_index=True, verbose_name='Tipo')
    desde = models.DateField(verbose_name='Desde')
    hasta = models.DateField(verbose_name='Hasta')
    bruto = models.DecimalField(max_digits=10, decimal_places=2, default=CERO, verbose_name='Devengado del periodo')
    compensado = models.DecimalField(max_digits=10, decimal_places=2, default=CERO,
                                     verbose_name='Compensado')
    total = models.DecimalField(max_digits=10, decimal_places=2, default=CERO, verbose_name='Total a pagar')
    saldo_arrastrado = models.DecimalField(max_digits=10, decimal_places=2, default=CERO,
                                           verbose_name='Saldo en contra que se arrastra')
    absorbido = models.DecimalField(max_digits=10, decimal_places=2, default=CERO,
                                    verbose_name='Saldo en contra absorbido')
    estado = models.CharField(max_length=10, choices=ESTADOS, default='BORRADOR', db_index=True,
                              verbose_name='Estado')
    aprobado_por = _usuario_fk('Aprobado por')
    aprobado_en = models.DateTimeField(null=True, blank=True, editable=False, verbose_name='Aprobado')
    aprobado_por_telefono = models.CharField(max_length=20, blank=True, editable=False,
                                             verbose_name='Teléfono que aprobó')
    pagado_por = _usuario_fk('Pagado por')
    pagado_en = models.DateTimeField(null=True, blank=True, editable=False, verbose_name='Pagado')
    metodo = models.CharField(max_length=15, choices=METODOS, blank=True, verbose_name='Método de pago')
    cuenta_bancaria = models.ForeignKey('contabilidad.CuentaBancaria', null=True, blank=True,
                                        on_delete=models.PROTECT, related_name='+', verbose_name='Cuenta bancaria',
                                        help_text='Vacío = cuenta pagadora.')
    referencia = models.CharField(max_length=100, blank=True, verbose_name='Referencia')
    archivo_pdf = models.FileField(upload_to='ventas/cortes/', blank=True, verbose_name='Estado de comisiones',
                                   storage=storage_privado)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')
    created_by = _usuario_fk('Creado por')
    updated_by = _usuario_fk('Actualizado por')

    class Meta:
        verbose_name = 'Corte'
        verbose_name_plural = 'Cortes'
        ordering = ['-hasta', '-id']
        constraints = [
            models.UniqueConstraint(fields=['vendedor', 'tipo', 'desde', 'hasta'],
                                    condition=~models.Q(estado='CANCELADO'),
                                    name='ventas_un_corte_vivo_por_periodo'),
        ]

    def __str__(self):
        return f'Corte {self.get_tipo_display()} {self.desde:%d/%m/%Y}–{self.hasta:%d/%m/%Y} · {self.vendedor}'


class EventoVenta(models.Model):
    """Bitácora de lo que le pasa a una venta (quién y cuándo). Solo se agrega."""

    TIPOS = [
        ('ATRIBUIDA', 'Atribuida'), ('CONFIRMADA', 'Confirmada'), ('PAGO', 'Pago'),
        ('REEMBOLSO', 'Reembolso'), ('CANCELADA', 'Cancelada'), ('COMISION', 'Comisión'),
        ('ABSORCION', 'Absorción'), ('OTRO', 'Otro'),
    ]

    cotizacion = models.ForeignKey('comercial.Cotizacion', on_delete=models.CASCADE, related_name='eventos_venta',
                                   verbose_name='Cotización')
    tipo = models.CharField(max_length=12, choices=TIPOS, verbose_name='Tipo')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='+', verbose_name='Usuario')
    vendedor = models.ForeignKey('nomina.Empleado', null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='+', verbose_name='Vendedor')
    detalle = models.CharField(max_length=300, blank=True, verbose_name='Detalle')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Evento'
        verbose_name_plural = 'Bitácora'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'COT-{self.cotizacion_id} · {self.get_tipo_display()}'

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionDenied('La bitácora no se edita.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied('La bitácora no se borra.')


class EntregaEfectivo(models.Model):
    """Efectivo de clientes que la vendedora entrega a Dirección. Solo control: no genera póliza
    (el `Pago` en efectivo ya cargó Caja) y NUNCA se compensa contra sus comisiones."""

    vendedor = models.ForeignKey('nomina.Empleado', on_delete=models.PROTECT, related_name='entregas_efectivo',
                                 verbose_name='Vendedor')
    fecha = models.DateField(verbose_name='Fecha de entrega')
    monto = models.DecimalField(max_digits=10, decimal_places=2, default=CERO, verbose_name='Monto entregado',
                                help_text='Es la suma de los cobros que cubre; no se captura aparte.')
    pagos = models.ManyToManyField('comercial.Pago', related_name='entregas_efectivo', verbose_name='Cobros cubiertos')
    recibido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                     verbose_name='Recibió')
    notas = models.TextField(blank=True, verbose_name='Notas')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creada')
    created_by = _usuario_fk('Creada por')

    class Meta:
        verbose_name = 'Entrega'
        verbose_name_plural = 'Entregas'
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f'Entrega de {self.vendedor} · {self.fecha:%d/%m/%Y} · {self.monto}'

    def delete(self, *args, **kwargs):
        raise PermissionDenied('Una entrega no se borra.')
