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
    
    # --- CONFIGURACIÓN DE BARRA MODULAR (CHECKBOXES) ---
    num_personas = models.IntegerField(default=50, verbose_name="Número de Personas")
    
    # NUEVOS CAMPOS (SEPARADOS)
    incluye_refrescos = models.BooleanField(default=True, verbose_name="Refrescos y Mezcladores")
    incluye_cerveza = models.BooleanField(default=False, verbose_name="Cerveza")
    
    # ALCOHOL SEPARADO
    incluye_licor_nacional = models.BooleanField(default=False, verbose_name="Licores Nacionales (Básico)")
    incluye_licor_premium = models.BooleanField(default=False, verbose_name="Licores Premium (Importado)")
    
    # COCTELERÍA SEPARADA
    incluye_cocteleria_basica = models.BooleanField(default=False, verbose_name="Coctelería Básica (Mojitos, Margaritas)")
    incluye_cocteleria_premium = models.BooleanField(default=False, verbose_name="Coctelería Premium (Carajillos, Autor)")
    
    clima = models.CharField(max_length=20, choices=CLIMA_CHOICES, default='calor', verbose_name="Clima / Entorno")
    horas_servicio = models.IntegerField(default=5, verbose_name="Horas Servicio")
    factor_utilidad_barra = models.DecimalField(max_digits=5, decimal_places=2, default=2.20, verbose_name="Factor Utilidad")

    # Selección Manual de Insumos (Precios base)
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
        Calcula costos de forma MODULAR.
        """
        checks = [
            self.incluye_refrescos, self.incluye_cerveza, 
            self.incluye_licor_nacional, self.incluye_licor_premium, 
            self.incluye_cocteleria_basica, self.incluye_cocteleria_premium
        ]
        
        # Si no hay nada seleccionado, retornamos None
        if not any(checks) or self.num_personas <= 0:
            return None

        # 1. CONSTANTES & COSTOS UNITARIOS
        costo_hielo_20kg = self._get_costo_real(self.insumo_hielo, '88.00')
        costo_mezclador_lt = self._get_costo_real(self.insumo_refresco, '18.00')
        costo_agua_lt = self._get_costo_real(self.insumo_agua, '8.00')
        
        # COSTOS DIFERENCIADOS PARA ALCOHOL
        costo_alcohol_nacional = self._get_costo_real(self.insumo_alcohol_basico, '250.00') 
        costo_alcohol_premium = self._get_costo_real(self.insumo_alcohol_premium, '550.00') 
        
        costo_barman = self._get_costo_real(self.insumo_barman, '1200.00')
        costo_auxiliar = self._get_costo_real(self.insumo_auxiliar, '800.00')

        COSTO_CERVEZA_UNITARIO = Decimal('16.00')
        COSTO_FRUTA_PP = Decimal('8.00') 
        
        # --- COSTOS DE COCTELERÍA ---
        COSTO_COCTELERIA_BASICA_PP = Decimal('25.00') # Mojitos (Hierbabuena, Limón, Jarabe)
        COSTO_COCTELERIA_PREMIUM_PP = Decimal('65.00') # Carajillos (Licor 43), Aperol, Gins

        # 2. FACTOR CLIMA
        mult_liquido = Decimal('1.0')
        mult_hielo = Decimal('1.0')
        if self.clima == 'calor':
            mult_liquido = Decimal('1.3')
            mult_hielo = Decimal('1.4')
        elif self.clima == 'extremo':
            mult_liquido = Decimal('1.5')
            mult_hielo = Decimal('1.6')

        # 3. ACUMULADORES (INICIALIZAR EN CERO)
        total_alcohol = Decimal('0.00')
        total_insumos_varios = Decimal('0.00')
        
        litros_mezcladores_base = 0.0
        kilos_hielo_base = 0.0
        
        # Variables de Conteo Físico
        num_botellas_nac = 0
        num_botellas_prem = 0
        qty_cervezas = 0
        bolsas_hielo = 0
        litros_mezcladores_total = 0
        litros_agua = 0
        num_staff = 0
        
        # --- LÓGICA MODULAR ---

        # A) Refrescos
        if self.incluye_refrescos:
            consumo_base = 0.6 if self.incluye_cerveza else 1.5
            litros_mezcladores_base += (self.num_personas * consumo_base)
            kilos_hielo_base += (self.num_personas * 1.5)
            total_insumos_varios += (self.num_personas * COSTO_FRUTA_PP)

        # B) Cerveza
        if self.incluye_cerveza:
            qty_cervezas = math.ceil(self.num_personas * 1.2 * self.horas_servicio * float(mult_liquido))
            total_alcohol += (qty_cervezas * COSTO_CERVEZA_UNITARIO)
            kilos_hielo_base += (self.num_personas * 0.5)

        # C) Licores Nacionales
        if self.incluye_licor_nacional:
            factor_botella = 5.0
            if self.clima != 'normal': factor_botella = 4.5
            num_botellas_nac = math.ceil(self.num_personas / factor_botella)
            total_alcohol += (num_botellas_nac * costo_alcohol_nacional)
            
            if not self.incluye_refrescos:
                litros_mezcladores_base += (self.num_personas * 1.0)
                kilos_hielo_base += (self.num_personas * 1.0)

        # D) Licores Premium
        if self.incluye_licor_premium:
            factor_botella = 5.0
            if self.clima != 'normal': factor_botella = 4.5
            num_botellas_prem = math.ceil(self.num_personas / factor_botella)
            total_alcohol += (num_botellas_prem * costo_alcohol_premium)
            
            if not self.incluye_refrescos and not self.incluye_licor_nacional:
                litros_mezcladores_base += (self.num_personas * 1.0)
                kilos_hielo_base += (self.num_personas * 1.0)

        # E) Coctelería BÁSICA
        if self.incluye_cocteleria_basica:
            total_insumos_varios += (self.num_personas * COSTO_COCTELERIA_BASICA_PP)
            kilos_hielo_base += (self.num_personas * 0.8) # Shakers
            litros_mezcladores_base += (self.num_personas * 0.3)

        # F) Coctelería PREMIUM
        if self.incluye_cocteleria_premium:
            total_insumos_varios += (self.num_personas * COSTO_COCTELERIA_PREMIUM_PP)
            kilos_hielo_base += (self.num_personas * 1.0) 
            litros_mezcladores_base += (self.num_personas * 0.2) 

        # 4. OPERATIVOS FINALES
        litros_agua = math.ceil((self.num_personas * 0.5) * float(mult_liquido))
        costo_agua = litros_agua * costo_agua_lt

        kilos_hielo_total = kilos_hielo_base * float(mult_hielo)
        bolsas_hielo = math.ceil(kilos_hielo_total / 20.0)
        costo_hielo = bolsas_hielo * costo_hielo_20kg

        litros_mezcladores_total = math.ceil(litros_mezcladores_base * float(mult_liquido))
        costo_mezcladores = litros_mezcladores_total * costo_mezclador_lt

        total_insumos_varios += (costo_agua + costo_hielo + costo_mezcladores)

        # 5. STAFF
        # Si hay coctelería (cualquiera), ratio 1:40
        ratio = 40 if (self.incluye_cocteleria_basica or self.incluye_cocteleria_premium) else 50
        num_staff = math.ceil(self.num_personas / ratio)
        costo_staff = (num_staff * costo_barman) + (num_staff * costo_auxiliar)

        # 6. TOTALES
        costo_total_operativo = total_alcohol + total_insumos_varios + costo_staff
        precio_venta_total = costo_total_operativo * Decimal(str(self.factor_utilidad_barra))

        return {
            'costo_total_estimado': costo_total_operativo,
            'precio_venta_sugerido_total': precio_venta_total,
            'botellas': num_botellas_nac + num_botellas_prem,
            'botellas_nacional': num_botellas_nac,
            'botellas_premium': num_botellas_prem,
            'cervezas_unidades': qty_cervezas,
            'bolsas_hielo_20kg': bolsas_hielo,
            'litros_mezcladores': litros_mezcladores_total,
            'litros_agua': litros_agua,
            'num_barmans': num_staff,
            'num_auxiliares': num_staff,
            'costo_alcohol': round(total_alcohol, 2),
            'costo_insumos_varios': round(total_insumos_varios, 2),
            'costo_staff': round(costo_staff, 2),
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
            
            checks = sum([
                self.incluye_refrescos, self.incluye_cerveza, 
                self.incluye_licor_nacional, self.incluye_licor_premium,
                self.incluye_cocteleria_basica, self.incluye_cocteleria_premium
            ])
            
            nombre_paquete = "Personalizado"
            if checks == 0: nombre_paquete = "Sin Servicio"
            elif checks == 1: nombre_paquete = "Básico"
            elif checks == 2: nombre_paquete = "Plus"
            elif checks >= 3: nombre_paquete = "Premium / Todo Incluido"

            tag_clima = "🔥" if self.clima in ['calor', 'extremo'] else ""
            nueva_descripcion = f"{desc_clave} {nombre_paquete} {tag_clima} | {self.num_personas} Pax - {self.horas_servicio} Hrs"
            
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