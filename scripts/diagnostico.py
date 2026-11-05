"""Diagnóstico temporal: muestra en el log los enlaces de los sitios del BO de Corrientes."""
import re, sys
sys.path.insert(0, "scripts")
import actualizar as a
for url in ["https://boletinoficial.corrientes.gob.ar/", "https://www.corrientes.gob.ar/boletinoficial"]:
    try:
        s = a.texto(url)
        print("==", url, len(s))
        for h, t in re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', s, re.S)[:120]:
            print("  ", h[:120], "|", a.limpio(t)[:80])
    except Exception as e:
        print("ERR", url, e)
