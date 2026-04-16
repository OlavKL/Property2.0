import json
import re
from urllib.parse import urlparse, urlunparse

import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="Utleie-kalkulator", layout="wide")

st.title("Utleiekalkulator")
st.write("Beregn egenkapital, lånekostnader, total EK-belastning og netto kontantstrøm før skatt.")


# -------------------------
# Hjelpefunksjoner: format
# -------------------------
def format_nok(value: float, use_mill_threshold: int = 0) -> str:
    """Format a number as NOK. If use_mill_threshold > 0 and value >= threshold, use mill format."""
    sign = "-" if value < 0 else ""
    abs_val = abs(value)
    if use_mill_threshold and abs_val >= use_mill_threshold:
        mill = abs_val / 1_000_000
        formatted = f"{mill:.3f}".rstrip("0").rstrip(".")
        return f"{sign}{formatted} mill"
    return f"{sign}{abs_val:,.0f} kr".replace(",", "\u00a0")


def format_mill(value: float) -> str:
    return format_nok(value, use_mill_threshold=1_000_000)


# -------------------------
# Hjelpefunksjoner: FINN-url
# -------------------------
def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    cleaned = parsed._replace(fragment="")
    return urlunparse(cleaned)


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.text


def clean_text(value) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def extract_first_number(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def find_json_ld_objects(soup: BeautifulSoup) -> list[dict]:
    objects = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                objects.extend([x for x in data if isinstance(x, dict)])
            elif isinstance(data, dict):
                objects.append(data)
        except Exception:
            continue
    return objects


def recursive_find_value(obj, wanted_keys: set[str]):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in wanted_keys:
                return v
            found = recursive_find_value(v, wanted_keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = recursive_find_value(item, wanted_keys)
            if found is not None:
                return found
    return None


def normalize_ownership(value: str | None) -> str | None:
    if not value:
        return None
    v = value.lower().strip()
    if "selveier" in v:
        return "Selveier"
    if "andel" in v:
        return "Andel"
    if "aksje" in v:
        return "Aksje"
    if "borettslag" in v:
        return "Andel"
    return clean_text(value)


def is_valid_area(area: str | None) -> bool:
    if not area:
        return False
    area = clean_text(area)
    if not area:
        return False
    bad_fragments = [
        "vedlikeholdsfond", "felleskost", "prisantydning", "totalpris",
        "omkostninger", "andel fellesgjeld", "kommunale avgifter",
        "strøm", "soverom", "inkluderer",
    ]
    lower_area = area.lower()
    if any(fragment in lower_area for fragment in bad_fragments):
        return False
    if len(area) < 2 or len(area) > 40:
        return False
    return True


def extract_area_from_address(address: str | None) -> str | None:
    if not address:
        return None
    match = re.search(r",\s*\d{4}\s+([A-ZÆØÅa-zæøå .\-]+)$", address)
    if match:
        candidate = clean_text(match.group(1))
        if is_valid_area(candidate):
            return candidate
    return None


def extract_address_from_kart_line(full_text: str) -> str | None:
    patterns = [
        r"Kart\s+([A-ZÆØÅa-zæøå0-9.\- ]+\d+[A-Za-z]?,\s*\d{4}\s+[A-ZÆØÅa-zæøå .\-]+)",
        r"Kart\s+([A-ZÆØÅa-zæøå0-9.\- ]+,\s*\d{4}\s+[A-ZÆØÅa-zæøå .\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, full_text, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
    return None


def extract_address_candidates(text: str) -> list[str]:
    if not text:
        return []
    patterns = [
        r"\b[A-ZÆØÅ][A-Za-zÆØÅæøå0-9.\- ]{2,40}\s+\d+[A-Za-z]?,\s*\d{4}\s+[A-ZÆØÅ][A-Za-zÆØÅæøå.\- ]{2,30}\b",
        r"\b[A-ZÆØÅ][A-Za-zÆØÅæøå0-9.\- ]{2,40}\s+\d+[A-Za-z]?\s*,\s*\d{4}\s+[A-ZÆØÅ][A-Za-zÆØÅæøå.\- ]{2,30}\b",
    ]
    candidates = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            cleaned = clean_text(match)
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
    return candidates


def choose_best_address(candidates: list[str]) -> str | None:
    if not candidates:
        return None
    candidates = sorted(candidates, key=len)
    for candidate in candidates:
        if "," in candidate and re.search(r"\d{4}", candidate):
            return candidate
    return candidates[0]


def extract_address_from_links(soup: BeautifulSoup) -> str | None:
    candidates = []
    for a_tag in soup.find_all("a", href=True):
        text = clean_text(a_tag.get_text(" ", strip=True))
        if not text:
            continue
        candidates.extend(extract_address_candidates(text))
    return choose_best_address(candidates)


def extract_address_from_visible_text(full_text: str) -> str | None:
    candidates = extract_address_candidates(full_text)
    return choose_best_address(candidates)


def extract_address_from_raw_html(html: str) -> str | None:
    candidates = extract_address_candidates(html)
    return choose_best_address(candidates)


def extract_area_from_uppercase_line(full_text: str) -> str | None:
    for line in full_text.split("  "):
        candidate = clean_text(line)
        if not candidate:
            continue
        if re.fullmatch(r"[A-ZÆØÅ/\- ]{3,40}", candidate):
            candidate = candidate.title()
            if is_valid_area(candidate):
                return candidate
    return None


def extract_area_from_breadcrumb(full_text: str) -> str | None:
    match = re.search(
        r"Eiendom\s*/\s*Bolig til salgs\s*/\s*[A-ZÆØÅa-zæøå .\-]+\s*/\s*([A-ZÆØÅa-zæøå .\-]+)(?:\s*/\s*([A-ZÆØÅa-zæøå .\-]+))?",
        full_text
    )
    if match:
        second = clean_text(match.group(2)) if match.group(2) else None
        first = clean_text(match.group(1))
        if second and is_valid_area(second):
            return second
        if first and is_valid_area(first):
            return first
    return None


def estimate_rent_from_bedrooms(bedrooms: int | None) -> int | None:
    if not bedrooms or bedrooms <= 0:
        return None
    if bedrooms == 1:
        return 9_000
    if bedrooms >= 4:
        return bedrooms * 6_000
    return bedrooms * 6_500


def normalize_lookup_text(text: str | None) -> str:
    if not text:
        return ""
    text = clean_text(text) or ""
    return re.sub(r"\s+", " ", text.lower()).strip()


# -------------------------
# Eiendomsskatt
# -------------------------
MUNICIPALITY_TAX_RATES: dict[str, float] = {
    "Kristiansand": 1.96,
    "Grimstad": 0.0,
    "Lillesand": 0.0,
    "Arendal": 0.0,
    "Oslo": 0.0,
    "Bergen": 2.0,
    "Stavanger": 0.0,
    "Trondheim": 0.0,
}


def detect_municipality(area: str | None, address: str | None) -> str | None:
    candidates = []
    if area:
        candidates.append(area)
    if address:
        candidates.append(address)
        address_area = extract_area_from_address(address)
        if address_area:
            candidates.append(address_area)

    for candidate in candidates:
        value = normalize_lookup_text(candidate)
        for municipality in MUNICIPALITY_TAX_RATES:
            if municipality.lower() in value:
                return municipality
    return None


def get_property_tax_rate_per_mille(municipality: str | None) -> float:
    return MUNICIPALITY_TAX_RATES.get(municipality, 0.0)


def estimate_property_tax(
    purchase_price: float,
    municipality: str | None,
    valuation_factor: float = 0.85,
) -> tuple[float, float, str | None]:
    if purchase_price <= 0:
        return 0.0, 0.0, municipality
    rate_per_mille = get_property_tax_rate_per_mille(municipality)
    if rate_per_mille <= 0:
        return 0.0, 0.0, municipality
    taxable_value = purchase_price * valuation_factor
    annual = taxable_value * (rate_per_mille / 1000)
    return annual, annual / 12, municipality


# -------------------------
# FINN-scraping
# -------------------------
def _extract_price(full_text: str, soup: BeautifulSoup, jsonld_objects: list[dict]) -> int | None:
    # Try JSON-LD first
    for obj in jsonld_objects:
        jsonld_price = recursive_find_value(obj, {"price"})
        if jsonld_price is not None:
            parsed = extract_first_number(str(jsonld_price))
            if parsed:
                return parsed

    # Then text patterns
    for pattern in [
        r"Totalpris\s*([\d\s\u00A0.,]+)\s*kr",
        r"Prisantydning\s*([\d\s\u00A0.,]+)\s*kr",
    ]:
        match = re.search(pattern, full_text, flags=re.IGNORECASE)
        if match:
            parsed = extract_first_number(match.group(1))
            if parsed:
                return parsed
    return None


def _extract_common_costs(full_text: str) -> int | None:
    for pattern in [
        r"Felleskost/mnd\.?\s*([\d\s\u00A0.,]+)\s*kr",
        r"Felleskostnader\s*([\d\s\u00A0.,]+)\s*kr",
        r"Felleskostnader pr\. mnd\.?\s*([\d\s\u00A0.,]+)\s*kr",
        r"Felleskostnader per måned\s*([\d\s\u00A0.,]+)\s*kr",
    ]:
        match = re.search(pattern, full_text, flags=re.IGNORECASE)
        if match:
            parsed = extract_first_number(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _extract_ownership(full_text: str) -> str | None:
    for pattern in [
        r"Eieform\s*(Selveier|Andel|Aksje|Borettslag)",
        r"Eierform\s*(Selveier|Andel|Aksje|Borettslag)",
        r"\b(selveier|andel|aksje|borettslag)\b",
    ]:
        match = re.search(pattern, full_text, flags=re.IGNORECASE)
        if match:
            raw = match.group(1) if match.groups() else match.group(0)
            return normalize_ownership(raw)
    return None


def _extract_bedrooms(full_text: str) -> int | None:
    for pattern in [r"(\d+)\s+soverom", r"Soverom\s*(\d+)", r"(\d+)\s+sov"]:
        match = re.search(pattern, full_text, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
    return None


def _extract_area(full_text: str, soup: BeautifulSoup, address: str | None, jsonld_objects: list[dict]) -> str | None:
    # From address
    if address:
        candidate = extract_area_from_address(address)
        if candidate:
            return candidate

    # From JSON-LD
    for obj in jsonld_objects:
        jsonld_area = recursive_find_value(obj, {"addresslocality", "addressregion", "locality"})
        if isinstance(jsonld_area, str):
            candidate = clean_text(jsonld_area)
            if is_valid_area(candidate):
                return candidate

    # From page text
    candidate = extract_area_from_uppercase_line(full_text)
    if candidate:
        return candidate

    candidate = extract_area_from_breadcrumb(full_text)
    if candidate:
        return candidate

    return None


def parse_finn_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    full_text = clean_text(soup.get_text(" ", strip=True)) or ""
    jsonld_objects = find_json_ld_objects(soup)

    address = extract_address_from_kart_line(full_text)
    if not address:
        address = (
            extract_address_from_links(soup)
            or extract_address_from_visible_text(full_text)
            or extract_address_from_raw_html(html)
        )

    area = _extract_area(full_text, soup, address, jsonld_objects)
    bedrooms = _extract_bedrooms(full_text)

    return {
        "purchase_price": _extract_price(full_text, soup, jsonld_objects),
        "common_costs": _extract_common_costs(full_text),
        "area": area if is_valid_area(area) else None,
        "address": address,
        "ownership": _extract_ownership(full_text),
        "bedrooms": bedrooms,
        "estimated_rent": estimate_rent_from_bedrooms(bedrooms),
    }


# -------------------------
# Låneberegning
# -------------------------
def annuity_payment(principal: float, annual_rate_percent: float, years: int) -> float:
    months = years * 12
    monthly_rate = annual_rate_percent / 100 / 12
    if principal <= 0 or months <= 0:
        return 0.0
    if monthly_rate == 0:
        return principal / months
    return principal * (monthly_rate * (1 + monthly_rate) ** months) / ((1 + monthly_rate) ** months - 1)


def serial_schedule_first_month(principal: float, annual_rate_percent: float, years: int) -> tuple[float, float, float]:
    months = years * 12
    monthly_rate = annual_rate_percent / 100 / 12
    if principal <= 0 or months <= 0:
        return 0.0, 0.0, 0.0
    monthly_principal = principal / months
    first_month_interest = principal * monthly_rate
    return monthly_principal + first_month_interest, monthly_principal, first_month_interest


def serial_schedule_last_month(principal: float, annual_rate_percent: float, years: int) -> tuple[float, float, float]:
    months = years * 12
    monthly_rate = annual_rate_percent / 100 / 12
    if principal <= 0 or months <= 0:
        return 0.0, 0.0, 0.0
    monthly_principal = principal / months
    last_month_interest = monthly_principal * monthly_rate
    return monthly_principal + last_month_interest, monthly_principal, last_month_interest


def monthly_payment_by_loan_type(principal: float, annual_rate_percent: float, years: int, loan_type: str) -> float:
    if loan_type == "Annuitetslån":
        return annuity_payment(principal, annual_rate_percent, years)
    total, _, _ = serial_schedule_first_month(principal, annual_rate_percent, years)
    return total


def break_even_rate(
    loan_amount: float,
    repayment_years: int,
    loan_type: str,
    monthly_rent: float,
    monthly_operating_costs: float,
    step_size: float = 0.01,
    max_rate: float = 25.0,
) -> float | None:
    """Find the interest rate at which cashflow turns negative."""
    rate = 0.0
    while rate <= max_rate:
        cost = monthly_payment_by_loan_type(loan_amount, rate, repayment_years, loan_type)
        if monthly_rent - monthly_operating_costs - cost < 0:
            return rate
        rate += step_size
    return None


def calculate_rate_hikes_tolerated(
    loan_amount: float,
    base_nominal_rate: float,
    repayment_years: int,
    loan_type: str,
    monthly_rent: float,
    monthly_operating_costs: float,
    step_size: float = 0.25,
    max_steps: int = 100,
) -> int:
    for step in range(1, max_steps + 1):
        test_rate = base_nominal_rate + step * step_size
        test_cost = monthly_payment_by_loan_type(loan_amount, test_rate, repayment_years, loan_type)
        if monthly_rent - monthly_operating_costs - test_cost < 0:
            return step - 1
    return max_steps


def build_amortization_series(
    principal: float,
    annual_rate_percent: float,
    years: int,
    loan_type: str,
) -> pd.DataFrame:
    """Build a yearly summary of remaining balance, interest paid, and principal paid."""
    months = years * 12
    monthly_rate = annual_rate_percent / 100 / 12
    rows = []
    balance = principal

    if loan_type == "Annuitetslån":
        monthly_total = annuity_payment(principal, annual_rate_percent, years)
        for month in range(1, months + 1):
            interest = balance * monthly_rate
            principal_payment = monthly_total - interest
            balance = max(0.0, balance - principal_payment)
            if month % 12 == 0:
                rows.append({"År": month // 12, "Restgjeld": balance})
    else:
        monthly_principal = principal / months
        for month in range(1, months + 1):
            balance = max(0.0, balance - monthly_principal)
            if month % 12 == 0:
                rows.append({"År": month // 12, "Restgjeld": balance})

    return pd.DataFrame(rows)


# -------------------------
# Session state defaults
# -------------------------
defaults = {
    "purchase_price": 3_000_000,
    "equity_percent": 15,
    "max_loan_amount": 2_550_000,
    "closing_cost_percent": 2.5,
    "monthly_rent": 18_000,
    "electricity": 1_000,
    "common_costs": 2_500,
    "municipal_fees": 800,
    "other_costs": 500,
    "loan_type": "Annuitetslån",
    "rate_type": "Nominell rente",
    "rate_input": 4.85,
    "repayment_years": 30,
    "finn_url": "",
    "detected_area": "",
    "detected_address": "",
    "detected_ownership": "",
    "detected_bedrooms": 0,
    "detected_estimated_rent": 0,
    "_prev_purchase_price": 3_000_000,
    "_prev_equity_percent": 15,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# -------------------------
# Auto-sync max_loan_amount when purchase_price or equity_percent changes
# -------------------------
def _auto_sync_loan():
    price = st.session_state.get("purchase_price", 0)
    eq_pct = st.session_state.get("equity_percent", 15)
    prev_price = st.session_state.get("_prev_purchase_price", price)
    prev_eq = st.session_state.get("_prev_equity_percent", eq_pct)

    if price != prev_price or eq_pct != prev_eq:
        st.session_state["max_loan_amount"] = int(price * (1 - eq_pct / 100))
        st.session_state["_prev_purchase_price"] = price
        st.session_state["_prev_equity_percent"] = eq_pct


_auto_sync_loan()


# -------------------------
# Sidebar: FINN-import
# -------------------------
st.sidebar.header("Hent fra FINN")
st.sidebar.text_input(
    "Lim inn FINN-url",
    key="finn_url",
    placeholder="https://www.finn.no/realestate/homes/ad.html?finnkode=..."
)

if st.sidebar.button("Hent fra annonse"):
    url = normalize_url(st.session_state["finn_url"])
    if not url:
        st.sidebar.warning("Lim inn en URL først.")
    else:
        try:
            with st.sidebar.spinner("Henter annonse..."):
                html = fetch_html(url)
            scraped = parse_finn_page(html)
            found_anything = False

            updates = {
                "purchase_price": scraped["purchase_price"],
                "common_costs": scraped["common_costs"],
            }
            for key, val in updates.items():
                if val is not None:
                    st.session_state[key] = val
                    found_anything = True

            for key, scraped_key in [
                ("detected_address", "address"),
                ("detected_area", "area"),
                ("detected_ownership", "ownership"),
            ]:
                if scraped[scraped_key]:
                    st.session_state[key] = scraped[scraped_key]
                    found_anything = True

            if scraped["bedrooms"] is not None:
                st.session_state["detected_bedrooms"] = scraped["bedrooms"]
                found_anything = True

            if scraped["estimated_rent"] is not None:
                st.session_state["detected_estimated_rent"] = scraped["estimated_rent"]
                st.session_state["monthly_rent"] = scraped["estimated_rent"]
                found_anything = True

            ownership = scraped.get("ownership")
            if ownership in ("Andel", "Aksje"):
                st.session_state["closing_cost_percent"] = 0.0
            elif ownership == "Selveier" and st.session_state["closing_cost_percent"] == 0.0:
                st.session_state["closing_cost_percent"] = 2.5

            # Re-sync loan after price update
            _auto_sync_loan()

            if found_anything:
                st.sidebar.success("Fant data og fylte inn det som var tilgjengelig.")
            else:
                st.sidebar.warning("Fant ingen tydelige felter i annonsen. Legg inn manuelt.")

        except requests.HTTPError as e:
            st.sidebar.error(f"HTTP-feil: {e}")
        except requests.RequestException as e:
            st.sidebar.error(f"Nettverksfeil: {e}")
        except Exception as e:
            st.sidebar.error(f"Noe gikk galt: {e}")

if st.session_state["detected_address"]:
    st.sidebar.caption(f"Adresse: {st.session_state['detected_address']}")
if st.session_state["detected_area"]:
    st.sidebar.caption(f"Område: {st.session_state['detected_area']}")
if st.session_state["detected_ownership"]:
    st.sidebar.caption(f"Eierform: {st.session_state['detected_ownership']}")
if st.session_state["detected_bedrooms"]:
    st.sidebar.caption(f"Soverom: {st.session_state['detected_bedrooms']}")
if st.session_state["detected_estimated_rent"]:
    st.sidebar.caption(f"Estimert leie: {format_nok(st.session_state['detected_estimated_rent'])}")


# -------------------------
# Sidebar: inputs
# -------------------------
st.sidebar.header("Inndata")

purchase_price = st.sidebar.number_input("Kjøpesum", min_value=0, step=50_000, key="purchase_price")
equity_percent = st.sidebar.slider("EK-krav (%)", min_value=0, max_value=100, step=1, key="equity_percent")
max_loan_amount = st.sidebar.number_input("Maks lån", min_value=0, step=50_000, key="max_loan_amount")
closing_cost_percent = st.sidebar.number_input("Omkostninger / dokumentavgift (%)", min_value=0.0, max_value=20.0, step=0.1, key="closing_cost_percent")
monthly_rent = st.sidebar.number_input("Månedlig leie", min_value=0, step=500, key="monthly_rent")
electricity = st.sidebar.number_input("Strøm per måned", min_value=0, step=100, key="electricity")
common_costs = st.sidebar.number_input("Felleskost per måned", min_value=0, step=100, key="common_costs")
municipal_fees = st.sidebar.number_input("Kommunale avgifter per måned", min_value=0, step=100, key="municipal_fees")
other_costs = st.sidebar.number_input("Andre kostnader per måned", min_value=0, step=100, key="other_costs")
loan_type = st.sidebar.selectbox("Lånetype", ["Annuitetslån", "Serielån"], key="loan_type")
rate_type = st.sidebar.selectbox("Rentetype", ["Nominell rente", "Effektiv rente"], key="rate_type")
rate_input = st.sidebar.number_input("Rente (%)", min_value=0.0, max_value=20.0, step=0.1, key="rate_input")
repayment_years = st.sidebar.number_input("Nedbetalingstid (år)", min_value=1, max_value=40, step=1, key="repayment_years")

# Input validation warnings
if monthly_rent < 1000 and monthly_rent > 0:
    st.sidebar.warning("Månedlig leie virker veldig lav – er den riktig?")
if rate_input < 1.0 and rate_input > 0:
    st.sidebar.warning("Renten virker veldig lav – er den i prosent?")
if equity_percent < 15:
    st.sidebar.warning("Under 15 % EK er under minstekravet for utleiebolig i Norge.")


# -------------------------
# Beregninger: eiendomsskatt
# -------------------------
area_for_tax = st.session_state["detected_area"]
address_for_tax = st.session_state["detected_address"]
municipality_for_tax = detect_municipality(area_for_tax, address_for_tax)
annual_property_tax, monthly_property_tax, detected_municipality = estimate_property_tax(
    purchase_price=purchase_price,
    municipality=municipality_for_tax,
    valuation_factor=0.85,
)


# -------------------------
# Info fra annonse
# -------------------------
has_scraped_data = any([
    st.session_state["detected_area"],
    st.session_state["detected_address"],
    st.session_state["detected_ownership"],
    st.session_state["purchase_price"] != defaults["purchase_price"],
    st.session_state["common_costs"] != defaults["common_costs"],
    st.session_state["detected_bedrooms"],
    st.session_state["detected_estimated_rent"],
])

if has_scraped_data:
    st.subheader("Data hentet fra annonse")
    area_to_show = st.session_state["detected_area"] or extract_area_from_address(st.session_state["detected_address"])
    detected_municipality_preview = detect_municipality(area_to_show, st.session_state["detected_address"])
    annual_tax_preview, _, _ = estimate_property_tax(
        purchase_price=st.session_state["purchase_price"],
        municipality=detected_municipality_preview,
        valuation_factor=0.85,
    )

    col1, col2 = st.columns(2)
    with col1:
        st.write("**Kjøpesum:**", format_mill(st.session_state["purchase_price"]) if st.session_state["purchase_price"] else "Fant ikke")
        st.write("**Felleskost:**", format_nok(st.session_state["common_costs"]) if st.session_state["common_costs"] else "Fant ikke")
        st.write("**Adresse:**", st.session_state["detected_address"] or "Fant ikke")
        st.write("**Soverom:**", st.session_state["detected_bedrooms"] if st.session_state["detected_bedrooms"] else "Fant ikke")
    with col2:
        st.write("**Område:**", area_to_show or "Fant ikke")
        st.write("**Kommune:**", detected_municipality_preview or "Fant ikke / ikke støttet")
        st.write("**Eierform:**", st.session_state["detected_ownership"] or "Fant ikke")
        st.write("**Estimert leie:**", format_nok(st.session_state["detected_estimated_rent"]) if st.session_state["detected_estimated_rent"] else "Fant ikke")
        st.write(
            "**Estimert eiendomsskatt:**",
            format_nok(annual_tax_preview) + " / år" if annual_tax_preview > 0 else "Ikke beregnet"
        )

    if detected_municipality_preview is None:
        st.info(
            f"Kommunen ble ikke gjenkjent fra adressen. "
            f"Støttede kommuner: {', '.join(MUNICIPALITY_TAX_RATES.keys())}. "
            "Eiendomsskatt er satt til 0."
        )

    st.caption("Estimert leie er foreløpig basert på antall soverom fra annonsen.")
    st.divider()


# -------------------------
# Beregninger: EK og finansiering
# -------------------------
closing_costs = purchase_price * (closing_cost_percent / 100)
required_equity_base = purchase_price * (equity_percent / 100)
loan_amount = min(max_loan_amount, purchase_price)
ltv_percent = (loan_amount / purchase_price * 100) if purchase_price > 0 else 0.0
purchase_gap_due_to_loan_limit = max(0, purchase_price - max_loan_amount - required_equity_base)
minimum_cash_needed_to_close = purchase_price + closing_costs - max_loan_amount
total_equity_needed = required_equity_base + closing_costs + purchase_gap_due_to_loan_limit


# -------------------------
# Beregninger: rente, drift og yield
# -------------------------
if rate_type == "Nominell rente":
    nominal_rate = rate_input
    effective_rate = ((1 + nominal_rate / 100 / 12) ** 12 - 1) * 100
else:
    effective_rate = rate_input
    nominal_rate = 12 * ((1 + effective_rate / 100) ** (1 / 12) - 1) * 100

annual_rent = monthly_rent * 12
gross_yield_percent = (annual_rent / (purchase_price + closing_costs) * 100) if (purchase_price + closing_costs) > 0 else 0.0
monthly_operating_costs = electricity + common_costs + municipal_fees + other_costs + monthly_property_tax

if loan_type == "Annuitetslån":
    monthly_loan_cost = annuity_payment(loan_amount, nominal_rate, repayment_years)
    monthly_principal_payment = None
    monthly_interest_payment = None
    loan_info_text = "Fast terminbeløp hver måned."
else:
    first_total, first_principal, first_interest = serial_schedule_first_month(loan_amount, nominal_rate, repayment_years)
    last_total, _, _ = serial_schedule_last_month(loan_amount, nominal_rate, repayment_years)
    monthly_loan_cost = first_total
    monthly_principal_payment = first_principal
    monthly_interest_payment = first_interest
    loan_info_text = "Terminbeløpet er høyest i starten og synker over tid."

monthly_cashflow_before_tax = monthly_rent - monthly_operating_costs - monthly_loan_cost
annual_cashflow_before_tax = monthly_cashflow_before_tax * 12
break_even_rent = monthly_operating_costs + monthly_loan_cost

rate_hikes_tolerated = calculate_rate_hikes_tolerated(
    loan_amount=loan_amount,
    base_nominal_rate=nominal_rate,
    repayment_years=repayment_years,
    loan_type=loan_type,
    monthly_rent=monthly_rent,
    monthly_operating_costs=monthly_operating_costs,
)

be_rate = break_even_rate(
    loan_amount=loan_amount,
    repayment_years=repayment_years,
    loan_type=loan_type,
    monthly_rent=monthly_rent,
    monthly_operating_costs=monthly_operating_costs,
)


# -------------------------
# Nøkkeltall
# -------------------------
st.subheader("Nøkkeltall")

col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    st.metric("Kjøpesum", format_mill(purchase_price), help=format_nok(purchase_price))
with col2:
    st.metric("Brutto yield", f"{gross_yield_percent:.2f} %", help="(Månedlig leie × 12) / (kjøpesum + omkostninger).")
with col3:
    st.metric("Break-even leie", format_nok(break_even_rent))
with col4:
    st.metric(
        "Eiendomsskatt / år",
        format_nok(annual_property_tax) if annual_property_tax > 0 else "Ikke beregnet",
        help=f"Estimert som kjøpesum × 85 % × kommunens promillesats."
    )
with col5:
    st.metric(
        "Netto kontantstrøm / mnd",
        format_nok(monthly_cashflow_before_tax),
        help="Leie minus alle kostnader inkludert terminbeløp på lånet. Viser faktisk penger inn/ut per måned."
    )
with col6:
    hike_help = (
        f"Antall rentehopp à 0,25 %-poeng før netto kontantstrøm blir negativ. "
        + (f"Break-even skjer ved {be_rate:.2f} % nominell rente." if be_rate else "Tåler alle testede rentehopp.")
    )
    st.metric("Rente-stresstest", f"{rate_hikes_tolerated} stk", help=hike_help)

st.divider()


# -------------------------
# EK-struktur + diagram
# -------------------------
left_top, right_top = st.columns([1, 1])

with left_top:
    st.subheader(f"Kontantbehov: {format_nok(total_equity_needed)}")

    ek_krav = required_equity_base
    omkost = closing_costs
    ekstra_ek = purchase_gap_due_to_loan_limit

    fig, ax = plt.subplots(figsize=(5, 6))
    ax.bar(["Totalt EK-behov"], [ek_krav], label="EK-krav")
    ax.bar(["Totalt EK-behov"], [omkost], bottom=[ek_krav], label="Omkostninger / dokumentavgift")
    ax.bar(["Totalt EK-behov"], [ekstra_ek], bottom=[ek_krav + omkost], label="Ekstra EK pga. lånegrense")

    for (segment_val, segment_bottom, label) in [
        (ek_krav, 0, f"EK-krav\n{format_nok(ek_krav)}"),
        (omkost, ek_krav, f"Omkost\n{format_nok(omkost)}"),
        (ekstra_ek, ek_krav + omkost, f"Ekstra EK\n{format_nok(ekstra_ek)}"),
    ]:
        if segment_val > 0:
            ax.text(0, segment_bottom + segment_val / 2, label,
                    ha="center", va="center", color="white", fontsize=10, fontweight="bold")

    ax.set_ylabel("Beløp (kr)")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    st.pyplot(fig)

with right_top:
    st.subheader("EK-struktur")
    equity_df = pd.DataFrame({
        "Post": [
            "EK-krav", "Omkostninger / dokumentavgift", "Ekstra EK pga. lånebegrensning",
            "Totalt EK-behov", "Maks lån", "Belåningsgrad", "Minimum kontantbehov for å lukke kjøpet",
        ],
        "Verdi": [
            format_nok(required_equity_base), format_nok(closing_costs), format_nok(purchase_gap_due_to_loan_limit),
            format_nok(total_equity_needed), format_nok(max_loan_amount),
            f"{ltv_percent:.1f} %", format_nok(minimum_cash_needed_to_close),
        ],
    })
    st.dataframe(equity_df, use_container_width=True, hide_index=True)

st.divider()


# -------------------------
# Låneberegning og kontantstrøm
# -------------------------
left, right = st.columns([1.2, 1])

with left:
    st.subheader("Låneberegning")

    if loan_type == "Annuitetslån":
        loan_df = pd.DataFrame({
            "Post": ["Lånetype", "Lånebeløp", "Belåningsgrad", "Nominell rente", "Effektiv rente", "Nedbetalingstid", "Månedlig terminbeløp"],
            "Verdi": [loan_type, format_nok(loan_amount), f"{ltv_percent:.1f} %", f"{nominal_rate:.2f} %", f"{effective_rate:.2f} %", f"{repayment_years} år", format_nok(monthly_loan_cost)],
        })
    else:
        loan_df = pd.DataFrame({
            "Post": ["Lånetype", "Lånebeløp", "Belåningsgrad", "Nominell rente", "Effektiv rente", "Nedbetalingstid",
                     "Første måneds avdrag", "Første måneds renter", "Første måneds totalbeløp", "Siste måneds totalbeløp"],
            "Verdi": [loan_type, format_nok(loan_amount), f"{ltv_percent:.1f} %", f"{nominal_rate:.2f} %", f"{effective_rate:.2f} %",
                      f"{repayment_years} år", format_nok(monthly_principal_payment or 0),
                      format_nok(monthly_interest_payment or 0), format_nok(monthly_loan_cost), format_nok(last_total)],
        })

    st.dataframe(loan_df, use_container_width=True, hide_index=True)
    st.caption(loan_info_text)

    # Amortization chart
    with st.expander("Vis restgjeld over tid"):
        amort_df = build_amortization_series(loan_amount, nominal_rate, repayment_years, loan_type)
        if not amort_df.empty:
            fig2, ax2 = plt.subplots(figsize=(6, 3))
            ax2.plot(amort_df["År"], amort_df["Restgjeld"] / 1_000_000)
            ax2.set_xlabel("År")
            ax2.set_ylabel("Restgjeld (mill kr)")
            ax2.spines["top"].set_visible(False)
            ax2.spines["right"].set_visible(False)
            ax2.fill_between(amort_df["År"], amort_df["Restgjeld"] / 1_000_000, alpha=0.15)
            st.pyplot(fig2)

with right:
    st.subheader("Kontantstrøm før skatt")
    cashflow_df = pd.DataFrame({
        "Post": [
            "Månedlig leie", "Strøm", "Felleskost", "Kommunale avgifter",
            "Estimert eiendomsskatt", "Andre kostnader", "Lånekostnad per måned",
            "Netto kontantstrøm per måned", "Netto kontantstrøm per år",
            "Break-even leie per måned", "Yield",
        ],
        "Verdi": [
            format_nok(monthly_rent), format_nok(electricity), format_nok(common_costs),
            format_nok(municipal_fees), format_nok(monthly_property_tax), format_nok(other_costs),
            format_nok(monthly_loan_cost), format_nok(monthly_cashflow_before_tax),
            format_nok(annual_cashflow_before_tax), format_nok(break_even_rent),
            f"{gross_yield_percent:.2f} %",
        ],
    })
    st.dataframe(cashflow_df, use_container_width=True, hide_index=True)

st.divider()


# -------------------------
# Oppsummering
# -------------------------
st.subheader("Oppsummering")

if purchase_gap_due_to_loan_limit > 0:
    st.warning(f"Lånegrensen gjør at du må skyte inn ekstra {format_nok(purchase_gap_due_to_loan_limit)} utover ordinært EK-krav.")
else:
    st.success("Maks lån er høy nok til å dekke kjøpet innenfor valgt EK-krav.")

if monthly_cashflow_before_tax > 0:
    st.success(f"Boligen gir positiv netto kontantstrøm før skatt på {format_nok(monthly_cashflow_before_tax)} per måned.")
elif monthly_cashflow_before_tax < 0:
    st.error(f"Boligen gir negativ netto kontantstrøm før skatt på {format_nok(abs(monthly_cashflow_before_tax))} per måned.")
else:
    st.info("Boligen går omtrent i null før skatt.")

be_rate_text = f"{be_rate:.2f} %" if be_rate else "Tåler alle testede renter"

st.write(f"""
- **Kjøpesum:** {format_nok(purchase_price)}
- **Lånebeløp:** {format_nok(loan_amount)}
- **Belåningsgrad:** {ltv_percent:.1f} %
- **EK-krav:** {format_nok(required_equity_base)}
- **Omkostninger:** {format_nok(closing_costs)}
- **Ekstra EK pga. lånegrense:** {format_nok(purchase_gap_due_to_loan_limit)}
- **Totalt EK-behov:** {format_nok(total_equity_needed)}
- **Månedlige driftskostnader ekskl. lån:** {format_nok(monthly_operating_costs)}
- **Estimert eiendomsskatt:** {format_nok(annual_property_tax)} per år / {format_nok(monthly_property_tax)} per måned
- **Kommune brukt i beregning:** {detected_municipality or "Ikke funnet / ikke støttet"}
- **Break-even leie:** {format_nok(break_even_rent)} per måned
- **Break-even rente:** {be_rate_text}
- **Prosjektert netto kontantstrøm:** {format_nok(monthly_cashflow_before_tax)} per måned
- **Brutto yield:** {gross_yield_percent:.2f} %
- **Antall rentehopp på 0,25 %-poeng du tåler:** {rate_hikes_tolerated}
""")

st.divider()


# -------------------------
# Forklaringer
# -------------------------
with st.expander("Hva betyr tallene?"):
    st.write("""
**Yield** = årlig leieinntekt (månedlig leie × 12) delt på kjøpesum + omkostninger.

**Break-even leie** = hvor høy leien må være for at kontantstrøm før skatt blir 0.

**Break-even rente** = høyeste nominelle rente du tåler før kontantstrømmen blir negativ.

**Prosjektert netto kontantstrøm per måned** = leie minus lånekostnader og øvrige månedlige kostnader.

**Antall rentehopp du tåler** = hvor mange hopp på 0,25 %-poeng renten kan øke før netto månedlig kontantstrøm blir negativ.

**EK-krav** = prosentandel av kjøpesummen du må dekke med egenkapital.

**Omkostninger / dokumentavgift** = transaksjonskostnader som kommer i tillegg til kjøpesummen.

**Ekstra EK pga. lånebegrensning** = ekstra kontanter du må legge inn hvis maks lån er lavere enn det som trengs.

**Totalt EK-behov** = EK-krav + omkostninger + eventuelt ekstra tilskudd fordi lånet ikke dekker nok.

**Estimert eiendomsskatt** = beregnet som justert markedsverdi (85 % av kjøpesum) × kommunens promillesats.
Forutsetter sekundærbolig. Dette er en forenklet modell og ikke en offisiell takst.
Støttede kommuner: """ + ", ".join(MUNICIPALITY_TAX_RATES.keys()) + ".")
