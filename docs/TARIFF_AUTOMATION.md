# Automatické ceníky ve FRAKON Energy

## Cíl

FRAKON Energy má umět sledovat nové ceníky dodavatelů a distributorů, stáhnout je, vytěžit cenové položky, porovnat je s aktuálním ceníkem a nabídnout bezpečné převzetí změn.

## Zásady

- Nikdy nepřepsat aktivní ceník bez ověření.
- Každý import musí uchovat původní dokument, URL, datum stažení a kontrolní součet.
- Extrahované hodnoty musí mít stav `draft`, `verified` nebo `rejected`.
- Automatická aktivace je možná až po ověření a pouze k datu `valid_from`.
- Zpětný přepočet nákladů se provádí pouze z verzovaných ceníků.

## Pipeline

1. **Source watcher**
   - kontrola známých stránek dodavatele/distributora,
   - sledování odkazu, názvu souboru, ETag/Last-Modified a SHA-256,
   - detekce nového nebo změněného PDF.

2. **Document ingestion**
   - stažení PDF,
   - uložení metadat a hashe,
   - extrakce textu a tabulek,
   - OCR pouze jako fallback pro naskenované dokumenty.

3. **Tariff extraction**
   - dodavatel a produkt,
   - distribuční sazba,
   - platnost od/do,
   - cena dodávky VT/NT,
   - distribuce VT/NT,
   - systémové služby,
   - daň z elektřiny,
   - POZE,
   - jistič a další měsíční platby,
   - DPH a jednotky.

4. **Validation**
   - kontrola jednotek Kč/MWh, Kč/kWh a Kč/měsíc,
   - kontrola DPH,
   - kontrola součtů,
   - porovnání proti předchozí verzi,
   - označení nejasných nebo chybějících polí.

5. **Approval**
   - uživatel uvidí zdrojový řádek z PDF vedle vytěžené hodnoty,
   - může hodnotu potvrdit, opravit nebo zamítnout,
   - po schválení se vytvoří verzovaný ceník.

6. **Activation**
   - ceník se automaticky aktivuje v `valid_from`,
   - starý zůstane v historii,
   - náklady se počítají podle ceníku platného v daném okamžiku.

## Režimy

- `manual`: uživatel nahraje PDF.
- `watch`: FRAKON kontroluje nastavený zdroj a upozorní na nový ceník.
- `assisted`: FRAKON vytěží hodnoty, ale vyžaduje potvrzení.
- `trusted`: automatická aktivace pouze pro ověřené strukturované zdroje.

## Bezpečnost

- Přihlašovací údaje se neukládají do logů ani diagnostiky.
- Dokumenty s osobními údaji se nepoužijí jako veřejný zdroj.
- Přednost mají veřejné produktové ceníky bez zákaznických údajů.
- Parser nesmí odhadovat nečitelné hodnoty bez označení nízké jistoty.

## První podporovaný scénář

1. Ruční nahrání aktuálního PDF ceníku.
2. Extrakce položek s potvrzením uživatele.
3. Uložení ceníku s platností.
4. Později sledování veřejné stránky konkrétního dodavatele.
