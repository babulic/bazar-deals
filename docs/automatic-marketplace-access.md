# Automatický prístup: presné chýbajúce údaje a oprávnenia

Overené 31. 8. 2026. Základná implementácia je nasadená cez
[PR #23](https://github.com/babulic/bazar-deals/pull/23), merge `4f8a4fd`.
Tento dokument rozlišuje funkčné prihlasovanie od povolenia vyhľadávať cudzie
ponuky. Žiadny náhodne vygenerovaný reťazec nenahradí kľúč vydaný platformou.

## Allegro PL a SK

Oficiálna podpora **21. 8. 2026** potvrdila pozastavenie nových overení pre
`GET /offers/listing`. Staršie prístupy závisia od individuálnych zmlúv.
[Odpoveď podpory a nadväzujúce vysvetlenie](https://github.com/allegro/allegro-api/issues/13887).
V aktuálnych GitHub Secrets ani lokálnom nastavení prístup nie je. Pokus otvoriť
vývojársku konzolu v dostupnom prehliadači zastavila ochrana webu; nebola obídená.

**Najprv potrebujeme:** informáciu, či vlastníš existujúcu aplikáciu s povolením
pre `/offers/listing`, a potvrdenie rozsahu pre `allegro-pl`, `allegro-sk` a
filter `shipping.country=SK`. Nezakladaj novú aplikáciu iba v nádeji, že tým
získa toto oprávnenie. Katalóg produktov ani API vlastných ponúk nestačí.

**Ak aplikácia také povolenie má:**

1. V [Moje aplikacje](https://apps.developer.allegro.pl/) vyber existujúcu
   oprávnenú aplikáciu; prihlásenie/2FA dokonči ty. Neregeneruj existujúci secret
   bez dôvodu, pretože ho môžu používať iné integrácie.
2. Jej vlastník nastaví v GitHub Actions Secrets tohto projektu
   `ALLEGRO_CLIENT_ID` a `ALLEGRO_CLIENT_SECRET`. Prípadne ich uloží lokálne
   do ignorovaného `.env`; hodnoty neposielaj do chatu ani do repozitára.
3. Až po potvrdení oprávnenia nastav repository variable
   `ALLEGRO_LISTING_ACCESS_CONFIRMED=true`. Toto je lokálna bezpečnostná podmienka,
   nie žiadosť platforme a nie mechanizmus udelenia práva.
4. Implementovaná OAuth podpora požiada o app token cez `client_credentials`,
   drží ho iba v pamäti, rešpektuje `expires_in` a pri 401 ho obnoví najviac raz.
   Pri 403 sa zastaví. Statický `ALLEGRO_ACCESS_TOKEN` zostáva len alternatívou
   pre test existujúceho prístupu. Ak staršia zmluva vyžaduje iný OAuth flow,
   treba najprv získať jeho dokumentáciu; úspešné vydanie tokenu nie je dôkaz
   prístupu k vyhľadávaniu.
5. Overiť oba trhy živým `hunt --source allegro_pl --fetch-only` a
   `hunt --source allegro_sk --fetch-only`, potom reálnu dopravu do SR.
   Bez pozitívneho testu netvrdiť, že integrácia funguje.

[Oficiálny OAuth postup](https://developer.allegro.pl/tutorials/uwierzytelnianie-i-autoryzacja-zlq9e75GdIR).
Nové kľúče neboli vydané: nie je dostupná oprávnená aplikácia ani prístup do konzoly.
OAuth je pripravený a otestovaný s mock odpoveďami; živá autorizácia ostáva neoverená.

Ak oprávnenú aplikáciu nemáš, potrebujeme individuálne schválený feed alebo
poskytovateľa, ktorý vie doložiť právo poskytovať tieto ponuky na tento účel.
Samotné predplatné služby nazvanej scraper nie je dôkaz oprávnenia.

## OLX.pl

[Oficiálne FAQ, bod 6](https://developer.olx.pl/artykuly/czeste-pytania) výslovne
obmedzuje štandardné API na vlastné inzeráty. Bežný OLX účet, client ID a secret
preto neriešia vyhľadávanie cudzích predajných ani nákupných inzerátov.
HTTP 403 nie je chyba, ktorú opraví doplnenie takého kľúča.

Potrebujeme **osobitne schválený dátový prístup** alebo oprávnený externý feed:
URL dokumentácie, povolený rozsah dát, spôsob autentifikácie, limity a anonymizovanú
ukážku odpovede. Až podľa skutočného rozhrania sa dá implementovať adaptér;
neexistujúce `OLX_SEARCH_API_KEY` netreba generovať ani nastavovať.
Domáca OLX doprava sama osebe nepotvrdzuje dopravu do SR.
[Dokumentácia OLX](https://developer.olx.pl/api/doc).

## Facebook Marketplace

Overený [Marketplace Partner Program](https://about.fb.com/news/2024/11/our-response-to-the-european-commissions-decision-on-facebook-marketplace/amp/)
slúži na distribúciu inventára partnerských inzertných služieb. Nevytvára týmto
projektom všeobecný prístup k vyhľadávaniu cudzích ponúk.
[Meta Content Library](https://about.fb.com/news/2023/11/new-tools-to-support-independent-research/amp/)
je určená kvalifikovanému akademickému alebo neziskovému výskumu; tento nákupný
tracker nesmie predstierať taký účel.
Nenašiel som verejný samoobslužný API prístup vhodný pre tento komerčný sken.
Priame čítanie niektorých developer stránok navyše skončilo 429, takže netvrdím,
že boli preverené neverejné partnerské rozhrania.

Potrebujeme potvrdený oprávnený spôsob čítania dát: schválenie Meta alebo
poskytovateľa s doloženým oprávnením pre tento účel, dokumentáciu a autentifikáciu.
Samotné prihlásenie používateľa, App ID, Page token alebo cookies to nenahrádzajú.
Heslo, cookies, 2FA kódy ani export profilu do projektu neposielaj.

## Čo musí poskytnúť feed, aby umožnil automatické BUY

- Zdroj, stabilné ID, kanonická URL, názov a popis vrátane stavu výrobku.
- Aktuálna pevná cena a mena; odlíšenie predaja od dopytu, aukcie a rezervácie.
- Čas posledného overenia a dostupnosť na kúpu.
- Pozitívne potvrdenie dopravy do SR alebo prevzatia v SR, spôsob získania dôkazu
  a celkové náklady dopravy/prevzatia. Samotný odhad či lokalita predajcu nestačia.
- Dokumentované limity, oprávnenie používať údaje pre hodinové vyhľadávanie a
  uchovávanie porovnateľných cien. Nepotrebujeme súkromné správy ani kontakty.

Ak feed nevie potvrdiť SR dostupnosť, môže automaticky dodať kandidátov, ale
nemôže splniť podmienku automatického BUY. Stále bude potrebné potvrdenie človekom.
Existujúci lokálny JSON/CSV import zostáva okamžite dostupnou alternatívou.

## Pripravené texty pre podporu — NEODOSLANÉ

Žiadna správa podpore ani objednávka poskytovateľa nebola odoslaná. Pred odoslaním
je potrebný tvoj súhlas s konkrétnym adresátom a textom; prípadné meno účtu alebo
ID aplikácie doplň iba v bezpečnom kanáli podpory.

### Allegro — cez oficiálny API kontakt

[Oficiálny kontakt](https://developer.allegro.pl/contact) odporúča API projekt na GitHube.

> Dzień dobry, rozwijamy prywatny tracker ofert zakupowych dostępnych dla kupującego
> na Słowacji. Znamy odpowiedź w zgłoszeniu #13887 i rozumiemy, że nowe weryfikacje
> GET /offers/listing zostały wstrzymane. Czy istnieje obecnie dozwolony feed lub
> inna ścieżka umowna do wyszukiwania cudzych ofert na allegro.pl i allegro.sk,
> z potwierdzeniem dostawy do SK? Potrzebujemy ceny, waluty, dostępności, URL oraz
> kosztu dostawy, w cyklu godzinowym. Nie chodzi o katalog produktów ani własne
> oferty sprzedawcy. Prosimy o warunki dostępu, dokumentację, limity i koszty.

### OLX — podpora Developer Portal

[OLX Developer Portal](https://developer.olx.pl/en).

> Dzień dobry, potrzebujemy zgodnego z zasadami, godzinowego odczytu wybranych
> cudzych ogłoszeń sprzedaży i typu „kupię” na OLX.pl. Znamy punkt 6 FAQ — standardowe
> API zarządza wyłącznie własnymi ogłoszeniami. Czy OLX oferuje oddzielny dostęp
> partnerski lub licencjonowany feed do tego zastosowania? Kupujący znajduje się
> na Słowacji, więc potrzebujemy także informacji pozwalających potwierdzić możliwość
> dostawy do SK, a nie jedynie krajową Przesyłkę OLX. Prosimy o dokumentację,
> zasady, limity i koszty. Publiczne wyszukiwanie zwraca HTTP 403; nie chcemy
> obchodzić zabezpieczeń ani wykorzystywać prywatnych endpointów.

### Meta alebo potenciálny poskytovateľ

Použiť iba overený kontaktný kanál danej organizácie; žiadny e-mail adresáta nebol
vymyslený a partnerské schválenie sa nepredpokladá.

> We operate a private hourly second-hand offer tracker for a buyer in Slovakia.
> Do you provide authorized read-only access to third-party Facebook Marketplace
> sale listings and genuine wanted ads for this use case? We do not need messaging,
> posting, account credentials or private user data. Required fields are canonical
> listing URL/ID, title, description, current fixed price/currency, availability,
> observation time, and evidence/cost of delivery to Slovakia or pickup in Slovakia.
> Please confirm your authorization to supply these data, supported geography,
> retention terms, API documentation, rate limits and pricing. A catalog publishing
> API or academic-research-only license would not satisfy this request.
