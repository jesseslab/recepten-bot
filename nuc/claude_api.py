"""
claude_api.py — Recipe generation via Anthropic API
"""
import json
import logging
import os
import anthropic

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

FAMILY_CONTEXT = """
Je genereert weekmenu's voor een Nederlands gezin van 4 personen in Amstelveen.

GEZIN:
- 4 personen, grote eters — beide ouders sporten intensief en verbranden veel
- Porties: ~150g vlees/vis per persoon = netto eetbaar gewicht (600g totaal)
  → Boneless (kipfilet, gehakt, vis): 600g totaal rauw
  → Bot-in stukken (kippendijen, spareribs, hele vis): corrigeer voor botverlies (~30-40%) → gebruik 800-900g rauw
- Dochter: glutenallergie — gebruik GF tarwebloem/pasta/paneermeel waar nodig, geef dit aan
- Couscous vermijden in GF-versies — GF couscous smaakt niet goed; gebruik rijst, quinoa of aardappel als alternatief
- Eenheden: altijd metrisch (gram, ml, kg, °C)
- Stijl: clean en gezond maar wel echt lekker — geen dieetrecepten

VLEES & VIS VOORKEUREN:
- Altijd in huis: gehakt (rund), kipfilet, kippendijen, merguezworstjes
- Graag: kip, lam, rund, vis (niet zalm), merguez
- Varkensvlees vermijden als hoofdeiwit — uitzondering: spekjes als smaakmaker (door stamppot/bami),
  spareribs (af en toe), salami
- Geen zalm

APPARATUUR (gebruik dit):
- 2 ovens waarvan 1 combistoomoven
- Sous vide machine
- Green Egg (large) BBQ
- KitchenAid Artisan + gehaktmolen
- Vitamix blender, Magimix keukenmachine
- Alle soorten pannen
- Ruim assortiment kruiden en specerijen

INGREDIËNTEN:
- Snelle weekdaggerechten: houd het bij ~10 ingrediënten (excl. olie, zout, peper)
- Serieuze/lange gerechten: geen limiet, mag complex zijn
- Een paar recepten per week met meer ingrediënten is prima, maar niet allemaal
- Geen obscure speciale ingrediënten die je alleen online vindt

NOOIT GEBRUIKEN:
- Orgaanvlees (lever, nier, hart, zwezerik etc.)
- Slakken / escargot
- Zalm

VERDELING VAN DE 10 RECEPTEN:
- 5x vegetarisch (eiwitrijk en vullend — geen saaie pasta met groente)
- 5x vlees/vis/gevogelte
- Maximaal 2x serieus koken (meerdere componenten, langere bereidingstijd, techniek)
- Mix van snelle weekdaggerechten (30-40 min) en uitgebreidere recepten
- 1-3x wildcard (échte andere keuken — NIET Italiaans of Hollands)
- Reguliere (niet-wildcard) recepten: schroom niet voor Italiaans, Grieks, Spaans,
  Mediterraans — dit is gewenst en wordt te weinig voorgesteld.

WILDCARDS: Denk aan Koreaans, Ethiopisch, Peruaans, Libanees, Japans, Mexicaans,
Vietnamese, Marokkaans, Georgisch etc. — iets wat ze normaal niet koken.

SERIEUS KOKEN: sous vide, slow-braise, Green Egg, zelfgemaakte GF pasta, hele vis,
complexe sauzen. Maak hier gebruik van de beschikbare apparatuur.

VEGETARISCH: niet gewoon pasta met groente. Denk aan dhal, shakshuka, halloumi,
paneer, gevulde paprika's, Aziatische tofu gerechten, linzencurry, etc.
Geen kant-en-klare vleesvervangers (vegaburgers, gehakt, schnitzels uit pak) — eiwitten
uit peulvruchten, eieren, kaas, tofu, tempeh of noten.
"""


async def generate_proposals(top_picks: list, recent_picks: list, blacklist: list = None,
                             liked: list = None, disliked: list = None) -> list:
    """Phase 1: Generate 10 lightweight proposals (no ingredients/steps). Fast."""
    learning_context = ""
    if top_picks:
        favs = ", ".join([f"{r['recipe_name']} ({r['times_picked']}x)" for r in top_picks[:5]])
        learning_context += f"\nFAVORIETEN (vaker voorstellen): {favs}"
    if liked:
        learning_context += f"\nEERDER LEKKER GEVONDEN 👍 (meer van dit soort): {', '.join(liked[:8])}"
    if recent_picks:
        recent = ", ".join(recent_picks[:8])
        learning_context += f"\nRECENT GEKOOKT (vermijd herhaling): {recent}"
    if disliked:
        learning_context += f"\nMINDER LEKKER GEVONDEN 👎 (minder voorstellen of varieer): {', '.join(disliked[:8])}"
    if blacklist:
        learning_context += f"\nNOOIT MEER VOORSTELLEN 🚫: {', '.join(blacklist)}"

    prompt = f"""{FAMILY_CONTEXT}
{learning_context}

Genereer precies 10 receptvoorstellen. Alleen naam en omschrijving — GEEN ingrediënten of bereidingswijze.

Geef je antwoord ALLEEN als een JSON array, geen andere tekst, geen markdown:
[
  {{
    "nummer": 1,
    "naam": "Naam van het recept",
    "type": "vlees|vis|vega|gevogelte",
    "wildcard": true,
    "serieus": false,
    "keuken": "Koreaans",
    "tijd_minuten": 40,
    "moeilijkheid": "makkelijk|gemiddeld|moeilijk",
    "gluten": "geen|aanpasbaar|bevat",
    "gluten_tip": "gebruik GF sojasaus",
    "beschrijving": "Één zin beschrijving"
  }}
]
"""

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    logger.info(f"generate_proposals: {message.usage.input_tokens} in / {message.usage.output_tokens} out, stop={message.stop_reason}")

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


async def generate_full_recipes_and_shopping(picked_proposals: list, week_start: str) -> tuple[dict, str, list]:
    """Phase 2: Generate full recipes for picked proposals + shopping list + day plan."""
    # Cooking week runs Fri-Thu (proposals Tuesday, shopping Thursday)
    days = ["Vrijdag", "Zaterdag", "Zondag", "Maandag", "Dinsdag", "Woensdag", "Donderdag"]

    proposals_summary = [
        {
            "nummer": r["nummer"],
            "naam": r["naam"],
            "type": r.get("type", ""),
            "keuken": r.get("keuken", ""),
            "serieus": r.get("serieus", False),
            "gluten": r.get("gluten", "geen"),
        }
        for r in picked_proposals
    ]

    prompt = f"""{FAMILY_CONTEXT}

Genereer volledige recepten (ingrediënten + bereiding) voor deze {len(picked_proposals)} gerechten,
plus een gecombineerde boodschappenlijst voor alle recepten samen.

Gerechten:
{json.dumps(proposals_summary, ensure_ascii=False)}

Geef ALLEEN als JSON, geen andere tekst, geen markdown:
{{
  "recepten": [
    {{
      "nummer": 1,
      "naam": "...",
      "type": "vlees|vis|vega|gevogelte",
      "wildcard": false,
      "serieus": false,
      "keuken": "...",
      "tijd_minuten": 30,
      "moeilijkheid": "makkelijk|gemiddeld|moeilijk",
      "gluten": "geen|aanpasbaar|bevat",
      "gluten_tip": "...",
      "beschrijving": "...",
      "ingredienten": [{{"naam": "kipfilet", "hoeveelheid": 600, "eenheid": "g"}}],
      "bereidingswijze": ["Stap 1...", "Stap 2..."],
      "tip": "..."
    }}
  ],
  "boodschappen": {{
    "vlees_vis": [{{"naam": "...", "hoeveelheid": "600g", "gf": false}}],
    "groente_fruit": [],
    "zuivel_eieren": [],
    "droog_blik": [],
    "kruiden_sauzen": [],
    "overig": []
  }}

BELANGRIJK voor het gf-veld:
- gf: true ALLEEN als de koper expliciet een glutenvrije variant moet kopen — bijv. GF pasta, GF meel, GF paneermeel/panko, GF sojasaus/tamari, GF bouillonblokjes
- Vlees, vis, groente, fruit, eieren, rauwe rijst, pure kruiden, melk, kaas zijn VAN NATURE glutenvrij — gf: false voor deze items
- Twijfelgeval: alleen gf: true als het product regulier gluten bevat (sojasaus, paneermeel, gewone bloem etc.)
}}
"""

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=20000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    logger.info(f"generate_full_recipes: {message.usage.input_tokens} in / {message.usage.output_tokens} out, stop={message.stop_reason}")

    if message.stop_reason == "max_tokens":
        raise ValueError(f"Claude response truncated (output tokens exhausted)")

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)
    full_recipes = result["recepten"]
    shopping_text = format_shopping_list(result["boodschappen"])

    # Assign days: serious meals to weekend, rest spread across week
    serious = [r for r in full_recipes if r.get("serieus")]
    other = [r for r in full_recipes if not r.get("serieus")]

    assigned = {}
    day_index = 0
    for recipe in other:
        while day_index < len(days) and days[day_index] in ["Zaterdag", "Zondag"] and serious:
            day_index += 1
        if day_index < len(days):
            assigned[days[day_index]] = recipe
            day_index += 1

    for recipe in serious:
        for day in ["Zaterdag", "Zondag", "Vrijdag"]:
            if day not in assigned:
                assigned[day] = recipe
                break

    plan = {"week_start": week_start, "days": assigned}
    return plan, shopping_text, full_recipes


async def generate_single_recipe(proposal: dict) -> dict:
    """Generate one full recipe from a lightweight proposal (used by /vervang)."""
    prompt = f"""{FAMILY_CONTEXT}

Genereer één volledig recept voor: {proposal['naam']} ({proposal.get('keuken', '')}, {proposal.get('type', '')})

Geef ALLEEN als JSON, geen andere tekst:
{{
  "nummer": {proposal.get('nummer', 0)},
  "naam": "{proposal['naam']}",
  "type": "{proposal.get('type', '')}",
  "wildcard": {str(proposal.get('wildcard', False)).lower()},
  "serieus": {str(proposal.get('serieus', False)).lower()},
  "keuken": "{proposal.get('keuken', '')}",
  "tijd_minuten": {proposal.get('tijd_minuten', 30)},
  "moeilijkheid": "{proposal.get('moeilijkheid', 'gemiddeld')}",
  "gluten": "{proposal.get('gluten', 'geen')}",
  "gluten_tip": "{proposal.get('gluten_tip', '')}",
  "beschrijving": "{proposal.get('beschrijving', '')}",
  "ingredienten": [{{"naam": "...", "hoeveelheid": 0, "eenheid": "g"}}],
  "bereidingswijze": ["Stap 1..."],
  "tip": "..."
}}
"""

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    logger.info(f"generate_single_recipe ({proposal['naam']}): {message.usage.output_tokens} out tokens")

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


def format_shopping_list(data: dict) -> str:
    icons = {
        "vlees_vis": "🥩 Vlees & Vis",
        "groente_fruit": "🥦 Groente & Fruit",
        "zuivel_eieren": "🧀 Zuivel & Eieren",
        "droog_blik": "🌾 Droog & Blik",
        "kruiden_sauzen": "🫙 Kruiden & Sauzen",
        "overig": "🛍️ Overig"
    }

    lines = ["🛒 *Boodschappenlijst*\n"]
    for key, label in icons.items():
        items = data.get(key, [])
        if items:
            lines.append(f"*{label}*")
            for item in items:
                gf = " ⚠️ GF" if item.get("gf") else ""
                lines.append(f"• {item['hoeveelheid']} {item['naam']}{gf}")
            lines.append("")

    return "\n".join(lines)
