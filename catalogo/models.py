from decimal import Decimal, ROUND_HALF_UP

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from comercial.models import Producto

IVA = Decimal('0.16')


def _con_iva(precio):
    """Precio con IVA incluido, cuantizado a 2 decimales (ROUND_HALF_UP).
    Consistente con cómo el portal del cotizador muestra sus precios."""
    if precio is None:
        return None
    return (precio * (1 + IVA)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class ConfiguracionCatalogo(models.Model):
    """
    Singleton: datos generales del catálogo (portada + contacto).
    Solo debe existir un registro. Se fuerza en save().
    """
    nombre_empresa = models.CharField(
        max_length=100, default="Quinta",
        help_text="Parte NO destacada del nombre en la portada (ej. 'Quinta').",
    )
    nombre_empresa_enfasis = models.CharField(
        max_length=100, default="Ko'ox Tanil",
        help_text="Parte en cursiva/dorado del nombre en la portada (ej. \"Ko'ox Tanil\").",
    )
    ubicacion = models.CharField(max_length=150, default="Umán, Yucatán · México")
    titulo_catalogo = models.CharField(
        max_length=60, default="Catálogo", verbose_name="Título del catálogo (portada)",
    )
    titulo_catalogo_enfasis = models.CharField(
        max_length=60, default="de Servicios",
        verbose_name="Título del catálogo — énfasis (portada)",
    )
    subtitulo_portada = models.CharField(
        max_length=150, default="Donde los momentos se vuelven recuerdos"
    )
    telefono = models.CharField(max_length=20, default="(999) 445 71 78")
    email_contacto = models.EmailField(blank=True)
    sitio_web = models.CharField(max_length=100, default="quintakooxtanil.com")
    url_cotizador = models.CharField(max_length=150, blank=True)
    url_portal_clientes = models.CharField(max_length=150, blank=True)
    url_kaan_room = models.CharField(max_length=150, blank=True, verbose_name="Link Ka'an Room (Airbnb)")
    url_otoch_room = models.CharField(max_length=150, blank=True, verbose_name="Link Otoch Room (Airbnb)")
    direccion = models.CharField(max_length=200, blank=True)
    facebook = models.CharField(max_length=100, blank=True)
    instagram = models.CharField(max_length=100, blank=True)
    eyebrow_contacto = models.CharField(max_length=60, default="Reserva tu fecha")
    titulo_contacto = models.CharField(max_length=100, default="Hablemos de")
    titulo_contacto_enfasis = models.CharField(max_length=100, default="tu evento")
    subtitulo_contacto = models.CharField(
        max_length=150, default="Estamos para acompañarte en cada detalle",
    )
    tagline_cierre = models.CharField(
        max_length=200, default="A 20 minutos de Mérida · Abiertos 365 días al año",
        help_text="Frase final de la última página, junto al nombre de la empresa.",
    )
    imagen_portada = models.ImageField(upload_to='catalogo/portada/', blank=True, null=True)
    imagen_footer = models.ImageField(upload_to='catalogo/footer/', blank=True, null=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración del Catálogo"
        verbose_name_plural = "Configuración del Catálogo"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # el singleton no se elimina

    @classmethod
    def cargar(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Configuración del Catálogo"


class BadgeServicio(models.Model):
    """Pills de la portada: EVENTOS Y BODAS, PASADÍA, etc."""
    texto = models.CharField(max_length=40)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['orden']
        verbose_name = "Badge de servicio (portada)"
        verbose_name_plural = "Badges de servicio (portada)"

    def __str__(self):
        return self.texto


class SeccionCatalogo(models.Model):
    """Una sección completa del catálogo: Eventos, Pasadía, Hospedaje, Mobiliario..."""
    numero = models.CharField(
        max_length=4, blank=True,
        help_text="Ej: 01, 02, 03. Vacío = sección sin numerar (ej. servicios adicionales).",
    )
    slug = models.SlugField(unique=True, help_text="Identificador interno, ej: eventos")
    categoria = models.CharField(
        max_length=80, blank=True,
        verbose_name="Categoría (encabezado corto)",
        help_text="Texto corto junto al número, ej: 'Eventos sociales y bodas'. "
                   "Si se deja vacío, se usa el título completo.",
    )
    titulo = models.CharField(max_length=100, help_text="Ej: El espacio para")
    titulo_enfasis = models.CharField(
        max_length=100, blank=True,
        help_text="Parte en cursiva/verde del título, ej: tu celebración"
    )
    descripcion = models.TextField(blank=True)
    imagen_hero = models.ImageField(upload_to='catalogo/secciones/', blank=True, null=True)
    imagen_hero_caption = models.CharField(max_length=150, blank=True)
    nota_pie = models.CharField(
        max_length=300, blank=True,
        help_text="Ej: La carpa tiene costo adicional y está sujeta a disponibilidad."
    )
    orden = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['orden']
        verbose_name = "Sección del catálogo"
        verbose_name_plural = "Secciones del catálogo"

    def __str__(self):
        return f"{self.numero} — {self.titulo}"


class CaracteristicaSeccion(models.Model):
    """Bullets de una sección (ej: 'Jardín con capacidad para hasta 200 personas')."""
    seccion = models.ForeignKey(
        SeccionCatalogo, related_name='caracteristicas', on_delete=models.CASCADE
    )
    texto = models.CharField(max_length=200)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['orden']
        verbose_name = "Característica"
        verbose_name_plural = "Características"

    def __str__(self):
        return self.texto


class TarjetaCatalogo(models.Model):
    """
    Una tarjeta de producto/servicio dentro de una sección
    (ej: Tiffany, Crossback, Vintage dentro de Mobiliario;
    Ka'an Room, Otoch Room dentro de Hospedaje).
    El precio SIEMPRE viene de producto.sugerencia_precio() — nunca se captura a mano aquí.
    """
    seccion = models.ForeignKey(
        SeccionCatalogo, related_name='tarjetas', on_delete=models.CASCADE
    )
    producto = models.ForeignKey(
        Producto, null=True, blank=True, on_delete=models.PROTECT,
        related_name='tarjetas_catalogo',
        help_text="Selecciona el producto real del ERP. El precio se toma de aquí, "
                   "nunca se escribe a mano.",
    )
    titulo = models.CharField(
        max_length=100,
        help_text="Si hay producto vinculado y este campo está vacío, se usa producto.nombre",
        blank=True,
    )
    subtitulo = models.CharField(max_length=150, blank=True)
    descripcion = models.TextField(blank=True)
    imagen = models.ImageField(upload_to='catalogo/tarjetas/', blank=True, null=True)
    badge_texto = models.CharField(
        max_length=60, blank=True,
        help_text="Ej: SET PARA 10 INVITADOS"
    )
    mostrar_precio = models.BooleanField(default=True)
    unidad_precio = models.CharField(
        max_length=30, blank=True,
        help_text="Ej: /noche, /persona — se muestra junto al precio. "
                   "El precio ya incluye IVA, no escribas '+IVA' aquí."
    )
    orden = models.PositiveIntegerField(default=0)
    activa = models.BooleanField(default=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['orden']
        verbose_name = "Tarjeta del catálogo"
        verbose_name_plural = "Tarjetas del catálogo"

    def get_titulo(self):
        if self.titulo:
            return self.titulo
        if self.producto:
            return self.producto.nombre
        return "(sin título)"
    get_titulo.short_description = "Título"

    def get_precio(self):
        """Precio con IVA incluido a mostrar, o None si no aplica.
        Consistente con el precio final que el cliente ve en el cotizador."""
        if not self.mostrar_precio or not self.producto:
            return None
        return _con_iva(self.producto.sugerencia_precio())

    def __str__(self):
        return f"{self.seccion.slug} · {self.get_titulo()}"


class PaqueteCatalogo(models.Model):
    """
    Paquete completo (mobiliario + cristalería). Dos casos de uso:

    1. Paquete propio de la Quinta (ej. "Paquete Taquiza 50 Personas"): liga
       `producto` a un Producto real (es_paquete=True) del ERP. El precio se
       lee en vivo de producto.sugerencia_precio() — se actualiza solo si
       cambias el precio en Productos.
    2. Paquete de proveedor externo sin Producto propio (ej. los de David
       Vera Banquetes): deja `producto` vacío y captura `precio_venta_fijo`
       a mano.

    Si ambos están definidos, gana `producto` (una sola fuente de verdad).
    """
    nombre = models.CharField(max_length=100)
    producto = models.ForeignKey(
        Producto, null=True, blank=True, on_delete=models.PROTECT,
        related_name='paquetes_catalogo', limit_choices_to={'es_paquete': True},
        help_text="Si ligas un Producto (paquete) del ERP, el precio se toma "
                   "de ahí y se actualiza solo. Déjalo vacío para paquetes de "
                   "proveedor externo con precio_venta_fijo manual.",
    )
    imagen = models.ImageField(upload_to='catalogo/paquetes/', blank=True, null=True)
    proveedor = models.CharField(
        max_length=100, blank=True,
        help_text="Ej: David Vera Banquetes — se muestra en el crédito al pie"
    )
    precio_venta_fijo = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Solo se usa si no hay Producto ligado. Vacío = 'Precio por confirmar'."
    )
    orden = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['orden']
        verbose_name = "Paquete completo"
        verbose_name_plural = "Paquetes completos"

    def get_precio(self):
        """Precio con IVA incluido si viene de un Producto ligado (misma
        regla que TarjetaCatalogo). Si es precio_venta_fijo manual (paquete
        de proveedor externo), se muestra tal cual — se asume que ya es el
        precio final que se cobra al cliente."""
        if self.producto:
            return _con_iva(self.producto.sugerencia_precio())
        return self.precio_venta_fijo

    def __str__(self):
        return self.nombre


class ItemPaqueteCatalogo(models.Model):
    GRUPO_CHOICES = [
        ('MOBILIARIO', 'Mobiliario'),
        ('CRISTALERIA', 'Cristalería'),
        ('CRISTALERIA_PREMIUM', 'Cristalería premium'),
    ]
    paquete = models.ForeignKey(
        PaqueteCatalogo, related_name='items', on_delete=models.CASCADE
    )
    grupo = models.CharField(max_length=25, choices=GRUPO_CHOICES)
    texto = models.CharField(max_length=200)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['grupo', 'orden']
        verbose_name = "Item de paquete"
        verbose_name_plural = "Items de paquete"

    def __str__(self):
        return f"{self.paquete.nombre} · {self.texto}"


class DescuentoTarjeta(models.Model):
    """
    Descuento con vigencia por fecha sobre una tarjeta específica.
    Ej: Eventos $4,400 -> $2,999 válido solo reservando en octubre 2026.
    """
    tarjeta = models.OneToOneField(
        TarjetaCatalogo, related_name='descuento', on_delete=models.CASCADE
    )
    precio_regular = models.DecimalField(max_digits=10, decimal_places=2)
    precio_descuento = models.DecimalField(max_digits=10, decimal_places=2)
    etiqueta_badge = models.CharField(
        max_length=60, default="Oferta",
        help_text="Ej: Oferta · Reserva en octubre"
    )
    nota_vigencia = models.CharField(
        max_length=250,
        help_text="Ej: Precio válido para eventos apartados durante octubre 2026. "
                   "A partir de noviembre 2026: $4,400.00 +IVA."
    )
    vigente_desde = models.DateField(null=True, blank=True)
    vigente_hasta = models.DateField(null=True, blank=True)
    activo = models.BooleanField(default=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Descuento de tarjeta"
        verbose_name_plural = "Descuentos de tarjeta"

    def clean(self):
        if self.precio_descuento >= self.precio_regular:
            raise ValidationError({
                'precio_descuento': "El precio de descuento debe ser menor al precio regular."
            })
        if self.vigente_desde and self.vigente_hasta and self.vigente_desde > self.vigente_hasta:
            raise ValidationError({
                'vigente_hasta': "La fecha de fin no puede ser anterior a la de inicio."
            })

    def esta_vigente(self):
        if not self.activo:
            return False
        hoy = timezone.now().date()
        if self.vigente_desde and hoy < self.vigente_desde:
            return False
        if self.vigente_hasta and hoy > self.vigente_hasta:
            return False
        return True

    def __str__(self):
        return f"{self.tarjeta} · ${self.precio_regular} → ${self.precio_descuento}"
