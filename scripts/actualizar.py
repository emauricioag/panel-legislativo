"""Actualiza data/live.json con las fuentes del panel.

Lo corre GitHub Actions cada 30 minutos. Cada fuente va en su propio
try/except: si una falla, se conserva el último dato bueno y el resto sigue.
Solo usa la biblioteca estándar de Python y pdftotext (poppler-utils).
"""
import datetime as dt
import html
import json
import os
import re
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(RAIZ, "data", "live.json")
BASE = os.path.join(RAIZ, "data", "base.json")
AR = dt.timezone(dt.timedelta(hours=-3))
HOY = dt.datetime.now(AR)
UA = "Mozilla/5.0"  # un UA de navegador completo dispara el desafío anti-bots del Senado
HCD = "https://hcdcorrientes.gov.ar"


_CTX = {}


def _contexto_completo(host):
    """Algunos sitios oficiales no envían el certificado intermedio. Se lo baja
    de la dirección que indica el propio certificado (AIA) y se agrega a los
    certificados de confianza del sistema: la verificación sigue siendo completa."""
    if host in _CTX:
        return _CTX[host]
    hoja = ssl.get_server_certificate((host, 443))
    texto_cert = subprocess.run(["openssl", "x509", "-noout", "-text"], input=hoja,
                                capture_output=True, text=True).stdout
    m = re.search(r"CA Issuers - URI:(\S+)", texto_cert)
    if not m:
        raise ValueError("el certificado no indica dónde bajar el intermedio")
    der = urllib.request.urlopen(m.group(1), timeout=30).read()
    try:
        pem = ssl.DER_cert_to_PEM_cert(der)
    except Exception:  # noqa: BLE001  (ya venía en PEM)
        pem = der.decode()
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=pem)
    _CTX[host] = ctx
    return ctx


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-AR,es"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        ctx = _contexto_completo(urllib.parse.urlparse(url).hostname)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()


def texto(url):
    b = get(url)
    for enc in ("utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "ignore")


def limpio(s):
    s = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]+")


def sin_emojis(t):
    return re.sub(r"\s{2,}", " ", EMOJI.sub("", t)).strip()


def rss(url, n=12):
    raiz = ET.fromstring(get(url))
    items = []
    for it in raiz.iter("item"):
        t = (it.findtext("title") or "").strip()
        fuente = it.findtext("source") or ""
        if fuente and t.endswith(" - " + fuente):
            t = t[: -len(fuente) - 3]
        items.append({
            "titulo": sin_emojis(html.unescape(t)),
            "link": (it.findtext("link") or "").strip(),
            "fecha": (it.findtext("pubDate") or "").strip(),
            "fuente": fuente.strip(),
        })
        if len(items) >= n:
            break
    return items


OFICIAL = re.compile(r"oficializ|bolet[ií]n oficial|decreto|resoluci[oó]n|publica", re.I)


def gnews(q, n=12):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "es-419", "gl": "AR", "ceid": "AR:es-419"})
    return rss(url, n)


def pdf_a_texto(url):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.pdf")
        with open(p, "wb") as f:
            f.write(get(url, 60))
        return subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True,
                              text=True, timeout=60).stdout


# ---------------------------------------------------------------- fuentes

def dolar():
    datos = json.loads(get("https://dolarapi.com/v1/dolares"))
    return [{"casa": d["nombre"], "compra": d.get("compra"), "venta": d.get("venta")}
            for d in datos if d.get("casa") in ("oficial", "blue", "bolsa", "contadoconliqui", "mayorista")]


def riesgo_pais():
    d = json.loads(get("https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais/ultimo"))
    return {"valor": d["valor"], "fecha": d["fecha"]}


def bo_nacional():
    s = texto("https://www.boletinoficial.gob.ar/seccion/primera")
    avisos = []
    for m in re.finditer(r'href="(/detalleAviso/primera/[^"]+)"[^>]*>(.*?)</a>', s, re.S):
        partes = [limpio(x) for x in re.findall(r"<p[^>]*>(.*?)</p>", m.group(2), re.S)]
        partes = [p for p in partes if p]
        if not partes:
            continue
        avisos.append({"organismo": partes[0], "detalle": " ".join(partes[1:])[:220],
                       "link": "https://www.boletinoficial.gob.ar" + m.group(1)})
    if not avisos:
        raise ValueError("sin avisos en la página")
    return {"modo": "directo", "items": avisos[:40]}


def bo_nacional_respaldo():
    return {"modo": "noticias", "items": [
        {"organismo": i["fuente"], "detalle": i["titulo"], "link": i["link"]}
        for i in gnews('"Boletín Oficial" when:2d', 30) if OFICIAL.search(i["titulo"])][:15]}


def bo_corrientes():
    """Últimas ediciones del BO de Corrientes (www.corrientes.gob.ar/boletinoficial) y sus PDF."""
    raiz = "https://www.corrientes.gob.ar"
    s = texto(raiz + "/boletinoficial")
    ediciones = re.findall(r'href="(/\d+/boletin-oficial-[\d-]+)"[^>]*>(.*?)</a>', s, re.S)
    if not ediciones:
        raise ValueError("sin ediciones en la página")
    items = []
    for n, (href, titulo) in enumerate(ediciones[:6]):
        archivos = []
        if n < 2:  # el detalle de los PDF solo para las dos últimas ediciones
            try:
                e = texto(raiz + href)
                archivos = [a for a in re.findall(r"([^<>]{3,120}?\.pdf)\s*<", e) if "Descargar" not in a]
                archivos = [limpio(a) for a in archivos]
            except Exception as ex:  # noqa: BLE001
                print("edición", href, ex)
        items.append({"organismo": limpio(titulo), "link": raiz + href,
                      "detalle": ("Archivos: " + ", ".join(dict.fromkeys(archivos))) if archivos
                      else "Edición publicada"})
    return {"modo": "directo", "items": items}


def bo_corrientes_respaldo():
    return {"modo": "noticias", "items": [
        {"organismo": i["fuente"], "detalle": i["titulo"], "link": i["link"]}
        for i in gnews('Corrientes ("Boletín Oficial" OR decreto OR oficializó) when:7d', 40)
        if OFICIAL.search(i["titulo"]) and re.search(r"Corrientes|correntin|Valdés", i["titulo"] + i["fuente"])][:15]}


def senado_nacion():
    s = texto("https://www.senado.gob.ar/parlamentario/Agenda/AgendaWeb/"
              + HOY.strftime("%d,%m,%Y"))
    eventos = []
    for fecha, cuerpo in re.findall(r"eventDates\[ new Date\( '(\d{4}/\d\d/\d\d)' \)\] = '(.*?)';", s):
        f = dt.date(*map(int, fecha.split("/")))
        if not (HOY.date() <= f <= HOY.date() + dt.timedelta(days=14)):
            continue
        for e in cuerpo.split("\\n"):
            e = html.unescape(e.strip(" -"))
            if e and not re.search(r"visita", e, re.I):
                eventos.append({"fecha": f.isoformat(), "evento": e})
    eventos.sort(key=lambda x: x["fecha"])
    return eventos[:30]


def diputados_nacion():
    s = texto("https://www.hcdn.gob.ar/comisiones/agenda/")
    filas = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
        celdas = [limpio(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        celdas = [c for c in celdas if c]
        if celdas and not re.match(r"(?i)fecha|d[ií]a|hora", celdas[0]):
            filas.append(" · ".join(celdas)[:260])
    if not filas and "No hay reuniones" in s:
        return {"mensaje": "Sin reuniones de comisión programadas por el momento.", "items": []}
    return {"mensaje": "", "items": filas[:25]}


def hcd_ordenes():
    """Último orden del día de la HCD Corrientes, con sus asuntos."""
    s = texto(HCD + "/sesiones/orden-del-dia/")
    lista = re.findall(r"href='([^']+\.pdf)'[^>]*class='item-lista'\s*>([^<]+)", s)
    url, rotulo = lista[0]
    t = pdf_a_texto(url)
    asuntos = []
    seccion = ""
    bloque = []

    def cerrar():
        if not bloque:
            return
        txt = re.sub(r"\s+", " ", " ".join(bloque)).replace("\u200b", "").strip()
        m = re.match(r"([a-z]\.\d+)\.?\s*(.*)", txt)
        if not m:
            return
        cuerpo = m.group(2)
        ex = re.search(r"Expte\.?\s*(\d[\d.]*)", cuerpo)
        autores = ""
        ma = re.search(r"\s(Dips?\.\s.*)$", cuerpo)
        if ma:
            autores = ma.group(1)
            cuerpo = cuerpo[: ma.start()]
        nota = re.match(r"Notas?\s([\d.]+)", cuerpo)
        cuerpo = re.sub(r"^(Expte\.?\s*[\d.]+[^:]*:|Notas?\s[\d, y]+:)\s*", "", cuerpo)
        asuntos.append({"item": m.group(1), "seccion": seccion,
                        "expte": ex.group(1).replace(".", "") if ex else "",
                        "nota": nota.group(1).replace(".", "") if nota else "",
                        "texto": cuerpo[:300], "autores": autores,
                        "leconte": "Leconte" in txt})
        bloque.clear()

    for linea in t.splitlines():
        l = linea.strip().replace("\u200b", "")
        if not l or re.fullmatch(r"\d+", l):
            continue
        ms = re.match(r"^([a-z])\)\s+(.+?):?$", l)
        if ms and ms.group(2).isupper():
            cerrar()
            seccion = ms.group(2).rstrip(":").capitalize()
            continue
        if re.match(r"^[a-z]\.\d+\.", l):
            cerrar()
            bloque.append(l)
        elif bloque:
            if l.isupper() and len(l) < 40:
                cerrar()
                continue
            bloque.append(l)
    cerrar()
    m = re.search(r"(\d+ª\s+SESI[ÓO]N\s+\w+).*?\n\s*(\d{1,2} de \w+ de \d{4}[^\n]*)", t, re.S)
    titulo = (m.group(1).title() + " del " + m.group(2).strip()) if m else rotulo.strip()
    s2 = texto(HCD + "/sesiones/informe-del-orden-del-dia/")
    inf = re.findall(r"href='([^']+\.pdf)'[^>]*class='item-lista'\s*>([^<]+)", s2)
    return {"titulo": titulo, "rotulo": rotulo.strip(), "pdf": url,
            "informe": inf[0][0] if inf else "", "asuntos": asuntos,
            "anteriores": [{"rotulo": r.strip(), "pdf": u} for u, r in lista[1:4]]}


def hcd_noticias():
    return rss(HCD + "/feed/", 8)


def senado_corrientes():
    s = texto("https://senadoctes.gov.ar/ordenes.php")
    ods = re.findall(r'href="(ordenes/\d{4}/(\d\d)-(\d\d)-(\d{4})\.pdf)"[^>]*>\s*(\d+)', s)
    out = []
    for href, d, m, a, n in ods[:3]:
        out.append({"numero": n, "fecha": f"{a}-{m}-{d}",
                    "pdf": "https://senadoctes.gov.ar/" + href})
    return out


def expedientes_leconte(base, previo):
    """Revisa la ficha de cada expediente y marca los que cambiaron."""
    nuevos = []
    try:
        for i in rss(HCD + "/?autor-res=Leconte&post_type=expedientes&feed=rss2", 20):
            n = re.match(r"\d+", i["titulo"])
            if n:
                nuevos.append(n.group(0))
    except Exception as e:  # noqa: BLE001
        print("feed Leconte:", e)
    conocidos = [p["expte"] for p in base["proyectos"]]
    lista = conocidos + [n for n in nuevos if n not in conocidos]
    estado = {}
    for ex in lista:
        ant = previo.get(ex, {})
        try:
            t = limpio(texto(f"{HCD}/expedientes/{ex}/"))
            asunto = re.search(r"Asunto:\s*(.*?)\s*Autor", t)
            comision = re.search(r"Comisi[oó]n:\s*(.*?)\s*Expediente completo", t)
            tipo = re.search(r"Tipo de Proyecto:\s*(\w+)", t)
            reg = {
                "asunto": asunto.group(1) if asunto else "",
                "comision": comision.group(1).rstrip(".") if comision else "",
                "tipo": tipo.group(1) if tipo else "",
            }
        except Exception as e:  # noqa: BLE001
            print("expediente", ex, e)
            if ant:
                estado[ex] = ant
            continue
        reg["nuevo"] = ex not in conocidos
        reg["visto"] = ant.get("visto", HOY.date().isoformat())
        if ant and (ant.get("asunto"), ant.get("comision")) != (reg["asunto"], reg["comision"]):
            reg["cambio"] = HOY.date().isoformat()
            reg["antes"] = ant.get("asunto", "")
        else:
            reg["cambio"] = ant.get("cambio", "")
            reg["antes"] = ant.get("antes", "")
        estado[ex] = reg
    return estado


def noticias_corrientes():
    vistos, out = set(), []
    for q in ["Corrientes provincia when:1d", "Corrientes Valdés OR Legislatura OR diputados when:2d"]:
        for i in gnews(q, 15):
            clave = i["titulo"].lower()[:60]
            if clave not in vistos:
                vistos.add(clave)
                out.append(i)
    return out[:20]


def noticias_nacionales():
    """Titulares nacionales: portada de Google Noticias Argentina (sección Nacional)."""
    url = "https://news.google.com/rss/headlines/section/topic/NATION?hl=es-419&gl=AR&ceid=AR:es-419"
    vistos, out = set(), []
    for i in rss(url, 30):
        clave = i["titulo"].lower()[:60]
        if clave not in vistos:
            vistos.add(clave)
            out.append(i)
    return out[:16]


def medios_afines():
    """La Derecha Diario (solo política y economía) y los últimos programas de Carajo en YouTube."""
    ldd = [dict(i, fuente="La Derecha Diario") for i in rss("https://derechadiario.com.ar/rss", 60)
           if re.search(r"derechadiario\.com\.ar/(politica|economia)/", i["link"])][:8]
    ns = {"a": "http://www.w3.org/2005/Atom"}
    raiz = ET.fromstring(get("https://www.youtube.com/feeds/videos.xml?channel_id=UC4mdhKZXjrKoq5aVG6juHEg"))
    carajo = []
    for e in raiz.findall("a:entry", ns)[:6]:
        link = e.find("a:link", ns)
        carajo.append({"titulo": sin_emojis(e.findtext("a:title", "", ns) or ""),
                       "link": link.get("href") if link is not None else "",
                       "fecha": e.findtext("a:published", "", ns), "fuente": "Carajo"})
    return {"derecha_diario": ldd, "carajo": carajo}


def finanzas_argy():
    """Últimos mensajes del canal público de Telegram de Finanzas Argy (t.me/s/ se lee sin cuenta)."""
    s = texto("https://t.me/s/FinanzasArgy")
    out = []
    for b in s.split('<div class="tgme_widget_message_wrap')[1:]:
        num = re.search(r'data-post="[^"/]+/(\d+)"', b)
        cuando = re.search(r'<time datetime="([^"]+)"', b)
        cuerpo = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        if not (num and cuando and cuerpo):
            continue  # fotos o videos sin texto
        t = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", cuerpo.group(1))))
        renglones = [re.sub(r"\s+([:.,;%])", r"\1", sin_emojis(x)).strip(" -|") for x in t.split("\n")]
        renglones = [r for r in renglones if r]
        if not renglones:
            continue
        # Si el primer renglón es un rótulo corto ("Jornada financiera"), se le suman los que siguen.
        titulo = renglones[0]
        if len(titulo) < 40:
            for r in renglones[1:]:
                if len(titulo) + len(r) > 230:
                    break
                titulo += (": " if titulo == renglones[0] and not titulo.endswith((":", ".")) else " · ") + r
        if len(titulo) > 280:
            titulo = titulo[:277].rsplit(" ", 1)[0] + "…"
        out.append({"titulo": titulo, "fecha": cuando.group(1), "fuente": "Finanzas Argy",
                    "link": f"https://t.me/FinanzasArgy/{num.group(1)}"})
    if not out:
        raise ValueError("el canal no trajo mensajes con texto")
    return sorted(out, key=lambda i: i["fecha"], reverse=True)[:12]


def mercado():
    """Riesgo país (y el Merval cuando figura) del último resumen "Jornada financiera" del canal de
    Finanzas Argy. La portada de finanzasargy.com tiene el dato completo pero rechaza a GitHub (403)."""
    s = texto("https://t.me/s/FinanzasArgy")
    for b in reversed(s.split('<div class="tgme_widget_message_wrap')[1:]):  # del más nuevo al más viejo
        cuerpo = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        cuando = re.search(r'<time datetime="([^"]+)"', b)
        if not (cuerpo and cuando):
            continue
        t = sin_emojis(html.unescape(re.sub(r"<[^>]+>", " ", cuerpo.group(1))))
        i = t.lower().find("riesgo pa")
        if "jornada financiera" not in t.lower() or i < 0:
            continue
        tramo = t[i:i + 90]  # "Riesgo País: 852 puntos" o "el riesgo país subió 8,6%, a 925 puntos"
        n = re.search(r"(\d[\d.]{2,5})\s*puntos", tramo)
        if not n:
            continue
        valor = float(n.group(1).replace(".", ""))
        v = re.search(r"(subi|baj|cay|trep|aument|retrocedi|descendi)[oó]\s+(?:un\s+)?(\d+(?:,\d+)?)\s*%", tramo, re.I)
        variacion = float(v.group(2).replace(",", ".")) * (-1 if v and v.group(1).lower() in ("baj", "cay", "retrocedi", "descendi") else 1) if v else None
        if not 100 <= valor <= 5000:
            raise ValueError(f"riesgo país fuera de rango: {valor}")
        fecha = dt.datetime.fromisoformat(cuando.group(1)).astimezone(AR).date().isoformat()
        out = {"riesgo_pais": {"valor": valor, "variacion": variacion, "fecha": fecha}}
        m = re.search(r"merval[^:\d%]{0,15}:\s*([+\-−]?\d+(?:,\d+)?)\s*%", t, re.I)
        if m:
            out["merval"] = {"valor": None, "variacion": float(m.group(1).replace("−", "-").replace(",", ".")), "fecha": fecha}
        return out
    raise ValueError("no hay un resumen de la jornada en los últimos mensajes")


# ---------------------------------------------------------------- main

def main():
    try:
        previo = json.load(open(LIVE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        previo = {}
    base = json.load(open(BASE, encoding="utf-8"))
    live = dict(previo)
    for viejo in ("bo_nacional", "senado_nacion", "diputados_nacion", "presidente"):  # secciones quitadas el 5/11 y el 6/11
        live.pop(viejo, None)
    errores = {}

    def correr(clave, fn, respaldo=None):
        try:
            live[clave] = fn()
            errores.pop(clave, None)
        except Exception as e:  # noqa: BLE001
            print(f"[{clave}] falló: {e}")
            if respaldo:
                try:
                    live[clave] = respaldo()
                    return
                except Exception as e2:  # noqa: BLE001
                    print(f"[{clave}] respaldo falló: {e2}")
            errores[clave] = str(e)[:200]

    correr("dolar", dolar)
    correr("riesgo_pais", riesgo_pais)
    correr("bo_corrientes", bo_corrientes, bo_corrientes_respaldo)
    correr("hcd_od", hcd_ordenes)
    correr("hcd_noticias", hcd_noticias)
    correr("senado_corrientes", senado_corrientes)
    correr("noticias", noticias_corrientes)
    correr("nacionales", noticias_nacionales)
    correr("medios", medios_afines)
    correr("finanzas_argy", finanzas_argy)
    correr("mercado", mercado)
    correr("expedientes", lambda: expedientes_leconte(base, previo.get("expedientes", {})))

    live["errores"] = errores
    sin_hora = {k: v for k, v in live.items() if k != "actualizado"}
    previo_sin_hora = {k: v for k, v in previo.items() if k != "actualizado"}
    live["actualizado"] = HOY.isoformat(timespec="minutes")
    if sin_hora == previo_sin_hora:
        print("Sin cambios.")
        return
    with open(LIVE, "w", encoding="utf-8") as f:
        json.dump(live, f, ensure_ascii=False, indent=1)
    print("live.json actualizado.")


if __name__ == "__main__":
    main()
