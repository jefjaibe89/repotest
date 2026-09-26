# Landing comercial

Página de presentación del producto para clientes potenciales.

`index.html` es un documento HTML autónomo: ábrelo directamente en el navegador
o sírvelo con cualquier servidor estático. No necesita compilación ni
dependencias — las fuentes vienen de Google Fonts y el resto es CSS y JS
inline.

```bash
python -m http.server 8080 --directory landing
```

## Qué hay que cambiar antes de enseñarla

**Los precios son una propuesta, no una decisión.** Están puestos para que
tengas algo concreto sobre lo que opinar. Búscalos con:

```bash
grep -n "data-precio" landing/index.html
```

Aparecen en seis sitios: las tres tarjetas de plan y los tres tramos de
suscripción.

**El correo de contacto** es un marcador: `hola@tu-dominio.com`. Está como
texto seleccionable con un botón de copiar en lugar de un enlace `mailto:`,
porque los enlaces de correo no funcionan de forma fiable dentro de un
artefacto publicado.

**El nombre.** «Fabric Health» es un nombre de trabajo. Aparece en el
`<title>`, en la barra superior y en el pie.

## Las capturas

`img/dashboard.png` e `img/device-detail.png` salen del producto real
corriendo en modo demostración. Cuando el panel cambie, vuelve a generarlas
desde `sdwan_dashboard/docs/` para que la página no enseñe una versión que ya
no existe.

## Contenido

La página se apoya en lo que el producto hace de verdad: los pesos de la
puntuación (40/25/15/12/8), las métricas de túnel y el comportamiento de las
alertas son los que están implementados. Si cambias el producto, revisa que la
página siga contando la verdad.
