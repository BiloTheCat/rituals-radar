# Handleiding: Rituals Radar opzetten

Deze handleiding brengt je van een lege Databricks Free Edition-workspace naar een werkend project met dashboard. Reken op ongeveer drie kwartier.

**Je doorloopt drie fases:**

1. **Deployen + voorbeelddata** (stap 1 t/m 4). Je dashboard werkt meteen, met gegenereerde data.
2. **Echte data** (stap 5). GitHub Actions haalt elke 3 uur de voorraad op en elke dag de sitemap.
3. **Overstappen** (stap 6). Na een paar dagen zet je de voorbeelddata uit.

---

## Hoe het werkt, in het kort

```
GitHub Actions (elke 3 uur / elke dag)        Databricks Free Edition
┌─────────────────────────────┐                ┌───────────────────────────────────────────┐
│ python -m radar stock       │   Files API    │ Volume: landing/  (ruwe JSON-bestanden)    │
│ python -m radar catalog     │ ─────────────▶ │   ↓ Auto Loader                            │
└─────────────────────────────┘                │ Pipeline: bronze → silver → gold           │
                                               │   ↓                                        │
                                               │ Dashboard "Rituals Radar"                  │
                                               └───────────────────────────────────────────┘
```

**Waarom draait het ophalen niet in Databricks zelf?** De gratis versie blokkeert uitgaand internetverkeer naar de meeste websites, dus een notebook kan rituals.com niet bereiken. GitHub Actions kan dat wel, en dat is ook gratis. In stap 8 lees je hoe je alles in Databricks laat draaien als je workspace wel internettoegang krijgt.

---

## Wat je nodig hebt

- Een **Databricks Free Edition**-account: <https://www.databricks.com/learn/free-edition>
- Een **GitHub**-account
- **Git**, en **Python 3.11 of nieuwer**. Python heb je alleen nodig om lokaal te testen.
- De **Databricks CLI** (zie stap 2)

---

## Stap 1 · Zet het project op GitHub

1. Pak de zip uit en open een terminal in de map `rituals-radar`.
2. In `config/radar.toml` staat je GitHub-naam al in de regel `user_agent`. Zo kan Rituals zien wie hun site bezoekt. Dat hoort bij netjes scrapen.
3. Maak op GitHub een nieuwe, lege repository `rituals-radar`. Voor een portfolio kies je **Public**. Er staan geen geheimen in de code.
4. Push de code:

```bash
git init
git add .
git commit -m "Rituals Radar: eerste versie"
git branch -M main
git remote add origin https://github.com/<jouw-naam>/rituals-radar.git
git push -u origin main
```

Na de push draait de workflow **ci** automatisch: je ziet een groen vinkje als alle tests slagen.

---

## Stap 2 · Installeer de Databricks CLI en log in

**Installeren:**

| Systeem | Commando |
|---|---|
| macOS | `brew tap databricks/tap && brew install databricks` |
| Windows | `winget install Databricks.DatabricksCLI` |
| Linux / macOS (zonder Homebrew) | `curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh \| sh` |

**Inloggen:** open je Databricks-workspace in de browser en kopieer de URL, bijvoorbeeld `https://dbc-a1b2c3d4-e5f6.cloud.databricks.com`. Dan:

```bash
databricks auth login --host https://dbc-a1b2c3d4-e5f6.cloud.databricks.com
```

Er opent een browservenster om in te loggen. Je workspace-URL staat al in `databricks.yml` op de regel `host:`.

---

## Stap 3 · Deploy alles naar Databricks

```bash
databricks bundle validate   # controleert de configuratie
databricks bundle deploy     # maakt alles aan in je workspace
```

Dit heb je daarna in je workspace:

| Wat | Waar te vinden |
|---|---|
| Schema `workspace.rituals_radar` | Catalog → workspace → rituals_radar |
| Volume `landing` (voor de ruwe bestanden) | Catalog → workspace → rituals_radar → Volumes |
| Pipeline `rituals-radar-pipeline` | Jobs & Pipelines |
| Job `rituals-radar-refresh` (4× per dag) | Jobs & Pipelines |
| Dashboard `Rituals Radar` | Dashboards |
| De code zelf | Workspace → Users → (jij) → .bundle → rituals-radar → free → files |

> **Foutmelding over de warehouse?** De bundle zoekt een SQL-warehouse met de naam "Serverless Starter Warehouse". Heet die van jou anders? Kijk onder **SQL Warehouses**, kopieer het ID en deploy met:
> `databricks bundle deploy --var="warehouse_id=<jouw-id>"`

---

## Stap 4 · Laad voorbeelddata en draai de pipeline

1. Ga in Databricks naar **Workspace → Users → (jij) → .bundle → rituals-radar → free → files → src → notebooks**.
2. Open **`01_load_sample_data`**, kies rechtsboven **Serverless** als compute en klik op **Run all**. Het notebook schrijft 30 dagen aan gegenereerde data naar het volume. Alle rijen hebben `is_sample = true`.
3. Start de pipeline:

```bash
databricks bundle run rituals_radar_refresh
```

   Je kunt ook in de UI naar **Jobs & Pipelines → rituals-radar-refresh → Run now** gaan.

4. Open **Dashboards → Rituals Radar**. Je ziet de voorraad op basis van de voorbeelddata.

Wil je zien wat er onder de motorkap gebeurt? Open de pipeline. Je ziet de hele graaf van bronze naar gold, met per tabel hoeveel rijen er door de **datakwaliteitsregels** (expectations) zijn gekomen of afgevallen. Het notebook **`02_explore_results`** bevat voorbeeldvragen in SQL.

---

## Stap 5 · Echte data verzamelen met GitHub Actions

### 5a. Maak een Databricks-token aan

In Databricks: klik op je profiel (rechtsboven) → **Settings → Developer → Access tokens → Manage → Generate new token**. Geef het een naam, bijvoorbeeld *github-actions*, en een looptijd, bijvoorbeeld 90 dagen. Kopieer het token (het begint met `dapi`). Je ziet het maar één keer.

### 5b. Zet de geheimen in GitHub

Ga in je repository naar **Settings → Secrets and variables → Actions → New repository secret** en voeg toe:

| Naam | Waarde |
|---|---|
| `DATABRICKS_HOST` | `https://dbc-3725dd5d-75d0.cloud.databricks.com` |
| `DATABRICKS_TOKEN` | het token uit 5a |

### 5c. Test de verzameling

1. Ga in GitHub naar **Actions → collect → Run workflow**, kies `all` en klik **Run workflow**.
2. Open de run en bekijk de log van de stap *Run collectors*. Je ziet een samenvatting van de voorraadstatus en hoeveel producten er in de sitemap staan.
3. Kijk in Databricks onder **Catalog → workspace → rituals_radar → Volumes → landing**: daar staan nu nieuwe `.jsonl`-bestanden.
4. Draai de pipeline opnieuw, of wacht op de volgende geplande run.

Vanaf nu draait alles vanzelf: voorraad elke 3 uur, de sitemap elke dag om 05:45 UTC, en de pipeline 4× per dag.

> **Goed om te weten over GitHub-schedules**
> - Geplande runs kunnen een paar minuten later starten dan gepland. Dat is normaal.
> - Bij een **public** repo zet GitHub geplande workflows uit als er 60 dagen niets aan de repo verandert. Een kleine commit af en toe houdt hem actief.
> - Bij een **private** repo kost dit ongeveer 600 à 900 Actions-minuten per maand. Het gratis tegoed is 2.000 minuten.

---

## Stap 6 · Van voorbeelddata naar echte data

Wacht een paar dagen tot er genoeg echte data is. Zet dan de voorbeelddata uit en bouw de tabellen opnieuw op:

```bash
databricks bundle deploy --var="include_sample_data=false"
databricks bundle run rituals_radar_pipeline --full-refresh-all
```

Wil je dit permanent? Zet dan in `databricks.yml` de `default` van `include_sample_data` op `"false"`.

Een *full refresh* leest alle bestanden in het volume opnieuw in. Er gaat dus niets verloren: de landing zone is je bron van waarheid. Dat is een mooi punt om in een gesprek te benoemen.

---

## Stap 7 · Maak er een portfolio-stuk van

- **Screenshot:** maak screenshots van de twee dashboardpagina's, sla ze op als `docs/dashboard.png` en zet `![Dashboard](docs/dashboard.png)` bovenaan de README.
- **Pipelinegraaf:** een screenshot van de pipeline (bronze → silver → gold) laat in één beeld zien wat je gebouwd hebt.
- **Vertel de keuzes, niet alleen de techniek.** Voorbeelden die je in een sollicitatiegesprek kunt gebruiken:
  - "De tekst *tijdelijk uitverkocht* staat op elke productpagina, ook als iets op voorraad is. Daarom lees ik de gestructureerde schema.org-data. Er zit een regressietest op."
  - "De gratis Databricks kan geen websites bereiken. Daarom heb ik ingestion en verwerking gescheiden met een landing zone, net als in grote bedrijven."
  - "Voorraad wordt elke 3 uur gecheckt, maar ik sla alleen *veranderingen* op, met SCD Type 2 via AUTO CDC. Met een gaps-and-islands-query maak ik daar stock-out-periodes van."
  - "Collectors en pipeline delen een datacontract, en een test bewaakt dat ze gelijk blijven."
- **LinkedIn:** een korte post met de screenshot, de architectuurtekening uit de README en één inzicht uit je data werkt goed, bijvoorbeeld welk Limited Edition-product het snelst uitverkocht was.

---

## Stap 8 (optioneel) · Alles binnen Databricks draaien

Databricks noemt LinkedIn-verificatie als manier om sommige Free Edition-limieten te verhogen, waaronder uitgaand internetverkeer. Bij elke regio en elk account kan dat anders uitpakken, dus test het:

1. Draai het notebook **`00_check_connectivity`**. Staat rituals.com op `reachable`? Ga dan door.
2. Verplaats `resources/optional/collectors_in_databricks.yml` naar `resources/` en draai `databricks bundle deploy`.
3. Haal in `.github/workflows/collect.yml` het blok `schedule:` weg, zodat niet allebei tegelijk draaien.

---

## Welke producten worden gevolgd?

`config/radar.toml` volgt standaard **alle producten van de huidige Limited Edition**: de Uukumwe Collection (dat is waar het menu-item "Limited Edition" op rituals.com op 13 november 2026 naartoe ging). Bij elke run zoekt de collector in de sitemap alle producten met "uukumwe" in de URL op. Nieuwe producten komen er dus vanzelf bij.

Komt er een nieuwe Limited Edition? Voeg die als collectie toe en zet hem in de watchlist:

```toml
[watchlist]
collections = ["uukumwe", "nieuwe_collectie"]

[collections.nieuwe_collectie]
display_name = "Naam van de nieuwe collectie"
keywords = ["woord-uit-de-product-url"]
```

Losse producten volgen kan ook: zet hun URL in `products = [...]`.

---

## De tab "Stores": winkels bij een postcode

De derde dashboardpagina toont bij een postcode (de eerste 4 cijfers) de 10 dichtstbijzijnde Rituals-winkels, met adres, afstand en openingstijden. Kies ook een Uukumwe-product: de link brengt je naar de productpagina. Daar klik je op *Of controleer beschikbaarheid in de winkel* voor de voorraad per winkel. Die voorraad zelf zit bij Rituals achter een afgeschermde dienst, en daar blijft dit project vanaf.

Waar de gegevens vandaan komen:
- **Winkels:** de openbare winkelpagina's van rituals.com (116 in Nederland), elke maandag opgehaald.
- **Postcodes:** het middelpunt van elk 4-cijferig postcodegebied, uit open data van het CBS via PDOK (licentie CC BY 4.0), ook elke maandag.

**Eenmalig nodig na het deployen:** ga in GitHub naar **Actions → collect → Run workflow**, kies `reference` en start de run. Die duurt ongeveer 8 minuten. Draai daarna de pipeline (`databricks bundle run rituals_radar_refresh`). Tot die tijd toont de tab 10 voorbeeldwinkels en 16 voorbeeldpostcodes.

---

## Lokaal testen (optioneel)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest                                # alle tests, zonder internet

export PYTHONPATH=src/collectors      # Windows PowerShell: $env:PYTHONPATH="src/collectors"
python -m radar check                 # toont de configuratie
python -m radar stock --dry-run       # haalt echt de productpagina's op (± 1,5 minuut), schrijft niets weg
python -m radar sample --out landing_local   # voorbeelddata in een lokale map
```

Met `DATABRICKS_HOST` en `DATABRICKS_TOKEN` als omgevingsvariabelen kun je ook vanaf je laptop rechtstreeks naar het volume schrijven, door `--dry-run` weg te laten.

---

## Problemen oplossen

| Probleem | Oplossing |
|---|---|
| `bundle deploy`: *schema already exists* | Je hebt het schema eerder met de hand aangemaakt. Verwijder het lege schema in Catalog Explorer en deploy opnieuw. |
| `bundle deploy`: warehouse niet gevonden | Zie stap 3: geef het warehouse-ID mee met `--var`. |
| Pipeline faalt met *path does not exist* | Het volume is nog leeg. Laad eerst de voorbeelddata (stap 4) of draai de collectors (stap 5c). |
| Alle voorraadchecks falen met HTTP 403 | rituals.com blokkeert de servers van GitHub. Draai de stock-collector vanaf je eigen computer (lokaal testen, met een planner zoals cron of Taakplanner), of binnen Databricks (stap 8). De mislukte checks staan in `silver_stock_quarantine`. |
| `parse_status = no_jsonld` in de quarantaine | Rituals heeft de pagina-opbouw veranderd. Bekijk de pagina en pas `parse_product_page` in `src/collectors/radar/rituals.py` aan. De tests in `tests/test_rituals.py` helpen daarbij. |
| Compute staat uit tot morgen | Je hebt het dagelijkse Free Edition-quotum bereikt. Laat de job minder vaak draaien (`resources/jobs.yml`). |
| GitHub-run faalt met 401/403 bij uploaden | Je Databricks-token is verlopen. Maak een nieuw token aan (5a) en vervang de secret. |
| Dashboard leeg | Draai eerst de pipeline. Heb je de schemanaam veranderd? Pas dan ook de queries in `src/dashboards/rituals_radar.lvdash.json` en `config/radar.toml` aan. |

---

## Kosten

| Onderdeel | Kosten |
|---|---|
| Databricks Free Edition | €0 (binnen het dagelijkse fair-use-quotum) |
| GitHub Actions | €0 voor een public repo; private: binnen het gratis tegoed |

---

## Netjes blijven

- Het project haalt alleen openbare productpagina's en de sitemap op. Beide zijn toegestaan in robots.txt. Er zit 3 seconden tussen verzoeken en de User-Agent zegt eerlijk wie je bent.
- Databricks Free Edition en dit project zijn voor **niet-commercieel** gebruik. Respecteer de voorwaarden van Rituals. Vraagt iemand je om te stoppen of data te verwijderen? Doe dat dan.
