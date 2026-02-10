import xml.etree.ElementTree as ET
from decimal import Decimal
import math
from django.db import models
from django.db.models import Sum, Q
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.contrib.auth.models import User

from facturacion.choices import RegimenFiscal, UsoCFDI
from cloudinary_storage.storage import RawMediaCloudinaryStorage

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
    
    factor_rendimiento = models.DecimalField(
        max_digits=10, decimal_places=2, default=1.00,
        verbose_name="Rendimiento (Divisor)",
        help_text="Ej: Caja de 6 refrescos de 3L = 18 (Litros). Caja de 12 botellas = 12 (Piezas). Si es unitario, deja 1."
    )
    
    cantidad_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    categoria = models.CharField(max_length=20, choices=TIPOS, default='CONSUMIBLE')

    crear_como_subproducto = models.BooleanField(
        default=False, verbose_name="¿Crear también como Subproducto?",
        help_text="Si marcas esto, se creará un Subproducto automático."
    )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.crear_como_subproducto:
            sub_prod, _ = SubProducto.objects.get_or_create(
                nombre=self.nombre,
                defaults={'descripcion': f"Generado autom. desde insumo: {self.nombre}"}
            )
            RecetaSubProducto.objects.get_or_create(
                subproducto=sub_prod, insumo=self, defaults={'cantidad': 1}
            )

    def __str__(self):
        return f"{self.nombre} (${self.costo_unitario})"

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
    def calcular_costo(self): return sum(c.subtotal_costo() for c in self.componentes.all())
    def sugerencia_precio(self): return round(self.calcular_costo() * (1 + self.margen_ganancia), 2)
    def __str__(self): return self.nombre

class ComponenteProducto(models.Model):
    producto = models.ForeignKey(Producto, related_name='componentes', on_delete=models.CASCADE)
    subproducto = models.ForeignKey(SubProducto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    def subtotal_costo(self): return self.subproducto.costo_insumos() * self.cantidad

# ==========================================
# 4. CLIENTES
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
# 5. COTIZACIONES
# ==========================================
class Cotizacion(models.Model):
    ESTADOS = [('BORRADOR', 'Borrador'), ('CONFIRMADA', 'Venta Confirmada'), ('CANCELADA', 'Cancelada')]
    
    BARRA_CHOICES = [
        ('ninguna', 'Sin Servicio de Alcohol'),
        ('sin_alcohol', 'Barra Refrescos (Solo Mezcladores)'), 
        ('cocteleria_insumos', 'Barra Coctelería (Solo Insumos, Fruta y Jarabes)'),
        ('cerveza_refrescos', 'Barra Cerveza y Refrescos'),
        ('basico', 'Paquete Básico (Botellas 1L - Nacional)'),
        ('premium', 'Paquete Premium (Botellas 1L - Importado)'),
        # NUEVA OPCIÓN B:
        ('premium_cocteleria', '💎 Todo Incluido (Premium + Coctelería + Staff)'),
    ]

    # --- NUEVO: FACTOR CLIMA MÉRIDA ---
    CLIMA_CHOICES = [
        ('normal', 'Interior / Aire Acondicionado (Consumo Normal)'),
        ('calor', 'Exterior / Calor Mérida (Consumo Alto +30%)'),
        ('extremo', 'Ola de Calor / Mayo (Consumo Extremo +50%)'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    nombre_evento = models.CharField(max_length=200, default="Evento General")
    fecha_evento = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    
    # --- CONFIGURACIÓN DE BARRA ---
    num_personas = models.IntegerField(default=50, verbose_name="Número de Personas")
    tipo_barra = models.CharField(max_length=20, choices=BARRA_CHOICES, default='ninguna', verbose_name="Tipo Barra")
    clima = models.CharField(max_length=20, choices=CLIMA_CHOICES, default='calor', verbose_name="Clima / Entorno")
    
    horas_servicio = models.IntegerField(default=5, verbose_name="Horas Servicio")
    factor_utilidad_barra = models.DecimalField(max_digits=5, decimal_places=2, default=2.20, verbose_name="Factor Utilidad")

    # Selección Manual de Insumos
    insumo_hielo = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name="Insumo Hielo (20kg)")
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
    created_at = models.DateTimeField(auto_now_add=True)
    archivo_pdf = models.FileField(upload_to='cotizaciones_pdf/', blank=True, null=True, storage=RawMediaCloudinaryStorage())

    def _get_costo_real(self, insumo, precio_default):
        if not insumo: return Decimal(precio_default)
        factor = insumo.factor_rendimiento if insumo.factor_rendimiento > 0 else Decimal(1)
        return insumo.costo_unitario / factor

    def calcular_barra_insumos(self):
        """
        Calcula costos considerando Tipo de Barra y FACTOR CLIMA MÉRIDA.
        """
        if self.tipo_barra == 'ninguna' or self.num_personas <= 0:
            return None

        # 1. CONSTANTES BASE
        costo_hielo_20kg = self._get_costo_real(self.insumo_hielo, '88.00')
        costo_mezclador_lt = self._get_costo_real(self.insumo_refresco, '18.00')
        costo_agua_lt = self._get_costo_real(self.insumo_agua, '8.00')
        costo_barman = self._get_costo_real(self.insumo_barman, '1200.00')
        costo_auxiliar = self._get_costo_real(self.insumo_auxiliar, '800.00')
        costo_base = self._get_costo_real(self.insumo_alcohol_basico, '250.00')
        costo_premium = self._get_costo_real(self.insumo_alcohol_premium, '550.00')

        # Costos Extras
        COSTO_CERVEZA_UNITARIO = Decimal('16.00')
        COSTO_FRUTA_PP = Decimal('8.00')
        COSTO_COCTELERIA_PP = Decimal('25.00')

        # --- APLICAR FACTOR CLIMA (MÉRIDA) ---
        # Factor Líquido: Cuánto más beben.
        # Factor Hielo: Cuánto más se usa y se derrite.
        factor_clima_liquido = Decimal('1.0')
        factor_clima_hielo = Decimal('1.0')

        if self.clima == 'calor':
            factor_clima_liquido = Decimal('1.3') # +30% Bebida
            factor_clima_hielo = Decimal('1.4')   # +40% Hielo
        elif self.clima == 'extremo':
            factor_clima_liquido = Decimal('1.5') # +50% Bebida
            factor_clima_hielo = Decimal('1.6')   # +60% Hielo

        # Inicialización
        botellas = 0
        cervezas_totales = 0
        costo_alcohol_total = Decimal('0.00')
        costo_fruta_jarabes = Decimal('0.00')
        factor_hielo_base = 2.0 
        litros_mezcladores_base = 0

        # 2. LÓGICA SEGÚN TIPO DE BARRA
        
        # A) Paquetes Tradicionales (Básico / Premium)
        if self.tipo_barra in ['basico', 'premium']:
            factor_consumo = 4.5 if self.horas_servicio >= 5 else 5.5
            precio_botella = costo_premium if self.tipo_barra == 'premium' else costo_base
            
            # Con calor, la gente "fondoa" más rápido el trago
            if self.clima in ['calor', 'extremo']: factor_consumo -= 0.5 
            
            botellas = math.ceil(self.num_personas / factor_consumo)
            costo_alcohol_total = botellas * precio_botella
            litros_mezcladores_base = math.ceil(botellas * 4.5)
            costo_fruta_jarabes = self.num_personas * COSTO_FRUTA_PP

        # B) Coctelería Solo Insumos
        elif self.tipo_barra == 'cocteleria_insumos':
            litros_mezcladores_base = math.ceil(self.num_personas * 1.8)
            factor_hielo_base = 2.6
            costo_fruta_jarabes = self.num_personas * (COSTO_FRUTA_PP + COSTO_COCTELERIA_PP)
        
        # C) Cerveza y Refrescos
        elif self.tipo_barra == 'cerveza_refrescos':
            # Calor = Más Cerveza
            factor_cheve = Decimal(1.2) * factor_clima_liquido 
            consumo_cerveza = factor_cheve * self.horas_servicio * self.num_personas
            cervezas_totales = math.ceil(consumo_cerveza)
            costo_alcohol_total = cervezas_totales * COSTO_CERVEZA_UNITARIO
            
            litros_mezcladores_base = math.ceil(self.num_personas * 0.6) 
            costo_fruta_jarabes = self.num_personas * (COSTO_FRUTA_PP * Decimal(0.5))

        # D) NUEVO: TODO INCLUIDO (PREMIUM + COCTELERÍA)
        elif self.tipo_barra == 'premium_cocteleria':
            # 1. Alcohol: Cálculo de botellas Premium
            factor_consumo = 4.0 # Beben más porque está rico el coctel
            botellas = math.ceil(self.num_personas / factor_consumo)
            costo_alcohol_total = botellas * costo_premium

            # 2. Insumos Coctelería: Se cobra el full de fruta y jarabes
            costo_fruta_jarabes = self.num_personas * (COSTO_FRUTA_PP + COSTO_COCTELERIA_PP)

            # 3. Líquidos: Mucho jugo y agua mineral
            litros_mezcladores_base = math.ceil(self.num_personas * 2.2)
            
            # 4. Hielo: Máximo consumo
            factor_hielo_base = 2.8

        # E) Solo Refrescos
        else:
            litros_mezcladores_base = math.ceil(self.num_personas * 1.5)
            costo_fruta_jarabes = self.num_personas * COSTO_FRUTA_PP

        # 3. APLICAR FACTOR CLIMA A OPERATIVOS
        
        # Hielo Final (Base * Clima)
        kilos_hielo = (self.num_personas * factor_hielo_base) * float(factor_clima_hielo)
        bolsas_hielo_20kg = math.ceil(kilos_hielo / 20.0)
        costo_hielo_total = bolsas_hielo_20kg * costo_hielo_20kg
        
        # Mezcladores Finales (Base * Clima)
        litros_mezcladores = math.ceil(litros_mezcladores_base * float(factor_clima_liquido))
        costo_mezcladores_total = litros_mezcladores * costo_mezclador_lt
        
        # Agua (Base * Clima)
        litros_agua = math.ceil((self.num_personas * 0.5) * float(factor_clima_liquido))
        costo_agua_total = litros_agua * costo_agua_lt
        
        subtotal_insumos_varios = costo_hielo_total + costo_mezcladores_total + costo_agua_total + costo_fruta_jarabes

        # Staff
        # Si es Coctelería o Todo Incluido, se necesita más staff (1 cada 40 pax)
        ratio_staff = 40 if 'cocteleria' in self.tipo_barra else 50
        num_staff = math.ceil(self.num_personas / ratio_staff) 
        costo_staff_total = (num_staff * costo_barman) + (num_staff * costo_auxiliar)

        # 4. RESULTADOS
        costo_total_operativo = costo_alcohol_total + subtotal_insumos_varios + costo_staff_total
        factor_margen = Decimal(str(self.factor_utilidad_barra))
        precio_venta_total = costo_total_operativo * factor_margen

        return {
            'botellas': botellas,
            'cervezas_unidades': cervezas_totales,
            'bolsas_hielo_20kg': bolsas_hielo_20kg,
            'litros_mezcladores': litros_mezcladores,
            'litros_agua': litros_agua,
            'num_barmans': num_staff,
            'num_auxiliares': num_staff,
            'costo_alcohol': round(costo_alcohol_total, 2),
            'costo_insumos_varios': round(subtotal_insumos_varios, 2),
            'costo_staff': round(costo_staff_total, 2),
            'costo_total_estimado': round(costo_total_operativo, 2),
            'costo_pax': round(costo_total_operativo / self.num_personas, 2),
            'precio_venta_sugerido_total': round(precio_venta_total, 2),
            'precio_venta_sugerido_pax': round(precio_venta_total / self.num_personas, 2),
            'margen_aplicado': self.factor_utilidad_barra
        }

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
        datos_barra = self.calcular_barra_insumos()
        desc_clave = "Servicio de Barra"
        item_barra = self.items.filter(descripcion__startswith=desc_clave).first()

        if datos_barra:
            precio_sugerido = Decimal(datos_barra['precio_venta_sugerido_total'])
            nombres = {
                'basico': 'Libre Nacional', 
                'premium': 'Libre Premium', 
                'sin_alcohol': 'Refrescos Ilimitados',
                'cocteleria_insumos': 'Coctelería (Solo Insumos)',
                'cerveza_refrescos': 'Cerveza y Refrescos',
                'premium_cocteleria': 'Todo Incluido (Premium + Mixología)' # Nombre comercial
            }
            nombre_tipo = nombres.get(self.tipo_barra, 'General')
            # Agregamos indicador de Clima en la descripción si es relevante
            tag_clima = "🔥" if self.clima in ['calor', 'extremo'] else ""
            nueva_descripcion = f"{desc_clave} {nombre_tipo} {tag_clima} | {self.num_personas} Pax - {self.horas_servicio} Hrs"
            
            if item_barra:
                if abs(item_barra.precio_unitario - precio_sugerido) > Decimal('0.01') or item_barra.descripcion != nueva_descripcion:
                    item_barra.precio_unitario = precio_sugerido
                    item_barra.descripcion = nueva_descripcion
                    item_barra.cantidad = 1
                    item_barra.save()
            else:
                ItemCotizacion.objects.create(cotizacion=self, descripcion=nueva_descripcion, cantidad=1, precio_unitario=precio_sugerido)
        else:
            if item_barra: item_barra.delete()

        self.calcular_totales()
        Cotizacion.objects.filter(pk=self.pk).update(
            subtotal=self.subtotal, iva=self.iva, retencion_isr=self.retencion_isr,
            retencion_iva=self.retencion_iva, precio_final=self.precio_final
        )

    def total_pagado(self): return self.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
    def saldo_pendiente(self): return self.precio_final - self.total_pagado()
    def __str__(self): return f"{self.cliente} - {self.nombre_evento}"
    class Meta: verbose_name = "Cotización"; verbose_name_plural = "Cotizaciones"

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
                subtotal=self.cotizacion.subtotal, iva=self.cotizacion.iva,
                retencion_isr=self.cotizacion.retencion_isr, retencion_iva=self.cotizacion.retencion_iva,
                precio_final=self.cotizacion.precio_final
            )
    def subtotal(self): return self.cantidad * self.precio_unitario

class Pago(models.Model):
    METODOS = [('EFECTIVO', 'Efectivo'), ('TRANSFERENCIA', 'Transferencia Electrónica'), ('TARJETA_CREDITO', 'Tarjeta de Crédito'), ('TARJETA_DEBITO', 'Tarjeta de Débito'), ('CHEQUE', 'Cheque Nominativo'), ('DEPOSITO', 'Depósito Bancario'), ('PLATAFORMA', 'Plataforma'), ('CONDONACION', 'Condonación / Cortesía'), ('OTRO', 'Otro Método')]
    cotizacion = models.ForeignKey(Cotizacion, related_name='pagos', on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_pago = models.DateField(default=now, verbose_name="Fecha de Pago")
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS)
    referencia = models.CharField(max_length=100, blank=True)
    def __str__(self): return f"${self.monto}"

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
                    if timbre is not None: self.uuid = timbre.attrib.get('UUID', '').upper()
                self.iva = Decimal('0.00')
                impuestos = root.find('cfdi:Impuestos', ns)
                if impuestos is not None:
                    traslados = impuestos.find('cfdi:Traslados', ns)
                    if traslados is not None:
                        for t in traslados.findall('cfdi:Traslado', ns):
                            if t.attrib.get('Impuesto') == '002': self.iva += Decimal(t.attrib.get('Importe', 0))
                    retenciones = impuestos.find('cfdi:Retenciones', ns)
                    if retenciones is not None:
                        for r in retenciones.findall('cfdi:Retencion', ns):
                            imp = r.attrib.get('Impuesto')
                            val = Decimal(r.attrib.get('Importe', 0))
                            if imp == '001': self.ret_isr += val
                            elif imp == '002': self.ret_iva += val
            except Exception as e: print(f"Error parseando cabecera: {e}")
            self.archivo_xml.seek(0)
        super().save(*args, **kwargs)
        if self.archivo_xml: self._procesar_conceptos_xml()
    def _procesar_conceptos_xml(self):
        if self.gastos.exists(): return 
        try:
            if self.archivo_xml.closed: self.archivo_xml.open()
            self.archivo_xml.seek(0)
            tree = ET.parse(self.archivo_xml)
            root = tree.getroot()
            ns = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
            if 'http://www.sat.gob.mx/cfd/3' in root.tag: ns = {'cfdi': 'http://www.sat.gob.mx/cfd/3'}
            conceptos = root.find('cfdi:Conceptos', ns)
            if conceptos is not None:
                for c in conceptos.findall('cfdi:Concepto', ns):
                    descripcion = c.attrib.get('Descripcion', '')[:250]
                    cantidad = Decimal(c.attrib.get('Cantidad', 1))
                    valor_unitario = Decimal(c.attrib.get('ValorUnitario', 0))
                    importe = Decimal(c.attrib.get('Importe', 0))
                    clave_sat = c.attrib.get('ClaveProdServ', '')
                    unidad = c.attrib.get('ClaveUnidad', '')
                    iva_linea = Decimal('0.00')
                    impuestos_c = c.find('cfdi:Impuestos', ns)
                    if impuestos_c:
                        traslados_c = impuestos_c.find('cfdi:Traslados', ns)
                        if traslados_c:
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
    fecha_gasto = models.DateField(blank=True, null=True)
    proveedor = models.CharField(max_length=200, blank=True)
    archivo_xml = models.FileField(upload_to='xml_gastos/', blank=True, null=True, storage=RawMediaCloudinaryStorage())
    archivo_pdf = models.FileField(upload_to='pdf_gastos/', blank=True, null=True, storage=RawMediaCloudinaryStorage())
    def __str__(self): return f"{self.descripcion} (${self.total_linea})"