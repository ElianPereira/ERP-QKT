import xml.etree.ElementTree as ET
from decimal import Decimal
from django.db import models, transaction
from django.db.models import F, Sum
from django.utils.timezone import now
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from facturacion.choices import RegimenFiscal, UsoCFDI
from cloudinary_storage.storage import RawMediaCloudinaryStorage
import secrets

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
        verbose_name = "Inventario"
        verbose_name_plural = "Inventarios"
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
        """Guarda el movimiento y actualiza el stock del insumo atómicamente."""
        with transaction.atomic():
            # Lock el insumo para evitar actualizaciones concurrentes
            insumo = Insumo.objects.select_for_update().get(pk=self.insumo_id)
            self.stock_anterior = insumo.cantidad_stock
            self.stock_posterior = insumo.cantidad_stock  # Placeholder; se recalcula abajo

            # Validar cantidad y stock suficiente
            self.clean()

            # Update atómico con F() expression
            if self.tipo in ('ENTRADA', 'AJUSTE_POS'):
                Insumo.objects.filter(pk=self.insumo_id).update(
                    cantidad_stock=F('cantidad_stock') + self.cantidad
                )
            elif self.tipo in ('SALIDA', 'AJUSTE_NEG', 'DEVOLUCION'):
                Insumo.objects.filter(pk=self.insumo_id).update(
                    cantidad_stock=F('cantidad_stock') - self.cantidad
                )

            # Leer valor real post-update
            insumo.refresh_from_db()
            self.insumo = insumo
            self.stock_posterior = insumo.cantidad_stock
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
    GRUPO_COTIZADOR_CHOICES = [
        ('ENTRETENIMIENTO', 'Entretenimiento'),
        ('COMIDA', 'Comida'),
        ('MOBILIARIO', 'Mobiliario'),
        ('DECORACION', 'Decoración'),
        ('INFANTIL', 'Infantil'),
        ('OTRO', 'Otros'),
    ]

    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    margen_ganancia = models.DecimalField(max_digits=4, decimal_places=2, default=0.30)
    imagen_promocional = models.ImageField(upload_to='productos/', blank=True, null=True)

    visible_cotizador = models.BooleanField(default=False, verbose_name="Mostrar en cotizador web")
    grupo_cotizador = models.CharField(
        max_length=20, blank=True, choices=GRUPO_COTIZADOR_CHOICES,
        verbose_name="Grupo en cotizador",
    )
    icono = models.CharField(max_length=10, blank=True, help_text="Emoji, ej: 🎧")
    descripcion_corta = models.CharField(
        max_length=120, blank=True, verbose_name="Descripción corta",
        help_text="Texto debajo del nombre en el cotizador",
    )
    orden_cotizador = models.PositiveIntegerField(default=0, verbose_name="Orden en cotizador")
    grupo_exclusion = models.CharField(
        max_length=30, blank=True, verbose_name="Grupo de exclusión",
        help_text="Productos con el mismo valor son mutuamente exclusivos. Ej: DJ",
    )
    cantidad_por_persona = models.BooleanField(
        default=False, verbose_name="Cantidad según personas",
        help_text="Si activo, cantidad = ceil(personas / factor)",
    )
    factor_personas = models.PositiveIntegerField(
        default=1, verbose_name="Factor divisor",
        help_text="Ej: 10 → una unidad cada 10 personas",
    )
    cotizador_evento = models.BooleanField(default=False, verbose_name="Disponible para Evento")
    cotizador_pasadia = models.BooleanField(default=False, verbose_name="Disponible para Pasadía")
    cotizador_arrendamiento = models.BooleanField(default=False, verbose_name="Disponible para Arrendamiento de Mobiliario")

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
        ('CONFIRMADA', 'Venta Confirmada'),
        ('EJECUTADA', 'Evento Ejecutado'),
        ('CERRADA', 'Cerrada / Completada'),
        ('CANCELADA', 'Cancelada'),
    ]

    # Transiciones permitidas: estado_actual -> [estados_destino]
    TRANSICIONES_PERMITIDAS = {
        'BORRADOR': ['COTIZADA', 'CANCELADA'],
        'COTIZADA': ['CONFIRMADA', 'CANCELADA'],
        'CONFIRMADA': ['EJECUTADA', 'CANCELADA'],
        'EJECUTADA': ['CERRADA'],
        'CERRADA': [],  # Estado final
        'CANCELADA': ['BORRADOR'],  # Permite reactivar
    }
    
    CLIMA_CHOICES = [
        ('normal', 'Interior / Aire Acondicionado'),
        ('calor', 'Exterior / Calor Mérida (+30% Hielo)'),
        ('extremo', 'Ola de Calor / Mayo (+60% Hielo)'),
    ]
    
    TIPO_SERVICIO_CHOICES = [
        ('EVENTO', 'Evento'),
        ('PASADIA', 'Pasadía'),
        ('ARRENDAMIENTO', 'Arrendamiento de Mobiliario'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    tipo_servicio = models.CharField(
        max_length=15, choices=TIPO_SERVICIO_CHOICES, default='EVENTO',
        verbose_name="Tipo de servicio"
    )
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
    archivo_contrato = models.FileField(upload_to='contratos_pdf/', blank=True, null=True, storage=RawMediaCloudinaryStorage(), verbose_name="Contrato PDF")

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
            try:
                from contabilidad.signals import crear_polizas_reversion_cancelacion
                crear_polizas_reversion_cancelacion(self, usuario, motivo)
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception(
                    "Error generando póliza de reversión para Cotización #%s: %s", self.pk, e
                )
        
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
        self.iva = base * Decimal('0.16')
        if self.cliente.tipo_persona == 'MORAL':
            self.retencion_isr = base * Decimal('0.0125')
            self.retencion_iva = Decimal('0.00')
        else:
            self.retencion_isr = Decimal('0.00')
            self.retencion_iva = Decimal('0.00')
        self.precio_final = base + self.iva - self.retencion_isr - self.retencion_iva
    def clean(self):
        """Si la cotización está apartando una fecha (anticipo o superior),
        valida que no choque con Airbnb u otra cotización ya apartada."""
        super().clean()
        if self.fecha_evento and self.estado == 'CONFIRMADA':
            try:
                from airbnb.validacion_fechas import verificar_disponibilidad_fecha
                disponible, msg = verificar_disponibilidad_fecha(
                    self.fecha_evento, cotizacion_id=self.pk
                )
                if not disponible:
                    raise ValidationError({'fecha_evento': msg})
            except ValidationError:
                raise
            except Exception:
                pass

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            from .services import actualizar_item_cotizacion
            actualizar_item_cotizacion(self)
            self.calcular_totales()
            Cotizacion.objects.filter(pk=self.pk).update(
                subtotal=self.subtotal, iva=self.iva,
                retencion_isr=self.retencion_isr, retencion_iva=self.retencion_iva,
                precio_final=self.precio_final
            )
            try:
                from .models import PortalCliente
                PortalCliente.objects.get_or_create(
                    cotizacion=self,
                    defaults={'activo': True}
                )
            except Exception:
                pass

    def total_pagado(self):
        """Total neto cobrado (ingresos - reembolsos)."""
        return self.total_pagado_neto()

    def total_pagado_neto(self, excluir_pk=None):
        qs = self.pagos.all()
        if excluir_pk:
            qs = qs.exclude(pk=excluir_pk)
        ingresos = qs.filter(tipo='INGRESO').aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')
        reembolsos = qs.filter(tipo='REEMBOLSO').aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')
        return ingresos - reembolsos

    def total_cobrado_bruto(self, excluir_pk=None):
        qs = self.pagos.filter(tipo='INGRESO')
        if excluir_pk:
            qs = qs.exclude(pk=excluir_pk)
        return qs.aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    def total_reembolsado(self, excluir_pk=None):
        qs = self.pagos.filter(tipo='REEMBOLSO')
        if excluir_pk:
            qs = qs.exclude(pk=excluir_pk)
        return qs.aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')
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
    TIPOS = [('INGRESO', 'Ingreso'), ('REEMBOLSO', 'Reembolso')]
    tipo = models.CharField(max_length=10, choices=TIPOS, default='INGRESO', verbose_name="Tipo")
    cotizacion = models.ForeignKey(Cotizacion, related_name='pagos', on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, 
                                 verbose_name="Registrado por")
    fecha_pago = models.DateField(default=now, verbose_name="Fecha de Pago")
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS)
    referencia = models.CharField(max_length=100, blank=True)
    
    # Facturación
    solicitar_factura = models.BooleanField(
        default=False,
        verbose_name="¿Solicitar factura?",
        help_text="Genera automáticamente una solicitud de factura al guardar"
    )
    
    # Auditoría
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Registro")
    updated_at = models.DateTimeField(auto_now=True)
    notas = models.CharField(max_length=255, blank=True, verbose_name="Notas")
    
    def clean(self):
        """Valida que el pago no exceda el saldo pendiente (no aplica a reembolsos)."""
        if self.cotizacion_id and self.tipo == 'INGRESO':
            total_pagado = self.cotizacion.total_pagado_neto(excluir_pk=self.pk)
            saldo_disponible = self.cotizacion.precio_final - total_pagado

            if self.monto > saldo_disponible + Decimal('0.50'):  # Tolerancia de 50 centavos
                raise ValidationError({
                    'monto': f'El monto (${self.monto:,.2f}) excede el saldo pendiente (${saldo_disponible:,.2f}). '
                             f'Total cotización: ${self.cotizacion.precio_final:,.2f}, Ya pagado: ${total_pagado:,.2f}'
                })

        if self.cotizacion_id and self.tipo == 'REEMBOLSO':
            # Un reembolso no puede exceder lo realmente cobrado
            cobrado = self.cotizacion.total_cobrado_bruto(excluir_pk=self.pk)
            reembolsado = self.cotizacion.total_reembolsado(excluir_pk=self.pk)
            disponible = cobrado - reembolsado
            if self.monto > disponible + Decimal('0.50'):
                raise ValidationError({
                    'monto': f'El reembolso (${self.monto:,.2f}) excede lo cobrado neto disponible (${disponible:,.2f}).'
                })

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.cotizacion_id:
                # Lock la cotización para evitar pagos simultáneos
                Cotizacion.objects.select_for_update().get(pk=self.cotizacion_id)
            self.full_clean()
            super().save(*args, **kwargs)

    def __str__(self): return f"${self.monto}"
    
    class Meta:
        indexes = [
            models.Index(fields=['fecha_pago']),
            models.Index(fields=['cotizacion', 'fecha_pago']),
        ]
class ContratoServicio(models.Model):
    """
    Registro de contratos generados por cotización.
    Guarda historial: versión, quién lo generó y cuándo.
    """
    TIPO_CHOICES = [
        ('EVENTO',         'Evento'),
        ('PASADIA',        'Pasadía'),
        ('ARRENDAMIENTO',  'Arrendamiento de Mobiliario'),
    ]

    cotizacion   = models.ForeignKey(Cotizacion, on_delete=models.CASCADE, related_name='contratos')
    numero       = models.CharField(max_length=30, unique=True, verbose_name="Número de Contrato")
    tipo_servicio = models.CharField(max_length=20, choices=TIPO_CHOICES, default='EVENTO')
    deposito_garantia = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
                                            verbose_name="Depósito en Garantía (MXN)")
    archivo = models.FileField(upload_to='contratos_pdf/', storage=RawMediaCloudinaryStorage(), verbose_name="Archivo PDF")
    generado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    generado_en  = models.DateTimeField(auto_now_add=True)
    enviado_email = models.BooleanField(default=False)
    notas        = models.TextField(blank=True)

    def __str__(self):
        return f"{self.numero} — {self.cotizacion.cliente.nombre}"

    class Meta:
        verbose_name = "Contrato"
        verbose_name_plural = "Contratos"
        ordering = ['-generado_en']

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
    unidad_negocio = models.ForeignKey(
        'contabilidad.UnidadNegocio',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Unidad de Negocio"
    )
    
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
                if fecha_str:
                    from datetime import datetime
                    self.fecha_emision = datetime.strptime(fecha_str.split('T')[0], '%Y-%m-%d').date() 
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
            finally:
                if self.archivo_xml: self.archivo_xml.seek(0)
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

# ==========================================
# AGREGAR AL FINAL DE comercial/models.py
# ==========================================

class PlanPago(models.Model):

    cotizacion = models.OneToOneField(
        Cotizacion, on_delete=models.CASCADE, 
        related_name='plan_pago',
        verbose_name="Cotización"
    )
    
    fecha_generacion = models.DateTimeField(auto_now_add=True)
    generado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Generado por"
    )
    notas = models.TextField(blank=True, verbose_name="Notas / Condiciones especiales")
    activo = models.BooleanField(default=True)
    
    def total_plan(self):
        return self.parcialidades.aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')
    
    def parcialidades_pagadas(self):
        return self.parcialidades.filter(pagada=True).count()
    
    def parcialidades_pendientes(self):
        return self.parcialidades.filter(pagada=False).count()
    
    def siguiente_pago(self):
        """Retorna la próxima parcialidad pendiente."""
        return self.parcialidades.filter(pagada=False).order_by('fecha_limite').first()
    
    def __str__(self):
        return f"Plan de pagos COT-{self.cotizacion.id:03d} ({self.parcialidades.count()} parcialidades)"
    
    class Meta:
        verbose_name = "Plan de Pago"
        verbose_name_plural = "Planes de Pago"


class ParcialidadPago(models.Model):

    plan = models.ForeignKey(
        PlanPago, on_delete=models.CASCADE, 
        related_name='parcialidades',
        verbose_name="Plan"
    )
    numero = models.PositiveIntegerField(verbose_name="# Parcialidad")
    concepto = models.CharField(max_length=100, verbose_name="Concepto",
                                 help_text="Ej: Anticipo, 2da parcialidad, Liquidación")
    monto = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Monto")
    porcentaje = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="% del Total")
    fecha_limite = models.DateField(verbose_name="Fecha Límite de Pago")
    
    pagada = models.BooleanField(default=False, verbose_name="¿Pagada?")
    fecha_pago_real = models.DateField(null=True, blank=True, verbose_name="Fecha de Pago Real")
    pago_vinculado = models.ForeignKey(
        'Pago', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Pago Registrado",
        help_text="Vincular con el pago real cuando se reciba"
    )
    
    @property
    def dias_restantes(self):
        """Días que faltan para la fecha límite. Negativo = vencido."""
        from django.utils import timezone
        return (self.fecha_limite - timezone.now().date()).days
    
    @property
    def estado(self):
        if self.pagada:
            return 'PAGADA'
        if self.dias_restantes < 0:
            return 'VENCIDA'
        if self.dias_restantes <= 7:
            return 'URGENTE'
        return 'PENDIENTE'
    
    def __str__(self):
        estado = "" if self.pagada else "⏳"
        return f"{estado} #{self.numero} - ${self.monto:,.2f} - {self.fecha_limite}"
    
    class Meta:
        verbose_name = "Parcialidad"
        verbose_name_plural = "Parcialidades"
        ordering = ['numero']
        unique_together = ['plan', 'numero']

# ==========================================
# RECORDATORIOS DE PAGO (WHATSAPP)
# ==========================================
class RecordatorioPago(models.Model):
    ESTADOS = [
        ('ENVIADO', 'Enviado'),
        ('FALLIDO', 'Fallido'),
        ('OMITIDO', 'Omitido (sin teléfono)'),
    ]

    parcialidad = models.ForeignKey(
        ParcialidadPago, on_delete=models.CASCADE,
        related_name='recordatorios',
        verbose_name="Parcialidad"
    )
    fecha_envio = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ENVIADO')
    mensaje_enviado = models.TextField(blank=True, verbose_name="Mensaje enviado")
    respuesta_api = models.TextField(blank=True, verbose_name="Respuesta API WhatsApp")
    error_detalle = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Recordatorio de Pago"
        verbose_name_plural = "Recordatorios de Pago"
        ordering = ['-fecha_envio']

    def __str__(self):
        return f"Recordatorio {self.parcialidad} — {self.get_estado_display()}"

#PAGINA PARA CLIENTES


class PortalCliente(models.Model):
    """
    Token de acceso público para que el cliente vea su cotización,
    plan de pagos, contrato y estado de pagos sin necesidad de login.
    
    Acceso: código de cotización + últimos 4 dígitos del teléfono.
    URL: /mi-evento/<token>/
    """
    cotizacion = models.OneToOneField(
        Cotizacion, on_delete=models.CASCADE,
        related_name='portal', verbose_name="Cotización"
    )
    token = models.CharField(
        max_length=64, unique=True, db_index=True,
        verbose_name="Token de Acceso",
        help_text="Se genera automáticamente. No editar."
    )
    activo = models.BooleanField(default=True, verbose_name="Portal Activo")
    visitas = models.PositiveIntegerField(default=0, verbose_name="Visitas")
    ultima_visita = models.DateTimeField(null=True, blank=True)
    
    # Auditoría
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Creado por"
    )
    
    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)
    
    def get_url(self):
        """Retorna la URL pública del portal."""
        return f"/mi-evento/{self.token}/"
    
    def get_full_url(self, request=None):
        """Retorna la URL completa con dominio.
        Usa PORTAL_URL para el subdominio dedicado a clientes.
        """
        from django.conf import settings
        base = getattr(settings, 'PORTAL_URL', 'https://clientes.quintakooxtanil.com')
        if request:
            base = request.build_absolute_uri('/')[:-1]
        return f"{base}{self.get_url()}"
    
    def registrar_visita(self):
        """Incrementa contador de visitas."""
        from django.utils import timezone
        self.visitas += 1
        self.ultima_visita = timezone.now()
        self.save(update_fields=['visitas', 'ultima_visita'])
    
    def __str__(self):
        return f"Portal COT-{self.cotizacion.id:03d} ({self.visitas} visitas)"
    
    class Meta:
        verbose_name = "Portal del Cliente"
        verbose_name_plural = "Portales de Clientes"

# ==========================================
# 5. ESPACIOS Y ASIGNACIONES (Fase 4)
# ==========================================
class Espacio(models.Model):
    """Espacios físicos rentables: jardín, terraza, salón, palapa."""
    TIPO_CHOICES = [
        ('JARDIN', 'Jardín'),
        ('TERRAZA', 'Terraza'),
        ('SALON', 'Salón'),
        ('PALAPA', 'Palapa'),
        ('ALBERCA', 'Alberca'),
        ('OTRO', 'Otro'),
    ]
    nombre = models.CharField(max_length=100, unique=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default='OTRO')
    capacidad_max = models.PositiveIntegerField(default=50, verbose_name="Capacidad máxima")
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Espacio"
        verbose_name_plural = "Espacios"
        ordering = ['nombre']

    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_display()})"


def _rangos_solapados(a_ini, a_fin, b_ini, b_fin):
    """True si dos rangos [ini, fin] se traslapan."""
    return a_ini < b_fin and b_ini < a_fin


class AsignacionEspacio(models.Model):
    """Reserva de un espacio para una cotización en una franja horaria."""
    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.CASCADE,
                                    related_name='espacios_asignados')
    espacio = models.ForeignKey(Espacio, on_delete=models.PROTECT,
                                 related_name='asignaciones')
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    notas = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Asignación de espacio"
        verbose_name_plural = "Asignaciones de espacios"
        indexes = [
            models.Index(fields=['espacio', 'fecha']),
        ]

    def _intervalos(self):
        """Devuelve [(datetime_ini, datetime_fin)] considerando overnight."""
        from datetime import datetime, timedelta
        ini = datetime.combine(self.fecha, self.hora_inicio)
        if self.hora_fin <= self.hora_inicio:
            fin = datetime.combine(self.fecha + timedelta(days=1), self.hora_fin)
        else:
            fin = datetime.combine(self.fecha, self.hora_fin)
        return ini, fin

    def clean(self):
        ini, fin = self._intervalos()
        # Validar conflictos contra otras asignaciones del mismo espacio
        from datetime import timedelta
        candidatas = AsignacionEspacio.objects.filter(
            espacio=self.espacio,
            fecha__gte=self.fecha - timedelta(days=1),
            fecha__lte=self.fecha + timedelta(days=1),
        ).exclude(pk=self.pk)
        for otra in candidatas:
            o_ini, o_fin = otra._intervalos()
            if _rangos_solapados(ini, fin, o_ini, o_fin):
                raise ValidationError(
                    f"Conflicto: el espacio '{self.espacio}' ya está asignado a la "
                    f"cotización #{otra.cotizacion_id} en ese horario."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.espacio} {self.fecha} {self.hora_inicio}-{self.hora_fin}"


class AsignacionPersonal(models.Model):
    """Asignación de un empleado a una cotización en una franja horaria."""
    ROL_CHOICES = [
        ('COORDINADOR', 'Coordinador'),
        ('BARMAN', 'Barman'),
        ('MESERO', 'Mesero'),
        ('LIMPIEZA', 'Limpieza'),
        ('SEGURIDAD', 'Seguridad'),
        ('COCINA', 'Cocina'),
        ('OTRO', 'Otro'),
    ]
    cotizacion = models.ForeignKey(Cotizacion, on_delete=models.CASCADE,
                                    related_name='personal_asignado')
    empleado = models.ForeignKey('nomina.Empleado', on_delete=models.PROTECT,
                                  related_name='asignaciones')
    rol = models.CharField(max_length=20, choices=ROL_CHOICES, default='OTRO')
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    notas = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Asignación de personal"
        verbose_name_plural = "Asignaciones de personal"
        indexes = [
            models.Index(fields=['empleado', 'fecha']),
        ]

    def _intervalos(self):
        from datetime import datetime, timedelta
        ini = datetime.combine(self.fecha, self.hora_inicio)
        if self.hora_fin <= self.hora_inicio:
            fin = datetime.combine(self.fecha + timedelta(days=1), self.hora_fin)
        else:
            fin = datetime.combine(self.fecha, self.hora_fin)
        return ini, fin

    def clean(self):
        ini, fin = self._intervalos()
        from datetime import timedelta
        candidatas = AsignacionPersonal.objects.filter(
            empleado=self.empleado,
            fecha__gte=self.fecha - timedelta(days=1),
            fecha__lte=self.fecha + timedelta(days=1),
        ).exclude(pk=self.pk)
        for otra in candidatas:
            o_ini, o_fin = otra._intervalos()
            if _rangos_solapados(ini, fin, o_ini, o_fin):
                raise ValidationError(
                    f"Conflicto: {self.empleado} ya está asignado a la "
                    f"cotización #{otra.cotizacion_id} en ese horario."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.empleado} → COT-{self.cotizacion_id} ({self.rol})"


# ==========================================
# CONTENIDO DE LANDING PAGE
# ==========================================
class ImagenLanding(models.Model):
    SECCION_CHOICES = [
        ('HERO', 'Banner principal'),
        ('NOSOTROS', 'Quiénes Somos'),
        ('EVENTO', 'Servicio — Eventos'),
        ('PASADIA', 'Servicio — Pasadía'),
        ('HOSPEDAJE', 'Servicio — Hospedaje'),
        ('GALERIA', 'Galería de fotos'),
    ]
    POSICION_CHOICES = [
        ('top', 'Arriba'),
        ('20%', 'Arriba-centro'),
        ('center', 'Centro'),
        ('80%', 'Abajo-centro'),
        ('bottom', 'Abajo'),
    ]
    seccion = models.CharField(max_length=20, choices=SECCION_CHOICES, verbose_name="Sección")
    imagen = models.ImageField(upload_to='landing/', verbose_name="Imagen")
    posicion_vertical = models.CharField(
        max_length=10, choices=POSICION_CHOICES, default='center',
        verbose_name="Enfoque vertical",
        help_text="Qué parte de la imagen se muestra: arriba, centro o abajo",
    )
    titulo = models.CharField(max_length=120, blank=True, verbose_name="Título / descripción interna")
    alt_text = models.CharField(max_length=200, blank=True, verbose_name="Texto alternativo",
                                help_text="Describe la imagen para accesibilidad y SEO")
    orden = models.PositiveIntegerField(default=0, verbose_name="Orden")
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['seccion', 'orden']
        verbose_name = "Imagen de la página web"
        verbose_name_plural = "Página Web — Imágenes"

    def __str__(self):
        return f"{self.get_seccion_display()} — {self.titulo or 'Sin título'}"


class TestimonioLanding(models.Model):
    nombre = models.CharField(max_length=100, verbose_name="Nombre del cliente")
    evento = models.CharField(max_length=100, verbose_name="Tipo de evento",
                              help_text="Ej: Boda · 150 invitados")
    texto = models.TextField(verbose_name="Testimonio")
    estrellas = models.PositiveIntegerField(default=5, verbose_name="Estrellas (1-5)")
    activo = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['orden']
        verbose_name = "Testimonio de cliente"
        verbose_name_plural = "Página Web — Testimonios"

    def __str__(self):
        return f"{self.nombre} — {self.evento}"
