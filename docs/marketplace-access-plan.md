# Sprístupnenie nových bazárov — konkrétny postup

Realizácia 31. 8. 2026: automatické kurzy, 2 % FX rezerva, diagnostika,
ručný JSON/CSV import s 24-hodinovým potvrdením SR dostupnosti a odstránenie
automatického mazania komentárov sú implementované. Testy a výsledky živých
skúšok sú v `update-validation-2026-08-31.md`; publikovanie sa overuje cez PR/CI.
Polymarket je nasadený. Facebook/OLX zostávajú manuálne; pre Allegro nie je
lokálne ani v GitHub Secrets dostupný oprávnený prístup. Prihlásenie, zmluvné
API oprávnenie a potvrdenie reálnej dopravy nemožno nahradiť zmenou kódu.

Nižšie je pôvodný schválený plán, nie tvrdenie, že všetky externé prístupy fungujú.

## 1. CZK a PLN: automatické kurzy — implementované lokálne

Aktualizácia 31. 8. 2026: spoločné ECB načítanie CZK/PLN, datovaná cache,
kontrola veku, manuálne overrides a použitie pri nákupe/predaji/doprave sú už
implementované a otestované. Živý test načítal sadzby z 28. 8. 2026. Zmeny
zatiaľ nie sú publikované do produkčných GitHub jobov. Nižšie navrhovaná
samostatná 2 % rezerva na konverzné poplatky je už implementovaná cez
FX_FEE_RATE: náklady zvyšuje, odhad výnosov znižuje.

Implementácia bez ďalšieho účtu:

1. Pri prvom použití PLN v online behu načítať ECB XML
   https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml.
2. Overiť dátum publikácie, prítomnosť PLN a kladný konečný Decimal kurz.
   Uložiť dátum aj kurz do lokálnej cache; predvolená maximálna veková hranica
   bude 7 kalendárnych dní, aby fungovali víkendy a sviatky.
3. Na ďalšie behy používať čerstvú cache, načítanie aktualizovať najviac denne.
   Pri chybe použiť iba cache v povolenom veku. Ak nie je platný kurz, PLN
   ponuku vyradiť s konkrétnym dôvodom. Offline testy nesmú volať ECB.
4. Pre nákup počítať EUR = PLN / kurz, zvlášť pre cenu aj dopravu.
   ECB je referenčný kurz, nie garantovaný kurz karty. Pridať nastaviteľnú
   rezervu na konverziu k nákladom; navrhovaný počiatočný odhad 2 %, neskôr
   nahradiť skutočnými nákladmi platobnej metódy. Pri predajnom odhade rezerva
   výnos znižuje. EUR_PLN ponechať iba ako vedomý ručný override.
5. Testovať inverziu kurzu, poštovné v PLN, víkend, starý/budúci dátum,
   chýbajúcu menu, poškodené XML a výpadok siete.

Hotové až keď online beh získa datovaný kurz bez nastaveného EUR_PLN a testy
potvrdia konzervatívne zaokrúhlenie a správanie pri výpadku.

[ECB: dátové zdroje a obmedzenia referenčných kurzov](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)

## 2. Allegro: token nie je jediná ani hlavná prekážka

Oficiálna podpora Allegro 20. 8. 2026 v odpovedi k issue #13887 uvádza, že
verifikácia nových aplikácií pre GET /offers/listing bola zastavená a endpoint
už nie je v aktuálnej dokumentácii. Staršie prístupy závisia od individuálnych
dohôd. Vytvorenie novej aplikácie preto nie je spoľahlivý postup na získanie
vyhľadávania cudzích ponúk.

Postup:

1. Zistiť, či vlastník už má aplikáciu alebo poskytovateľa so zmluvne povoleným
   prístupom k vyhľadávaniu cudzích ponúk. Nežiadať heslá/tokeny do chatu.
2. Ak taký prístup existuje, overiť rozsah pre PL aj SK a overenie dopravy do SK
   na konkrétnej ponuke. Nestačí produktový katalóg ani API vlastných inzerátov.
   Poskytovateľ musí preukázať oprávnenie poskytovať údaje; neobjednávať službu
   len preto, že sľubuje scraping Allegra.
3. Až potom doplniť príslušnú integráciu. Pre povolený pôvodný endpoint použiť
   OAuth client credentials, automaticky získavať krátkodobý token a rešpektovať
   expires_in. Pridať identifikačný User-Agent zodpovedajúci aplikácii; adaptér už túto hlavičku výslovne nastavuje.
4. Client ID/secret uložiť do lokálnej .env a GitHub Actions Secrets, nikdy do
   repozitára. Statický ALLEGRO_ACCESS_TOKEN je vhodný len na krátky test, nie
   na dlhodobý bezobslužný beh. Pri 401 obnoviť token raz; pri 403 VerificationRequired
   zastaviť zdroj a vypísať ACCESS_NOT_GRANTED, neopakovať prihlasovanie donekonečna.
5. Bez existujúceho povoleného prístupu použiť bežné vyhľadávanie v prehliadači
   a import používateľom vybraných ponúk. Automatický hodinový sken označiť
   ako nepodporovaný, nie ako fungujúci s chýbajúcim kľúčom.

Hotové až keď živý test vráti reálne ponuky v oboch trhoch a detail/checkout
potvrdí SK dostupnosť. Samotný úspešný OAuth test nestačí.

[Aktuálna odpoveď podpory](https://github.com/allegro/allegro-api/issues/13887#issuecomment-5365450607)
[Staršie prístupy sú individuálne](https://github.com/allegro/allegro-api/issues/13887#issuecomment-5366710181)
[OAuth a životnosť tokenov](https://developer.allegro.pl/tutorials/uwierzytelnianie-i-autoryzacja-zlq9e75GdIR)
[Požadovaný User-Agent](https://developer.allegro.pl/faq)

## 3. OLX: rozlíšiť prístup k stránke od API vlastných ponúk

Oficiálne FAQ, bod 6, hovorí, že štandardné API nesprístupňuje inzeráty iných
používateľov. OLX API účet preto nevyrieši nákupné vyhľadávanie ani hľadanie
cudzích dopytov. Navyše štandardná OLX Delivery je podľa dokumentácie dostupná
len v Poľsku; jej odznak nie je dôkaz dopravy do SR.

Postup:

1. Otvoriť presný vyhľadávací odkaz bežne v prehliadači. Ak stránka vyžaduje
   prihlásenie alebo CAPTCHA, používateľ ich vykoná sám. Nekopírovať cookies
   do GitHub Secrets a nepokúšať sa obísť blokovanie proxy rotáciou.
2. Ak nefunguje ani bežné používanie, pripraviť podpore URL, čas, HTTP stav a
   Request ID, ak je dostupné. Požiadať o preverenie a povolený spôsob prístupu
   na zamýšľané vyhľadávanie. Správu bez súhlasu používateľa neodosielať.
3. Kým nie je povolený strojový zdroj dát, implementovať lokálny import
   používateľom vybraných ponúk. Nestačí importovať iba URL: potrebné sú názov,
   cena, mena, popis, dostupnosť a dôkaz SK dopravy. Aktuálne --listings-in vie
   čítať interný JSON; jednoduchý používateľský import je už dostupný cez --manual-in (JSON/CSV).
4. Záznam s neoverenou zahraničnou dopravou označiť NEEDS_DELIVERY_CONFIRMATION.
   Do BUY ho pustiť až po potvrdení možnosti nákupu zo Slovenska a nákladov.
5. Pri získaní povoleného feedu/integrácie pridať automatický adaptér a živé
   testy. Nezamieňať schválenie API pre vlastné inzeráty s povolením čítať trh.

[OLX FAQ — cudzie inzeráty](https://developer.olx.pl/artykuly/czeste-pytania)
[OLX Delivery — územné obmedzenie](https://developer.olx.pl/api/doc)

## 4. Facebook Marketplace: prihlásenie vyrieši reláciu, nie bezobslužný zber

Postup:

1. Používateľ otvorí Marketplace v prehliadači a sám dokončí prihlásenie/2FA.
   Overí, že účet má prístup k Marketplace. Relácia zostáva lokálna.
2. Vyhľadá konkrétny produkt s miestom na Slovensku. Lokalita vyhľadávania
   nevylučuje výsledky zo zahraničia, preto sa musí overiť každý detail.
3. Na vybraných ponukách overiť možnosť osobného prevzatia v SR alebo dopravy
   do SR. Výsledky vyhľadávania samy osebe nie sú dôkaz dostupnosti.
4. Použiť rovnaký lokálny import ako pre OLX. Pridať dátum kontroly, URL,
   skutočnú cenu/poštovné a stav dostupnosti. Pred kúpou vyžadovať čerstvé
   potvrdenie; neodosielať automaticky správy predávajúcim.
5. Trvalý automatický sken povoliť iba s potvrdeným oprávneným spôsobom
   dátového prístupu. Pri strate relácie zdroj označiť LOGIN_REQUIRED a
   zastaviť. Samotné presunutie skriptu na Alwyzon ani uloženie cookies
   nezaručuje funkčný či povolený zber.

[Meta: automatizovaný zber a jeho blokovanie](https://www.facebook.com/help/463983701520800)

## 5. Poradie realizácie a skutočné nasadenie

1. Implementovať automatický ECB kurz a jeho testy.
2. Opraviť diagnostiku: rozlišovať READY, LOGIN_REQUIRED, BLOCKED,
   ACCESS_NOT_GRANTED, STALE_FX a NEEDS_DELIVERY_CONFIRMATION. HTTP 200 bez
   použiteľných dát sa nesmie vydávať za úspešný sken.
3. Doplniť lokálny import a evidenciu potvrdenia SK dostupnosti; normalizovať
   dopravu vs osobné prevzatie v SR. Ochranu použiť pred rozhodnutím BUY.
4. Dokončiť dostupné autorizácie/feed kontrakty. Zdroje bez prístupu nechať
   explicitne v manuálnom režime, bez prísľubu hodinového automatického skenu.
5. Spustiť offline testy a živé fetch-only skúšky bez --notify. Overiť aj
   dopyty: bežná predajná ponuka na Allegre nie je potenciálny kupujúci.
6. Pred publikovaním preveriť workflow cleanup: súčasný hunt.yml volá mazanie
   komentárov issue #1. Tento deštruktívny krok oddeliť od bežného nasadenia.
7. Commitnúť iba projektové zmeny, bez používateľových .idea/.env súborov;
   publikovať cez kontrolovanú vetvu/PR a potom main. Až publikované zmeny
   bude používať hodinový GitHub Actions job. Jednorazový beh následne overiť.
8. Za kompletné nasadenie všetkých zdrojov považovať až úspešný živý beh
   každého zdroja vrátane SK kontroly; dovtedy uvádzať presnú čiastočnú podporu.

Čo vyžaduje používateľa: prípadné prihlásenie/2FA, informáciu o existujúcom
povolenom Allegro prístupe, schválenie kontaktovania podpory či plateného
poskytovateľa a potvrdenie dopravy tam, kde ho stránka neposkytuje. Kódy,
heslá, cookies ani API secrets netreba posielať do konverzácie.
