---
titulo: Expediente y condiciones de pago de una vendedora
capitulo: Nómina y pagos
orden: 1320
roles: [DIRECCION]
---
Antes de que una vendedora trabaje en el ERP hay que dar de alta su expediente, acordar sus condiciones de pago y registrar su acuerdo de confidencialidad.

### 1. Alta del empleado

1. En **Nómina → Empleados**, agrega al empleado con puesto **Ventas** y captura su expediente: nombre legal, identificación, domicilio, contacto de emergencia, seguridad social y datos de pago.
2. CURP, RFC, NSS y CLABE se validan al guardar (la CLABE con su dígito verificador). Si hay un error, el mensaje **no repite el dato**.

:::nota
El permiso para ver el expediente completo es aparte: solo lo ven quienes lo tengan asignado. Sin él, el empleado se ve sin esos datos y sus documentos no se pueden descargar.
:::

### 2. Documentos

En **Nómina → Expedientes** sube, por empleado, la INE (frente y reverso) y el **acuerdo de confidencialidad firmado** (PDF, JPG o PNG de hasta 10 MB). El acuerdo se genera desde el sistema con el texto aprobado por el abogado; si falta algún dato del empleado (nombre legal, domicilio, identificación), no se genera y te dice cuál falta.

### 3. Condiciones de pago

En **Nómina → Condiciones** (solo la Dirección):

1. Crea las condiciones del empleado: porcentajes (como fracción, por ejemplo 0.05 es 5%), escalones, tope y plazo de atribución.
2. Descarga la **propuesta** en PDF y entrégala. Luego usa **Marcar propuesta como entregada**.
3. Cuando la vendedora acepta, usa **Aceptar condiciones**: esa versión sustituye a la anterior y **ya no se puede editar**.

Solo puede haber **una versión aceptada** por empleado. Para cambiar algo, crea una versión nueva.

### 4. Acceso al ERP

- Crea su usuario del ERP y agrégalo al grupo **Vendedor**. Para correr los permisos del grupo, el equipo técnico ejecuta `crear_grupos_permisos`.
- **No se puede ligar el usuario al empleado** si no tiene el acuerdo firmado cargado.
- Da de alta su **Responsable de Ventas** en **Comunicación → Responsables**, con su teléfono de WhatsApp, para que reciba los avisos de cotizaciones, visitas y prospectos. Su número debe contestar «ACEPTO» al mensaje que recibe.
- Captura los **horarios de visita** en **Comunicación → Horarios**.

### Cuando se va

Al capturar la **fecha de baja**, se abre un pendiente para la Dirección para retirar su acceso y su responsable.
