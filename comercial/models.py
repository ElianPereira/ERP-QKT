import xml.etree.ElementTree as ET
from decimal import Decimal
from django.db import models
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.contrib.auth.models import User

# --- IMPORTACIÓN DE CATÁLOGOS SAT ---
# Asegúrate de que esta importación exista en tu proyecto o coméntala si no la usas aún
from facturacion.choices import RegimenFiscal, UsoCFDI

# ==========================================
# 1. INSUMOS (NIVEL 1: Materia Prima)
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
# 2. SUBPRODUCTOS (NIVEL 2: Recetas/Platillos)
# ==========================================
class SubProducto(models.Model):
    """ Ej: Una Margarita, Un Platillo de Pollo, Un Centro de Mesa """
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    
    def costo_insumos(self):
        # Calcula el costo sumando sus insumos
        total = sum(r.subtotal_costo() for r in self.receta.all())
        return total

    def __str__(self):
        return self.nombre

class RecetaSubProducto(models.Model):
    """ Qué insumos lleva este SubProducto """
    subproducto = models.ForeignKey(SubProducto, related_name='receta', on_delete=models.CASCADE)
    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=4, help_text="Cantidad de insumo por unidad de SubProducto")

    def subtotal_costo(self):
        return self.insumo.costo_unitario * self.cantidad
    
    def __str__(self):
        return f"{self.subproducto.nombre} <- {self.insumo.nombre}"

# ==========================================
# 3. PRODUCTOS (NIVEL 3: Paquetes de Venta)
# ==========================================
class Producto(models.Model):
    """ Ej: Barra Libre Premium, Banquete de Bodas (Contiene SubProductos) """
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    margen_ganancia = models.DecimalField(max_digits=4, decimal_places=2, default=0.30)
    
    def calcular_costo(self):
        # Suma el costo de los SubProductos que lo componen
        return sum(c.subtotal_costo() for c in self.componentes.all())

    def sugerencia_precio(self):
        costo = self.calcular_costo()
        return round(costo * (1 + self.margen_ganancia), 2)

    def __str__(self):
        return self.nombre

class ComponenteProducto(models.Model):
    """ Qué SubProductos lleva este Producto Final """
    producto = models.ForeignKey(Producto, related_name='componentes', on_delete=models.CASCADE)
    subproducto = models.ForeignKey(SubProducto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, help_text="Ej: 50 Margaritas en este paquete")

    def subtotal_costo(self):
        return self.subproducto.costo_insumos() * self.cantidad

# ==========================================
# 4. CLIENTES
# ==========================================
class Cliente(models.Model):
    # --- Datos de Contacto ---
    nombre = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    origen = models.CharField(max_length=50, choices=[
        ('Instagram', 'Instagram'), ('Facebook', 'Facebook'), ('Google', 'Google'), ('Recomendacion', 'Recomendación'), ('Otro', 'Otro')
    ], default='Otro')

    # --- Datos Fiscales ---
    es_cliente_fiscal = models.BooleanField(default=False, verbose_name="¿Datos Fiscales Completos?")
    
    TIPOS_FISCALES = [
        ('FISICA', 'Persona Física (Juan Pérez)'),
        ('MORAL', 'Persona Moral (Empresa S.A. de C.V.)'),
    ]
    tipo_persona = models.CharField(max_length=10, choices=TIPOS_FISCALES, default='FISICA')
    
    rfc = models.CharField(max_length=13, blank=True, null=True, verbose_name="RFC")
    razon_social = models.CharField(max_length=200, blank=True, null=True, verbose_name="Razón Social")
    codigo_postal_fiscal = models.CharField(max_length=5, blank=True, null=True, verbose_name="C.P. Fiscal")
    
    regimen_fiscal = models.CharField(
        max_length=3, 
        choices=RegimenFiscal.choices, 
        blank=True, null=True,
        default=RegimenFiscal.SIN_OBLIGACIONES_FISCALES
    )

    uso_cfdi = models.CharField(
        max_length=4, 
        choices=UsoCFDI.choices, 
        blank=True, null=True,
        default=UsoCFDI.GASTOS_EN_GENERAL
    )
    
    def __str__(self):
        tipo = "(Física)" if self.tipo_persona == 'FISICA' else "(Moral)"
        nombre_mostrar = self.razon_social if self.razon_social else self.nombre
        return f"{nombre_mostrar} {tipo}"

# ==========================================
# 5. COTIZACIONES
# ==========================================
class Cotizacion(models.Model):
    ESTADOS = [
        ('BORRADOR', 'Borrador'),
        ('CONFIRMADA', 'Venta Confirmada'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    nombre_evento = models.CharField(max_length=200, help_text="Ej: Boda de Laura y Luis", default="Evento General")
    
    # --- FECHAS Y HORARIOS ---
    fecha_evento = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True, verbose_name="Hora Inicio")
    hora_fin = models.TimeField(null=True, blank=True, verbose_name="Hora Fin")

    # --- DATOS ADMINISTRATIVOS ---
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Elaborado por")

    # --- CAMPOS FISCALES ---
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Descuento ($)")
    requiere_factura = models.BooleanField(default=False, help_text="Si se marca, calcula IVA y Retenciones")
    
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Suma de todos los ítems")
    
    # Desglose 
    iva = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)
    retencion_isr = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)
    retencion_iva = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, editable=False)
    
    precio_final = models.DecimalField(max_digits=10, decimal_places=2, editable=False, help_text="Total a Pagar (Neto)")
    
    estado = models.CharField(max_length=20, choices=ESTADOS, default='BORRADOR')
    created_at = models.DateTimeField(auto_now_add=True)
    
    archivo_pdf = models.FileField(upload_to='cotizaciones_pdf/', blank=True, null=True)

    def calcular_totales(self):
        """ Método auxiliar para recalcular todo desde los items """
        # --- FIX CRÍTICO: Si no tiene ID (es nueva), no puede tener items aún ---
        if not self.pk:
            return 
            
        suma_items = sum(item.subtotal() for item in self.items.all())
        self.subtotal = suma_items
        
        base = Decimal(self.subtotal) - Decimal(self.descuento)
        if base < 0: base = Decimal('0.00')
        
        if self.requiere_factura:
            self.iva = base * Decimal('0.16')
            if self.cliente.tipo_persona == 'MORAL':
                self.retencion_isr = base * Decimal('0.0125')
                self.retencion_iva = Decimal('0.00') # Usualmente es 0 o 10.6667% dependiendo el servicio, ajustado a 0 por defecto
            else:
                self.retencion_isr = Decimal('0.00')
                self.retencion_iva = Decimal('0.00')
        else:
            self.iva = Decimal('0.00')
            self.retencion_isr = Decimal('0.00')
            self.retencion_iva = Decimal('0.00')
            
        self.precio_final = base + self.iva - self.retencion_isr - self.retencion_iva

    def save(self, *args, **kwargs):
        # Guardamos normal. La lógica pesada la maneja el Admin o las Signals
        super().save(*args, **kwargs)

    def total_pagado(self):
        resultado = self.pagos.aggregate(Sum('monto'))['monto__sum']
        return resultado if resultado else 0

    def saldo_pendiente(self):
        return self.precio_final - self.total_pagado()

    def __str__(self):
        return f"{self.cliente} - {self.nombre_evento}"

    class Meta:
        verbose_name = "Cotización"
        verbose_name_plural = "Cotizaciones"

# ==========================================
# 5.1 ÍTEMS DE LA COTIZACIÓN (Múltiples Productos)
# ==========================================
class ItemCotizacion(models.Model):
    cotizacion = models.ForeignKey(Cotizacion, related_name='items', on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Producto (Paquete)")
    insumo = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Insumo Individual (Extra)")
    
    descripcion = models.CharField(max_length=255, blank=True, help_text="Opcional: Detalle específico")
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=1.00)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def save(self, *args, **kwargs):
        # --- FIX URGENTE PARA ERROR DE INTEGRIDAD ---
        # Si el precio_final está vacío (None), le asignamos 0.00 por defecto
        # para que la base de datos permita guardarlo.
        if self.precio_final is None:
            self.precio_final = Decimal('0.00')

        # Guardamos normal
        super().save(*args, **kwargs)
        
        # Intentamos recalcular el padre, pero protegemos contra recursión infinita
        if self.cotizacion.pk:
            self.cotizacion.calcular_totales()
            Cotizacion.objects.filter(pk=self.cotizacion.pk).update(
                subtotal=self.cotizacion.subtotal,
                iva=self.cotizacion.iva,
                retencion_isr=self.cotizacion.retencion_isr,
                retencion_iva=self.cotizacion.retencion_iva,
                precio_final=self.cotizacion.precio_final
            )

    def subtotal(self):
        return self.cantidad * self.precio_unitario

    def __str__(self):
        nombre = self.producto.nombre if self.producto else (self.insumo.nombre if self.insumo else "Ítem")
        return f"{self.cantidad} x {nombre}"

# ==========================================
# 6. PAGOS
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
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Recibido por")
    fecha_pago = models.DateField(auto_now_add=True)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS)
    referencia = models.CharField(max_length=100, blank=True)
    
    def __str__(self):
        return f"${self.monto}"

# ==========================================
# 7. GASTOS
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

    fecha_gasto = models.DateField(blank=True, null=True)
    descripcion = models.CharField(max_length=255, blank=True, null=True)
    monto = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default='PROVEEDOR')
    proveedor = models.CharField(max_length=200, blank=True)
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