from django.db import models

class RegimenFiscal(models.TextChoices):
    # --- PERSONAS MORALES ---
    GRAL_LEY_PERSONAS_MORALES = '601', '601 - General de Ley Personas Morales'
    PERSONAS_MORALES_CON_FINES_NO_LUCRATIVOS = '603', '603 - Personas Morales con Fines no Lucrativos'
    OPCIONAL_PARA_GRUPOS_DE_SOCIEDADES = '623', '623 - Opcional para Grupos de Sociedades'
    COORDINADOS = '624', '624 - Coordinados'
    RESICO_MORALES = '626', '626 - Régimen Simplificado de Confianza (RESICO)'
    
    # --- PERSONAS FÍSICAS ---
    SUELDOS_Y_SALARIOS = '605', '605 - Sueldos y Salarios e Ingresos Asimilados a Salarios'
    ARRENDAMIENTO = '606', '606 - Arrendamiento'
    REGIMEN_DE_ENAJENACION_O_ADQUISICION_DE_BIENES = '607', '607 - Régimen de Enajenación o Adquisición de Bienes'
    DEMAS_INGRESOS = '608', '608 - Demás ingresos'
    RESIDENTES_EN_EL_EXTRANJERO = '610', '610 - Residentes en el Extranjero sin Establecimiento Permanente en México'
    INGRESOS_POR_DIVIDENDOS = '611', '611 - Ingresos por Dividendos (socios y accionistas)'
    PERSONAS_FISICAS_ACTIVIDADES_EMPRESARIALES = '612', '612 - Personas Físicas con Actividades Empresariales y Profesionales'
    INGRESOS_POR_INTERESES = '614', '614 - Ingresos por intereses'
    OBTENCION_DE_PREMIOS = '615', '615 - Régimen de los ingresos por obtención de premios'
    SIN_OBLIGACIONES_FISCALES = '616', '616 - Sin obligaciones fiscales'
    SOCIEDADES_COOPERATIVAS = '620', '620 - Sociedades Cooperativas de Producción'
    INCORPORACION_FISCAL = '621', '621 - Incorporación Fiscal (RIF)'
    ACTIVIDADES_AGRICOLAS_GANADERAS = '622', '622 - Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras'
    PLATAFORMAS_TECNOLOGICAS = '625', '625 - Plataformas Tecnológicas'

class UsoCFDI(models.TextChoices):
    # --- GASTOS ---
    ADQUISICION_MERCANCIAS = 'G01', 'G01 - Adquisición de mercancías'
    DEVOLUCIONES_DESCUENTOS = 'G02', 'G02 - Devoluciones, descuentos o bonificaciones'
    GASTOS_EN_GENERAL = 'G03', 'G03 - Gastos en general'
    # --- INVERSIONES ---
    CONSTRUCCIONES = 'I01', 'I01 - Construcciones'
    MOBILIARIO_Y_EQUIPO = 'I02', 'I02 - Mobiliario y equipo de oficina'
    EQUIPO_TRANSPORTE = 'I03', 'I03 - Equipo de transporte'
    EQUIPO_COMPUTO = 'I04', 'I04 - Equipo de computo y accesorios'
    # --- DEDUCCIONES PERSONALES ---
    HONORARIOS_MEDICOS = 'D01', 'D01 - Honorarios médicos, dentales y gastos hospitalarios'
    GASTOS_MEDICOS_INCAPACIDAD = 'D02', 'D02 - Gastos médicos por incapacidad'
    GASTOS_FUNERALES = 'D03', 'D03 - Gastos funerales'
    DONATIVOS = 'D04', 'D04 - Donativos'
    INTERESES_REALES = 'D05', 'D05 - Intereses reales hipotecarios'
    APORTACIONES_SAR = 'D06', 'D06 - Aportaciones voluntarias al SAR'
    PRIMAS_SEGUROS = 'D07', 'D07 - Primas por seguros de gastos médicos'
    GASTOS_TRANSPORTACION_ESCOLAR = 'D08', 'D08 - Gastos de transportación escolar obligatoria'
    CUENTAS_AHORRO_PENSIONES = 'D09', 'D09 - Depósitos en cuentas para el ahorro'
    SERVICIOS_EDUCATIVOS = 'D10', 'D10 - Pagos por servicios educativos (Colegiaturas)'
    # --- ESPECIALES ---
    SIN_EFECTOS_FISCALES = 'S01', 'S01 - Sin efectos fiscales'
    PAGOS = 'CP01', 'CP01 - Pagos'
    NOMINA = 'CN01', 'CN01 - Nómina'

class FormaPago(models.TextChoices):
    EFECTIVO = '01', '01 - Efectivo'
    CHEQUE = '02', '02 - Cheque nominativo'
    TRANSFERENCIA = '03', '03 - Transferencia electrónica de fondos'
    TARJETA_CREDITO = '04', '04 - Tarjeta de crédito'
    MONEDERO_ELECTRONICO = '05', '05 - Monedero electrónico'
    VALES_DESPENSA = '08', '08 - Vales de despensa'
    DACION_EN_PAGO = '12', '12 - Dación en pago'
    TARJETA_DEBITO = '28', '28 - Tarjeta de débito'
    POR_DEFINIR = '99', '99 - Por definir'

class MetodoPago(models.TextChoices):
    PUE = 'PUE', 'PUE - Pago en una sola exhibición'
    PPD = 'PPD', 'PPD - Pago en parcialidades o diferido'