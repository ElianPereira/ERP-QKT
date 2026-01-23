import xml.etree.ElementTree as ET
from decimal import Decimal
from django.db import models
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.contrib.auth.models import User

# --- IMPORTACIÓN DE CATÁLOGOS SAT ---
from facturacion.choices import RegimenFiscal, UsoCFDI

# ==========================================
# 1. INSUMOS
# ==========================================
class Insumo(models.Model):
    TIPOS = [
        ('CONSUMIBLE', 'Consumible (Se gasta: Hielo, Comida, Desechables)'),
        ('MOBILIARIO', 'Mobiliario (Se renta: Sillas, Mesas, Manteles)'),
        ('SERVICIO', 'Personal (RH: Meseros, Seguridad, Staff)')
    ]
    nombre = models.CharField(max_length=200)
    unidad_medida = models.CharField(max_length=50) 
    costo_unitario = models.DecimalField(max_digits=10, decimal_places=2) 
    cantidad_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    categoria = models.CharField(max_length=20, choices=TIPOS, default='CONSUMIBLE')

    def __str__(self):
        return f"{self.nombre} ({self.categoria})"

# ==========================================
# 2. PRODUCTOS
# ==========================================
class Producto(models.Model):
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    margen_ganancia = models.DecimalField(max_digits=4, decimal_places=2, default=0.30)
    
    def calcular_costo(self):
        return sum(c.subtotal_costo() for c in self.componentes.all())

    def sugerencia_precio(self):
        costo = self.calcular_costo()
        return round(costo * (1 + self.margen_ganancia), 2)

    def __str__(self):
        return self.nombre

class ComponenteProducto(models.Model):
    producto = models.ForeignKey(Producto, related_name='componentes', on_delete=models.CASCADE)
    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)

    def subtotal_costo(self):
        return self.insumo.costo_unitario * self.cantidad

# ==========================================
# 3. CLIENTES
# ==========================================
class Cliente(models.Model):
    # --- Datos de Contacto ---
    nombre = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True, help_text="Fecha de creación")
    origen = models.CharField(max_length=50, choices=[
        ('Instagram', 'Instagram'), ('Facebook', 'Facebook'), ('Google', 'Google'), ('Recomendacion', 'Recomendación'), ('Otro', 'Otro')
    ], default='Otro')

    # --- Datos Fiscales ---
    es_cliente_fiscal = models.BooleanField(default=False, verbose_name="¿Datos Fiscales Completos?")
    
    TIPOS_FISCALES = [
        ('FISICA', 'Persona Física (Juan Pérez)'),
        ('MORAL', 'Persona Moral (Empresa S.A. de C.V.)'),
    ]
    tipo_persona = models.CharField(max_length=10, choices=TIPOS_FISCALES, default='FISICA', help_text="Define si lleva Retenciones")
    
    rfc = models.CharField(max_length=13, blank=True, null=True, verbose_name="RFC")
    razon_social = models.CharField(max_length=200, blank=True, null=True, verbose_name="Razón Social (Sin Régimen Societario)")
    codigo_postal_fiscal = models.CharField(max_length=5, blank=True, null=True, verbose_name="C.P. Fiscal")
    
    regimen_fiscal = models.CharField(
        max_length=3, 
        choices=RegimenFiscal.choices, 
        blank=True, 
        null=True,
        default=RegimenFiscal.SIN_OBLIGACIONES_FISCALES,
        verbose_name="Régimen Fiscal"
    )

    uso_cfdi = models.CharField(
        max_length=4, 
        choices=UsoCFDI.choices, 
        blank=True, 
        null=True,
        default=UsoCFDI.GASTOS_EN_GENERAL,
        verbose_name="Uso de CFDI Habitual"
    )
    
    def __str__(self):
        tipo = "(Física)" if self.tipo_persona == 'FISICA' else "(Moral)"
        nombre_mostrar = self.razon_social if self.razon_social else self.nombre
        return f"{nombre_mostrar} {tipo}"

# ==========================================
# 4. COTIZACIONES
# ==========================================
class Cotizacion(models.Model):
    ESTADOS = [
        ('BORRADOR', 'Borrador'),
        ('CONFIRMADA', 'Venta Confirmada'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT) # Paquete base
    
    # --- FECHAS Y HORARIOS ---
    fecha_evento = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True, verbose_name="Hora Inicio")
    hora_fin = models.TimeField(null=True, blank=True, verbose_name="Hora Fin")

    # --- DATOS ADMINISTRATIVOS ---
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Elaborado por")

    # --- CAMPOS FISCALES ---
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Descuento ($)") # <--- NUEVO CAMPO
    requiere_factura = models.BooleanField(default=False, help_text="Si se marca, calcula IVA y Retenciones")
    
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Precio del servicio ANTES de impuestos")
    
    # Desglose 
    iva = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)
    retencion_isr = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)
    retencion_iva = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)
    
    precio_final = models.DecimalField(max_digits=10, decimal_places=2, editable=False, help_text="Total a Pagar (Neto)")
    
    estado = models.CharField(max_length=20, choices=ESTADOS, default='BORRADOR')
    created_at = models.DateTimeField(auto_now_add=True)
    
    archivo_pdf = models.FileField(upload_to='cotizaciones_pdf/', blank=True, null=True)

    def save(self, *args, **kwargs):
        # 1. Calcular Base (Subtotal - Descuento)
        base = Decimal(self.subtotal) - Decimal(self.descuento)
        if base < 0: base = Decimal('0.00') # Evitar negativos
        
        if self.requiere_factura:
            # A. IVA General (16%) sobre la base ya descontada
            self.iva = base * Decimal('0.16')
            
            # B. Retenciones (Solo ISR)
            if self.cliente.tipo_persona == 'MORAL':
                self.retencion_isr = base * Decimal('0.0125') # 1.25% ISR (RESICO)
                self.retencion_iva = Decimal('0.00')          # SIN Retención de IVA
            else:
                self.retencion_isr = Decimal('0.00')
                self.retencion_iva = Decimal('0.00')
        else:
            self.iva = Decimal('0.00')
            self.retencion_isr = Decimal('0.00')
            self.retencion_iva = Decimal('0.00')
            
        # 2. Cálculo Final
        self.precio_final = base + self.iva - self.retencion_isr - self.retencion_iva
        
        super().save(*args, **kwargs)

    def total_pagado(self):
        resultado = self.pagos.aggregate(Sum('monto'))['monto__sum']
        return resultado if resultado else 0

    def saldo_pendiente(self):
        return self.precio_final - self.total_pagado()

    def __str__(self):
        factura = " (Factura)" if self.requiere_factura else ""
        return f"{self.cliente} - ${self.precio_final}{factura}"

    class Meta:
        verbose_name = "Cotización"
        verbose_name_plural = "Cotizaciones"

# ==========================================
# 4.1 ÍTEMS DE LA COTIZACIÓN
# ==========================================
class ItemCotizacion(models.Model):
    cotizacion = models.ForeignKey(Cotizacion, related_name='items', on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.SET_NULL, null=True, blank=True)
    insumo = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=1.00)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def save(self, *args, **kwargs):
        if self.precio_unitario == 0:
            if self.producto:
                self.precio_unitario = self.producto.sugerencia_precio()
            elif self.insumo:
                self.precio_unitario = self.insumo.costo_unitario
        super().save(*args, **kwargs)

    def subtotal(self):
        return self.cantidad * self.precio_unitario

    def __str__(self):
        nombre = self.producto.nombre if self.producto else (self.insumo.nombre if self.insumo else "Ítem")
        return f"{self.cantidad} x {nombre}"

# ==========================================
# 5. PAGOS
# ==========================================
class Pago(models.Model):
    METODOS = [
        ('EFECTIVO', 'Efectivo'), 
        ('TRANSFERENCIA', 'Transferencia Electrónica'),
        ('TARJETA_CREDITO', 'Tarjeta de Crédito'),
        ('TARJETA_DEBITO', 'Tarjeta de Débito'),
        ('CHEQUE', 'Cheque Nominativo'),
        ('DEPOSITO', 'Depósito Bancario'),
        ('PLATAFORMA', 'Plataforma (PayPal/Stripe)'),
        ('CONDONACION', 'Condonación / Cortesía'),
        ('OTRO', 'Otro Método'),
    ]
    cotizacion = models.ForeignKey(Cotizacion, related_name='pagos', on_delete=models.CASCADE)
    
    # --- USUARIO QUE RECIBE EL COBRO ---
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Recibido por")
    
    fecha_pago = models.DateField(auto_now_add=True)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS)
    referencia = models.CharField(max_length=100, blank=True)
    
    def __str__(self):
        return f"${self.monto}"

# ==========================================
# 6. GASTOS
# ==========================================
class Gasto(models.Model):
    CATEGORIAS = [
        ('PROVEEDOR', 'Pago a Proveedor (Hielo, Comida)'),
        ('NOMINA', 'Pago de Nómina (Meseros, Staff)'),
        ('SERVICIOS', 'Servicios (Luz, Agua, Gas)'),
        ('MANTENIMIENTO', 'Mantenimiento Quinta'),
        ('IMPUESTOS', 'Pago de Impuestos'),
        ('OTRO', 'Otros Gastos'),
    ]

    fecha_gasto = models.DateField(blank=True, null=True, help_text="Si subes XML, se llena sola")
    descripcion = models.CharField(max_length=255, blank=True, null=True, help_text="Se llena sola con el XML")
    monto = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Se llena sola con el XML")
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default='PROVEEDOR')
    proveedor = models.CharField(max_length=200, blank=True, help_text="Nombre del Emisor (del XML)")
    archivo_xml = models.FileField(upload_to='xml_gastos/', blank=True, null=True)
    archivo_pdf = models.FileField(upload_to='pdf_gastos/', blank=True, null=True)
    uuid = models.CharField(max_length=36, blank=True, null=True, unique=True)
    evento_relacionado = models.ForeignKey('Cotizacion', on_delete=models.SET_NULL, null=True, blank=True)

    def clean(self):
        if self.archivo_xml:
            try:
                if self.archivo_xml.closed:
                    self.archivo_xml.open() 
                self.archivo_xml.seek(0)
                tree = ET.parse(self.archivo_xml)
                root = tree.getroot()
                ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                if 'http://www.sat.gob.mx/cfd/3' in root.tag: ns = {'cfdi': 'http://www.sat.gob.mx/cfd/3'}

                self.monto = root.attrib.get('Total', 0)
                fecha_str = root.attrib.get('Fecha', '')
                if fecha_str: self.fecha_gasto = fecha_str.split('T')[0] 
                
                emisor = root.find('cfdi:Emisor', ns)
                if emisor is not None: self.proveedor = emisor.attrib.get('Nombre', '')

                conceptos = root.find('cfdi:Conceptos', ns)
                if conceptos is not None:
                    primer_concepto = conceptos.find('cfdi:Concepto', ns)
                    if primer_concepto is not None:
                        desc = primer_concepto.attrib.get('Descripcion', '')
                        self.descripcion = (desc[:250] + '...') if len(desc) > 250 else desc

                complemento = root.find('cfdi:Complemento', ns)
                if complemento is not None:
                    ns_tfd = {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'}
                    timbre = complemento.find('tfd:TimbreFiscalDigital', ns_tfd)
                    if timbre is not None: self.uuid = timbre.attrib.get('UUID', '').upper()
            except Exception as e:
                raise ValidationError(f"Error XML: {e}")
        else:
            if not self.fecha_gasto or not self.monto:
                raise ValidationError("Llena los campos manuales.")

    def save(self, *args, **kwargs):
        self.full_clean() 
        super().save(*args, **kwargs)

    def __str__(self):
        return f"${self.monto} - {self.proveedor}"