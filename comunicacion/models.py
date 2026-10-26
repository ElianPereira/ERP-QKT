"""
Modelo unificado de comunicaciones salientes con clientes.
Registra cada email/WhatsApp/SMS/notificación enviado.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core_erp.storages_qkt import storage_privado


class ComunicacionCliente(models.Model):
    CANAL_CHOICES = [
        ('EMAIL', 'Email'),
        ('WHATSAPP', 'WhatsApp'),
        ('SMS', 'SMS'),
        ('PORTAL', 'Notificación en portal'),
    ]
    TIPO_CHOICES = [
        ('COTIZACION', 'Cotización enviada'),
        ('CONFIRMACION_PAGO', 'Confirmación de pago'),
        ('REEMBOLSO', 'Notificación de reembolso'),
        ('RECORDATORIO_PAGO', 'Recordatorio de pago'),
        ('CONTRATO', 'Contrato'),
        ('EVENTO_PROXIMO', 'Evento próximo'),
        ('CANCELACION', 'Cancelación'),
        ('FACTURA', 'Factura emitida'),
        ('AGENTE_IA', 'Respuesta del agente de WhatsApp'),
        ('SEGUIMIENTO', 'Seguimiento de cotización sin pago'),
        ('ENCUESTA', 'Encuesta de satisfacción'),
        ('REACTIVACION', 'Reactivación por aniversario'),
        ('COMPRAS', 'Lista de compras del evento'),
        ('OTRO', 'Otro'),
    ]
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('ENVIADO', 'Enviado'),
        ('ENTREGADO', 'Entregado'),
        ('ABIERTO', 'Abierto / leído'),
        ('FALLIDO', 'Fallido'),
    ]
    TRIGGER_CHOICES = [
        ('MANUAL', 'Manual'),
        ('SIGNAL', 'Signal automático'),
        ('CRON', 'Tarea programada'),
    ]

    cotizacion = models.ForeignKey(
        'comercial.Cotizacion', on_delete=models.CASCADE,
        related_name='comunicaciones', null=True, blank=True,
        verbose_name='Cotización',
    )
    pago = models.ForeignKey(
        'comercial.Pago', on_delete=models.SET_NULL,
        related_name='comunicaciones', null=True, blank=True
    )
    canal = models.CharField(max_length=15, choices=CANAL_CHOICES)
    tipo = models.CharField(max_length=25, choices=TIPO_CHOICES)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default='PENDIENTE')
    trigger = models.CharField(max_length=10, choices=TRIGGER_CHOICES, default='MANUAL', verbose_name='Disparador')

    destinatario = models.CharField(max_length=200, help_text="Email, teléfono o URL")
    asunto = models.CharField(max_length=255, blank=True)
    cuerpo = models.TextField(blank=True)
    error = models.TextField(blank=True)

    fecha_envio = models.DateTimeField(default=timezone.now, verbose_name='Fecha de envío')
    fecha_entrega = models.DateTimeField(null=True, blank=True)
    fecha_apertura = models.DateTimeField(null=True, blank=True)

    proveedor_id = models.CharField(max_length=100, blank=True,
                                     help_text="ID externo (Brevo, WhatsApp, etc.)", verbose_name='ID del proveedor')

    # Identifica de forma única el par (evento de negocio, canal). El índice
    # único es la reserva: se inserta la fila ANTES de enviar y un IntegrityError
    # significa "ya se envió". Es lo que impide duplicados cuando un signal corre
    # dos veces, el cron repite o Railway reinicia a media ejecución.
    # Formato: cotizacion:{id}:web:email · pago:{id}:whatsapp ·
    #          recordatorio:{parcialidad_id}:{YYYY-MM-DD}:email
    # Nulo en los envíos manuales, donde repetir es legítimo (NULL no colisiona
    # en un índice único ni en PostgreSQL ni en SQLite).
    clave_idempotencia = models.CharField(
        max_length=191, null=True, blank=True, unique=True,
        verbose_name="Clave de idempotencia",
        help_text="Evita envíos duplicados del mismo evento por el mismo canal.",
    )

    class Meta:
        verbose_name = "Comunicación con cliente"
        verbose_name_plural = "Bitácora"
        ordering = ['-fecha_envio']
        indexes = [
            models.Index(fields=['cotizacion', '-fecha_envio']),
            models.Index(fields=['estado', 'canal']),
        ]

    def __str__(self):
        return f"[{self.canal}/{self.tipo}] → {self.destinatario}"


class ConversacionWhatsApp(models.Model):
    """Conversación del agente de WhatsApp con un número (Issue #346).

    `historial` es exactamente lo que se le manda a la API de Claude y solo
    crece (append-only): el modelo invalida su razonamiento previo si una
    parte ya enviada cambia. Se reinicia entero, nunca a medias, cuando la
    conversación lleva más de 24 h sin mensajes.
    """
    telefono = models.CharField(max_length=20, unique=True, verbose_name='Teléfono')
    nombre = models.CharField(max_length=200, blank=True, verbose_name='Nombre en WhatsApp')
    cliente = models.ForeignKey(
        'comercial.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='conversaciones_whatsapp', verbose_name='Cliente',
    )
    historial = models.JSONField(default=list, blank=True, verbose_name='Historial para la IA')
    requiere_humano = models.BooleanField(
        default=False, verbose_name='Requiere humano',
        help_text='El agente pidió ayuda y dejó de contestar. Desmárcalo para reactivarlo.',
    )
    motivo_humano = models.CharField(max_length=300, blank=True, verbose_name='Motivo')
    pausado_hasta = models.DateTimeField(
        null=True, blank=True, verbose_name='Agente en pausa hasta',
        help_text='Se llena solo cuando alguien contesta desde la app de WhatsApp.',
    )
    ultimo_mensaje = models.DateTimeField(default=timezone.now, verbose_name='Último mensaje')
    # Consentimiento expreso para cotizar en el chat (Issue #366): el cliente
    # tocó «Acepto» en el mensaje con botones. Es la evidencia que pasa a
    # `legal.AceptacionLegal` cuando se crea la cotización.
    consentimiento_en = models.DateTimeField(null=True, blank=True, verbose_name='Aceptó el aviso el')
    consentimiento_wamid = models.CharField(max_length=191, blank=True, verbose_name='Mensaje con que aceptó')
    consentimiento_marketing = models.BooleanField(default=False, verbose_name='Aceptó promociones')
    # Origen del anuncio (Fase 2a): objeto `referral` de Meta en el primer
    # mensaje de un anuncio de clic a WhatsApp. Se guarda una vez, no se pisa.
    anuncio_id = models.CharField(max_length=100, blank=True, db_index=True, verbose_name='Anuncio (id)')
    anuncio_titulo = models.CharField(max_length=300, blank=True, verbose_name='Anuncio')
    anuncio_url = models.URLField(max_length=500, blank=True, verbose_name='Enlace del anuncio')
    anuncio_clid = models.CharField(max_length=300, blank=True, verbose_name='Clic en el anuncio (ctwa_clid)')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Conversación de WhatsApp'
        verbose_name_plural = 'Conversaciones'
        ordering = ['-ultimo_mensaje']

    def __str__(self):
        return f"{self.nombre or 'Sin nombre'} ({self.telefono})"

    def agente_en_pausa(self, ahora=None) -> bool:
        ahora = ahora or timezone.now()
        return self.requiere_humano or bool(self.pausado_hasta and self.pausado_hasta > ahora)


class MensajeWhatsApp(models.Model):
    """Bitácora legible de la conversación: lo que escribió el cliente, lo que
    contestó el agente y lo que contestó una persona desde la app."""
    DIRECCION_CHOICES = [
        ('ENTRADA', 'Cliente'),
        ('AGENTE', 'Agente IA'),
        ('HUMANO', 'Persona del equipo'),
    ]
    conversacion = models.ForeignKey(
        ConversacionWhatsApp, on_delete=models.CASCADE, related_name='mensajes',
        verbose_name='Conversación',
    )
    direccion = models.CharField(max_length=10, choices=DIRECCION_CHOICES, verbose_name='Quién')
    texto = models.TextField(blank=True, verbose_name='Texto')
    # ID de Meta (wamid). Único: Meta reintenta entregas y un mensaje repetido
    # no debe contestarse dos veces.
    wamid = models.CharField(max_length=191, null=True, blank=True, unique=True, verbose_name='ID de Meta')
    procesado = models.BooleanField(default=False, verbose_name='Procesado')
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Enviado por',
        help_text='Quién contestó desde el ERP (vacío si fue el cliente, el agente o la app).',
    )
    # Mensaje que el sistema manda por su cuenta (p. ej. el seguimiento de una
    # cotización sin pago): no es respuesta del modelo, así que la próxima
    # respuesta del agente lo recibe como contexto en vez de cortar ahí.
    automatico = models.BooleanField(default=False, verbose_name='Automático')
    # Archivo adjunto (foto o PDF) que mandó un responsable del directorio
    # (Fase 3): solo guarda la referencia de Meta; el archivo se descarga al
    # atenderlo y nunca llega al agente.
    media_id = models.CharField(max_length=191, blank=True, verbose_name='ID del archivo en Meta')
    media_mime = models.CharField(max_length=80, blank=True, verbose_name='Tipo del archivo')
    # Consumo de la respuesta del modelo (solo AGENTE escritos por la IA),
    # sumado entre las vueltas de herramientas de esa respuesta.
    modelo = models.CharField(max_length=60, blank=True, verbose_name='Modelo de IA')
    tokens_entrada = models.PositiveIntegerField(default=0, verbose_name='Tokens de entrada')
    tokens_salida = models.PositiveIntegerField(default=0, verbose_name='Tokens de salida')
    tokens_cache_lectura = models.PositiveIntegerField(default=0, verbose_name='Tokens leídos de caché')
    tokens_cache_escritura = models.PositiveIntegerField(default=0, verbose_name='Tokens escritos en caché')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Mensaje de WhatsApp'
        verbose_name_plural = 'Mensajes'
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['conversacion', 'procesado'])]

    def __str__(self):
        return f"[{self.get_direccion_display()}] {self.texto[:60]}"


class PaseAHumano(models.Model):
    """Cada vez que una conversación quedó para una persona del equipo, con su
    motivo. `ConversacionWhatsApp.motivo_humano` solo guarda el último; esto
    es lo que mide el tablero del agente. Se borra con su conversación."""
    conversacion = models.ForeignKey(
        ConversacionWhatsApp, on_delete=models.CASCADE, related_name='pases_a_humano',
        verbose_name='Conversación',
    )
    motivo = models.CharField(max_length=300, blank=True, verbose_name='Motivo')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Pase a humano'
        verbose_name_plural = 'Pases'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.conversacion.telefono}: {self.motivo[:60]}"


class RespuestaBloqueada(models.Model):
    """Respuesta del modelo que no se envió porque el filtro o el juez la
    detuvieron (Issue #366). El cliente recibió en su lugar un texto fijo.
    Se borra con su conversación."""
    CAPA_CHOICES = [('FILTRO', 'Filtro automático'), ('JUEZ', 'Juez de IA')]
    conversacion = models.ForeignKey(
        ConversacionWhatsApp, on_delete=models.CASCADE, related_name='respuestas_bloqueadas',
        verbose_name='Conversación',
    )
    texto = models.TextField(verbose_name='Respuesta detenida')
    capa = models.CharField(max_length=10, choices=CAPA_CHOICES, verbose_name='Quién la detuvo')
    motivo = models.CharField(max_length=300, blank=True, verbose_name='Motivo')
    # El historial del modelo es append-only: la próxima respuesta recibe una
    # nota de que esta no se envió, una sola vez.
    avisada_al_modelo = models.BooleanField(default=False, verbose_name='Avisada al modelo')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Respuesta bloqueada'
        verbose_name_plural = 'Bloqueadas'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_capa_display()}: {self.motivo[:60]}"


class PreguntaSinRespuesta(models.Model):
    """Lo que el agente no supo contestar con sus herramientas (Issue #366).
    El tablero las agrupa para completar las preguntas frecuentes o el
    catálogo. El texto lo redacta el modelo, sin datos personales."""
    conversacion = models.ForeignKey(
        ConversacionWhatsApp, on_delete=models.CASCADE, related_name='preguntas_sin_respuesta',
        verbose_name='Conversación',
    )
    pregunta = models.CharField(max_length=300, verbose_name='Pregunta')
    # Cuándo entró al resumen semanal (`services_preguntas`): no se vuelve a sugerir.
    resumida_en = models.DateTimeField(null=True, blank=True, verbose_name='Resumida el')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Pregunta sin respuesta'
        verbose_name_plural = 'Pendientes'
        ordering = ['-created_at']

    def __str__(self):
        return self.pregunta[:80]


class BajaWhatsApp(models.Model):
    """El cliente pidió no recibir más mensajes de promoción ni seguimientos.

    Va por teléfono y no cuelga de la conversación: la purga de
    conversaciones (`services_retencion`) no debe llevársela, porque es lo que
    impide volver a escribirle. Deja de valer si después vuelve a aceptar
    promociones (`services_bajas.dado_de_baja`). No se edita ni se borra.
    """
    ORIGEN_CHOICES = [
        ('PALABRA', 'Escribió BAJA'),
        ('AGENTE', 'Se lo pidió a Kooxi'),
    ]

    telefono = models.CharField(max_length=20, db_index=True, verbose_name='Teléfono')
    origen = models.CharField(max_length=10, choices=ORIGEN_CHOICES, verbose_name='Cómo la pidió')
    texto = models.CharField(max_length=300, blank=True, verbose_name='Mensaje del cliente')
    wamid = models.CharField(max_length=191, blank=True, verbose_name='ID del mensaje en WhatsApp')
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Baja de mensajes'
        verbose_name_plural = 'Bajas'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.telefono} — {self.created_at:%d/%m/%Y}'


class Responsable(models.Model):
    """Directorio de responsables internos: a quién le llega cada aviso.

    Reemplaza el número único de avisos de Railway: cambiar un número es editar
    un renglón, sin tocar Railway. Un número nuevo no se activa hasta que su
    dueño responde «ACEPTO» desde él (`telefono_pendiente` → `telefono_wa`),
    para que un número mal capturado no mande avisos a un desconocido.
    Un suplente es un renglón con rango de fechas: dentro del rango gana sobre
    el permanente. No se borra, se desactiva.
    """
    ROL_CHOICES = [
        ('ENCARGADO', 'Encargado'),
        ('DIRECCION', 'Dirección'),
        ('CONTADOR', 'Contador'),
        ('COMPRAS', 'Compras'),
    ]
    COPIA_CHOICES = [
        ('EN_VIVO', 'En vivo'),
        ('CADA_HORA', 'Cada hora'),
        ('OFF', 'Sin copia'),
    ]

    rol = models.CharField(max_length=10, choices=ROL_CHOICES, db_index=True, verbose_name='Rol')
    nombre = models.CharField(max_length=120, verbose_name='Nombre')
    telefono_wa = models.CharField(max_length=20, blank=True, verbose_name='Teléfono de WhatsApp activo')
    telefono_pendiente = models.CharField(max_length=20, blank=True, verbose_name='Teléfono esperando ACEPTO')
    email = models.EmailField(blank=True, verbose_name='Correo electrónico')
    modo_copia = models.CharField(
        max_length=10, choices=COPIA_CHOICES, default='EN_VIVO', verbose_name='Copia de avisos',
        help_text='Solo aplica a Dirección: si recibe copia de los avisos del encargado.')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    vigente_desde = models.DateField(null=True, blank=True, verbose_name='Vigente desde',
                                     help_text='Solo para suplencias; vacío = sin límite.')
    vigente_hasta = models.DateField(null=True, blank=True, verbose_name='Vigente hasta')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')

    class Meta:
        verbose_name = 'Responsable'
        verbose_name_plural = 'Responsables'
        ordering = ['rol', '-activo', 'nombre']

    def __str__(self):
        return f'{self.get_rol_display()} — {self.nombre}'

    @property
    def confirmado(self) -> bool:
        return bool(self.telefono_wa) and not self.telefono_pendiente


class CambioResponsable(models.Model):
    """Bitácora de solo lectura de los cambios al directorio."""
    responsable = models.ForeignKey(Responsable, on_delete=models.PROTECT, related_name='cambios',
                                    verbose_name='Responsable')
    campo = models.CharField(max_length=40, verbose_name='Campo')
    antes = models.CharField(max_length=300, blank=True, verbose_name='Antes')
    despues = models.CharField(max_length=300, blank=True, verbose_name='Después')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='+', verbose_name='Usuario')
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Cambio de responsable'
        verbose_name_plural = 'Cambios'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.responsable} · {self.campo}'


class Pendiente(models.Model):
    """Bandeja única de lo que requiere a una persona (Issue #422).

    Cada alerta interna deja un renglón con responsable (`rol`), severidad y
    plazo. No se borra: se resuelve o se descarta. El detalle nunca lleva el
    texto de mensajes de clientes (esta tabla no la alcanza la purga de
    conversaciones).
    """
    TIPO_CHOICES = [
        ('ALERTA', 'Alerta'),
        ('PASE_HUMANO', 'Pase a humano'),
        ('QUEJA', 'Queja'),
        ('VISITA', 'Visita'),
        ('LLEGADA', 'Llegada'),
        ('CHOQUE_FECHA', 'Choque de fecha'),
        ('CONTRACARGO', 'Contracargo'),
        ('SAT', 'SAT'),
        ('DESVIACION', 'Desviación'),
        ('HALLAZGO', 'Hallazgo'),
        ('CUMPLIMIENTO', 'Cumplimiento'),
        ('COMPRAS', 'Compras'),
        ('DEPOSITO', 'Depósito en garantía'),
        ('CRON', 'Proceso automático'),
        ('OTRO', 'Otro'),
    ]
    SEVERIDAD_CHOICES = [('URGENTE', 'Urgente'), ('NORMAL', 'Normal')]
    ESTADO_CHOICES = [('ABIERTO', 'Abierto'), ('RESUELTO', 'Resuelto'), ('DESCARTADO', 'Descartado')]

    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES, default='ALERTA', db_index=True,
                            verbose_name='Tipo')
    severidad = models.CharField(max_length=8, choices=SEVERIDAD_CHOICES, default='NORMAL', db_index=True,
                                 verbose_name='Severidad')
    rol = models.CharField(max_length=10, choices=Responsable.ROL_CHOICES, db_index=True, verbose_name='Responsable')
    titulo = models.CharField(max_length=200, verbose_name='Título')
    detalle = models.TextField(blank=True, verbose_name='Detalle')
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='+', verbose_name='Tipo de objeto')
    object_id = models.PositiveIntegerField(null=True, blank=True, verbose_name='Id del objeto')
    objeto = GenericForeignKey('content_type', 'object_id')
    vence_en = models.DateTimeField(db_index=True, verbose_name='Vence')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='ABIERTO', db_index=True,
                              verbose_name='Estado')
    tomado_por_direccion = models.BooleanField(default=False, verbose_name='Tomado por Dirección')
    escalado_en = models.DateTimeField(null=True, blank=True, verbose_name='Escalado')
    copiado_en = models.DateTimeField(null=True, blank=True, verbose_name='Incluido en la copia por hora')
    resuelto_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='+', verbose_name='Resuelto por')
    resuelto_en = models.DateTimeField(null=True, blank=True, verbose_name='Resuelto')
    clave_idempotencia = models.CharField(max_length=191, null=True, blank=True, unique=True,
                                          verbose_name='Clave de idempotencia')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')

    class Meta:
        verbose_name = 'Pendiente'
        verbose_name_plural = 'Pendientes'
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['estado', 'rol', 'vence_en'])]

    def __str__(self):
        return f'Pendiente {self.pk}: {self.titulo}'[:120]

    @property
    def vencido(self) -> bool:
        return self.estado == 'ABIERTO' and self.vence_en < timezone.now()


class Hallazgo(models.Model):
    """Algo que el ERP detectó y propone a Dirección en el resumen semanal (Issue #431).

    La clave identifica el hallazgo (p. ej. `margen:<producto>`): uno ignorado o
    convertido en tarea no se vuelve a proponer. No se borra. El detalle nunca
    lleva texto de mensajes de clientes.
    """
    TIPO_CHOICES = [
        ('MARGEN', 'Margen negativo'),
        ('SIN_FACTURA', 'Pago sin factura'),
        ('SIN_SEGUIMIENTO', 'Cotización sin seguimiento'),
        ('PRECIO_ENTRAR', 'Servicio fuera del plan'),
        ('DIAS_LIBRES', 'Días libres'),
    ]
    ESTADO_CHOICES = [('PROPUESTO', 'Propuesto'), ('TAREA', 'Tarea creada'), ('IGNORADO', 'Ignorado')]

    clave = models.CharField(max_length=191, unique=True, verbose_name='Clave')
    tipo = models.CharField(max_length=16, choices=TIPO_CHOICES, db_index=True, verbose_name='Tipo')
    titulo = models.CharField(max_length=200, verbose_name='Hallazgo')
    detalle = models.TextField(blank=True, verbose_name='Detalle')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='PROPUESTO', db_index=True,
                              verbose_name='Estado')
    pendiente = models.ForeignKey(Pendiente, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                  verbose_name='Pendiente creado')
    propuesto_en = models.DateTimeField(null=True, blank=True, verbose_name='Propuesto')
    resuelto_en = models.DateTimeField(null=True, blank=True, verbose_name='Resuelto')
    resuelto_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='+', verbose_name='Resuelto por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')

    class Meta:
        verbose_name = 'Hallazgo'
        verbose_name_plural = 'Hallazgos'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'Hallazgo {self.pk}: {self.titulo}'[:120]


class ConsultaCfdi(models.Model):
    """Pregunta al contador sobre un CFDI emitido que el ERP no pudo ligar solo (Issue #431).

    Bitácora e idempotencia: cada CFDI se pregunta una vez y, si no hay respuesta
    en 3 días, una vez más. Sin menú propio: el comprobante se revisa en
    Contabilidad → Comprobantes.
    """
    ESTADO_CHOICES = [('PREGUNTADA', 'Preguntada'), ('LIGADA', 'Ligada'), ('NINGUNA', 'Ninguna coincide')]

    cfdi = models.ForeignKey('contabilidad.CfdiSAT', on_delete=models.PROTECT, related_name='consultas',
                             verbose_name='Comprobante')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='PREGUNTADA', db_index=True,
                              verbose_name='Estado')
    candidatas = models.CharField(max_length=100, blank=True, verbose_name='Solicitudes candidatas',
                                  help_text='Ids de solicitud de factura separados por coma.')
    solicitud = models.ForeignKey('facturacion.SolicitudFactura', null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='+', verbose_name='Solicitud elegida')
    telefono = models.CharField(max_length=20, blank=True, verbose_name='Teléfono que respondió')
    respondida_en = models.DateTimeField(null=True, blank=True, verbose_name='Respondida')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Preguntada')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizada')

    class Meta:
        verbose_name = 'Consulta al contador'
        verbose_name_plural = 'Consultas'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'Consulta {self.pk} sobre {self.cfdi.uuid[:8]}'


class HorarioVisita(models.Model):
    """Ventana semanal en que el encargado recibe visitas a la Quinta (Fase 2a).

    Kooxi parte cada ventana en citas de `duracion_minutos` y solo ofrece las
    que no tienen una visita activa. Se desactiva, no se borra.
    """
    DIA_CHOICES = [(0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'),
                   (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')]

    dia_semana = models.PositiveSmallIntegerField(choices=DIA_CHOICES, verbose_name='Día de la semana')
    hora_inicio = models.TimeField(verbose_name='Desde')
    hora_fin = models.TimeField(verbose_name='Hasta')
    duracion_minutos = models.PositiveSmallIntegerField(default=60, verbose_name='Duración de cada visita (min)')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Horario de visita'
        verbose_name_plural = 'Horarios'
        ordering = ['dia_semana', 'hora_inicio']

    def __str__(self):
        return f'{self.get_dia_semana_display()} {self.hora_inicio:%H:%M}-{self.hora_fin:%H:%M}'

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.hora_inicio and self.hora_fin and self.hora_fin <= self.hora_inicio:
            raise ValidationError({'hora_fin': 'La hora final debe ser posterior a la inicial.'})
        if self.duracion_minutos is not None and self.duracion_minutos < 15:
            raise ValidationError({'duracion_minutos': 'La visita debe durar al menos 15 minutos.'})


class Visita(models.Model):
    """Cita para conocer la Quinta. Nace POR_CONFIRMAR y solo una persona
    (encargado o Dirección) la confirma: Kooxi nunca lo hace (Fase 2a)."""
    ESTADO_CHOICES = [
        ('POR_CONFIRMAR', 'Por confirmar'),
        ('CONFIRMADA', 'Confirmada'),
        ('RECHAZADA', 'No disponible'),
        ('CANCELADA', 'Cancelada'),
    ]
    ACTIVAS = ('POR_CONFIRMAR', 'CONFIRMADA')

    conversacion = models.ForeignKey(ConversacionWhatsApp, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='visitas', verbose_name='Conversación')
    cliente = models.ForeignKey('comercial.Cliente', null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='visitas', verbose_name='Cliente')
    nombre = models.CharField(max_length=200, verbose_name='Nombre del visitante')
    telefono = models.CharField(max_length=20, verbose_name='Teléfono')
    personas = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Personas que asisten')
    inicio = models.DateTimeField(db_index=True, verbose_name='Fecha y hora')
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default='POR_CONFIRMAR', db_index=True,
                              verbose_name='Estado')
    decidida_en = models.DateTimeField(null=True, blank=True, verbose_name='Decidida')
    decidida_por = models.CharField(max_length=100, blank=True, verbose_name='Decidida por')
    recordatorio_en = models.DateTimeField(null=True, blank=True, verbose_name='Recordatorio enviado')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Visita'
        verbose_name_plural = 'Visitas'
        ordering = ['inicio']
        constraints = [
            # Un horario no se agenda dos veces mientras la visita siga viva.
            models.UniqueConstraint(fields=['inicio'], condition=models.Q(estado__in=('POR_CONFIRMAR', 'CONFIRMADA')),
                                    name='visita_unica_por_horario_activo'),
        ]

    def __str__(self):
        return f'Visita {self.pk}: {self.nombre} {timezone.localtime(self.inicio):%d/%m/%Y %H:%M}'


class EncuestaPostEvento(models.Model):
    """Encuesta corta de satisfacción un día después del servicio (Fase 3).

    Una por cotización (la unicidad es la idempotencia del envío). La
    calificación llega por botón (`enc:<id>:<1-5>`) o como un número 1–5 en
    texto desde el mismo teléfono. No guarda texto libre del cliente.
    """
    ESTADO_CHOICES = [('ENVIADA', 'Enviada'), ('CONTESTADA', 'Contestada')]

    cotizacion = models.OneToOneField('comercial.Cotizacion', on_delete=models.CASCADE,
                                      related_name='encuesta', verbose_name='Cotización')
    telefono = models.CharField(max_length=20, db_index=True, verbose_name='Teléfono')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='ENVIADA', db_index=True,
                              verbose_name='Estado')
    calificacion = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name='Calificación')
    enviada_en = models.DateTimeField(default=timezone.now, verbose_name='Enviada')
    contestada_en = models.DateTimeField(null=True, blank=True, verbose_name='Contestada')
    resena_solicitada = models.BooleanField(default=False, verbose_name='Reseña solicitada')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')

    class Meta:
        verbose_name = 'Encuesta'
        verbose_name_plural = 'Encuestas'
        ordering = ['-enviada_en', '-id']

    def __str__(self):
        return f'Encuesta COT-{self.cotizacion_id:03d}'


class InspeccionDeposito(models.Model):
    """Inspección posterior al servicio que decide qué pasa con el depósito en
    garantía (Fase 3). MUEVE DINERO: la devolución o retención solo ocurre por
    `comercial.services_deposito.liquidar`, ver `comunicacion.services_inspeccion`.
    """
    ESTADO_CHOICES = [
        ('SOLICITADA', 'Esperando al encargado'),
        ('DANOS', 'Daños en reporte'),
        ('POR_APROBAR', 'Por aprobar por Dirección'),
        ('PROCESANDO', 'Procesando'),
        ('LIQUIDADA', 'Liquidada'),
        ('MANUAL', 'Requiere atención manual'),
    ]
    RESOLUCION_CHOICES = [
        ('SIN_DANOS', 'Sin daños (el encargado lo confirmó)'),
        ('SIN_REPORTE', 'Sin reporte en el plazo'),
        ('RETENCION_ENCARGADO', 'Retención dentro del tope'),
        ('RETENCION_APROBADA', 'Retención aprobada por Dirección'),
        ('RETENCION_RECHAZADA', 'Retención rechazada por Dirección'),
    ]

    deposito = models.OneToOneField('comercial.DepositoGarantia', on_delete=models.PROTECT,
                                    related_name='inspeccion', verbose_name='Depósito')
    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES, default='SOLICITADA', db_index=True,
                              verbose_name='Estado')
    resolucion = models.CharField(max_length=20, choices=RESOLUCION_CHOICES, blank=True, verbose_name='Resolución')
    solicitada_en = models.DateTimeField(default=timezone.now, verbose_name='Solicitada')
    vence_en = models.DateTimeField(db_index=True, verbose_name='Devolución automática',
                                    help_text='Si nadie reporta daños antes de esta hora, el depósito se devuelve solo.')
    reportado_por = models.CharField(max_length=20, blank=True, verbose_name='Reportó (teléfono)')
    monto_danos = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                      verbose_name='Monto de daños reportado')
    desglose = models.TextField(blank=True, verbose_name='Descripción de los daños')
    nota = models.TextField(blank=True, verbose_name='Resultado')
    resuelto_en = models.DateTimeField(null=True, blank=True, verbose_name='Resuelta')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')

    class Meta:
        verbose_name = 'Inspección'
        verbose_name_plural = 'Inspecciones'
        ordering = ['-solicitada_en', '-id']

    def __str__(self):
        return f'Inspección COT-{self.deposito.cotizacion_id:03d}'


class FotoInspeccion(models.Model):
    """Foto de evidencia de daños que mandó el encargado por WhatsApp."""
    inspeccion = models.ForeignKey(InspeccionDeposito, on_delete=models.CASCADE, related_name='fotos',
                                   verbose_name='Inspección')
    archivo = models.FileField(upload_to='comunicacion/inspecciones/', storage=storage_privado,
                               verbose_name='Archivo')
    media_id = models.CharField(max_length=191, blank=True, verbose_name='ID del archivo en Meta')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Recibida')

    class Meta:
        verbose_name = 'Foto de inspección'
        verbose_name_plural = 'Fotos'
        ordering = ['id']
