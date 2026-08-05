# FRAKON Energy

Samostatná open-source energetická platforma a custom integrace pro Home Assistant.

## Cíl

FRAKON Energy funguje nezávisle na FRAKON OS. FRAKON OS ji později využije jako energetický modul přes Home Assistant entity nebo vlastní lokální API.

## Funkce připravované pro verzi 1.0

- VisionQ ElIoT: živé odečty VT, NT, celkový stav, baterie a poslední aktivita
- trvalá lokální historie VisionQ nezávislá na Home Assistant Recorderu
- ČEZ HDO adaptér s aktuálním tarifem, odpočtem a rozvrhem
- spouštěče automatizací při zapnutí a ukončení nízkého tarifu
- zúčtovací období, měsíční zálohy a počáteční odečty
- výpočet dosavadních nákladů, průběžného rozdílu a odhadu vyúčtování
- doporučená měsíční záloha
- responzivní dashboard jako panel v levém menu Home Assistantu

## Vývojová instalace RC větve

1. V HACS otevřete vlastní repozitáře.
2. Přidejte `https://github.com/Krlesul/frakon-energy` jako typ **Integrace**.
3. Nainstalujte FRAKON Energy z větve nebo vydané RC verze.
4. Restartujte Home Assistant.
5. Otevřete **Nastavení → Zařízení a služby → Přidat integraci → FRAKON Energy**.
6. Přidejte VisionQ a případně samostatně ČEZ HDO.

Po načtení první položky FRAKON Energy se v levém menu zaregistruje panel **FRAKON Energy**.

## Nastavení vyúčtování

U položky VisionQ otevřete **Konfigurovat** a vyplňte:

- datum počátečního odečtu,
- počáteční stav VT v kWh,
- počáteční stav NT v kWh,
- začátek zúčtovacího období,
- očekávané datum vyúčtování,
- měsíční zálohu,
- cenu VT v Kč/kWh,
- cenu NT v Kč/kWh,
- stálé měsíční platby.

Výchozí očekávané datum vyúčtování je 31. ledna a výchozí měsíční záloha 5 000 Kč.

### Důležité rozlišení dat

Pro počáteční nastavení jsou potřeba **kumulativní stavy elektroměru** VT a NT k datu začátku období. Souhrnná spotřeba za období, například „VT 2 022 kWh a NT 1 526 kWh“, je rozdíl mezi dvěma odečty a nelze ji přímo použít jako počáteční stav elektroměru. Lze ji ale použít ke kontrole správnosti výpočtu a predikce.

## HDO

HDO adaptér vyhledá existující zdrojové entity Home Assistantu a vystaví vlastní stabilní entity FRAKON Energy. Obsahuje také událost a nativní device triggers pro:

- nízký tarif byl zapnut,
- nízký tarif byl ukončen.

Tyto spouštěče lze později použít pro hlasové oznámení přes Assist satelit nebo `media_player`.

## Stav projektu

Integrační větev `feat/frakon-energy-1.0-integration` je určena pro ověření RC funkčnosti. Stabilní verze bude vydána až po úspěšném testu instalace, restartu Home Assistantu, živých dat VisionQ, HDO a dashboardového panelu.
