"""Generate Binnen-style product_description via OpenRouter (matches table 742 reference)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Real product_description examples from Baserow table 742 (reference database).
REFERENCE_EXAMPLES = """
VOORBEELD 1 — Berlijnse stoel:
In 1923 ontwierp Gerrit Rietveld zijn iconische Berlijnse stoel voor de 'Juryfreie Kunstschau' in Berlijn. Naast de Rood-Blauwe stoel is dit één van Rietvelds meest bekende stoelen. In 1960 ontwierp Rietveld twee nieuwe versies van de stoel voor de bestuurskamer van de Rijksacademie van beeldende Kunsten in Amsterdam.
Rietveld bouwde de Berlijnse stoel op vanuit acht losse planken. De stoel wordt gemaakt uit massief eiken panelen en is gelakt in wit, zwart en grijs. De armleuning kan zowel rechts als links geplaatst worden.
De Berlijnse stoel is onderdeel van de Rietveld Originals collectie die wereldwijd exclusief door Spectrum wordt gevoerd.

VOORBEELD 2 — BZ lattenbank:
Museaal en minimalistisch: de BZ lattenbank werd in 1960 door Martin Visser ontworpen voor het Stedelijk Museum in Amsterdam. Het ontwerp werd zo goed ontvangen dat Spectrum besloot het bankje in de collectie op te nemen.
Het tijdloze design en de heldere vormtaal maken de lattenbank een echte klassieker. Door de beschikbare lengte-, breedte- en hoogtematen kan de BZ bank op veel manieren gebruikt worden; als salontafel, dressoir, bankje in de hal en aan het voeteneind van het bed. Het bankje is verkrijgbaar in massief eiken en daarnaast in massief essen, blank, donkerbruin en zwart gebeitst.

VOORBEELD 3 — DZ 05 kapstok:
Voor de collectie van Spectrum maakte Friso Kramer in 1954 de DH05 kapstok. Dit is meer dan alleen een functioneel item – het is een waar decorstuk in huis. In 2023 werd de DH05 kapstok opnieuw uitgebracht, onder creatieve leiding van Studio RENS.
De kapstok heeft een minimalistisch design en is gemaakt van geëpoxeerd staal. De kapstok is voorzien van een hoedenplank, heeft zeven witte knoppen en is beschikbaar in twee kleuren: geel en zwart. De kapstok heeft een breedte van 91 centimeter, een hoogte van 27 centimeter en een diepte van 17 centimeter.
"""

SYSTEM_PROMPT = f"""Je herschrijft productbeschrijvingen voor de Binnen meubeldatabase.
Schrijf ALLEEN het veld product_description — exact zoals de referentieproducten hieronder (table 742).

STIJLREGELS (strikt volgen):
- Taal: Nederlands
- 2 tot 4 alinea's, gescheiden door één lege regel (\\n\\n)
- Informatief/catalogus-toon: alsof het van de merkwebsite komt
- Eerste alinea: ontwerper, jaar, ontstaan of context (indien bekend uit bron)
- Vervolg: constructie, materialen, afmetingen, kleuren, varianten (alleen als in bron)
- Laatste alinea (optioneel): collectie, beschikbaarheid, certificaat
- GEEN opsommingstekens, GEEN markdown, GEEN **vet**, GEEN marketing-CTA
- GEEN koppen zoals "Belangrijkste Kenmerken" of "Technische Details"
- GEEN "Ontdek", "Voeg toe", "Maak kennis met" — dat is winkeltekst, niet product_description
- Verzin geen feiten die niet in de bron staan (geen kleuren, maten of materialen toevoegen als die niet in de bron staan)
- Als jaar of ontwerper onbekend is: laat weg, niet gokken
- Behoud product- en merknamen
- Toon: neutraal en feitelijk, zoals een museumcatalogus of merkwebsite — niet wervend

REFERENTIEVOORBEELDEN (table 742):
{REFERENCE_EXAMPLES}

Antwoord ALLEEN met geldige JSON:
{{"product_description":"..."}}
"""


@dataclass(frozen=True)
class EnhancedDescriptions:
    product_description: str


def _parse_json_content(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if isinstance(data, dict):
        desc = str(data.get("product_description", "")).strip()
    else:
        desc = str(data).strip()
    if not desc:
        raise ValueError("AI returned empty product_description")
    return desc


def enhance_product_description_nl(
    *,
    product_name: str,
    raw_description: str,
    designer: str = "",
    category: str = "",
    brand: str = "",
    api_key: str,
    model: str = "openai/gpt-4o-mini",
    timeout: float = 60,
) -> EnhancedDescriptions:
    """Rewrite scraped text into table-742 product_description style (Dutch, factual)."""
    raw_description = (raw_description or "").strip()
    if not raw_description:
        raise ValueError("raw_description is empty")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set")

    user_parts = [
        f"Product name: {product_name}",
        f"Brand: {brand or '(unknown)'}",
        f"Category: {category or '(unknown)'}",
        f"Designer: {designer or '(unknown)'}",
        "",
        "Bron (website scrape — gebruik alleen deze feiten):",
        raw_description,
    ]

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(user_parts)},
            ],
            "temperature": 0.3,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError(f"No choices in OpenRouter response: {payload}")

    content = choices[0].get("message", {}).get("content", "")
    desc = _parse_json_content(content)
    return EnhancedDescriptions(product_description=desc)


def apply_ai_descriptions_if_enabled(product, settings) -> None:
    """On --save: keep scraped product_description; write AI rewrite to ai_description_translated_NL."""
    if not settings.ai_product_descriptions or not settings.openrouter_api_key:
        return
    raw = (product.product_description or "").strip()
    if not raw:
        return
    try:
        enhanced = enhance_product_description_nl(
            product_name=product.product_name,
            raw_description=raw,
            designer=product.designer,
            category=product.product_category,
            brand=product.Brand_table,
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            timeout=max(settings.http_timeout, 60),
        )
        product.ai_description_translated_NL = enhanced.product_description
    except Exception as exc:
        print(f"  AI description warning ({product.product_name}): {exc}")
