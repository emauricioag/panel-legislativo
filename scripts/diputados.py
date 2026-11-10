"""Arma data/diputados.json: asistencias y proyectos de cada diputado en el período.

Lo corre GitHub Actions junto con actualizar.py, pero trabaja como mucho una vez
cada 12 horas. Guarda en el mismo archivo lo ya leído (portadas de las versiones
taquigráficas y fichas de expedientes), así cada corrida solo baja lo nuevo.
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from actualizar import AR, BASE, HCD, RAIZ, get, limpio, pdf_a_texto, texto  # noqa: E402

SALIDA = os.path.join(RAIZ, "data", "diputados.json")
CACHE = os.path.join(RAIZ, "data", "cache-diputados.json")   # lo ya leído; la página no lo usa
INICIO = "2025-12-10"   # asunción de la composición actual
CADA_HORAS = 12
MAX_FICHAS = 200        # fichas nuevas o por revisar en cada corrida


def plano(s):
    s = unicodedata.normalize("NFD", s or "")
    return re.sub(r"[^a-z ]", " ", "".join(c for c in s if unicodedata.category(c) != "Mn").lower()).strip()


def iso(dmy):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", dmy or "")
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else ""


def asistencia(cache):
    """Portada de cada versión taquigráfica: presentes y ausentes."""
    s = texto(HCD + "/sesiones/versiones-taquigraficas/")
    for url, rotulo in re.findall(r"href='([^']+\.pdf)'[^>]*class='item-lista'\s*>([^<]+)", s):
        fecha = iso(rotulo)
        if fecha < INICIO or url in cache:
            continue
        try:
            t = pdf_a_texto(urllib.parse.quote(url, safe=":/%"))
        except Exception as e:  # noqa: BLE001
            print("VT", rotulo, e)
            continue
        cab = t[: t.find("SUMARIO")] if "SUMARIO" in t[:6000] else t[:5000]
        reg = {"fecha": fecha, "rotulo": rotulo.strip(), "presentes": [], "ausentes": []}
        modo = None
        for linea in cab.splitlines():
            l = linea.strip()
            if "DIPUTADOS PRESENTES" in l:
                modo = "presentes"
            elif "DIPUTADOS AUSENTES" in l or "DIPUTADOS CON LICENCIA" in l:
                modo = "ausentes"
            elif modo:
                m = re.search(r"([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ ]+),\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ.]+)", l)
                if m:
                    reg[modo].append(plano(m.group(1)) + "," + plano(m.group(2)))
        if len(reg["presentes"]) >= 10:
            cache[url] = reg
    return cache


def numeros_del_periodo():
    """Números de expediente cargados desde el inicio del período (feed general, de a 10)."""
    out = []
    for pag in range(1, 400):
        try:
            raiz = ET.fromstring(get(f"{HCD}/feed/?post_type=expedientes&paged={pag}"))
        except Exception:  # noqa: BLE001
            break
        items = list(raiz.iter("item"))
        if not items:
            break
        viejo = False
        for it in items:
            n = re.match(r"\d+", (it.findtext("title") or "").strip())
            try:
                f = dt.datetime.strptime((it.findtext("pubDate") or "")[5:16], "%d %b %Y").date().isoformat()
            except ValueError:
                f = INICIO
            if f < "2025-11-20":
                viejo = True
            elif n:
                out.append(n.group(0))
        if viejo:
            break
    return out


def ficha(ex):
    t = limpio(texto(f"{HCD}/expedientes/{ex}/"))
    def campo(patron):
        m = re.search(patron, t)
        return m.group(1).strip() if m else ""
    com = campo(r"Comisi[oó]n:\s*(.*?)\s*Expediente completo").rstrip(".")
    asunto = campo(r"Asunto:\s*(.*?)\s*Autor/es:")
    return {"tipo": campo(r"Tipo de Proyecto:\s*([\wáéíóúñ]+)"), "autores": campo(r"Autor/es:\s*(.*?)\s*Bloque:"),
            "ingreso": iso(campo(r"Ingreso H\.C\.D\.:\s*([\d/]+)")), "comision": com[:160], "asunto": asunto[:220],
            "aprobado": bool(re.search(r"Aprobaci[oó]n:|Sanci[oó]n", com + " " + asunto, re.I)),
            "visto": dt.datetime.now(AR).date().isoformat()}


def main():
    try:
        datos = json.load(open(SALIDA, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        datos = {}
    ahora = dt.datetime.now(AR)
    if datos.get("actualizado") and "--ya" not in sys.argv:
        if (ahora - dt.datetime.fromisoformat(datos["actualizado"])).total_seconds() < CADA_HORAS * 3600:
            print("diputados.json: todavía no toca.")
            return
    base = json.load(open(BASE, encoding="utf-8"))
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cache = {}
    vts = cache.get("vt", {})
    fichas = cache.get("fichas", {})
    try:
        asistencia(vts)
    except Exception as e:  # noqa: BLE001
        print("asistencia:", e)
    try:
        nums = numeros_del_periodo()
    except Exception as e:  # noqa: BLE001
        print("feed de expedientes:", e)
        nums = []
    # primero las fichas nuevas; después, las no aprobadas que hace más tiempo no se miran
    nuevas = [n for n in dict.fromkeys(nums) if n not in fichas]
    viejas = sorted((n for n, f in fichas.items() if not f["aprobado"]), key=lambda n: fichas[n]["visto"])
    tanda = (nuevas + viejas)[:MAX_FICHAS]
    with cf.ThreadPoolExecutor(6) as ex:
        for n, r in zip(tanda, ex.map(lambda n: _seguro(n), tanda)):
            if r:
                fichas[n] = r

    sesiones = sorted(vts.values(), key=lambda v: v["fecha"])
    dips = {}
    apellidos = [plano(d["apellido"]) for d in base["diputados"]]
    for d in base["diputados"]:
        ap, nom = plano(d["apellido"]), plano(d["nombre"]).split()[0]
        repetido = apellidos.count(ap) > 1 or sum(a.split()[0] == ap.split()[0] for a in apellidos) > 1

        def es(marca):
            a, n = marca.split(",")
            return (a == ap or a.startswith(ap + " ") or ap.startswith(a + " ")) and (not repetido or n.startswith(nom[:3]))
        pres = sum(any(es(m) for m in s["presentes"]) for s in sesiones)
        aus = [s["fecha"] for s in sesiones if any(es(m) for m in s["ausentes"])]

        def autor(f):
            a = plano(f["autores"])
            if not re.search(r"\b" + re.escape(ap.split()[0]) + r"\b", a):
                return False
            if not repetido:
                return True
            # apellidos repetidos: la ficha aclara con la inicial o el nombre ("Benítez (E.)", "Benítez, Hugo")
            m = re.search(re.escape(ap.split()[0]) + r"\s+(\w+)", a)
            return bool(m) and nom.startswith(m.group(1)[:1]) and (len(m.group(1)) == 1 or nom.startswith(m.group(1)[:3]))
        prop = {n: f for n, f in fichas.items() if f["ingreso"] >= INICIO and autor(f)}
        tipos = {}
        for f in prop.values():
            tipos[f["tipo"] or "Otro"] = tipos.get(f["tipo"] or "Otro", 0) + 1
        ult = sorted(prop, key=lambda n: (prop[n]["ingreso"], n), reverse=True)[:4]
        dips[d["apellido"] + "|" + d["nombre"]] = {
            "presentes": pres, "ausencias": len(aus), "ultimas_ausencias": aus[-3:],
            "presentados": len(prop), "aprobados": sum(f["aprobado"] for f in prop.values()),
            "en_comision": sum(1 for f in prop.values() if not f["aprobado"]),
            "solo": sum(1 for f in prop.values() if not re.search(r"\by\b|,|dips", f["autores"], re.I)),
            "tipos": tipos,
            "ultimos": [{"expte": n, "tipo": prop[n]["tipo"], "asunto": prop[n]["asunto"], "aprobado": prop[n]["aprobado"]} for n in ult],
        }
    salida = {"actualizado": ahora.isoformat(timespec="minutes"), "desde": INICIO,
              "sesiones": len(sesiones), "ultima_sesion": sesiones[-1]["fecha"] if sesiones else "",
              "expedientes": sum(1 for f in fichas.values() if f["ingreso"] >= INICIO),
              "diputados": dips}
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=1)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"vt": vts, "fichas": fichas}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"diputados.json: {len(sesiones)} sesiones, {len(fichas)} fichas ({len(tanda)} leídas ahora).")


def _seguro(n):
    try:
        return ficha(n)
    except Exception as e:  # noqa: BLE001
        print("ficha", n, e)
        return None


if __name__ == "__main__":
    main()
