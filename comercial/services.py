from decimal import Decimal
import math
from django.conf import settings
from .models import ItemCotizacion, ConstanteSistema

class CalculadoraBarraService:
    """
    Servicio encargado de toda la lógica de cálculo de barra, 
    separando la lógica de negocio del modelo de base de datos.
    """

    def __init__(self, cotizacion):
        self.cot = cotizacion

    def _get_costo(self, insumo, clave_constante, default_val):
        if insumo:
            factor = insumo.factor_rendimiento if insumo.factor_rendimiento > 0 else 1
            return insumo.costo_unitario / Decimal(factor)
        
        try:
            const = ConstanteSistema.objects.get(clave=clave_constante)
            return const.valor
        except ConstanteSistema.DoesNotExist:
            return Decimal(default_val)

    def calcular(self):
        """
        Algoritmo V2: Cálculo de Hielo Avanzado + Desglose de Mixers
        """
        c = self.cot
        checks = {
            'refrescos': c.incluye_refrescos,
            'cerveza': c.incluye_cerveza,
            'nacional': c.incluye_licor_nacional,
            'premium': c.incluye_licor_premium,
            'coctel_base': c.incluye_cocteleria_basica,
            'coctel_prem': c.incluye_cocteleria_premium
        }

        if not any(checks.values()) or c.num_personas <= 0:
            return None

        # --- COSTOS ---
        C_HIELO = self._get_costo(c.insumo_hielo, 'PRECIO_HIELO_20KG', '90.00')
        C_MIXER = self._get_costo(c.insumo_refresco, 'PRECIO_REFRESCO_2L', '22.00')
        C_AGUA = self._get_costo(c.insumo_agua, 'PRECIO_AGUA_GAL', '10.00')
        C_ALC_NAC = self._get_costo(c.insumo_alcohol_basico, 'PRECIO_ALC_NAC', '380.00')
        C_ALC_PREM = self._get_costo(c.insumo_alcohol_premium, 'PRECIO_ALC_PREM', '1150.00')
        C_CERVEZA = Decimal('42.00')
        C_GIN = Decimal('550.00')
        C_INSUMO_COCTEL_BASE = Decimal('15.00')
        C_INSUMO_COCTEL_PREM = Decimal('28.00')

        R_BOTELLA = 16.0
        R_CAGUAMA = 3.0

        # --- FACTORES ---
        factor_termico = 1.0
        tragos_ph = 1.3
        if c.clima == 'calor':
            factor_termico = 1.3
            tragos_ph = 1.5
        elif c.clima == 'extremo':
            factor_termico = 1.6
            tragos_ph = 1.8

        TOTAL_TRAGOS = c.num_personas * c.horas_servicio * tragos_ph

        # --- PESOS ---
        pesos = {}
        if checks['cerveza']: pesos['cerveza'] = 55
        if checks['nacional']: pesos['nacional'] = 35
        if checks['premium']: pesos['premium'] = 25
        if checks['coctel_base']: pesos['coctel_base'] = 20
        if checks['coctel_prem']: pesos['coctel_prem'] = 15
        if checks['refrescos']:
            if not pesos: pesos['refrescos'] = 100
            else: pesos['refrescos'] = 15

        total_peso = sum(pesos.values()) or 1
        
        res = {
            'botellas_nacional': 0, 'botellas_premium': 0, 'cervezas_unidades': 0,
            'litros_mezcladores': 0, 'hielo_consumo_kg': 0.0, 'hielo_enfriamiento_kg': 0.0,
            'bolsas_hielo_20kg': 0, 'costo_alcohol': Decimal(0), 'costo_insumos_varios': Decimal(0)
        }
        
        costo_puro = {k: Decimal(0) for k in ['cerveza','nacional','premium','coctel','refrescos']}
        litros_mixer_calc = 0.0
        costo_fruta = Decimal(0) # ACUMULADOR DE FRUTA/GARNITURA

        # --- CÁLCULOS ---
        if 'cerveza' in pesos:
            share = pesos['cerveza'] / total_peso
            tragos = TOTAL_TRAGOS * share
            res['cervezas_unidades'] = math.ceil(tragos / R_CAGUAMA)
            costo = res['cervezas_unidades'] * C_CERVEZA
            res['costo_alcohol'] += costo
            costo_puro['cerveza'] += costo

        if 'nacional' in pesos:
            share = pesos['nacional'] / total_peso
            tragos = TOTAL_TRAGOS * share
            botellas = math.ceil(tragos / R_BOTELLA)
            res['botellas_nacional'] += botellas
            costo = botellas * C_ALC_NAC
            res['costo_alcohol'] += costo
            costo_puro['nacional'] += costo
            litros_mixer_calc += (tragos * 0.200)
            costo_puro['nacional'] += (Decimal(tragos * 0.200) * C_MIXER)

        if 'premium' in pesos:
            share = pesos['premium'] / total_peso
            tragos = TOTAL_TRAGOS * share
            botellas = math.ceil(tragos / R_BOTELLA)
            res['botellas_premium'] += botellas
            costo = botellas * C_ALC_PREM
            res['costo_alcohol'] += costo
            costo_puro['premium'] += costo
            litros_mixer_calc += (tragos * 0.180)
            costo_puro['premium'] += (Decimal(tragos * 0.180) * C_MIXER)

        if 'coctel_base' in pesos:
            share = pesos['coctel_base'] / total_peso
            tragos = TOTAL_TRAGOS * share
            # Insumo fruta (Se separa en variable temporal)
            c_ins = Decimal(tragos) * C_INSUMO_COCTEL_BASE
            costo_fruta += c_ins
            costo_puro['coctel'] += c_ins
            # Alcohol
            b_ron_teq = math.ceil(tragos / R_BOTELLA)
            res['botellas_nacional'] += b_ron_teq
            c_alc = b_ron_teq * C_ALC_NAC
            res['costo_alcohol'] += c_alc
            costo_puro['coctel'] += c_alc
            litros_mixer_calc += (tragos * 0.100)

        if 'coctel_prem' in pesos:
            share = pesos['coctel_prem'] / total_peso
            tragos = TOTAL_TRAGOS * share
            # Insumo fruta/cafe
            c_ins = Decimal(tragos) * C_INSUMO_COCTEL_PREM
            costo_fruta += c_ins
            costo_puro['coctel'] += c_ins
            # Gin
            b_gin = math.ceil((tragos * 0.3) / R_BOTELLA)
            res['botellas_premium'] += b_gin
            c_alc = b_gin * C_GIN
            res['costo_alcohol'] += c_alc
            costo_puro['coctel'] += c_alc

        if 'refrescos' in pesos:
            share = pesos['refrescos'] / total_peso
            tragos = TOTAL_TRAGOS * share
            litros_mixer_calc += (tragos * 0.355)

        res['litros_mezcladores'] = math.ceil(litros_mixer_calc)
        c_mixers_total = res['litros_mezcladores'] * C_MIXER
        
        # --- HIELO ---
        hielo_consumo = TOTAL_TRAGOS * 0.25 
        hielo_enfriamiento = 0.0
        if res['cervezas_unidades'] > 0:
            hielo_enfriamiento += (res['cervezas_unidades'] / 30.0) * 20.0
        
        volumen_a_enfriar = res['litros_mezcladores'] + (c.num_personas * 0.6)
        hielo_enfriamiento += (volumen_a_enfriar / 60.0) * 20.0

        res['hielo_consumo_kg'] = hielo_consumo * factor_termico
        res['hielo_enfriamiento_kg'] = hielo_enfriamiento * factor_termico
        
        total_hielo_kg = res['hielo_consumo_kg'] + res['hielo_enfriamiento_kg']
        res['bolsas_hielo_20kg'] = math.ceil(total_hielo_kg / 20.0)
        
        costo_hielo = res['bolsas_hielo_20kg'] * C_HIELO

        # --- AGUA Y STAFF ---
        litros_agua = math.ceil(c.num_personas * 0.6)
        res['litros_agua'] = litros_agua
        costo_agua = litros_agua * C_AGUA

        # TOTALIZAR COSTOS OPERATIVOS
        res['costo_insumos_varios'] = costo_agua + costo_hielo + c_mixers_total + costo_fruta

        # Staff
        ratio_barman = 40 if (checks['coctel_base'] or checks['coctel_prem']) else 50
        num_barmans = math.ceil(c.num_personas / ratio_barman)
        num_auxiliares = math.ceil(num_barmans / 2)
        if num_barmans > 1 and num_auxiliares == 0: num_auxiliares = 1
        
        C_BARMAN = self._get_costo(c.insumo_barman, 'COSTO_BARMAN', '1200.00')
        C_AUX = self._get_costo(c.insumo_auxiliar, 'COSTO_AUXILIAR', '800.00')
        costo_staff = (num_barmans * C_BARMAN) + (num_auxiliares * C_AUX)

        # --- TOTALES ---
        costo_total = res['costo_alcohol'] + res['costo_insumos_varios'] + costo_staff
        precio_sugerido = costo_total * Decimal(str(c.factor_utilidad_barra))

        # --- DESGLOSE FINAL ---
        costo_comun = costo_staff + costo_hielo + costo_agua
        costo_puro['refrescos'] += c_mixers_total
        
        total_asignable = sum(costo_puro.values()) or 1
        desglose = {}
        margen = Decimal(str(c.factor_utilidad_barra))

        def get_linea(key):
            if costo_puro[key] > 0:
                participacion = costo_puro[key] / total_asignable
                full = costo_puro[key] + (costo_comun * participacion)
                return full * margen
            return Decimal(0)

        desglose['refrescos'] = get_linea('refrescos')
        desglose['cerveza'] = get_linea('cerveza')
        desglose['nacional'] = get_linea('nacional')
        desglose['premium'] = get_linea('premium')
        desglose['coctel'] = get_linea('coctel')

        diff = precio_sugerido - sum(desglose.values())
        if abs(diff) > 0.1:
            k = max(desglose, key=desglose.get)
            desglose[k] += diff

        return {
            'costo_total_estimado': costo_total,
            'precio_venta_sugerido_total': precio_sugerido,
            'botellas': res['botellas_nacional'] + res['botellas_premium'],
            'botellas_nacional': res['botellas_nacional'],
            'botellas_premium': res['botellas_premium'],
            'cervezas_unidades': res['cervezas_unidades'],
            'bolsas_hielo_20kg': res['bolsas_hielo_20kg'],
            'hielo_info': f"{int(res['hielo_consumo_kg'])}kg Consumo + {int(res['hielo_enfriamiento_kg'])}kg Frío",
            'litros_mezcladores': res['litros_mezcladores'],
            'litros_agua': litros_agua,
            'num_barmans': num_barmans,
            'num_auxiliares': num_auxiliares,
            'costo_alcohol': res['costo_alcohol'],
            'costo_insumos_varios': res['costo_insumos_varios'],
            # VARIABLES NUEVAS PARA EL DESGLOSE:
            'costo_hielo': costo_hielo,
            'costo_mixers_agua': c_mixers_total + costo_agua,
            'costo_fruta': costo_fruta,
            # ----------------------------------
            'costo_staff': costo_staff,
            'margen_aplicado': c.factor_utilidad_barra,
            'desglose_venta': desglose
        }

def actualizar_item_cotizacion(cotizacion):
    calc = CalculadoraBarraService(cotizacion)
    datos = calc.calcular()
    desc_clave = "Servicio de Barra"
    item_barra = cotizacion.items.filter(descripcion__startswith=desc_clave).first()

    if datos:
        precio = datos['precio_venta_sugerido_total']
        partes = []
        if cotizacion.incluye_cerveza: partes.append("Cerveza")
        if cotizacion.incluye_licor_nacional: partes.append("Nacional")
        if cotizacion.incluye_licor_premium: partes.append("Premium")
        if cotizacion.incluye_cocteleria_basica: partes.append("Cocteles")
        if cotizacion.incluye_cocteleria_premium: partes.append("Mixología")
        
        info = "/".join(partes) if partes else "Básico"
        clima_tag = "🔥" if cotizacion.clima in ['calor', 'extremo'] else ""
        nueva_desc = f"{desc_clave} [{info}] {clima_tag} | {cotizacion.num_personas} Pax - {cotizacion.horas_servicio} Hrs"

        if item_barra:
            if abs(item_barra.precio_unitario - precio) > Decimal('0.50') or item_barra.descripcion != nueva_desc:
                item_barra.precio_unitario = precio
                item_barra.descripcion = nueva_desc
                item_barra.cantidad = 1
                item_barra.save()
        else:
            ItemCotizacion.objects.create(
                cotizacion=cotizacion, 
                descripcion=nueva_desc, 
                cantidad=1, 
                precio_unitario=precio
            )
    else:
        if item_barra: item_barra.delete()