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
# 4. COTIZACIONES
# ==========================================
class Cotizacion(models.Model):
    ESTADOS = [('BORRADOR', 'Borrador'), ('CONFIRMADA', 'Venta Confirmada'), ('CANCELADA', 'Cancelada')]
    
    CLIMA_CHOICES = [
        ('normal', 'Interior / Aire Acondicionado'),
        ('calor', 'Exterior / Calor Mérida (+20% Liq)'),
        ('extremo', 'Ola de Calor / Mayo (+40% Liq)'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    nombre_evento = models.CharField(max_length=200, default="Evento General")
    fecha_evento = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    
    # --- CONFIGURACIÓN DE BARRA MODULAR ---
    num_personas = models.IntegerField(default=50, verbose_name="Número de Personas")
    
    incluye_refrescos = models.BooleanField(default=True, verbose_name="Refrescos y Mezcladores")
    incluye_cerveza = models.BooleanField(default=False, verbose_name="Cerveza (Caguama)")
    
    incluye_licor_nacional = models.BooleanField(default=False, verbose_name="Licores Nacionales")
    incluye_licor_premium = models.BooleanField(default=False, verbose_name="Licores Premium")
    
    incluye_cocteleria_basica = models.BooleanField(default=False, verbose_name="Coctelería Básica (Mojitos/Marg)")
    incluye_cocteleria_premium = models.BooleanField(default=False, verbose_name="Mixología / Carajillos")
    
    clima = models.CharField(max_length=20, choices=CLIMA_CHOICES, default='calor', verbose_name="Clima / Entorno")
    horas_servicio = models.IntegerField(default=5, verbose_name="Horas Servicio")
    factor_utilidad_barra = models.DecimalField(max_digits=5, decimal_places=2, default=2.20, verbose_name="Factor Utilidad")

    # Selección Manual de Insumos (Precios Base)
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
        ALGORITMO DE DISTRIBUCIÓN PONDERADA (MARKET SHARE)
        CORREGIDO: Incluye costos de alcohol base en cocteles.
        """
        checks = {
            'refrescos': self.incluye_refrescos,
            'cerveza': self.incluye_cerveza,
            'nacional': self.incluye_licor_nacional,
            'premium': self.incluye_licor_premium,
            'coctel_base': self.incluye_cocteleria_basica,
            'coctel_prem': self.incluye_cocteleria_premium
        }
        
        if not any(checks.values()) or self.num_personas <= 0: return None

        # --- CONSTANTES ---
        C_HIELO = self._get_costo_real(self.insumo_hielo, '88.00')
        C_MIXER = self._get_costo_real(self.insumo_refresco, '18.00')
        C_AGUA = self._get_costo_real(self.insumo_agua, '8.00')
        
        # Alcohol
        C_ALC_NAC = self._get_costo_real(self.insumo_alcohol_basico, '380.00')
        C_ALC_PREM = self._get_costo_real(self.insumo_alcohol_premium, '1150.00')
        C_CERVEZA = Decimal('42.00')
        C_GIN_STD = Decimal('550.00') 
        
        # Insumos fruta/jarabes
        C_INSUMO_COCTEL_BASE = Decimal('12.00')
        C_INSUMO_COCTEL_PREM = Decimal('25.00')
        
        R_BOTELLA = 16.0
        R_CAGUAMA = 3.0

        # --- DEMANDA Y PESOS ---
        tragos_por_hora = 1.6 if self.clima == 'calor' else (1.8 if self.clima == 'extremo' else 1.3)
        mult_hielo = Decimal('1.4') if self.clima == 'calor' else (Decimal('1.6') if self.clima == 'extremo' else Decimal('1.0'))
        TOTAL_TRAGOS = self.num_personas * self.horas_servicio * tragos_por_hora

        pesos = {}
        if checks['cerveza']: pesos['cerveza'] = 55
        if checks['nacional']: pesos['nacional'] = 35
        if checks['premium']: pesos['premium'] = 25
        if checks['coctel_base']: pesos['coctel_base'] = 20
        if checks['coctel_prem']: pesos['coctel_prem'] = 15
        
        if checks['refrescos']:
            if not any([checks['cerveza'], checks['nacional'], checks['premium'], checks['coctel_base'], checks['coctel_prem']]):
                pesos['refrescos'] = 100 
            else:
                pesos['refrescos'] = 15 

        total_peso = sum(pesos.values()) or 1

        costo_puro = {
            'cerveza': Decimal(0),
            'nacional': Decimal(0),
            'premium': Decimal(0),
            'coctel': Decimal(0),
            'refrescos': Decimal(0)
        }
        
        res = {
            'botellas_nacional': 0, 'botellas_premium': 0, 'cervezas_unidades': 0,
            'litros_mezcladores': 0, 'bolsas_hielo_20kg': 0,
            'costo_alcohol': Decimal(0), 'costo_insumos_varios': Decimal(0)
        }

        # --- CÁLCULOS ---
        if 'cerveza' in pesos:
            share = pesos['cerveza'] / total_peso
            tragos = TOTAL_TRAGOS * share
            res['cervezas_unidades'] = math.ceil(tragos / R_CAGUAMA)
            c_tmp = res['cervezas_unidades'] * C_CERVEZA
            res['costo_alcohol'] += c_tmp
            costo_puro['cerveza'] += c_tmp

        if 'nacional' in pesos:
            share = pesos['nacional'] / total_peso
            tragos = TOTAL_TRAGOS * share
            res['botellas_nacional'] = math.ceil(tragos / R_BOTELLA)
            c_tmp = res['botellas_nacional'] * C_ALC_NAC
            res['costo_alcohol'] += c_tmp
            costo_puro['nacional'] += c_tmp
            mix_l = tragos * 0.3
            res['litros_mezcladores'] += mix_l
            costo_puro['nacional'] += (Decimal(mix_l) * C_MIXER)

        if 'premium' in pesos:
            share = pesos['premium'] / total_peso
            tragos = TOTAL_TRAGOS * share
            res['botellas_premium'] = math.ceil(tragos / R_BOTELLA)
            c_tmp = res['botellas_premium'] * C_ALC_PREM
            res['costo_alcohol'] += c_tmp
            costo_puro['premium'] += c_tmp
            mix_l = tragos * 0.25
            res['litros_mezcladores'] += mix_l
            costo_puro['premium'] += (Decimal(mix_l) * C_MIXER)

        # --- D) Coctelería Base (CORREGIDO) ---
        if 'coctel_base' in pesos:
            share = pesos['coctel_base'] / total_peso
            tragos = TOTAL_TRAGOS * share
            
            # 1. Insumos Fruta/Jarabe
            c_tmp = Decimal(tragos) * C_INSUMO_COCTEL_BASE
            res['costo_insumos_varios'] += c_tmp
            costo_puro['coctel'] += c_tmp
            res['litros_mezcladores'] += (tragos * 0.1)
            
            # 2. Alcohol Base (Ron y Tequila para Mojitos y Margaritas)
            # Asumimos que son el alcohol del cocktail. Si NO hay barra nacional, debemos cobrarlo.
            # E incluso si la hay, la distribución por pesos asigna estos tragos a esta categoría,
            # así que debemos cobrar el alcohol aquí para que la matemática cuadre.
            
            # 50% Mojitos (Ron), 50% Margaritas (Tequila)
            botellas_ron = math.ceil((tragos * 0.5) / R_BOTELLA)
            botellas_teq = math.ceil((tragos * 0.5) / R_BOTELLA)
            
            costo_bases = (botellas_ron + botellas_teq) * C_ALC_NAC
            res['costo_alcohol'] += costo_bases
            costo_puro['coctel'] += costo_bases
            res['botellas_nacional'] += (botellas_ron + botellas_teq)

        # --- E) Coctelería Premium (CORREGIDO GIN) ---
        if 'coctel_prem' in pesos:
            share = pesos['coctel_prem'] / total_peso
            tragos = TOTAL_TRAGOS * share
            
            # 1. Insumos
            c_tmp = Decimal(tragos) * C_INSUMO_COCTEL_PREM
            res['costo_insumos_varios'] += c_tmp
            costo_puro['coctel'] += c_tmp
            
            # 2. Ginebra para Mixología
            tragos_gin = tragos * 0.30 
            botellas_gin = math.ceil(tragos_gin / R_BOTELLA)
            costo_gin = botellas_gin * C_GIN_STD
            
            res['costo_alcohol'] += costo_gin
            costo_puro['coctel'] += costo_gin
            res['botellas_premium'] += botellas_gin 

        if 'refrescos' in pesos:
            if pesos['refrescos'] == 100:
                litros_soda = self.num_personas * self.horas_servicio * 0.6
                res['litros_mezcladores'] = litros_soda 
            else:
                share = pesos['refrescos'] / total_peso
                tragos_soda = TOTAL_TRAGOS * share
                litros_soda = tragos_soda * 0.355 
                res['litros_mezcladores'] += litros_soda
            
            costo_puro['refrescos'] += (Decimal(litros_soda) * C_MIXER)

        # Operativos Comunes
        litros_agua = math.ceil(self.num_personas * 0.6)
        costo_agua = litros_agua * C_AGUA
        
        factor_hielo = 2.0 if (checks['coctel_base'] or checks['coctel_prem']) else 1.5
        kilos_hielo = (self.num_personas * factor_hielo) * float(mult_hielo)
        if checks['cerveza']: kilos_hielo += (self.num_personas * 0.5)
        res['bolsas_hielo_20kg'] = math.ceil(kilos_hielo / 20.0)
        costo_hielo = res['bolsas_hielo_20kg'] * C_HIELO
        
        res['litros_mezcladores'] = math.ceil(res['litros_mezcladores'])
        costo_mixers_total = res['litros_mezcladores'] * C_MIXER
        
        res['costo_insumos_varios'] += (costo_agua + costo_hielo + costo_mixers_total)
        
        # Staff Optimizado
        ratio_barman = 40 if (checks['coctel_base'] or checks['coctel_prem']) else 50
        num_barmans = math.ceil(self.num_personas / ratio_barman)
        num_auxiliares = math.ceil(num_barmans / 2)
        if num_barmans > 1 and num_auxiliares == 0: num_auxiliares = 1
        
        C_BARMAN = self._get_costo_real(self.insumo_barman, '1200.00')
        C_AUX = self._get_costo_real(self.insumo_auxiliar, '800.00')
        
        costo_staff = (num_barmans * C_BARMAN) + (num_auxiliares * C_AUX)

        # --- TOTALES Y PRORRATEO ---
        costo_total_operativo = res['costo_alcohol'] + res['costo_insumos_varios'] + costo_staff
        precio_venta_total = costo_total_operativo * Decimal(str(self.factor_utilidad_barra))

        costo_comun_operativo = costo_staff + costo_hielo + costo_agua
        
        if checks['refrescos'] and not any([checks['cerveza'], checks['nacional'], checks['premium']]):
             costo_puro['refrescos'] += costo_comun_operativo
             costo_comun_operativo = 0

        total_costo_asignable = sum(costo_puro.values())
        if total_costo_asignable == 0: total_costo_asignable = 1
        
        desglose = {}
        margen = Decimal(str(self.factor_utilidad_barra))

        def calcular_precio_linea(key_puro):
            if costo_puro[key_puro] > 0:
                participacion = costo_puro[key_puro] / total_costo_asignable
                costo_full = costo_puro[key_puro] + (costo_comun_operativo * participacion)
                return costo_full * margen
            return Decimal(0)

        desglose['refrescos'] = calcular_precio_linea('refrescos')
        desglose['cerveza'] = calcular_precio_linea('cerveza')
        desglose['nacional'] = calcular_precio_linea('nacional')
        desglose['premium'] = calcular_precio_linea('premium')
        desglose['coctel'] = calcular_precio_linea('coctel')

        suma_desglose = sum(desglose.values())
        diferencia = precio_venta_total - suma_desglose
        if abs(diferencia) > 0.01:
            cat_max = max(desglose, key=desglose.get)
            desglose[cat_max] += diferencia

        return {
            'costo_total_estimado': costo_total_operativo,
            'precio_venta_sugerido_total': precio_venta_total,
            'botellas': res['botellas_nacional'] + res['botellas_premium'],
            'botellas_nacional': res['botellas_nacional'],
            'botellas_premium': res['botellas_premium'],
            'cervezas_unidades': res['cervezas_unidades'],
            'bolsas_hielo_20kg': res['bolsas_hielo_20kg'],
            'litros_mezcladores': res['litros_mezcladores'],
            'litros_agua': litros_agua,
            'num_barmans': num_barmans,
            'num_auxiliares': num_auxiliares,
            'costo_alcohol': round(res['costo_alcohol'], 2),
            'costo_insumos_varios': round(res['costo_insumos_varios'], 2),
            'costo_staff': round(costo_staff, 2),
            'margen_aplicado': self.factor_utilidad_barra,
            'desglose_venta': desglose
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
            
            partes = []
            if self.incluye_cerveza: partes.append("Cerveza")
            if self.incluye_licor_nacional: partes.append("Nacional")
            if self.incluye_licor_premium: partes.append("Premium")
            if self.incluye_cocteleria_basica: partes.append("Cocteles")
            if self.incluye_cocteleria_premium: partes.append("Mixología")
            
            info_paquete = "/".join(partes) if partes else "Básico"
            tag_clima = "🔥" if self.clima in ['calor', 'extremo'] else ""
            
            nueva_descripcion = f"{desc_clave} [{info_paquete}] {tag_clima} | {self.num_personas} Pax - {self.horas_servicio} Hrs"
            
            if item_barra:
                if abs(item_barra.precio_unitario - precio_sugerido) > Decimal('1.00') or item_barra.descripcion != nueva_descripcion:
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
            self.cotizacion.save()
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