"""
Tiny in-process translation table. Romanian only — the app's audience is RO.
Call t("Some English text") anywhere in the UI to render the localised version.

Unknown keys fall through (return the English untouched), so adding new strings
is safe even before they get translated.
"""

RO: dict[str, str] = {
    # ---- App-wide ----
    "Trendyol ↔ Oblio": "Trendyol ↔ Oblio",
    "Trendyol ↔ Oblio — Setup": "Trendyol ↔ Oblio — Configurare",
    "Ready": "Gata",

    # ---- Sidebar tabs ----
    "Trendyol products": "Produse Trendyol",
    "Mappings": "Mapări",
    "Variants": "Variante",
    "Bundles": "Pachete",
    "Invoices": "Facturi",
    "Campaign rebase": "Recalculare campanie",
    "Settings": "Setări",

    # ---- Generic actions ----
    "Refresh": "Reîmprospătează",
    "Refresh now": "Reîmprospătează acum",
    "Save settings": "Salvează setările",
    "Save as…": "Salvează ca…",
    "Compute": "Calculează",
    "Browse…": "Răsfoiește…",
    "Open invoice": "Deschide factura",
    "Open log folder": "Deschide folderul de log-uri",
    "Open it now?": "Deschide acum?",
    "Export": "Exportă",
    "Export selected…": "Exportă selecția…",
    "Export visible to folder…": "Exportă vizibilele în folder…",
    "Export as HTML…": "Exportă ca HTML…",
    "Pair Trendyol listings…": "Asociază produse Trendyol…",
    "View order details…": "Vezi detaliile comenzii…",
    "Remove": "Șterge",
    "Save": "Salvează",
    "Cancel": "Anulează",
    "Close": "Închide",
    "Yes": "Da",
    "No": "Nu",
    "OK": "OK",
    "Show only unmapped": "Arată doar neasociate",
    "Show:": "Afișează:",
    "Check all": "Bifează tot",
    "Uncheck all": "Debifează tot",
    "Test connection": "Testează conexiunea",
    "Run initial sync": "Rulează sincronizarea inițială",
    "Re-sync Oblio products": "Resincronizare produse Oblio",
    "Re-sync Trendyol listings": "Resincronizare produse Trendyol",
    "Re-sync both": "Resincronizează ambele",
    "Re-sync data": "Resincronizează date",
    "Diagnostics": "Diagnostic",

    # ---- Wizard ----
    "Welcome": "Bun venit",
    "Let's connect your Oblio account and Trendyol seller account.": "Hai să conectăm contul Oblio și contul de vânzător Trendyol.",
    "Oblio connection": "Conectare Oblio",
    "Credentials are stored locally, encrypted.": "Credențialele sunt salvate local, criptate.",
    "Trendyol connection": "Conectare Trendyol",
    "Enter the base URL and your seller credentials for Trendyol International.": "Introdu URL-ul de bază și credențialele de vânzător pentru Trendyol International.",
    "Initial sync": "Sincronizare inițială",
    "Pull your Oblio products and Trendyol listings now.": "Importă acum produsele Oblio și anunțurile Trendyol.",
    "Ready to sync.": "Gata de sincronizare.",
    "Connected successfully.": "Conectare reușită.",
    "Connected previously — Next to continue, or re-test if anything changed.": "Conectat anterior — apasă Next pentru a continua, sau retestează dacă s-a schimbat ceva.",
    "Credentials changed — please re-test.": "Credențialele s-au schimbat — te rog retestează.",
    "Please fill email, secret and CIF.": "Te rog completează email, secret și CIF.",
    "Please fill all fields (integration reference is optional for Trendyol TR).": "Te rog completează toate câmpurile (referința de integrare e opțională pentru Trendyol TR).",
    "Account email": "Email cont",
    "API secret": "Secret API",
    "Company CIF": "CIF firmă",
    "Workstation (workStation)": "Punct de lucru (workStation)",
    "Management (gestiune, optional)": "Gestiune (opțional)",
    "Base URL": "URL de bază",
    "Seller ID (ID entitate)": "ID Vânzător (ID entitate)",
    "Integration reference (Cod referință)": "Referință integrare (Cod referință)",
    "API key (Cheie API)": "Cheie API",
    "API secret (Secret API)": "Secret API",

    # ---- Trendyol products tab ----
    "Every listing from your order history, with its mapping status. "
    "New products appear after their first sale — map them here directly.":
        "Toate anunțurile din istoricul comenzilor, cu statusul lor de asociere. "
        "Produsele noi apar după prima vânzare — asociază-le direct de aici.",
    "Filter by title, barcode or product code…": "Filtrează după titlu, cod de bare sau cod produs…",
    "Mapped": "Asociat",
    "Unmapped": "Neasociat",
    "Map as variant…": "Asociază ca variantă…",
    "Convert to bundle…": "Transformă în pachet…",
    "Select a Trendyol listing first.": "Selectează mai întâi un anunț Trendyol.",
    "Which Oblio product is this listing?": "Ce produs Oblio este acest anunț?",
    "Pick a product": "Alege un produs",
    "Select an Oblio product first.": "Selectează mai întâi un produs Oblio.",

    # ---- Mappings tab ----
    "Pick an Oblio product, then connect one or more Trendyol listings to it. "
    "Listings are discovered from your order history — new ones appear after their first sale.":
        "Alege un produs Oblio, apoi conectează unul sau mai multe anunțuri Trendyol. "
        "Anunțurile sunt descoperite din istoricul comenzilor — cele noi apar după prima vânzare.",
    "Search Oblio products…": "Caută produse Oblio…",
    "Select an Oblio product on the left.": "Selectează un produs Oblio din stânga.",
    "Linked Trendyol listings for: ": "Anunțuri Trendyol asociate pentru: ",
    "Trendyol title": "Titlu Trendyol",
    "Barcode": "Cod de bare",
    "Stock code": "Stock code",
    "Buc": "Buc",
    "TVA %": "TVA %",
    "Code": "Cod",
    "Name": "Denumire",
    "Unit": "UM",
    "# linked": "# asociate",
    "Hide listings already mapped": "Ascunde anunțurile deja asociate",
    "Link selected": "Asociază selectate",
    "Pair listings — ": "Asociere anunțuri — ",
    "Linking to: ": "Asociere către: ",
    "Same physical product sold under several Trendyol titles. "
    "Pick the Oblio product, attach all Trendyol variants to it.":
        "Același produs fizic vândut sub mai multe titluri Trendyol. "
        "Alege produsul Oblio, atașează toate variantele Trendyol.",
    "One Trendyol listing that's a kit of multiple Oblio products "
    "(e.g. nailgun + nails). Pick the Trendyol listing, attach each Oblio component with its Buc.":
        "Un anunț Trendyol care e un kit compus din mai multe produse Oblio "
        "(ex: pistol cuie + cuie). Alege anunțul Trendyol, atașează fiecare componentă Oblio cu Buc-ul ei.",
    "Search Trendyol listings…": "Caută anunțuri Trendyol…",
    "Select a Trendyol listing on the left.": "Selectează un anunț Trendyol din stânga.",
    "Components for: ": "Componente pentru: ",
    "Oblio product": "Produs Oblio",
    "# components": "# componente",
    "Add Oblio component…": "Adaugă componentă Oblio…",
    "Add": "Adaugă",
    "Pick a listing": "Alege un anunț",
    "Select a Trendyol listing on the left first.": "Selectează un anunț Trendyol din stânga mai întâi.",
    "Choose Oblio products to add as components of: ":
        "Alege produsele Oblio de adăugat ca și componente pentru: ",
    "Buc:": "Buc:",
    "Title": "Titlu",

    # ---- Invoices tab ----
    "Auto-created from Trendyol orders that are Shipped or Delivered, with all lines mapped. "
    "Double-click a row to see the lines (Trendyol title → Oblio product substitution).":
        "Create automat din comenzi Trendyol cu statut Shipped sau Delivered, cu toate liniile mapate. "
        "Dublu-click pe o linie pentru a vedea conținutul (titlu Trendyol → produs Oblio).",
    "Last 30 days": "Ultimele 30 zile",
    "Last 60 days": "Ultimele 60 zile",
    "Last 90 days": "Ultimele 90 zile",
    "Last 6 months": "Ultimele 6 luni",
    "Last 1 year": "Ultimul an",
    "All time": "Tot timpul",
    "Source": "Sursă",
    "Series": "Seria",
    "Number": "Număr",
    "Order #": "Nr. comandă",
    "Order date": "Data comandă",
    "Status": "Status",
    "Buyer": "Cumpărător",
    "Total": "Total",
    "Issued": "Emis",
    "Issued locally": "Creat local",
    "Last error": "Ultima eroare",
    "Trendyol notify": "Notificare Trendyol",
    "Re-notify Trendyol": "Re-notifică Trendyol",
    "Resend invoice": "Retrimite factura",
    "Hide already-sent invoices": "Ascunde facturile deja trimise",
    "Send to Oblio": "Trimite la Oblio",
    "Send all visible unsent": "Trimite toate (vizibile, netrimise)",
    "Send out multiple invoices": "Trimite mai multe facturi",
    "Send": "Trimite",
    "Dry run (don't actually POST to Oblio)": "Dry run (nu trimite efectiv la Oblio)",
    "Link": "Link",
    "Local": "Local",
    "Oblio": "Oblio",
    "Pick an invoice": "Alege o factură",
    "Select an invoice row first.": "Selectează mai întâi o linie din tabel.",
    "Export invoice": "Exportă factura",
    "Export complete": "Export finalizat",
    "Export failed": "Exportul a eșuat",
    "Choose a folder for invoices": "Alege un folder pentru facturi",
    "Nothing to export": "Nimic de exportat",

    # ---- Campaign rebase ----
    "Take a previous Trendyol campaign export (with Campaign Sale Prices set), "
    "and recompute prices for a new campaign with different discount and "
    "Trendyol-coverage parameters. Match by barcode, fall back to product code. "
    "Capped at Maximum Allowed Price minus 0.01 when needed.":
        "Pornește de la un export Trendyol al unei campanii anterioare (cu prețuri de campanie setate) "
        "și recalculează prețurile pentru o campanie nouă cu alți parametri (discount și acoperire Trendyol). "
        "Match după cod de bare, fallback la cod produs. Plafonat la Preț Maxim Permis − 0.01 când e nevoie.",
    "Excel A — previous campaign (source of prices)": "Excel A — campania anterioară (sursa prețurilor)",
    "Excel B — new-campaign template": "Excel B — șablon campanie nouă",
    "I'll upload my own Excel B (new-campaign template from Trendyol)":
        "Am propriul meu Excel B (șablonul noii campanii de la Trendyol)",
    "New campaign parameters": "Parametrii campaniei noi",
    "Old campaign discount": "Discount campanie veche",
    "Old Trendyol coverage": "Acoperire Trendyol veche",
    "New campaign discount": "Discount campanie nouă",
    "New Trendyol coverage": "Acoperire Trendyol nouă",
    "Path to previous campaign Excel (with prices set)": "Calea către Excel-ul campaniei anterioare (cu prețuri setate)",
    "Path to new-campaign template Excel (Campaign Sale Price column will be filled)":
        "Calea către șablonul campaniei noi (coloana Preț de Campanie va fi completată)",
    "Product code": "Cod produs",
    "Old CSP": "Preț campanie vechi",
    "New CSP": "Preț campanie nou",
    "Max allowed": "Maxim permis",
    "Status": "Status",
    "Keep": "Păstrează",
    "Capped": "Plafonat",
    "OK_STATUS": "OK",

    # ---- Settings ----
    "Reset": "Resetare",
    "Reset all mappings…": "Șterge toate mapările…",
    "Wipe every mapping between Trendyol listings and Oblio products. "
    "All local invoices that were generated from those mappings will be deleted too. "
    "Settings, credentials, orders and Oblio/Trendyol catalogs are NOT touched.":
        "Șterge toate mapările dintre anunțurile Trendyol și produsele Oblio. "
        "Toate facturile locale generate din acele mapări vor fi șterse și ele. "
        "Setările, credențialele, comenzile și cataloagele Oblio/Trendyol NU sunt atinse.",
    "Poll interval": "Interval polling",
    "Default VAT": "TVA implicit",
    "Invoice series": "Seria facturilor",
    "Invoice series (other countries)": "Seria facturilor (alte țări)",
    "Refresh from Oblio": "Încarcă din Oblio",
    "Invoice language": "Limba facturilor",
    "Invoice currency": "Moneda facturilor",
    "Oblio workstation": "Punct de lucru Oblio",
    "Oblio management (optional)": "Gestiune Oblio (opțional)",
    "Company name": "Nume firmă",
    "Company address": "Adresa firmei",
    "Reg Com / IBAN / phone (optional)": "Reg Com / IBAN / telefon (opțional)",
    "Pull the latest products/listings from Oblio and Trendyol. "
    "Existing mappings are preserved as long as the Trendyol listing keeps its ID.":
        "Importă cele mai noi produse/anunțuri din Oblio și Trendyol. "
        "Mapările existente se păstrează atâta vreme cât anunțul Trendyol își păstrează ID-ul.",
}


def t(s: str) -> str:
    return RO.get(s, s)
