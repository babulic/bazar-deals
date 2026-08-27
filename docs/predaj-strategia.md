# Ako nájsť kupcov v SK, ČR, PL, HU, AT a okolitom Schengene

Analýza vychádza z tvojich štyroch účtov, stiahnutých 27. 8. 2026:
[Bazoš](https://www.bazos.sk/search.php?hledat=0948244165) 19 inzerátov,
[Aukro `ivqo`](https://aukro.sk/pouzivatel/ivqo/ponuky) 27 ponúk,
[Vinted `ivan_c64`](https://www.vinted.sk/member/312914388) 29 kúskov,
[eBay `berg-kristalle`](https://www.ebay.at/sch/i.html?sid=berg-kristalle) 19 položiek,
124 hodnotení a 197 predaných kusov.

Prehľad vieš kedykoľvek zregenerovať:

```bash
python -m bazar_deals sell --segment minerals
python -m bazar_deals sell --format json
```

## 1. Nepredávaš jeden sortiment, ale tri

Ten tovar má tri úplne odlišné skupiny kupcov, a to je dôvod, prečo jedna
spoločná stratégia pre všetko nefunguje.

| Segment | Kusov | Hodnota | Kto to kupuje |
|---|---:|---:|---|
| Minerály | 14 | ~420 € | Zberatelia, celoeurópsky trh, hľadajú druh + lokalitu |
| Retro Commodore | 11 | ~287 € | Opravári a zberatelia 8-bitov, hľadajú číslo súčiastky |
| Bežná spotreba | 10 | ~133 € | Nikto konkrétny, konkurencia je Temu a AliExpress |

**Minerály** sú tvoja najsilnejšia karta. Slovenské klasické lokality (Banská
Štiavnica, Hodruša-Hámre, Ľubietová, Zemplín) majú v strednej Európe meno, sú
ľahké, každý kus je unikát a nedajú sa kúpiť v Číne.

**Retro Commodore** má overený nedostatok ponuky. Nemecký Polyplay predáva
MOS 6569R5 za 29 € a MOS 8565R2 za 20 €, oboje je momentálne nedostupné;
švajčiarsky Digital Retro má 6569 za 52 CHF a je vypredaný. Ty tie isté čipy
držíš na sklade a väčšinu z nich ponúkaš len na Slovensku.

**Bežná spotreba** je iný prípad: 5 € sklo na hodinky alebo 4 € kábel neunesie
žiadne cezhraničné poštovné. Tento segment patrí domov, alebo ako prílepok
k väčšej objednávke.

## 2. Najväčšia brzda nie je dosah, ale poštovné na eBay

Tvoje eBay inzeráty účtujú 10 až 15 € „Versand aus Slowakei". Packeta
z firemného účtu stojí zlomok:

| Krajina | Výdajné miesto | Na adresu |
|---|---:|---:|
| SK | 2,72 € | 4,23 € |
| CZ | 2,95 € | 4,93 € |
| HU | 2,95 € | 4,93 € |
| PL | 3,54 € | 6,45 € |
| RO | 3,42 € | 5,40 € |
| DE | 4,70 € | 7,03 € |
| AT | 4,93 € | 7,61 € |

(Verejný cenník vrátane palivového príplatku 16,5 % a mýtneho 0,04 €/kg.
Zmluvné sadzby sú nižšie — prepíš ich v `selling.packeta` a čísla sa prepočítajú.)

Naprieč 19 eBay inzerátmi je rozdiel oproti reálnej cene **131 € nadhodnoteného
poštovného**, teda zhruba 7 € na kus. Najhoršie prípady:

- zdroj pre C64 za 32 €: poštovné 15 € namiesto 4,93 €,
- ružový chalcedón za 7 €: poštovné 14 €, teda dvojnásobok ceny tovaru,
- sklo na hodinky za 5 €: poštovné 10 €.

Nejde len o to, že kupujúci zaplatí navyše. eBay radí ponuky aj podľa celkovej
ceny s dopravou a zvýhodňuje „Kostenloser Versand", takže vysoké poštovné ti
zároveň zráža viditeľnosť. Pri veciach do 20 € je 13 € poštovné dôvod, prečo
ponuku nikto nedoklikne.

Aj keby si na eBay zostal pri doručení na adresu (výdajné miesto sa tam nedá
natívne vybrať), 7,61 € do Rakúska je stále o polovicu menej než dnešných 13 až
15 €. Toto je najlacnejšia zmena s najväčším dopadom a nevyžaduje žiadny nový
účet.

## 3. Kde kupci naozaj sú

| Krajina | Čo funguje dnes | Čo chýba |
|---|---|---|
| SK | Bazoš, Aukro, Vinted | pokryté |
| CZ | Vinted (koridor SK↔CZ) | Bazos.cz, Allegro.cz |
| PL | Vinted (koridor SK↔PL) | **Allegro.pl** |
| HU | **nič** | Allegro.hu |
| AT | eBay.at | Delcampe |
| DE | eBay (nemecké inzeráty) | Forum64, Delcampe |

Tri veci z toho vyplývajú.

**Maďarsko dnes nemáš pokryté vôbec.** Ani jeden zo štyroch účtov tam nedosiahne,
hoci Packeta doručí do HU za 2,95 € — rovnako lacno ako do Česka.

**Vinted ti cezhranične dáva len Česko a Poľsko.** Slovenský predajca má
predplatené štítky cez SPS Balíkovo pre SK↔CZ a SK↔PL a cez Packetu do Poľska.
Nemecko, Rakúsko ani Maďarsko v tom nie sú. Vinted teda nie je cesta do DACH.

**Allegro je jediný krok, ktorý otvorí PL, CZ aj HU naraz.** Jeden firemný účet
obsluhuje allegro.pl, .cz, .sk aj .hu, a Allegro od firiem registrovaných na
Slovensku nevyžaduje registračné doklady — stačí overenie totožnosti a bankového
účtu. Počítaj s registráciou EPR (obaly a elektro) pre trhy, kam budeš predávať.

**Willhaben je slepá ulička.** PayLivery sa dá použiť výlučne na predaj v rámci
Rakúska a willhaben v bezpečnostných pokynoch sám odrádza od zasielania do
zahraničia. Rakúsko pokrývaj cez eBay, nie cez willhaben.

Pre minerály navyše existujú kanály, kde je celé publikum zberateľské:
**Delcampe** má vlastnú kategóriu Mineralien & Fossilien a dosah na DE, AT, FR,
IT a Benelux; **Catawiki** má kurátorované aukcie, ktoré sa oplatia zhruba od
75 € nahor — z tvojho skladu tam patrí 110 € ametyst z Namíbie. Pre retro je
prirodzené miesto **Forum64.de**, kde sa náhradné diely na C64 predávajú za
plné maloobchodné ceny.

## 4. Titulky: každá platforma má iný rozpočet znakov

Namerané na tvojich vlastných inzerátoch:

| Platforma | Limit | Dôkaz |
|---|---:|---|
| Bazoš | 60 | „…peňaženka (hardware crypt" — useknuté v polovici slova |
| eBay | 80 | „…1570. 220V Netzkabe" — useknuté v polovici slova |
| Aukro | ≥68 | najdlhší pozorovaný titulok |
| Vinted | ≥98 | najdlhší pozorovaný titulok |

Preto sa ten istý text nedá kopírovať naprieč platformami. `sell` skladá titulok
zo štruktúrovaných polí a najmenej hodnotný kúsok zahodí, kým sa nezmestí:

```
bazos_sk  sk  Chalcedón 61g, Banská Štiavnica, Slovensko, zberateľský kus       59/60
ebay_at   de  Chalcedon 61g, Schemnitz, Banská Štiavnica, Slowakei, Sammlerstück, unbeschädigt  80/80
allegro   pl  Chalcedon 61g, Bańska Szczawnica, Banská Štiavnica, Słowacja      60/75
```

Dnes máš na eBay pri 80 znakoch často využitých 50 — to je 30 znakov kľúčových
slov zadarmo, ktoré nepoužívaš.

### Historické názvy lokalít sú to najcennejšie, čo máš

Nemeckí a rakúski zberatelia nehľadajú „Banská Štiavnica". Hľadajú **Schemnitz**,
lebo tak sú popísané staré etikety z rakúsko-uhorských baní. To isté platí pre
maďarské názvy:

| Lokalita | Nemecky | Maďarsky |
|---|---|---|
| Banská Štiavnica | Schemnitz | Selmecbánya |
| Hodruša-Hámre | Hodritsch | Hodrusbánya |
| Ľubietová | Libethen | Libetbánya |
| Kremnica | Kremnitz | Körmöcbánya |

Toto je konkrétne dôležité pri tvojom **pseudomalachite z Ľubietovej** za 29 €.
Ľubietová je typová lokalita libethenitu — minerál je pomenovaný priamo podľa
nemeckého mena obce. Pre zberateľa klasických európskych lokalít je to známa
adresa. Ty ten kus máš vystavený jedine na Vinted, teda na módnej platforme,
kde po ňom nikto takýto nepozerá.

Pri retre je logika opačná a jednoduchšia: číslo súčiastky je jazykovo neutrálne
a nesie celý dopyt, takže patrí na začiatok — `8565R2 VIC-II Videochip,
Commodore C64C`, nie `Videochip CSG 8565 R2 ... pre Commodore`.

## 5. Diery v pokrytí

Z 35 spárovaných položiek:

- **Minerály iba na Vinted:** pseudomalachit z Ľubietovej (29 €), celestín
  z Madagaskaru (22 €), jaspis zo Zemplína (22 €), zeolit (15 €), jadeit (12 €),
  prívesok z apatitu (6 €). Zberatelia minerálov na Vinted nie sú.
- **Retro iba na eBay:** MOS 6522 VIA, CSG 8500, druhý 8565R2 z roku 1991.
  Na slovenských kanáloch chýbajú.
- **6569R5 iba na Bazoši** za 35 €, hoci práve tento čip je v Nemecku vypredaný
  a Polyplay zaň pýta 29 €.
- **Joysticky iba na SK kanáloch**, hoci ich hlavný trh je nemecká a poľská
  retro scéna.
- **Aukro je z pohľadu snapshotu skoro prázdne** — z 27 ponúk sa dali vyčítať
  4, takže tam čísla ber orientačne.

## 6. Poradie krokov

1. **Zníž poštovné na eBay na reálnu Packetu.** Bez nového účtu, bez nových
   inzerátov, dotkne sa to všetkých 19 položiek naraz.
2. **Presuň minerály z Vinted na eBay a Delcampe** s nemeckými titulkami
   a historickými názvami lokalít. Začni pseudomalachitom z Ľubietovej.
3. **Otvor Allegro.** Je to jediný krok, ktorý naraz pridá Poľsko, Česko
   a Maďarsko, teda aj jedinú krajinu, ktorú dnes nemáš vôbec.
4. **Retro daj na Forum64** a doplň chýbajúce kusy na eBay — dopyt tam
   preukázateľne prevyšuje ponuku.
5. **Bežnú spotrebu nevyvážaj.** Nechaj ju na Bazoši a Vinted a použi ju ako
   prílepok k väčším zásielkam.
6. **Ametyst za 110 € skús na Catawiki.** Je to jediný kus, ktorý unesie
   kurátorovanú aukciu aj jej vyššiu províziu.

Jedna vec na overenie mimo tohto plánu: na eBay si vedený ako súkromný predajca
(„Privat") pri 197 predaných kusoch. Ak predávaš na firemný účet, patrí k tomu
aj štatút „gewerblich" a s ním povinnosti okolo DPH a práva na odstúpenie —
oplatí sa to prejsť s účtovníkom skôr, než objem ešte narastie.
