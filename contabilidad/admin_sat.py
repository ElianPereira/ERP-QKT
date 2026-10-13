"""Admin de la descarga masiva del SAT (e.firma, descargas y comprobantes)."""
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.descargas import url_descarga

from . import services_sat as sat
from . import services_sat_ws as ws
from .models import CfdiSAT, CredencialSAT, SolicitudDescargaSAT


class CredencialSATForm(forms.ModelForm):
    archivo_cer = forms.FileField(required=False, label="Certificado (.cer)")
    archivo_key = forms.FileField(required=False, label="Llave privada (.key)")
    password = forms.CharField(required=False, label="Contraseña de la e.firma",
                               widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'off'}))

    class Meta:
        model = CredencialSAT
        fields = ['activa']

    def clean(self):
        datos = super().clean()
        cer, key, password = datos.get('archivo_cer'), datos.get('archivo_key'), datos.get('password')
        nueva = not self.instance.pk
        if nueva and not (cer and key and password):
            raise ValidationError("Sube el .cer, el .key y escribe la contraseña de la e.firma.")
        if cer or key or password:
            if not (cer and key and password):
                raise ValidationError("Para reemplazar la e.firma hacen falta los tres: .cer, .key y contraseña.")
            try:
                fernet = sat._fernet()
                fiel = ws.Fiel(cer.read(), key.read(), password)
            except (ws.FielError, sat.ConfiguracionSATError) as e:
                raise ValidationError(str(e)) from e
            if not fiel.rfc:
                raise ValidationError("El certificado no trae RFC: ¿es realmente una e.firma?")
            if fiel.vencida:
                raise ValidationError(f"La e.firma venció el {fiel.vigente_hasta:%d/%m/%Y}.")
            if self.instance.pk and fiel.rfc != self.instance.rfc:
                raise ValidationError(f"Esta e.firma es de {fiel.rfc}, no de {self.instance.rfc}.")
            if CredencialSAT.objects.filter(rfc=fiel.rfc).exclude(pk=self.instance.pk).exists():
                raise ValidationError(f"Ya existe una e.firma de {fiel.rfc}; ábrela y reemplázala.")
            cer.seek(0)
            key.seek(0)
            i = self.instance
            i.rfc, i.cer, i.vigente_hasta = fiel.rfc, cer.read(), fiel.vigente_hasta
            i.key_cifrada = fernet.encrypt(key.read())
            i.password_cifrada = fernet.encrypt(password.encode())
            i.nombre = next((a.value for a in fiel.cert.subject if a.oid.dotted_string == '2.5.4.3'), '')
        return datos


class SoloDireccion:
    """La e.firma equivale a la firma del contribuyente: solo superusuario."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CredencialSAT)
class CredencialSATAdmin(SoloDireccion, admin.ModelAdmin):
    form = CredencialSATForm
    list_display = ['rfc', 'nombre', 'vigencia_display', 'cubierta_display', 'activa']
    columnas_texto = ('nombre',)
    actions = ['sincronizar_ahora']
    readonly_fields = ['rfc', 'nombre', 'vigente_hasta', 'ultima_fecha_cubierta', 'created_by',
                       'created_at', 'updated_at']

    def get_fields(self, request, obj=None):
        base = ['archivo_cer', 'archivo_key', 'password', 'activa']
        return base if obj is None else ['rfc', 'nombre', 'vigente_hasta', 'ultima_fecha_cubierta', *base,
                                         'created_by', 'created_at', 'updated_at']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Vigencia", ordering="vigente_hasta")
    def vigencia_display(self, obj):
        dias = (obj.vigente_hasta - timezone.localdate()).days
        tono = ui.ERROR if dias <= 0 else ui.ALERTA if dias <= 60 else ui.EXITO
        return ui.badge(f"{obj.vigente_hasta:%d/%m/%Y}", tono)

    @admin.display(description="Cubierta hasta", ordering="ultima_fecha_cubierta")
    def cubierta_display(self, obj):
        return f"{obj.ultima_fecha_cubierta:%d/%m/%Y}" if obj.ultima_fecha_cubierta else ui.vacio()

    @admin.action(description="Sincronizar con el SAT ahora")
    def sincronizar_ahora(self, request, queryset):
        for cred in queryset.filter(activa=True):
            creadas = sat.planear_sincronizacion(cred)
            messages.info(request, f"{cred.rfc}: {len(creadas)} solicitud(es) nueva(s). El SAT tarda en "
                                   "preparar los paquetes; se descargan solos en la siguiente corrida.")
        sat.procesar_pendientes()


class SolicitudDescargaForm(forms.ModelForm):
    class Meta:
        model = SolicitudDescargaSAT
        fields = ['credencial', 'tipo', 'desde', 'hasta']

    def clean(self):
        datos = super().clean()
        d, h = datos.get('desde'), datos.get('hasta')
        if d and h and (h < d or h >= timezone.localdate() + timedelta(days=1)):
            raise ValidationError("El rango debe ser válido y no incluir fechas futuras.")
        return datos


@admin.register(SolicitudDescargaSAT)
class SolicitudDescargaSATAdmin(admin.ModelAdmin):
    form = SolicitudDescargaForm
    list_display = ['periodo_display', 'tipo', 'estado_display', 'numero_cfdis', 'resultado_display',
                    'mensaje_display', 'created_at']
    columnas_texto = ('mensaje_display',)
    list_filter = [('estado', con_titulo('Estado')), ('tipo', con_titulo('Tipo')), filtro_periodo('created_at', 'Creada')]
    actions = ['avanzar_ahora']
    TONOS = {'DESCARGADA': ui.EXITO, 'ERROR': ui.ERROR, 'SIN_INFORMACION': ui.NEUTRO, 'PENDIENTE': ui.ALERTA}

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return [f.name for f in self.model._meta.fields]

    def get_fields(self, request, obj=None):
        return ['credencial', 'tipo', 'desde', 'hasta'] if obj is None else None

    def save_model(self, request, obj, form, change):
        obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Periodo", ordering="desde")
    def periodo_display(self, obj):
        return f"{obj.desde:%d/%m/%Y} – {obj.hasta:%d/%m/%Y}"

    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS, obj.get_estado_display())

    @admin.display(description="Resultado")
    def resultado_display(self, obj):
        if obj.estado not in ('DESCARGADA', 'TERMINADA'):
            return ui.vacio()
        return (f"{obj.importados} compras · {obj.vinculados} vinculadas · {obj.duplicados} duplicadas · "
                f"{obj.omitidos} omitidas · {obj.errores} con error")

    @admin.display(description="Mensaje del SAT")
    def mensaje_display(self, obj):
        return format_html('{}', obj.mensaje[:120]) if obj.mensaje else ui.vacio()

    @admin.action(description="Avanzar con el SAT ahora")
    def avanzar_ahora(self, request, queryset):
        for s in queryset.exclude(estado__in=SolicitudDescargaSAT.FINALES):
            avanzar_estado = sat.avanzar(s)
            messages.info(request, f"{s}: {dict(SolicitudDescargaSAT.ESTADO_CHOICES)[avanzar_estado]}")


@admin.register(CfdiSAT)
class CfdiSATAdmin(admin.ModelAdmin):
    list_display = ['uuid_display', 'direccion_display', 'fecha', 'contraparte_display', 'total_display',
                    'resultado_display', 'detalle_display', 'xml_display']
    columnas_texto = ('contraparte_display', 'detalle_display')
    list_filter = [('direccion', con_titulo('Dirección')), ('resultado', con_titulo('Resultado')),
                   filtro_periodo('fecha', 'Fecha')]
    search_fields = ['uuid', 'rfc_emisor', 'rfc_receptor', 'nombre_contraparte']
    raw_id_fields = ['solicitud_factura', 'compra']
    actions = ['reintentar_vinculos']
    TONOS = {'IMPORTADO': ui.EXITO, 'VINCULADO': ui.EXITO, 'SIN_VINCULAR': ui.ALERTA, 'OMITIDO': ui.NEUTRO}

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name != 'solicitud_factura']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if 'solicitud_factura' in form.changed_data and obj.solicitud_factura_id and obj.direccion == 'EMITIDO':
            sf = obj.solicitud_factura
            if sf.uuid_factura and sf.uuid_factura != obj.uuid:
                messages.error(request, f"SOL-{sf.pk:04d} ya tiene otra factura ({sf.uuid_factura}).")
                return
            sat.vincular(obj, sf)
            messages.success(request, f"XML ligado a SOL-{sf.pk:04d}; la solicitud quedó FACTURADA.")
            return
        super().save_model(request, obj, form, change)

    @admin.display(description="Folio fiscal", ordering="uuid")
    def uuid_display(self, obj):
        return format_html('<span class="qkt-codigo" title="{}">{}…</span>', obj.uuid, obj.uuid[:8])

    @admin.display(description="Dirección", ordering="direccion")
    def direccion_display(self, obj):
        return ui.badge(obj.get_direccion_display(), ui.INFO, categoria=True)

    @admin.display(description="Contraparte", ordering="nombre_contraparte")
    def contraparte_display(self, obj):
        return obj.nombre_contraparte or ui.vacio()

    @admin.display(description="Total", ordering="total")
    def total_display(self, obj):
        return ui.monto(obj.total)

    @admin.display(description="Resultado", ordering="resultado")
    def resultado_display(self, obj):
        return ui.badge_por_valor(obj.resultado, self.TONOS, obj.get_resultado_display())

    @admin.display(description="Detalle")
    def detalle_display(self, obj):
        return obj.detalle or ui.vacio()

    @admin.display(description="")
    def xml_display(self, obj):
        url = url_descarga(obj, 'archivo_xml')
        return ui.acciones(ui.boton_icono(url, 'file-code', 'XML', nueva_pestana=True) if url else ui.hueco_icono())

    @admin.action(description="Reintentar ligar emitidos con solicitudes de factura")
    def reintentar_vinculos(self, request, queryset):
        messages.info(request, f"{sat.reintentar_vinculos()} comprobante(s) ligado(s).")
