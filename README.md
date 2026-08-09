# FRAKON Energy

Samostatná open-source energetická platforma a custom integrace pro Home Assistant. FRAKON Energy funguje nezávisle na FRAKON OS; FRAKON OS ji může později využít jako energetický modul přes Home Assistant entity a lokální serverové API.

> **Stav:** release candidate. Části, které mohou fyzicky spínat řízené spotřebiče, jsou záměrně fail-closed a před použitím vyžadují commissioning, explicitní ARM a ověřené zdrojové entity.

## Co dnes FRAKON Energy obsahuje

### Měření, tarify a vyúčtování

- VisionQ ElIoT: živé odečty VT/NT, stav elektroměru, baterie a poslední aktivita,
- lokální historie VisionQ nezávislá na Home Assistant Recorderu,
- ČEZ HDO adaptér s aktuálním tarifem, odpočtem a rozvrhem,
- události a device triggers při zapnutí a ukončení nízkého tarifu,
- zúčtovací období, počáteční odečty, zálohy, ceny VT/NT a stálé platby,
- průběžné náklady, rozdíl proti zálohám, odhad vyúčtování a doporučená záloha.

### Energetický model a dashboard

- autoritativní serverový Energy Flow model,
- nativní Home Assistant senzory pro dům, FVE, síť, baterii a známé podružné spotřeby,
- dashboardový panel FRAKON Energy v levém menu,
- automatické mapování podporovaných zdrojových entit s ručním potvrzením,
- diagnostika kvality dat a fail-closed chování při neúplné nebo zastaralé telemetrii.

### Load Profiles a plánování

- profily řízených spotřebičů s výkonem, dobou běhu a Home Assistant entitou,
- explicitní fázová topologie `unknown`, `single_phase` nebo `three_phase`,
- explicitní proud L1/L2/L3 bez automatického odhadování z celkových kW,
- serverová projekce plánovaného startu proti aktuální kapacitě přípojky,
- pending-run/start/stop schedulery a durable execution lifecycle.

### Execution Safety

Fyzický start je oddělen od plánování a prochází více nezávislými kontrolami:

1. commissioning/readiness,
2. explicitní persistentní **ARM** interlock,
3. bounded dispatch gate,
4. celkový limit přípojky a aktuální import,
5. finální total-capacity recheck přímo na fyzické hranici,
6. L1/L2/L3 phase-capacity gate,
7. finální phase recheck přímo před `turn_on`,
8. durable stop ownership před fyzickým startem,
9. verification a recovery pro neurčitý výsledek service callu.

Souběžné starty používají durable rezervace celkového výkonu i proudů L1/L2/L3, takže druhý start nemůže spotřebovat kapacitu už přidělenou prvnímu ještě před aktualizací elektroměru.

### Phase reservation settlement

Fázová rezervace může být před původním TTL bezpečně uvolněna pouze po důkazním řetězci:

`baseline před startem → verified lifecycle → 1. nový vzorek → min. 5 s → 2. nový vzorek → finální nový proof → durable release`

Když kterýkoli krok chybí nebo selže, rezervace zůstává konzervativně aktivní do svého TTL. Settlement runtime nevolá Home Assistant služby.

## Instalace RC přes HACS

1. V HACS otevřete **Vlastní repozitáře**.
2. Přidejte `https://github.com/Krlesul/frakon-energy` jako typ **Integrace**.
3. Nainstalujte aktuální vydanou RC verzi.
4. Restartujte Home Assistant.
5. Otevřete **Nastavení → Zařízení a služby → Přidat integraci → FRAKON Energy**.
6. Přidejte VisionQ a případně samostatně ČEZ HDO.

Po načtení první položky FRAKON Energy se v levém menu zaregistruje panel **FRAKON Energy**.

## Commissioning před fyzickým řízením

Fyzické spínání nepovolujte pouze proto, že dashboard zobrazuje data. Před prvním ARM ověřte alespoň:

1. správné Home Assistant entity pro řízené spotřebiče,
2. správnou desired-state/action konfiguraci každého Load Profile,
3. hlavní import elektroměru a jeho znaménko/jednotku,
4. pokud používáte phase guard, potvrzené senzory proudu **L1, L2 a L3**,
5. jejich jednotky A/mA, čerstvost a správné přiřazení k jednotlivým fázím,
6. maximální celkovou kapacitu přípojky a maximální proud fáze,
7. explicitní 1f/3f topologii a proudy každého řízeného profilu,
8. stop ownership a ověření, že FRAKON dokáže zařízení bezpečně vypnout,
9. commissioning preflight bez blokujících chyb,
10. první test se spotřebičem, který lze bezpečně sledovat přímo na místě.

Pokud jsou zdrojová data neúplná, nedostupná nebo zastaralá, aktivní execution guard má start blokovat namísto odhadování chybějících hodnot.

## Nastavení vyúčtování

U položky VisionQ otevřete **Konfigurovat** a vyplňte datum počátečního odečtu, počáteční stav VT/NT, začátek zúčtovacího období, očekávané datum vyúčtování, měsíční zálohu, cenu VT/NT a stálé měsíční platby.

Pro počáteční nastavení jsou potřeba **kumulativní stavy elektroměru** VT a NT k datu začátku období. Souhrnná spotřeba za období je rozdíl mezi dvěma odečty a nelze ji přímo použít jako počáteční stav elektroměru.

## HDO

HDO adaptér vystavuje stabilní FRAKON Energy entity a události/device triggers pro zapnutí a ukončení nízkého tarifu. Pokud živá data nejsou dostupná, fallback plán musí být v UI považován za neověřený, nikoli za potvrzený stav distributora.

## Bezpečnostní principy

- **Fail closed:** neověřená execution data nesmějí vytvořit autoritu ke startu.
- **Server authoritative:** frontend zobrazuje serverové výsledky a bezpečnostní matematiku nepřepočítává jako vlastní zdroj pravdy.
- **Durable before physical:** kritický lifecycle/ownership stav je uložen před překročením fyzické service-call hranice.
- **No blind retry:** neznámý výsledek fyzického service callu přechází do recovery, ne do automatického opakovaného spínání.
- **Explicit phase model:** chybějící L1/L2/L3 se neodvozují z celkového výkonu.
- **Reload safe:** runtime, repository cache, WebSocket registrace a panel lifecycle jsou navrženy pro Home Assistant reload/restart bez duplicitních workerů nebo command handlerů.

## Stav projektu

Aktuální větev `main` odpovídá release-candidate vývoji. Stabilní `1.0.0` bude vhodné vydat až po úspěšném commissioning testu na reálné instalaci Home Assistantu, restart/reload testu, ověření živých VisionQ/HDO dat, dashboardu a kontrolovaném end-to-end testu fyzického startu i stopu řízeného spotřebiče.

Pro hlášení chyb používejte GitHub Issues. Při problému s execution vrstvou přiložte stav Commissioning Preflight / Execution Safety Status, ale nesdílejte přihlašovací údaje ani jiné tajné údaje.