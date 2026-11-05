"""Diagnóstico temporal: contenido de la última edición del BO de Corrientes."""
import re, sys
sys.path.insert(0, "scripts")
import actualizar as a
s = a.texto("https://www.corrientes.gob.ar/29124/boletin-oficial-04-11-2026")
i = s.find("Boletín Oficial 04-11-2026")
cuerpo = s[s.find("<main") if "<main" in s else 0:]
print("TEXTO:", a.limpio(cuerpo)[:3500])
for h, t in re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', s, re.S):
    if re.search(r"pdf|download|descarg|cdn|archivo", h + t, re.I):
        print("  LINK", h[:160], "|", a.limpio(t)[:80])
