# Changelog

## 1.0.0-rc.2

Druhý release candidate rozšiřuje FRAKON Energy z energetického přehledu na fail-closed řízení zátěží s durable execution lifecycle.

### Přidáno

- Energy Flow a Load Profiles pro řízené spotřebiče.
- Globální ARM interlock: fyzický start je po prvním použití výchozně zakázaný, dokud není explicitně povolen.
- Site Capacity a L1/L2/L3 kapacitní kontroly těsně před fyzickým startem.
- Durable rezervace kapacity a settlement jednotlivých fází.
- Crash/restart recovery pro rozpracované start/stop lifecycle záznamy.
- Occurrence-aware idempotency, aby stará dokončená událost neblokovala nový legitimní zásah.
- One-time WebSocket registration kontrakt pro multi-entry a reload.
- Retry-safe registrace frontend panelu a statických cest.
- Bezpečný unload/reload entry cache s procesově stabilními execution lock registry.
- Commissioning checklist a popis bezpečnostních hranic v README.

### Bezpečnostní principy RC

- Server je autorita; frontend sám neautorizuje fyzickou akci.
- Kritický stav se zapisuje durable před překročením fyzické service-call hranice.
- Neznámý výsledek fyzického příkazu se automaticky neopakuje.
- Kapacita se znovu kontroluje na finální fyzické hranici.
- Reload nesmí vytvořit druhý mutex pro stejný execution entry.

### Omezení RC

- Před běžným fyzickým řízením je stále nutný commissioning na reálném Home Assistantu a ověření konkrétních entit/spotřebičů.
- RC není náhradou elektrických ochran, jističů, stykačů, proudových chráničů ani jiných hardwarových bezpečnostních prvků.

## 1.0.0-rc.1

První instalační kandidát FRAKON Energy.

### Přidáno

- VisionQ ElIoT: živé odečty VT, NT, celkový stav, baterie a poslední aktivita.
- Trvalá lokální historie VisionQ a denní spotřeba.
- ČEZ HDO adaptér s tarifem, rozvrhem, odpočtem a nativními spouštěči automatizací.
- Zúčtovací období, počáteční odečty, měsíční zálohy a nastavitelné ceny VT/NT.
- Výpočet dosavadních nákladů, průběžného rozdílu, predikce vyúčtování a doporučené zálohy.
- Responzivní FRAKON Energy panel v levém menu Home Assistantu.
- Diagnostika bez ukládání hesel a citlivých údajů.
- Backendové a frontendové GitHub Actions kontroly.

### Omezení RC

- Verze vyžaduje ověření instalace a restartu v reálném Home Assistantu.
- Přesnost finanční predikce závisí na správně zadaných kumulativních počátečních stavech elektroměru a cenách.
- Automatické načítání budoucích ceníků z PDF zatím není součástí tohoto RC.
