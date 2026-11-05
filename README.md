# Panel legislativo

Panel para proyectar en una TV del despacho del Dip. Leconte (HCD Corrientes). Pasa solo de una sección a otra cada 20 segundos.

## Cómo se usa

Abrir la página en el navegador de la TV y ponerla en pantalla completa.

- `?t=30` cambia los segundos por pantalla.
- `?s=mapa` deja fija una sección (`proyectos`, `od`, `comisiones`, `congreso`, `bo`, `boctes`, `presidente`, `noticias`, `mapa`).
- Con teclado o control remoto, las flechas izquierda y derecha pasan de pantalla.

## Cómo se actualiza

GitHub Actions corre `scripts/actualizar.py` cada 30 minutos y guarda el resultado en `data/live.json`. La página relee los datos cada 10 minutos. No hace falta ningún servidor ni pagar nada.

Para forzar una actualización: pestaña **Actions** → "Actualizar datos del panel" → **Run workflow**.

## Qué se edita a mano

`data/base.json` tiene los datos que no salen de una página web:

- `proyectos`: los expedientes de Leconte con su tema y estado. El robot avisa en la TV cuando cambia la ficha de alguno en el sitio de la Cámara, y suma solo los expedientes nuevos que aparezcan a su nombre.
- `diputados` y `partidos`: el mapa de la Cámara (orden de izquierda a derecha y color de cada partido).
- `comisiones`: días y horarios de las comisiones permanentes.

## Fuentes

| Sección | Fuente |
|---|---|
| Proyectos de Leconte | Fichas de expedientes de hcdcorrientes.gov.ar |
| Orden del día | PDF del último orden del día de la HCD |
| Comisiones y sesiones | Tabla fija + órdenes del día de la HCD y del Senado de Corrientes |
| Congreso | Agenda del Senado de la Nación y agenda de comisiones de Diputados |
| Boletines oficiales | boletinoficial.gob.ar y boletinoficial.corrientes.gob.ar; si no responden, noticias sobre normas publicadas |
| Presidente | Noticias sobre sus publicaciones en X y discursos de Casa Rosada |
| Noticias | Google News (Corrientes) |
| Dólar y riesgo país | dolarapi.com y argentinadatos.com |
