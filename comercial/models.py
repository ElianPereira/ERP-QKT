import xml.etree.ElementTree as ET
from decimal import Decimal
from django.db import models
from django.db.models import Sum
from django.utils.timezone import now
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from facturacion.choices import RegimenFiscal, UsoCFDI
from cloudinary_storage.storage import RawMediaCloudinaryStorage

# ==========================================
# 0. CONFIGURACIÓN DEL SISTEMA
# ==========================================
class ConstanteSistema(models.Model):
    clave = models.CharField(max_length=50, unique=True, help_text="Ej: PRECIO_HIELO_20KG")
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descripcion = models.CharField(max_length=200, blank=True)

    def __str__(self): return f"{self.clave}: ${self.valor}"
    class Meta: verbose_name = "Constante del Sistema"


# ==========================================
# 0.5 PROVEEDORES
# ==========================================
class Proveedor(models.Model):
    nombre = models.CharField(max_length=200, unique=True, verbose_name="Nombre / Razón Social")
    contacto = models.CharField(max_length=200, blank=True, verbose_name="Persona de Contacto")
    telefono = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    notas = models.TextField(blank=True, verbose_name="Notas",
                             help_text="Horarios, condiciones de pago, dirección, etc.")
    activo = models.BooleanField(default=True)

    def __str__(self): return self.nombre
    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ['nombre']


# ==========================================
# 1. INSUMOS
# ==========================================
class Insumo(models.Model):
    TIPOS = [
        ('CONSUMIBLE', 'Consumible (Hielo, Comida, Desechables)'),
        ('MOBILIARIO', 'Mobiliario (Se renta: Sillas, Mesas)'),
        ('SERVICIO', 'Personal (Bartender, Staff, Seguridad)')
    ]
    nombre = models.CharField(max_length=200)
    unidad_medida = models.CharField(max_length=50) 
    costo_unitario = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Costo de Compra") 
    factor_rendimiento = models.DecimalField(max_digits=10, decimal_places=2, default=1.00, verbose_name="Rendimiento (Divisor)")
    cantidad_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    stock_minimo = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
                                        verbose_name="Stock Mínimo",
                                        help_text="Alerta cuando el stock baje de este nivel")
    categoria = models.CharField(max_length=20, choices=TIPOS, default='CONSUMIBLE')
    crear_como_subproducto = models.BooleanField(default=False, verbose_name="¿Crear también como Subproducto?")
    
    # CAMPO LEGACY — se eliminará después de migrar datos
    proveedor_legacy = models.CharField(max_length=200, blank=True, verbose_name="Proveedor (texto antiguo)",
                                         editable=False)
    
    # FK a Proveedor
    proveedor = models.ForeignKey(Proveedor, on_delete=models.SET_NULL, null=True, blank=True,
                                   verbose_name="Proveedor",
                                   help_text="Selecciona el proveedor de este insumo")
    
    presentacion = models.CharField(max_length=100, blank=True, verbose_name="Presentación",
                                     help_text="Ej: 940ml, Botella 750ml, Bolsa 20kg, Garrafón 20L")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.crear_como_subproducto:
            sub_prod, _ = SubProducto.objects.get_or_create(nombre=self.nombre)
            RecetaSubProducto.objects.get_or_create(subproducto=sub_prod, insumo=self, defaults={'cantidad': 1})

    @property
    def stock_bajo(self):
        """Retorna True si el stock está por debajo del mínimo."""
        return self.cantidad_stock < self.stock_minimo

    def __str__(self): 
        partes = [self.nombre]
        if self.presentacion:
            partes.append(f"({self.presentacion})")
        partes.append(f"- ${self.costo_unitario}")
        if self.proveedor:
            partes.append(f"[{self.proveedor.nombre}]")
        return " ".join(partes)


# ==========================================
# 1.2 MOVIMIENTOS DE INVENTARIO (NUEVO)
# ==========================================
class MovimientoInventario(models.Model):
    """
    Registro de entradas y salidas de inventario.
    Cada movimiento actualiza automáticamente el stock del insumo.
    Los registros NO se eliminan (auditoría).
    """
    TIPOS_MOVIMIENTO = [
        ('ENTRADA', 'Entrada (Compra / Recepción)'),
        ('SALIDA', 'Salida (Evento / Consumo)'),
        ('AJUSTE_POS', 'Ajuste Positivo (Inventario Físico)'),
        ('AJUSTE_NEG', 'Ajuste Negativo (Merma / Daño)'),
        ('DEVOLUCION', 'Devolución a Proveedor'),
    ]
    
    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT, related_name='movimientos',
                                verbose_name="Insumo")
    tipo = models.CharField(max_length=20, choices=TIPOS_MOVIMIENTO, verbose_name="Tipo de Movimiento")
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Cantidad",
                                    help_text="Siempre positiva. El tipo determina si suma o resta.")
    
    # Referencias opcionales
    compra = models.ForeignKey('Compra', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='movimientos_inventario',
                                verbose_name="Compra Relacionada")
    cotizacion = models.ForeignKey('Cotizacion', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='movimientos_inventario',
                                    verbose_name="Evento Relacionado")
    
    # Auditoría
    nota = models.CharField(max_length=255, blank=True, verbose_name="Nota / Motivo")
    stock_anterior = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Stock Anterior")
    stock_posterior = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Stock Posterior")
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    verbose_name="Registrado por")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Movimiento de Inventario"
        verbose_name_plural = "Movimientos de Inventario"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['insumo', '-created_at']),
            models.Index(fields=['tipo', '-created_at']),
        ]
    
    def clean(self):
        """Valida que la cantidad sea positiva y que haya stock suficiente para salidas."""
        if self.cantidad <= 0:
            raise ValidationError({'cantidad': 'La cantidad debe ser mayor a cero.'})
        
        if self.tipo in ('SALIDA', 'AJUSTE_NEG', 'DEVOLUCION'):
            if self.cantidad > self.insumo.cantidad_stock:
                raise ValidationError({
                    'cantidad': f'Stock insuficiente. Disponible: {self.insumo.cantidad_stock} {self.insumo.unidad_medida}'
                })
    
    def save(self, *args, **kwargs):
        """Guarda el movimiento y actualiza el stock del insumo."""
        self.stock_anterior = self.insumo.cantidad_stock
        
        if self.tipo in ('ENTRADA', 'AJUSTE_POS'):
            self.insumo.cantidad_stock += self.cantidad
        elif self.tipo in ('SALIDA', 'AJUSTE_NEG', 'DEVOLUCION'):
            self.insumo.cantidad_stock -= self.cantidad
        
        self.stock_posterior = self.insumo.cantidad_stock
        self.insumo.save(update_fields=['cantidad_stock'])
        super().save(*args, **kwargs)
    
    def __str__(self):
        signo = '+' if self.tipo in ('ENTRADA', 'AJUSTE_POS') else '-'
        return f"{signo}{self.cantidad} {self.insumo.nombre} ({self.get_tipo_display()})"


# ==========================================
# 1.5 PLANTILLA DE BARRA
# ==========================================
class PlantillaBarra(models.Model):
    CATEGORIAS_BARRA = [
        ('CERVEZA', 'Cerveza'),
        ('TEQUILA_NAC', 'Tequila Nacional'),
        ('WHISKY_NAC', 'Whisky Nacional'),
        ('RON_NAC', 'Ron Nacional'),
        ('VODKA_NAC', 'Vodka Nacional'),
        ('TEQUILA_PREM', 'Tequila Premium'),
        ('WHISKY_PREM', 'Whisky Premium'),
        ('GIN_PREM', 'Ginebra / Ron Premium'),
        ('REFRESCO_COLA', 'Refresco de Cola'),
        ('REFRESCO_TORONJA', 'Refresco de Toronja'),
        ('AGUA_MINERAL', 'Agua Mineral'),
        ('AGUA_NATURAL', 'Agua Natural'),
        ('HIELO', 'Hielo'),
        ('LIMON', 'Limón'),
        ('HIERBABUENA', 'Hierbabuena'),
        ('JARABE', 'Jarabe Natural'),
        ('FRUTOS_ROJOS', 'Frutos Rojos'),
        ('CAFE', 'Café Espresso'),
        ('SERVILLETAS', 'Servilletas / Popotes'),
    ]
    
    GRUPOS = [
        ('ALCOHOL_NACIONAL', 'Licores Nacionales'),
        ('ALCOHOL_PREMIUM', 'Licores Premium'),
        ('CERVEZA', 'Cerveza'),
        ('MEZCLADOR', 'Bebidas y Mezcladores'),
        ('HIELO', 'Hielo'),
        ('COCTELERIA', 'Frutas y Verduras'),
        ('CONSUMIBLE', 'Abarrotes y Consumibles'),
    ]
    
    categoria = models.CharField(max_length=30, choices=CATEGORIAS_BARRA, verbose_name="Concepto de Barra")
    grupo = models.CharField(max_length=30, choices=GRUPOS, verbose_name="Grupo en Lista de Compras",
                             help_text="Sección donde aparece en la lista de compras")
    insumo = models.ForeignKey(Insumo, on_delete=models.CASCADE, verbose_name="Insumo del Catálogo",
                               help_text="Selecciona el insumo real del catálogo")
    proporcion = models.DecimalField(max_digits=4, decimal_places=2, default=1.00, 
                                      verbose_name="Proporción", 
                                      help_text="Ej: 0.40 = 40% del total de su grupo de alcohol")
    activo = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0, help_text="Orden de aparición en la lista")
    
    class Meta:
        verbose_name = "Plantilla de Barra"
        verbose_name_plural = "Plantilla de Barra"
        ordering = ['grupo', 'orden', 'categoria']
        unique_together = ['categoria', 'insumo']
    
    def __str__(self):
        return f"{self.get_categoria_display()} → {self.insumo.nombre} ({self.proporcion*100:.0f}%)"


# ==========================================
# 2. SUBPRODUCTOS & PRODUCTOS
# ==========================================
class SubProducto(models.Model):
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    def costo_insumos(self): return sum(r.subtotal_costo() for r in self.receta.all())
    def __str__(self): return self.nombre

class RecetaSubProducto(models.Model):
    subproducto = models.ForeignKey(SubProducto, related_name='receta', on_delete=models.CASCADE)
    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=4)
    def subtotal_costo(self): return self.insumo.costo_unitario * self.cantidad
    def __str__(self): return f"{self.subproducto.nombre} <- {self.insumo.nombre}"

class Producto(models.Model):
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    margen_ganancia = models.DecimalField(max_digits=4, decimal_places=2, default=0.30)
    imagen_promocional = models.ImageField(upload_to='productos/', blank=True, null=True)
    def calcular_costo(self): return sum(c.subtotal_costo() for c in self.componentes.all())
    def sugerencia_precio(self): return round(self.calcular_costo() * (1 + self.margen_ganancia), 2)
    def __str__(self): return self.nombre

class ComponenteProducto(models.Model):
    producto = models.ForeignKey(Producto, related_name='componentes', on_delete=models.CASCADE)
    subproducto = models.ForeignKey(SubProducto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    def subtotal_costo(self): return self.subproducto.costo_insumos() * self.cantidad

# ==========================================
# 3. CLIENTES
# ==========================================
class Cliente(models.Model):
    nombre = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    origen = models.CharField(max_length=50, choices=[('Instagram','Instagram'), ('Facebook','Facebook'), ('Google','Google'), ('Recomendacion','Recomendación'), ('Otro','Otro')], default='Otro')
    es_cliente_fiscal = models.BooleanField(default=False, verbose_name="¿Datos Fiscales?")
    tipo_persona = models.CharField(max_length=10, choices=[('FISICA','Física'), ('MORAL','Moral')], default='FISICA')
    rfc = models.CharField(max_length=13, blank=True, null=True, verbose_name="RFC")
    razon_social = models.CharField(max_length=200, blank=True, null=True)
    codigo_postal_fiscal = models.CharField(max_length=5, blank=True, null=True)
    regimen_fiscal = models.CharField(max_length=3, choices=RegimenFiscal.choices, blank=True, null=True, default=RegimenFiscal.SIN_OBLIGACIONES_FISCALES)
    uso_cfdi = models.CharField(max_length=4, choices=UsoCFDI.choices, blank=True, null=True, default=UsoCFDI.GASTOS_EN_GENERAL)
    def __str__(self): return f"{self.nombre} ({self.razon_social})" if self.razon_social else self.nombre

# ==========================================
# 4. COTIZACIONES (CON MÁQUINA DE ESTADOS)
# ==========================================
class Cotizacion(models.Model):
    ESTADOS = [
        ('BORRADOR', 'Borrador'),
        ('COTIZADA', 'Cotización Enviada'),
        ('ANTICIPO', 'Anticipo Recibido'),
        ('CONFIRMADA', 'Venta Confirmada'),
        ('EN_PREPARACION', 'En Preparación'),
        ('EJECUTADA', 'Evento Ejecutado'),
        ('CERRADA', 'Cerrada / Completada'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    # Transiciones permitidas: estado_actual -> [estados_destino]
    TRANSICIONES_PERMITIDAS = {
        'BORRADOR': ['COTIZADA', 'CANCELADA'],
        'COTIZADA': ['ANTICIPO', 'CONFIRMADA', 'CANCELADA'],
        'ANTICIPO': ['CONFIRMADA', 'CANCELADA'],
        'CONFIRMADA': ['EN_PREPARACION', 'CANCELADA'],
        'EN_PREPARACION': ['EJECUTADA', 'CANCELADA'],
        'EJECUTADA': ['CERRADA'],
        'CERRADA': [],  # Estado final
        'CANCELADA': ['BORRADOR'],  # Permite reactivar
    }
    
    CLIMA_CHOICES = [
        ('normal', 'Interior / Aire Acondicionado'),
        ('calor', 'Exterior / Calor Mérida (+30% Hielo)'),
        ('extremo', 'Ola de Calor / Mayo (+60% Hielo)'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    nombre_evento = models.CharField(max_length=200, default="Evento General")
    fecha_evento = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    
    num_personas = models.IntegerField(default=50, verbose_name="Número de Personas")
    incluye_refrescos = models.BooleanField(default=True, verbose_name="Refrescos y Mezcladores")
    incluye_cerveza = models.BooleanField(default=False, verbose_name="Cerveza (Caguama)")
    incluye_licor_nacional = models.BooleanField(default=False, verbose_name="Licores Nacionales")
    incluye_licor_premium = models.BooleanField(default=False, verbose_name="Licores Premium")
    incluye_cocteleria_basica = models.BooleanField(default=False, verbose_name="Coctelería Básica")
    incluye_cocteleria_premium = models.BooleanField(default=False, verbose_name="Mixología")
    clima = models.CharField(max_length=20, choices=CLIMA_CHOICES, default='calor', verbose_name="Clima")
    horas_servicio = models.IntegerField(default=5, verbose_name="Horas Servicio")
    factor_utilidad_barra = models.DecimalField(max_digits=5, decimal_places=2, default=1.30, verbose_name="Factor Utilidad")

    insumo_hielo = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name="Insumo Hielo")
    insumo_refresco = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name="Insumo Refresco")
    insumo_agua = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name="Insumo Agua")
    insumo_alcohol_basico = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name="Alcohol Básico")
    insumo_alcohol_premium = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name="Alcohol Premium")
    insumo_barman = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name="Insumo Bartender")
    insumo_auxiliar = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name="Insumo Auxiliar")

    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    requiere_factura = models.BooleanField(default=False)
    
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    iva = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    retencion_isr = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    retencion_iva = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    precio_final = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    estado = models.CharField(max_length=20, choices=ESTADOS, default='BORRADOR')
    
    # Campos de cancelación
    motivo_cancelacion = models.TextField(blank=True, verbose_name="Motivo de Cancelación")
    cancelada_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='cotizaciones_canceladas', verbose_name="Cancelada por")
    fecha_cancelacion = models.DateTimeField(null=True, blank=True)
    
    # Auditoría
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archivo_pdf = models.FileField(upload_to='cotizaciones_pdf/', blank=True, null=True, storage=RawMediaCloudinaryStorage())

    def cambiar_estado(self, nuevo_estado, usuario=None, motivo=''):
        """
        Cambia el estado de la cotización validando transiciones permitidas.
        Retorna (exito: bool, mensaje: str)
        """
        estado_actual = self.estado
        permitidos = self.TRANSICIONES_PERMITIDAS.get(estado_actual, [])
        
        if nuevo_estado not in permitidos:
            return False, f"No se puede cambiar de '{self.get_estado_display()}' a '{dict(self.ESTADOS).get(nuevo_estado, nuevo_estado)}'. Transiciones permitidas: {', '.join(permitidos) or 'Ninguna (estado final)'}"
        
        # Validación: necesita items para avanzar de BORRADOR
        if estado_actual == 'BORRADOR' and nuevo_estado != 'CANCELADA':
            if not self.items.exists():
                return False, "La cotización debe tener al menos un item antes de avanzar."
        
        # Validación: anticipo mínimo para CONFIRMAR
        if nuevo_estado == 'CONFIRMADA':
            porcentaje_minimo = self._get_porcentaje_anticipo_minimo()
            if porcentaje_minimo > 0 and self.precio_final > 0:
                pagado = self.total_pagado()
                porcentaje_pagado = (pagado / self.precio_final) * 100
                if porcentaje_pagado < porcentaje_minimo:
                    return False, f"Se requiere al menos {porcentaje_minimo}% de anticipo para confirmar. Pagado: {porcentaje_pagado:.1f}% (${pagado:,.2f} de ${self.precio_final:,.2f})"
        
        # Validación: pagos completos para CERRAR
        if nuevo_estado == 'CERRADA':
            saldo = self.saldo_pendiente()
            if saldo > Decimal('0.50'):  # Tolerancia de 50 centavos
                return False, f"No se puede cerrar con saldo pendiente de ${saldo:,.2f}"
        
        # Aplicar cancelación
        if nuevo_estado == 'CANCELADA':
            if not motivo:
                return False, "Debe indicar el motivo de cancelación."
            self.motivo_cancelacion = motivo
            self.cancelada_por = usuario
            self.fecha_cancelacion = now()
        
        # Si reactiva desde CANCELADA, limpiar campos de cancelación
        if estado_actual == 'CANCELADA' and nuevo_estado == 'BORRADOR':
            self.motivo_cancelacion = ''
            self.cancelada_por = None
            self.fecha_cancelacion = None
        
        self.estado = nuevo_estado
        self.save(update_fields=['estado', 'motivo_cancelacion', 'cancelada_por', 'fecha_cancelacion', 'updated_at'])
        return True, f"Estado cambiado a '{dict(self.ESTADOS).get(nuevo_estado)}'"
    
    def _get_porcentaje_anticipo_minimo(self):
        """Obtiene el porcentaje mínimo de anticipo desde ConstanteSistema."""
        try:
            return float(ConstanteSistema.objects.get(clave='PORCENTAJE_ANTICIPO_MINIMO').valor)
        except ConstanteSistema.DoesNotExist:
            return 0  # Si no está configurado, no aplica restricción
    
    @property
    def porcentaje_pagado(self):
        """Retorna el porcentaje de pago como número."""
        if self.precio_final > 0:
            return round((self.total_pagado() / self.precio_final) * 100, 1)
        return Decimal('0.0')
    
    @property
    def dias_para_evento(self):
        """Días restantes para el evento. Negativo = ya pasó."""
        from django.utils import timezone
        hoy = timezone.now().date()
        return (self.fecha_evento - hoy).days

    def calcular_totales(self):
        if not self.pk: return 
        suma_items = sum(item.subtotal() for item in self.items.all())
        self.subtotal = suma_items
        base = Decimal(self.subtotal) - Decimal(self.descuento)
        if base < 0: base = Decimal('0.00')
        if self.requiere_factura:
            self.iva = base * Decimal('0.16')
            if self.cliente.tipo_persona == 'MORAL':
                self.retencion_isr = base * Decimal('0.0125')
                self.retencion_iva = Decimal('0.00')
            else:
                self.retencion_isr = Decimal('0.00')
                self.retencion_iva = Decimal('0.00')
        else:
            self.iva = Decimal('0.00')
            self.retencion_isr = Decimal('0.00')
            self.retencion_iva = Decimal('0.00')
        self.precio_final = base + self.iva - self.retencion_isr - self.retencion_iva

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from .services import actualizar_item_cotizacion
        actualizar_item_cotizacion(self)
        self.calcular_totales()
        Cotizacion.objects.filter(pk=self.pk).update(
            subtotal=self.subtotal, iva=self.iva, 
            retencion_isr=self.retencion_isr, retencion_iva=self.retencion_iva, 
            precio_final=self.precio_final
        )

    def total_pagado(self): return self.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
    def saldo_pendiente(self): return self.precio_final - self.total_pagado()
    def __str__(self): return f"{self.cliente} - {self.nombre_evento}"
    class Meta: 
        verbose_name = "Cotización"
        verbose_name_plural = "Cotizaciones"
        indexes = [
            models.Index(fields=['estado', 'fecha_evento']),
            models.Index(fields=['fecha_evento']),
        ]

class ItemCotizacion(models.Model):
    cotizacion = models.ForeignKey(Cotizacion, related_name='items', on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.SET_NULL, null=True, blank=True)
    insumo = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True)
    descripcion = models.CharField(max_length=255, blank=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=1.00)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    def save(self, *args, **kwargs):
        if self.precio_unitario == 0:
            if self.producto: self.precio_unitario = self.producto.sugerencia_precio()
            elif self.insumo: self.precio_unitario = self.insumo.costo_unitario
        super().save(*args, **kwargs)
        if self.cotizacion.pk:
            self.cotizacion.calcular_totales()
            Cotizacion.objects.filter(pk=self.cotizacion.pk).update(
                subtotal=self.cotizacion.subtotal, 
                precio_final=self.cotizacion.precio_final
            )

    def subtotal(self): return self.cantidad * self.precio_unitario

class Pago(models.Model):
    METODOS = [('EFECTIVO', 'Efectivo'), ('TRANSFERENCIA', 'Transferencia Electrónica'), ('TARJETA_CREDITO', 'Tarjeta de Crédito'), ('TARJETA_DEBITO', 'Tarjeta de Débito'), ('CHEQUE', 'Cheque Nominativo'), ('DEPOSITO', 'Depósito Bancario'), ('PLATAFORMA', 'Plataforma'), ('CONDONACION', 'Condonación / Cortesía'), ('OTRO', 'Otro Método')]
    cotizacion = models.ForeignKey(Cotizacion, related_name='pagos', on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, 
                                 verbose_name="Registrado por")
    fecha_pago = models.DateField(default=now, verbose_name="Fecha de Pago")
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS)
    referencia = models.CharField(max_length=100, blank=True)
    
    # Auditoría (NUEVO)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Registro")
    updated_at = models.DateTimeField(auto_now=True)
    notas = models.CharField(max_length=255, blank=True, verbose_name="Notas")
    
    def clean(self):
        """Valida que el pago no exceda el saldo pendiente."""
        if self.cotizacion_id:
            total_pagado = self.cotizacion.pagos.exclude(pk=self.pk).aggregate(
                Sum('monto'))['monto__sum'] or Decimal('0.00')
            saldo_disponible = self.cotizacion.precio_final - total_pagado
            
            if self.monto > saldo_disponible + Decimal('0.50'):  # Tolerancia de 50 centavos
                raise ValidationError({
                    'monto': f'El monto (${self.monto:,.2f}) excede el saldo pendiente (${saldo_disponible:,.2f}). '
                             f'Total cotización: ${self.cotizacion.precio_final:,.2f}, Ya pagado: ${total_pagado:,.2f}'
                })
    
    def __str__(self): return f"${self.monto}"
    
    class Meta:
        indexes = [
            models.Index(fields=['fecha_pago']),
            models.Index(fields=['cotizacion', 'fecha_pago']),
        ]

# --- COMPRA Y GASTO ---
class Compra(models.Model):
    proveedor = models.CharField(max_length=200, blank=True)
    rfc_emisor = models.CharField(max_length=13, blank=True)
    fecha_emision = models.DateField(blank=True, null=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    descuento = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    iva = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    ret_isr = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    ret_iva = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    archivo_xml = models.FileField(upload_to='xml_compras/', storage=RawMediaCloudinaryStorage(), blank=True, null=True)
    archivo_pdf = models.FileField(upload_to='pdf_compras/', blank=True, null=True, storage=RawMediaCloudinaryStorage())
    uuid = models.CharField(max_length=36, blank=True, null=True, unique=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    def save(self, *args, **kwargs):
        if self.archivo_xml and not self.pk:
            try:
                if self.archivo_xml.closed: self.archivo_xml.open()
                self.archivo_xml.seek(0)
                tree = ET.parse(self.archivo_xml)
                root = tree.getroot()
                ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                if 'http://www.sat.gob.mx/cfd/3' in root.tag: ns = {'cfdi': 'http://www.sat.gob.mx/cfd/3'}
                self.total = Decimal(root.attrib.get('Total', 0))
                self.subtotal = Decimal(root.attrib.get('SubTotal', 0))
                self.descuento = Decimal(root.attrib.get('Descuento', 0))
                fecha_str = root.attrib.get('Fecha', '')
                if fecha_str: self.fecha_emision = fecha_str.split('T')[0] 
                emisor = root.find('cfdi:Emisor', ns)
                if emisor is not None: 
                    self.proveedor = emisor.attrib.get('Nombre', '')
                    self.rfc_emisor = emisor.attrib.get('Rfc', '')
                complemento = root.find('cfdi:Complemento', ns)
                if complemento is not None:
                    ns_tfd = {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'}
                    timbre = complemento.find('tfd:TimbreFiscalDigital', ns_tfd)
                    if timbre is not None: self.uuid = timbre.attrib.get('UUID', '')
                impuestos = root.find('cfdi:Impuestos', ns)
                if impuestos is not None:
                    retenciones = impuestos.find('cfdi:Retenciones', ns)
                    if retenciones is not None:
                        for r in retenciones.findall('cfdi:Retencion', ns):
                            if r.attrib.get('Impuesto') == '001': self.ret_isr = Decimal(r.attrib.get('Importe', 0))
                            elif r.attrib.get('Impuesto') == '002': self.ret_iva = Decimal(r.attrib.get('Importe', 0))
                    traslados = impuestos.find('cfdi:Traslados', ns)
                    if traslados is not None:
                        for t in traslados.findall('cfdi:Traslado', ns):
                            if t.attrib.get('Impuesto') == '002': self.iva = Decimal(t.attrib.get('Importe', 0))
            except Exception as e: print(f"Error procesando XML cabecera: {e}")
        super().save(*args, **kwargs)
        if self.archivo_xml and self.pk:
            try:
                if not self.gastos.exists():
                    if self.archivo_xml.closed: self.archivo_xml.open()
                    self.archivo_xml.seek(0)
                    tree = ET.parse(self.archivo_xml)
                    root = tree.getroot()
                    ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                    if 'http://www.sat.gob.mx/cfd/3' in root.tag: ns = {'cfdi': 'http://www.sat.gob.mx/cfd/3'}
                    conceptos = root.find('cfdi:Conceptos', ns)
                    if conceptos is not None:
                        for c in conceptos.findall('cfdi:Concepto', ns):
                            descripcion = c.attrib.get('Descripcion', '')
                            cantidad = Decimal(c.attrib.get('Cantidad', 1))
                            valor_unitario = Decimal(c.attrib.get('ValorUnitario', 0))
                            importe = Decimal(c.attrib.get('Importe', 0))
                            clave_sat = c.attrib.get('ClaveProdServ', '')
                            unidad = c.attrib.get('ClaveUnidad', '')
                            iva_linea = Decimal(0)
                            traslados_c = c.find('cfdi:Impuestos/cfdi:Traslados', ns)
                            if traslados_c is not None:
                                for t in traslados_c.findall('cfdi:Traslado', ns):
                                    if t.attrib.get('Impuesto') == '002':
                                        try: iva_linea += Decimal(t.attrib.get('Importe', 0))
                                        except: iva_linea = importe * Decimal('0.16')
                            Gasto.objects.create(compra=self, descripcion=descripcion, cantidad=cantidad, precio_unitario=valor_unitario, total_linea=importe + iva_linea, clave_sat=clave_sat, unidad_medida=unidad, fecha_gasto=self.fecha_emision, proveedor=self.proveedor, categoria='SIN_CLASIFICAR')
            except Exception as e: print(f"Error procesando conceptos: {e}")
    def __str__(self): return f"{self.proveedor} - ${self.total}"

class Gasto(models.Model):
    CATEGORIAS = [('SIN_CLASIFICAR', 'Sin Clasificar'), ('SERVICIO_EXTERNO', 'Servicio Externo'), ('BEBIDAS_SIN_ALCOHOL', 'Bebidas Sin Alcohol'), ('BEBIDAS_CON_ALCOHOL', 'Bebidas Con Alcohol'), ('LIMPIEZA', 'Limpieza Y Desechables'), ('MOBILIARIO_EQ', 'Mobiliario Y Equipo'), ('MANTENIMIENTO', 'Mantenimiento Y Reparaciones'), ('NOMINA_EXT', 'Servicios Staff Externo'), ('IMPUESTOS', 'Pago De Impuestos'), ('PUBLICIDAD', 'Publicidad Y Marketing'), ('SERVICIOS_ADMON', 'Servicios Administrativos Y Bancarios'), ('OTRO', 'Otros Gastos')]
    compra = models.ForeignKey(Compra, related_name='gastos', on_delete=models.CASCADE)
    descripcion = models.CharField(max_length=255)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_linea = models.DecimalField(max_digits=10, decimal_places=2, default=0) 
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default='SIN_CLASIFICAR')
    evento_relacionado = models.ForeignKey('Cotizacion', on_delete=models.SET_NULL, null=True, blank=True)
    clave_sat = models.CharField(max_length=20, blank=True)
    unidad_medida = models.CharField(max_length=20, blank=True)
    fecha_gasto = models.DateField(blank=True, null=True, db_index=True)
    proveedor = models.CharField(max_length=200, blank=True)
    archivo_xml = models.FileField(upload_to='xml_gastos/', blank=True, null=True, storage=RawMediaCloudinaryStorage())
    archivo_pdf = models.FileField(upload_to='pdf_gastos/', blank=True, null=True, storage=RawMediaCloudinaryStorage())
    def __str__(self): return f"{self.descripcion} (${self.total_linea})"