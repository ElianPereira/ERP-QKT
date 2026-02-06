import xml.etree.ElementTree as ET
from decimal import Decimal
import math
from django.db import models
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.contrib.auth.models import User

# --- IMPORTACIÓN DE CATÁLOGOS SAT ---
from facturacion.choices import RegimenFiscal, UsoCFDI

# --- IMPORTACIÓN CLOUDINARY PARA ARCHIVOS RAW (XML/PDF) ---
from cloudinary_storage.storage import RawMediaCloudinaryStorage

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

    # --- INTERRUPTOR PARA CREAR SUBPRODUCTO ---
    crear_como_subproducto = models.BooleanField(
        default=False,
        verbose_name="¿Crear también como Subproducto?",
        help_text="Si marcas esto, se creará un Subproducto automático y su receta (1 a 1) al guardar."
    )

    def save(self, *args, **kwargs):
        # 1. Guardar el Insumo primero para tener un ID
        super().save(*args, **kwargs)

        # 2. Lógica para crear Subproducto automático
        if self.crear_como_subproducto:
            # Buscamos o creamos el SubProducto
            sub_prod, created = SubProducto.objects.get_or_create(
                nombre=self.nombre,
                defaults={
                    'descripcion': f"Generado autom. desde insumo: {self.nombre}"
                }
            )
            
            # 3. CRUCIAL: Crear la Receta (RecetaSubProducto)
            RecetaSubProducto.objects.get_or_create(
                subproducto=sub_prod,
                insumo=self,
                defaults={'cantidad': 1}
            )

    def __str__(self):
        return f"{self.nombre} ({self.categoria})"

# ==========================================
# 2. SUBPRODUCTOS
# ==========================================
class SubProducto(models.Model):
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    
    def costo_insumos(self):
        return sum(r.subtotal_costo() for r in self.receta.all())

    def __str__(self):
        return self.nombre

class RecetaSubProducto(models.Model):
    subproducto = models.ForeignKey(SubProducto, related_name='receta', on_delete=models.CASCADE)
    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=4)

    def subtotal_costo(self):
        return self.insumo.costo_unitario * self.cantidad
    
    def __str__(self):
        return f"{self.subproducto.nombre} <- {self.insumo.nombre}"

# ==========================================
# 3. PRODUCTOS
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
    subproducto = models.ForeignKey(SubProducto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)

    def subtotal_costo(self):
        return self.subproducto.costo_insumos() * self.cantidad

# ==========================================
# 4. CLIENTES
# ==========================================
class Cliente(models.Model):
    nombre = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    origen = models.CharField(max_length=50, choices=[
        ('Instagram', 'Instagram'), ('Facebook', 'Facebook'), ('Google', 'Google'), ('Recomendacion', 'Recomendación'), ('Otro', 'Otro')
    ], default='Otro')

    es_cliente_fiscal = models.BooleanField(default=False, verbose_name="¿Datos Fiscales Completos?")
    TIPOS_FISCALES = [
        ('FISICA', 'Persona Física'),
        ('MORAL', 'Persona Moral'),
    ]
    tipo_persona = models.CharField(max_length=10, choices=TIPOS_FISCALES, default='FISICA')
    rfc = models.CharField(max_length=13, blank=True, null=True, verbose_name="RFC")
    razon_social = models.CharField(max_length=200, blank=True, null=True, verbose_name="Razón Social")
    codigo_postal_fiscal = models.CharField(max_length=5, blank=True, null=True, verbose_name="C.P. Fiscal")
    regimen_fiscal = models.CharField(max_length=3, choices=RegimenFiscal.choices, blank=True, null=True, default=RegimenFiscal.SIN_OBLIGACIONES_FISCALES)
    uso_cfdi = models.CharField(max_length=4, choices=UsoCFDI.choices, blank=True, null=True, default=UsoCFDI.GASTOS_EN_GENERAL)
    
    def __str__(self):
        if self.razon_social:
            return f"{self.nombre} ({self.razon_social})"
        return self.nombre

# ==========================================
# 5. COTIZACIONES
# ==========================================
class Cotizacion(models.Model):
    ESTADOS = [
        ('BORRADOR', 'Borrador'),
        ('CONFIRMADA', 'Venta Confirmada'),
        ('CANCELADA', 'Cancelada'),
    ]

    BARRA_CHOICES = [
        ('ninguna', 'Sin Servicio de Alcohol'),
        ('basico', 'Paquete Básico (Botellas 1L - Nacional)'),
        ('premium', 'Paquete Premium (Botellas 1L - Importado)'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    nombre_evento = models.CharField(max_length=200, default="Evento General")
    fecha_evento = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    
    # --- DATOS PARA CÁLCULO DE BARRA ---
    num_personas = models.IntegerField(default=50, verbose_name="Número de Personas")
    tipo_barra = models.CharField(max_length=20, choices=BARRA_CHOICES, default='ninguna', verbose_name="Tipo de Barra Libre")
    horas_servicio = models.IntegerField(default=5, verbose_name="Horas de Servicio Barra")
    # -----------------------------------

    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    requiere_factura = models.BooleanField(default=False)
    
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    iva = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    retencion_isr = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    retencion_iva = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    precio_final = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    estado = models.CharField(max_length=20, choices=ESTADOS, default='BORRADOR')
    created_at = models.DateTimeField(auto_now_add=True)
    archivo_pdf = models.FileField(upload_to='cotizaciones_pdf/', blank=True, null=True, storage=RawMediaCloudinaryStorage())

    # --- MÉTODO PRINCIPAL: CALCULADORA DE BARRA (LÓGICA ECONÓMICA / MAYORISTA) ---
    def calcular_barra_insumos(self):
        """ 
        Calcula insumos basado en BOTELLAS DE 1 LITRO y precios MAYORISTAS.
        Optimizando para mayor rendimiento y menor costo.
        """
        if self.tipo_barra == 'ninguna' or self.num_personas <= 0:
            return None

        # 1. PRECIOS BASE (Estrategia Mayorista/Costco)
        # Básico: Promedio $250 (Bacardí 1L, Smirnoff 1L, Red Label 1L comprando por caja)
        # Premium: Promedio $550 (Etiqueta Negra 1L, Tradicional Cristalino, Absolut)
        PRECIOS = {
            'basico': 250.00,  
            'premium': 550.00, 
            'hielo_5kg': 35.00,
            'mezclador_lt': 20.00, # Bajamos precio considerando Refrescos "Big" (3L)
            'agua_lt': 8.00 # Garrafón o Galón
        }

        # 2. FACTORES DE CONSUMO (Botella de 1 LITRO)
        # 1 Litro = 1000ml = ~22 tragos de 45ml.
        # En 6 horas, consumo promedio = 5 tragos/pax.
        # Rendimiento: 22 tragos / 5 = 4.4 personas por botella.
        # Usamos factor 4.5 para ser eficientes pero seguros.
        
        factor_consumo = 4.5 if self.horas_servicio >= 5 else 5.5
        
        # 3. CÁLCULOS
        precio_botella = PRECIOS['premium'] if self.tipo_barra == 'premium' else PRECIOS['basico']
        
        # Alcohol (Botellas de 1L)
        botellas = math.ceil(self.num_personas / factor_consumo)
        costo_alcohol = botellas * precio_botella

        # Hielo (2kg por persona sigue siendo regla de oro en Mérida)
        kilos_hielo = self.num_personas * 2.0
        bolsas_hielo = math.ceil(kilos_hielo / 5)
        costo_hielo = bolsas_hielo * PRECIOS['hielo_5kg']

        # Mezcladores (Kit más ajustado: 4.5 Litros por botella de alcohol)
        # Al usar botellas de 1L, se requiere más mezclador proporcionalmente.
        litros_mezcladores = math.ceil(botellas * 4.5)
        costo_mezcladores = litros_mezcladores * PRECIOS['mezclador_lt']

        # Agua Natural (Hidratación)
        litros_agua = math.ceil(self.num_personas * 0.5)
        costo_agua = litros_agua * PRECIOS['agua_lt']

        # Totales
        costo_total_insumos = costo_alcohol + costo_hielo + costo_mezcladores + costo_agua
        costo_por_pax = costo_total_insumos / self.num_personas
        
        # Precio de Venta (Margen agresivo del 55-60% ya que bajamos costos)
        precio_venta_sugerido = costo_total_insumos * 2.2 
        precio_venta_pax = precio_venta_sugerido / self.num_personas

        return {
            'botellas': botellas,
            'bolsas_hielo_5kg': bolsas_hielo,
            'litros_mezcladores': litros_mezcladores,
            'litros_agua': litros_agua,
            'costo_total_estimado': round(costo_total_insumos, 2),
            'costo_pax': round(costo_por_pax, 2),
            'precio_venta_sugerido_total': round(precio_venta_sugerido, 2),
            'precio_venta_sugerido_pax': round(precio_venta_pax, 2)
        }
    # -----------------------------------------------------------

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
        if self.subtotal is None: self.subtotal = Decimal('0.00')
        if self.iva is None: self.iva = Decimal('0.00')
        if self.retencion_isr is None: self.retencion_isr = Decimal('0.00')
        if self.retencion_iva is None: self.retencion_iva = Decimal('0.00')
        if self.precio_final is None: self.precio_final = Decimal('0.00')
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
                iva=self.cotizacion.iva,
                retencion_isr=self.cotizacion.retencion_isr,
                retencion_iva=self.cotizacion.retencion_iva,
                precio_final=self.cotizacion.precio_final
            )

    def subtotal(self):
        return self.cantidad * self.precio_unitario

class Pago(models.Model):
    METODOS = [
        ('EFECTIVO', 'Efectivo'), 
        ('TRANSFERENCIA', 'Transferencia Electrónica'),
        ('TARJETA_CREDITO', 'Tarjeta de Crédito'),
        ('TARJETA_DEBITO', 'Tarjeta de Débito'),
        ('CHEQUE', 'Cheque Nominativo'),
        ('DEPOSITO', 'Depósito Bancario'),
        ('PLATAFORMA', 'Plataforma'),
        ('CONDONACION', 'Condonación / Cortesía'),
        ('OTRO', 'Otro Método'),
    ]
    cotizacion = models.ForeignKey(Cotizacion, related_name='pagos', on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    # --- FECHA EDITABLE (Default = Hoy) ---
    fecha_pago = models.DateField(default=now, verbose_name="Fecha de Pago")
    
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS)
    referencia = models.CharField(max_length=100, blank=True)
    
    def __str__(self):
        return f"${self.monto}"

# ==========================================
# 7. COMPRAS Y GASTOS
# ==========================================

class Compra(models.Model):
    """ Representa la Factura General (El contenedor XML/PDF) """
    proveedor = models.CharField(max_length=200, blank=True)
    rfc_emisor = models.CharField(max_length=13, blank=True)
    fecha_emision = models.DateField(blank=True, null=True)
    
    # Totales Globales
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    descuento = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    iva = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    ret_isr = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    ret_iva = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    # Archivos: USAMOS STORAGE RAW PARA QUE CLOUDINARY ACEPTE XML
    archivo_xml = models.FileField(upload_to='xml_compras/', storage=RawMediaCloudinaryStorage())
    archivo_pdf = models.FileField(upload_to='pdf_compras/', blank=True, null=True, storage=RawMediaCloudinaryStorage())
    
    uuid = models.CharField(max_length=36, blank=True, null=True, unique=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # 1. Leer cabecera del XML
        if self.archivo_xml and not self.pk: # Solo al crear
            try:
                if self.archivo_xml.closed:
                    self.archivo_xml.open()
                self.archivo_xml.seek(0)
                tree = ET.parse(self.archivo_xml)
                root = tree.getroot()
                
                ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
                if 'http://www.sat.gob.mx/cfd/3' in root.tag: 
                    ns = {'cfdi': 'http://www.sat.gob.mx/cfd/3'}

                self.total = Decimal(root.attrib.get('Total', 0))
                self.subtotal = Decimal(root.attrib.get('SubTotal', 0))
                self.descuento = Decimal(root.attrib.get('Descuento', 0))
                
                fecha_str = root.attrib.get('Fecha', '')
                if fecha_str: self.fecha_emision = fecha_str.split('T')[0] 
                
                emisor = root.find('cfdi:Emisor', ns)
                if emisor is not None: 
                    self.proveedor = emisor.attrib.get('Nombre', '')
                    self.rfc_emisor = emisor.attrib.get('Rfc', '')

                # UUID
                complemento = root.find('cfdi:Complemento', ns)
                if complemento is not None:
                    ns_tfd = {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'}
                    timbre = complemento.find('tfd:TimbreFiscalDigital', ns_tfd)
                    if timbre is not None: self.uuid = timbre.attrib.get('UUID', '').upper()
                
                # Impuestos Globales (Resumen)
                self.iva = Decimal('0.00')
                impuestos = root.find('cfdi:Impuestos', ns)
                if impuestos is not None:
                    traslados = impuestos.find('cfdi:Traslados', ns)
                    if traslados is not None:
                        for t in traslados.findall('cfdi:Traslado', ns):
                            if t.attrib.get('Impuesto') == '002':
                                self.iva += Decimal(t.attrib.get('Importe', 0))
                    
                    retenciones = impuestos.find('cfdi:Retenciones', ns)
                    if retenciones is not None:
                        for r in retenciones.findall('cfdi:Retencion', ns):
                            imp = r.attrib.get('Impuesto')
                            val = Decimal(r.attrib.get('Importe', 0))
                            if imp == '001': self.ret_isr += val
                            elif imp == '002': self.ret_iva += val

            except Exception as e:
                print(f"Error parseando cabecera: {e}")
            
            # --- FIX: REBOBINAR EL ARCHIVO ANTES DE SUBIR A CLOUDINARY ---
            self.archivo_xml.seek(0)

        super().save(*args, **kwargs)
        
        # 2. Leer Conceptos y crear Gastos (Hijos)
        if self.archivo_xml:
            self._procesar_conceptos_xml()

    def _procesar_conceptos_xml(self):
        if self.gastos.exists(): return 

        try:
            if self.archivo_xml.closed:
                self.archivo_xml.open()
            self.archivo_xml.seek(0)
            tree = ET.parse(self.archivo_xml)
            root = tree.getroot()
            
            ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
            if 'http://www.sat.gob.mx/cfd/3' in root.tag: 
                ns = {'cfdi': 'http://www.sat.gob.mx/cfd/3'}
            
            conceptos = root.find('cfdi:Conceptos', ns)
            if conceptos is not None:
                for c in conceptos.findall('cfdi:Concepto', ns):
                    descripcion = c.attrib.get('Descripcion', '')[:250]
                    cantidad = Decimal(c.attrib.get('Cantidad', 1))
                    valor_unitario = Decimal(c.attrib.get('ValorUnitario', 0))
                    importe = Decimal(c.attrib.get('Importe', 0))
                    clave_sat = c.attrib.get('ClaveProdServ', '')
                    unidad = c.attrib.get('ClaveUnidad', '')
                    
                    # Intentar leer IVA por línea
                    iva_linea = Decimal('0.00')
                    impuestos_c = c.find('cfdi:Impuestos', ns)
                    if impuestos_c:
                        traslados_c = impuestos_c.find('cfdi:Traslados', ns)
                        if traslados_c:
                            for t in traslados_c.findall('cfdi:Traslado', ns):
                                if t.attrib.get('Impuesto') == '002':
                                    try: iva_linea += Decimal(t.attrib.get('Importe', 0))
                                    except: iva_linea = importe * Decimal('0.16')

                    Gasto.objects.create(
                        compra=self,
                        descripcion=descripcion,
                        cantidad=cantidad,
                        precio_unitario=valor_unitario,
                        total_linea=importe + iva_linea,
                        clave_sat=clave_sat,
                        unidad_medida=unidad,
                        fecha_gasto=self.fecha_emision,
                        proveedor=self.proveedor,
                        categoria='SIN_CLASIFICAR'
                    )
        except Exception as e:
            print(f"Error procesando conceptos: {e}")

    def __str__(self):
        return f"{self.proveedor} - ${self.total}"

class Gasto(models.Model):
    # --- CATEGORÍAS ACTUALIZADAS ---
    CATEGORIAS = [
        ('SIN_CLASIFICAR', 'Sin Clasificar'),
        ('SERVICIO_EXTERNO', 'Servicio Externo'),
        ('BEBIDAS_SIN_ALCOHOL', 'Bebidas Sin Alcohol'),
        ('BEBIDAS_CON_ALCOHOL', 'Bebidas Con Alcohol'),
        ('LIMPIEZA', 'Limpieza Y Desechables'),
        ('MOBILIARIO_EQ', 'Mobiliario Y Equipo'),
        ('MANTENIMIENTO', 'Mantenimiento Y Reparaciones'),
        ('NOMINA_EXT', 'Servicios Staff Externo'),
        ('IMPUESTOS', 'Pago De Impuestos'),
        ('PUBLICIDAD', 'Publicidad Y Marketing'),
        ('SERVICIOS_ADMON', 'Servicios Administrativos Y Bancarios'),
        ('OTRO', 'Otros Gastos'),
    ]

    compra = models.ForeignKey(Compra, related_name='gastos', on_delete=models.CASCADE)
    
    descripcion = models.CharField(max_length=255)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_linea = models.DecimalField(max_digits=10, decimal_places=2, default=0) 
    
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default='SIN_CLASIFICAR')
    evento_relacionado = models.ForeignKey('Cotizacion', on_delete=models.SET_NULL, null=True, blank=True)
    
    clave_sat = models.CharField(max_length=20, blank=True)
    unidad_medida = models.CharField(max_length=20, blank=True)
    
    fecha_gasto = models.DateField(blank=True, null=True)
    proveedor = models.CharField(max_length=200, blank=True)
    
    # También actualizamos estos campos en Gasto por seguridad
    archivo_xml = models.FileField(upload_to='xml_gastos/', blank=True, null=True, storage=RawMediaCloudinaryStorage())
    archivo_pdf = models.FileField(upload_to='pdf_gastos/', blank=True, null=True, storage=RawMediaCloudinaryStorage())

    def __str__(self):
        return f"{self.descripcion} (${self.total_linea})"