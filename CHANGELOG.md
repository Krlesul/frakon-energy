# Changelog

## 1.0.0-rc.3

Třetí release candidate je zaměřený na reálný commissioning v Home Assistantu, ověřené all-in ceny elektřiny a stabilní živé HDO zobrazení. Současně přechází FRAKON Energy z commitových HACS aktualizací na skutečné verzované release balíčky.

### Přidáno

- Ověřený tarifní workflow pro obchodní i regulovanou část ceny elektřiny s explicitním potvrzením před aktivací.
- Oficiální 2026 D25d regulovaná data včetně přesného párování distribučního území, sazby, jističe a dne platnosti.
- Historie potvrzených all-in tarifů a výpočet vyúčtování podle skutečně platné historické verze ceny.
- Denní all-in náklady ze skutečné denní spotřeby; fixní měsíční platby se neúčtují do denní variabilní ceny.
- Read-only diagnostika zdroje ceny, fingerprintu, autority a použitého dokumentu.
- Explicitní migrace původních ručně zadaných VT/NT cen do historického snapshotu bez přepsání nových potvrzených tarifů.
- All-in cenové scénáře pro flexibilní zátěže, například EV a bojler.
- Samostatný HDO regresní gate proti Home Assistantu pro živý stav, rozvrh, odpočet a přechody přes půlnoc.
- V Nastavení je nová sekce „Zobrazení přehledu“ s trvalými přepínači pro HDO, HDO plán, spotové ceny, denní a měsíční spotřebu, stav baterie VisionQ, odhad vyúčtování, technická měření, technologie domu, fotovoltaiku a energetické toky.
- Nastavení viditelnosti je uložené v Home Assistant config entry options, takže platí shodně na telefonu, tabletu i počítači a přežije restart.

### Opraveno

- Zamrzání načítání reálného ČEZ PDF ceníku: parser má omezené fallback čtení a frontendový watchdog místo nekonečného čekání.
- Opakované překreslování a skákání tarifního průvodce při živých změnách Home Assistant stavů.
- Směrování na správný aktivní VisionQ entry při více integračních entry a reloadu.
- Kompatibilita WebSocket administrátorských handlerů s aktuálním Home Assistant API.
- HDO už nepoužívá vymyšlený náhradní rozvrh. Pokud je strukturovaný rozvrh dostupný, používá jej jako primární autoritu.
- Obnova přesného upstream ČEZ HDO rozvrhu ze správně svázaných sibling entit, včetně ochrany proti napojení FRAKONu zpět na vlastní zrcadlenou entitu.
- Horní HDO karta nyní umí zobrazit čas další změny a odpočet i při dočasně chybějícím normalizovaném `next_switch`, pokud stejné živé ČEZ HDO zařízení poskytuje přesný `LowTariffEnd` nebo `HighTariffEnd`.
- HDO karta už nezobrazuje samotný odpočet, pokud není současně dostupný ověřený čas další změny. Tím nevzniká rozpor typu „Vypnutí NT za …“ a zároveň „Čas další změny není dostupný“.
- Přechody HDO přes půlnoc a výběr správného signálu při více HDO zdrojích.
- Billing základní data a zaplacené zálohy zůstávají viditelné i tehdy, když zatím chybí potvrzená cena.
- Vypnuté moduly už v hlavním přehledu nezabírají prázdné místo; doplňkové karty technologií, FVE a Energy Flow respektují stejné nastavení viditelnosti.

### Distribuce a aktualizace

- HACS nově používá skutečné GitHub Releases místo sedmimístných SHA commitů.
- Verze se zobrazuje jako sémantické číslo, například `1.0.0-rc.3`.
- Každý release musí mít vlastní neprázdnou sekci v tomto changelogu; release bez popisu preflight odmítne.
- HACS instaluje pouze validovaný release artefakt `frakon-energy.zip` a výchozí větev `main` je v nabídce verzí skrytá.
- Release notes v Home Assistantu vycházejí přímo z odpovídající sekce `CHANGELOG.md`.

### Omezení RC

- Stabilní `1.0.0` bude vydána až po dokončení reálného commissioningu: instalace/restart/reload, živé VisionQ a HDO ověření, kontrola dashboardu a řízený end-to-end fyzický start/stop test.
- FRAKON Energy nenahrazuje hardwarové jištění, stykače, proudové chrániče ani další elektrické ochrany.

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
