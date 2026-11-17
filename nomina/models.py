import logging
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction

from core_erp.storages_qkt import storage_privado

logger = logging.getLogger(__name__)


class Empleado(models.Model):
    # PUESTOS DISPONIBLES
    PUESTOS = [
        ('MESERO', 'Mesero'),
        ('COCINA', 'Cocina/Chef'),
        ('STAFF', 'Staff General'),
        ('CHOFER', 'Chofer'),
        ('SEGURIDAD', 'Seguridad'),
        ('VENTAS', 'Ventas'),
        ('OTRO', 'Otro'),
    ]
    TIPOS_IDENTIFICACION = [
        ('INE', 'INE / IFE'),
        ('PASAPORTE', 'Pasaporte'),
        ('LICENCIA', 'Licencia de conducir'),
        ('OTRA', 'Otra'),
    ]
    FORMAS_PAGO = [('EFECTIVO', 'Efectivo'), ('TRANSFERENCIA', 'Transferencia')]

    nombre = models.CharField(max_length=200, help_text="Debe coincidir con el nombre en el Excel si usas carga masiva")
    puesto = models.CharField(max_length=20, choices=PUESTOS, default='MESERO')
    telefono = models.CharField(max_length=20, blank=True, verbose_name='Teléfono')
    tarifa_base = models.DecimalField(max_digits=10, decimal_places=2, default=50.00)
    activo = models.BooleanField(default=True)

    # --- Identificación (expediente; solo con `nomina.ver_expediente_empleado`) ---
    nombre_legal = models.CharField(max_length=200, blank=True, verbose_name='Nombre legal completo',
                                    help_text='Como aparece en su identificación oficial.')
    fecha_nacimiento = models.DateField(null=True, blank=True, verbose_name='Fecha de nacimiento')
    curp = models.CharField(max_length=30, blank=True, verbose_name='CURP')
    rfc = models.CharField(max_length=30, blank=True, verbose_name='RFC')
    tipo_identificacion = models.CharField(max_length=10, choices=TIPOS_IDENTIFICACION, blank=True,
                                           verbose_name='Tipo de identificación')
    numero_identificacion = models.CharField(max_length=30, blank=True, verbose_name='Número de identificación')
    identificacion_vigencia = models.DateField(null=True, blank=True, verbose_name='Vigencia de la identificación')

    # --- Contacto y domicilio ---
    email = models.EmailField(blank=True, verbose_name='Correo')
    calle_numero = models.CharField(max_length=200, blank=True, verbose_name='Calle y número')
    colonia = models.CharField(max_length=120, blank=True, verbose_name='Colonia')
    codigo_postal = models.CharField(max_length=30, blank=True, verbose_name='Código postal')
    municipio = models.CharField(max_length=120, blank=True, verbose_name='Municipio')
    estado = models.CharField(max_length=120, blank=True, verbose_name='Estado')

    # --- Contacto de emergencia ---
    emergencia_nombre = models.CharField(max_length=200, blank=True, verbose_name='Contacto de emergencia')
    emergencia_parentesco = models.CharField(max_length=60, blank=True, verbose_name='Parentesco')
    emergencia_telefono = models.CharField(max_length=20, blank=True, verbose_name='Teléfono de emergencia')

    # --- Relación con la Quinta ---
    usuario = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='empleado', verbose_name='Usuario del ERP',
                                   help_text='Un empleado de Ventas solo puede tener usuario con el acuerdo '
                                             'de confidencialidad firmado en su expediente.')
    fecha_alta = models.DateField(null=True, blank=True, verbose_name='Fecha de alta')
    fecha_baja = models.DateField(null=True, blank=True, verbose_name='Fecha de baja')
    motivo_baja = models.CharField(max_length=200, blank=True, verbose_name='Motivo de la baja')

    # --- Seguridad social (sin datos de salud) ---
    nss = models.CharField(max_length=30, blank=True, verbose_name='NSS')
    afiliado_imss = models.BooleanField(default=False, verbose_name='Afiliado al IMSS')
    fecha_alta_imss = models.DateField(null=True, blank=True, verbose_name='Fecha de alta en el IMSS')
    notas_seguridad_social = models.TextField(blank=True, verbose_name='Notas de seguridad social')

    # --- Datos de pago ---
    forma_pago = models.CharField(max_length=15, choices=FORMAS_PAGO, blank=True, verbose_name='Forma de pago')
    banco = models.CharField(max_length=60, blank=True, verbose_name='Banco')
    clabe = models.CharField(max_length=30, blank=True, verbose_name='CLABE')
    titular_cuenta = models.CharField(max_length=200, blank=True, verbose_name='Titular de la cuenta')

    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True, verbose_name='Actualizado')
    created_by = models.ForeignKey(User, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(User, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')

    class Meta:
        verbose_name = 'Empleado'
        verbose_name_plural = 'Empleados'
        permissions = [
            ('ver_expediente_empleado', 'Puede ver el expediente de los empleados (identificación, '
                                        'domicilio, seguridad social, pago, documentos y condiciones)'),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.get_puesto_display()})"

    # --- Propiedades para los documentos ---
    @property
    def nombre_para_documentos(self) -> str:
        return (self.nombre_legal or self.nombre or '').strip()

    @property
    def domicilio_completo(self) -> str:
        """Domicilio en una línea; vacío si falta calle, colonia, C.P. o municipio."""
        if not (self.calle_numero and self.colonia and self.codigo_postal and self.municipio):
            return ''
        partes = [self.calle_numero, f'Col. {self.colonia}', f'C.P. {self.codigo_postal}', self.municipio]
        if self.estado:
            partes.append(self.estado)
        return ', '.join(partes)

    @property
    def identificacion_texto(self) -> str:
        """«INE número …» para el acuerdo; vacío si falta el tipo o el número."""
        if not (self.tipo_identificacion and self.numero_identificacion):
            return ''
        return f'{self.get_tipo_identificacion_display()} número {self.numero_identificacion}'

    # --- Validación ---
    def clean(self):
        from comunicacion.services import normalizar_telefono_wa

        from . import validadores as v

        errores = {}

        def normalizar(campo, validador=None):
            valor = v.limpiar(getattr(self, campo))
            if not valor:
                setattr(self, campo, '')
                return
            setattr(self, campo, valor)
            if validador:
                try:
                    validador(valor)
                except ValidationError as e:
                    errores[campo] = e.messages

        normalizar('curp', validador=v.validar_curp)
        normalizar('rfc', validador=v.validar_rfc)
        normalizar('nss', validador=v.validar_nss)
        normalizar('clabe', validador=v.validar_clabe)
        normalizar('codigo_postal', validador=v.validar_codigo_postal)
        self.numero_identificacion = (self.numero_identificacion or '').strip().upper()

        telefonos = ['emergencia_telefono'] + (['telefono'] if self.puesto == 'VENTAS' else [])
        for campo in telefonos:
            valor = getattr(self, campo)
            if valor:
                normalizado = normalizar_telefono_wa(valor)
                if not normalizado:
                    errores[campo] = ['Teléfono no válido.']
        if self.fecha_baja and self.fecha_alta and self.fecha_baja < self.fecha_alta:
            errores['fecha_baja'] = ['La baja no puede ser anterior al alta.']
        if self.clabe and not self.forma_pago:
            self.forma_pago = 'TRANSFERENCIA'
        try:
            self.validar_candado_usuario()
        except ValidationError as e:
            errores['usuario'] = e.messages
        if errores:
            raise ValidationError(errores)

    def validar_candado_usuario(self):
        """Sin acuerdo firmado no hay usuario del ERP para el personal de Ventas."""
        if self.usuario_id and self.puesto == 'VENTAS':
            if not self.pk or not self.documentos.filter(tipo='ACUERDO_FIRMADO', activo=True).exists():
                raise ValidationError(
                    'Sin el acuerdo de confidencialidad firmado en el expediente (documento activo) '
                    'no se puede ligar un usuario del ERP a este empleado.')

    def save(self, *args, **kwargs):
        self.validar_candado_usuario()
        baja_previa = None
        if self.pk:
            baja_previa = Empleado.objects.filter(pk=self.pk).values_list('fecha_baja', flat=True).first()
        super().save(*args, **kwargs)
        if self.fecha_baja and self.fecha_baja != baja_previa:
            transaction.on_commit(self._avisar_baja)

    def _avisar_baja(self):
        # Nunca tumba el guardado del empleado (patrón de las señales del repo).
        try:
            from .services_expediente import avisar_baja
            avisar_baja(self)
        except Exception:
            logger.exception('Nómina: no se pudo avisar la baja del empleado %s', self.pk)


class ReciboNomina(models.Model):
    ESTADO_CHOICES = [
        ('CALCULADO', 'Calculado (sin pagar)'),
        ('PAGADO', 'Pagado en efectivo'),
        ('CANCELADO', 'Cancelado'),
    ]

    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE)
    fecha_generacion = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de generación')

    # Datos leídos del Excel
    periodo = models.CharField(max_length=100)
    horas_trabajadas = models.DecimalField(max_digits=10, decimal_places=2)
    tarifa_aplicada = models.DecimalField(max_digits=10, decimal_places=2)
    total_pagado = models.DecimalField(
        max_digits=10, decimal_places=2,
        verbose_name="Total a pagar"
    )

    # Solo control administrativo — sin ningún vínculo con contabilidad.
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='CALCULADO')
    fecha_pago = models.DateField(null=True, blank=True, verbose_name="Fecha de pago en efectivo")
    pagado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='nominas_pagadas_por', verbose_name="Pagado por"
    )

    # El archivo PDF generado
    archivo_pdf = models.FileField(
        upload_to='nominas_pdf/', blank=True, null=True, storage=storage_privado,
        verbose_name='Archivo PDF',
    )

    class Meta:
        verbose_name = "Recibo"
        verbose_name_plural = "Recibos"

    def __str__(self):
        return f"Pago {self.empleado} - ${self.total_pagado} [{self.estado}]"


class AprobacionNomina(models.Model):
    """Visto bueno de Dirección al total de nómina de una semana (Issue #431).

    Solo deja registro de quién y cuándo: no genera póliza, no mueve dinero ni
    cambia el estado de los recibos. La transferencia sigue siendo en el banco.
    """
    semana_inicio = models.DateField(unique=True, verbose_name='Semana (lunes)')
    semana_fin = models.DateField(verbose_name='Semana (domingo)')
    total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Total a pagar')
    recibos = models.PositiveIntegerField(default=0, verbose_name='Recibos')
    notificada_en = models.DateTimeField(null=True, blank=True, verbose_name='Avisada a Dirección')
    aprobada_en = models.DateTimeField(null=True, blank=True, verbose_name='Aprobada')
    aprobada_por_nombre = models.CharField(max_length=120, blank=True, verbose_name='Aprobada por')
    aprobada_por_telefono = models.CharField(max_length=20, blank=True, verbose_name='Teléfono que aprobó')
    aprobada_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                     verbose_name='Usuario que aprobó')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creada')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizada')

    class Meta:
        verbose_name = 'Aprobación de nómina'
        verbose_name_plural = 'Aprobaciones'
        ordering = ['-semana_inicio']

    def __str__(self):
        return f'Nómina {self.semana_inicio:%d/%m/%Y} al {self.semana_fin:%d/%m/%Y}'

    @property
    def aprobada(self) -> bool:
        return self.aprobada_en is not None


TIPOS_DOCUMENTO_EMPLEADO = [
    ('INE_FRENTE', 'INE (frente)'),
    ('INE_REVERSO', 'INE (reverso)'),
    ('CURP', 'CURP'),
    ('COMPROBANTE_DOMICILIO', 'Comprobante de domicilio'),
    ('CONSTANCIA_FISCAL', 'Constancia de situación fiscal'),
    ('NSS', 'Constancia de NSS'),
    ('ACUERDO_FIRMADO', 'Acuerdo de confidencialidad firmado'),
    ('PROPUESTA_FIRMADA', 'Propuesta firmada'),
    ('OTRO', 'Otro'),
]


class DocumentoEmpleado(models.Model):
    """Documento del expediente de un empleado. Storage privado, sin borrado:
    se desactiva (`activo=False`)."""

    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name='documentos',
                                 verbose_name='Empleado')
    tipo = models.CharField(max_length=25, choices=TIPOS_DOCUMENTO_EMPLEADO, verbose_name='Tipo')
    archivo = models.FileField(upload_to='nomina/expedientes/', storage=storage_privado, verbose_name='Archivo')
    vigencia = models.DateField(null=True, blank=True, verbose_name='Vigencia')
    notas = models.CharField(max_length=200, blank=True, verbose_name='Notas')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Subido')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')
    created_by = models.ForeignKey(User, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Subido por')
    updated_by = models.ForeignKey(User, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')

    MAX_BYTES = 10 * 1024 * 1024

    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Expedientes'
        ordering = ['empleado_id', 'tipo', '-id']

    def __str__(self):
        return f'{self.get_tipo_display()} — {self.empleado}'

    def clean(self):
        from core_erp.validadores import FIRMAS_IMAGEN_O_PDF, validar_archivo

        # Solo un archivo recién subido se revisa (el ya guardado no se relee).
        if self.archivo and not getattr(self.archivo, '_committed', True):
            try:
                validar_archivo(self.archivo.file, FIRMAS_IMAGEN_O_PDF, self.MAX_BYTES)
            except ValidationError as e:
                raise ValidationError({'archivo': e.messages}) from None


class CondicionesPago(models.Model):
    """Condiciones económicas de un empleado, versionadas (Issue #505).

    Una vez ACEPTADA no se edita: se sustituye por otra versión. El motor de
    comisiones (PR 3) lee las vigentes en la fecha del hecho y sella los valores.
    """

    ESTADOS = [
        ('BORRADOR', 'Borrador'),
        ('PROPUESTA', 'Propuesta entregada'),
        ('ACEPTADA', 'Aceptada'),
        ('SUSTITUIDA', 'Sustituida'),
        ('CANCELADA', 'Cancelada'),
    ]
    PERIODOS_PAGO_FIJO = [('SEMANAL', 'Semanal'), ('QUINCENAL', 'Quincenal'), ('MENSUAL', 'Mensual')]
    PERIODICIDAD_VISITAS = [('SEMANAL', 'Semanal')]
    PERIODICIDAD_COMISIONES = [('QUINCENAL', 'Quincenal'), ('SEMANAL', 'Semanal'), ('MENSUAL', 'Mensual')]

    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name='condiciones',
                                 verbose_name='Empleado')
    puesto_descripcion = models.CharField(max_length=120, default='Asesora de ventas',
                                          verbose_name='Descripción del puesto')
    vigente_desde = models.DateField(verbose_name='Vigente desde')
    vigente_hasta = models.DateField(null=True, blank=True, verbose_name='Vigente hasta')
    estado = models.CharField(max_length=12, choices=ESTADOS, default='BORRADOR', verbose_name='Estado')
    aceptada_en = models.DateTimeField(null=True, blank=True, editable=False, verbose_name='Aceptada')

    tarifa_hora = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                      verbose_name='Tarifa por hora')
    pago_fijo = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                    verbose_name='Pago fijo')
    pago_fijo_periodo = models.CharField(max_length=10, choices=PERIODOS_PAGO_FIJO, blank=True,
                                         verbose_name='Periodo del pago fijo')
    pago_visita = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                      verbose_name='Pago por visita atendida')
    tope_visitas_semana = models.PositiveSmallIntegerField(default=5, verbose_name='Visitas pagadas por semana')
    porcentaje_extras = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True, verbose_name='Porcentaje sobre extras',
        help_text='Fracción: 0.05 = 5 %. Se aplica al valor antes de IVA, neto de descuento.')
    dias_atribucion = models.PositiveSmallIntegerField(
        default=30, verbose_name='Días de atribución',
        help_text='Ventana en que una visita o cotización suya sigue atribuyéndole la venta.')
    tope_compensacion = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal('0.3000'), verbose_name='Tope de compensación',
        help_text='Fracción máxima de cada pago de comisiones con la que se compensa un saldo en contra.')
    periodicidad_visitas = models.CharField(max_length=10, choices=PERIODICIDAD_VISITAS, default='SEMANAL',
                                            verbose_name='Periodicidad de visitas')
    periodicidad_comisiones = models.CharField(max_length=10, choices=PERIODICIDAD_COMISIONES,
                                               default='QUINCENAL', verbose_name='Periodicidad de comisiones')

    responsabilidades = models.TextField(blank=True, verbose_name='Responsabilidades',
                                         help_text='Una por renglón; salen en la propuesta.')
    horario = models.TextField(blank=True, verbose_name='Horario de visitas',
                               help_text='Un bloque por renglón, p. ej. «Mañana|10:00 a.m. a 1:00 p.m.».')
    notas = models.TextField(blank=True, verbose_name='Notas')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creada')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizada')
    created_by = models.ForeignKey(User, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creada por')
    updated_by = models.ForeignKey(User, null=True, blank=True, editable=False, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizada por')

    class Meta:
        verbose_name = 'Condición'
        verbose_name_plural = 'Condiciones'
        ordering = ['empleado_id', '-vigente_desde', '-id']
        constraints = [
            models.UniqueConstraint(fields=['empleado'], condition=models.Q(estado='ACEPTADA'),
                                    name='nomina_una_condicion_aceptada_por_empleado'),
        ]

    def __str__(self):
        return f'Condiciones de {self.empleado} desde {self.vigente_desde:%d/%m/%Y} ({self.get_estado_display()})'

    @property
    def inmutable(self) -> bool:
        return self.estado in ('ACEPTADA', 'SUSTITUIDA', 'CANCELADA')

    def clean(self):
        errores = {}
        if self.vigente_hasta and self.vigente_desde and self.vigente_hasta < self.vigente_desde:
            errores['vigente_hasta'] = ['No puede ser anterior al inicio.']
        for campo in ('porcentaje_extras', 'tope_compensacion'):
            valor = getattr(self, campo)
            if valor is not None and not (0 <= valor <= 1):
                errores[campo] = ['Debe ser una fracción entre 0 y 1 (0.05 = 5 %).']
        if self.pago_fijo and not self.pago_fijo_periodo:
            errores['pago_fijo_periodo'] = ['Indica a qué periodo corresponde el pago fijo.']
        if self.empleado_id and self.empleado.puesto != 'VENTAS' and self.pago_visita:
            errores['pago_visita'] = ['El pago por visita aplica solo al puesto de Ventas.']
        if self.pk:
            anterior = CondicionesPago.objects.filter(pk=self.pk).values(
                'estado', 'vigente_desde', 'tarifa_hora', 'pago_fijo', 'pago_visita',
                'porcentaje_extras', 'tope_visitas_semana', 'dias_atribucion', 'tope_compensacion').first()
            if anterior and anterior['estado'] in ('ACEPTADA', 'SUSTITUIDA', 'CANCELADA'):
                cambios = [k for k, v in anterior.items() if k != 'estado' and getattr(self, k) != v]
                if cambios:
                    errores['__all__'] = ['Una condición aceptada no se edita: crea una versión nueva.']
        if errores:
            raise ValidationError(errores)

    # --- Lectura para documentos y para el motor (PR 3) ---
    def escalones_ordenados(self):
        return list(self.escalones.order_by('desde_contratacion'))

    def monto_de_contratacion(self, numero: int):
        """Monto del escalón que corresponde al número de contratación del año, o None."""
        monto = None
        for e in self.escalones_ordenados():
            if e.desde_contratacion <= numero:
                monto = e.monto
        return monto


class EscalonComision(models.Model):
    """Monto fijo por contratación a partir de la contratación `desde_contratacion` del año."""

    condiciones = models.ForeignKey(CondicionesPago, on_delete=models.CASCADE, related_name='escalones',
                                    verbose_name='Condiciones')
    desde_contratacion = models.PositiveSmallIntegerField(verbose_name='Desde la contratación n.º')
    monto = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Monto por contratación')

    class Meta:
        verbose_name = 'Escalón'
        verbose_name_plural = 'Escalones'
        ordering = ['desde_contratacion']
        constraints = [
            models.UniqueConstraint(fields=['condiciones', 'desde_contratacion'],
                                    name='nomina_escalon_unico_por_numero'),
        ]

    def __str__(self):
        return f'Desde la {self.desde_contratacion}: ${self.monto}'

    def clean(self):
        if self.desde_contratacion is not None and self.desde_contratacion < 1:
            raise ValidationError({'desde_contratacion': ['Debe ser 1 o mayor.']})
        if self.condiciones_id and self.condiciones.inmutable:
            raise ValidationError('Una condición aceptada no se edita: crea una versión nueva.')
