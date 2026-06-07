"""
claude_api.py — Recipe generation via Anthropic API
"""
import json
import os
import anthropic

client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

FAMILY_CONTEXT = """
Je genereert weekmenu's voor een Nederlands gezin van 4 personen in Amstelveen.

GEZIN:
- 4 personen, grote eters
- Porties: ~120g vlees/vis per persoon per maaltijd (480g totaal)
- Dochter: glutenallergie — gebruik GF tarwebloem/pasta/paneermeel waar nodig, geef dit aan
- Eenheden: altijd metrisch (gram, ml, kg, °C)

NOOIT GEBRUIKEN:
- Orgaanvlees (lever, nier, hart, zwezerik etc.)
- Slakken / escargot

WEEKSTRUCTUUR (per week van 7 avonden):
- 2x vegetarisch (eiwitrijk en vullend)
- 2x serieus koken (meerdere componenten, langere bereidingstijd, techniek)
- 1-3x wildcard (échte andere keuken of techniek — NIET Italiaans of Hollands)
- Rest: snelle gevarieerde weekdagen

WILDCARDS: Denk aan Koreaans, Ethiopisch, Peruaans, Libanees, Japans, Mexicaans,
Vietnamese, Marokkaans, Georgisch etc. — iets wat ze normaal niet koken.

SERIEUS KOKEN: slow-braise, zelfgemaakte GF pasta, hele vis, complexe sauzen,
meerdere componenten die samenkomen.

VEGETARISCH: niet gewoon pasta met groente. Denk aan dhal, shakshuka, halloumi,
paneer, gevulde paprika's, Aziatische tofu gerechten, etc.
"""


async def generate_proposals(top_picks: list, recent_picks: list) -> list:
    """Generate 10 recipe proposals with learning context."""

    learning_context = ""
    if top_picks:
        favs = ", ".join([f"{r['recipe_name']} ({r['times_picked']}x)" for r in top_picks[:5]])
        learning_context += f"\nFAVORIETEN (vaker voorstellen): {favs}"
    if recent_picks:
        recent = ", ".join(recent_picks[:8])
        learning_context += f"\nRECENT GEKOOKT (vermijd herhaling): {recent}"

    prompt = f"""{FAMILY_CONTEXT}
{learning_context}

Genereer precies 10 recepten voor deze week. Mix de types zoals beschreven.

Geef je antwoord ALLEEN als een JSON array, geen andere tekst, geen markdown:
[
  {{
    "nummer": 1,
    "naam": "Naam van het recept",
    "type": "vlees|vis|vega|gevogelte",
    "wildcard": true/false,
    "serieus": true/false,
    "keuken": "Italiaans|Koreaans|etc",
    "tijd_minuten": 30,
    "moeilijkheid": "makkelijk|gemiddeld|moeilijk",
    "gluten": "geen|aanpasbaar|bevat",
    "gluten_tip": "gebruik GF pasta" (alleen als aanpasbaar),
    "beschrijving": "Één zin beschrijving",
    "ingredienten": [
      {{"naam": "kipfilet", "hoeveelheid": 480, "eenheid": "g"}},
      ...
    ],
    "bereidingswijze": [
      "Stap 1...",
      "Stap 2...",
      ...
    ],
    "tip": "optionele kooktip"
  }},
  ...
]
"""

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


async def generate_plan_and_shopping(picked_recipes: list, week_start: str) -> tuple[dict, str]:
    """Generate weekly plan with day assignments and shopping list."""

    days = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    n = len(picked_recipes)
    # Assign serious meals to weekend, rest spread across week
    serious = [r for r in picked_recipes if r.get("serieus")]
    other = [r for r in picked_recipes if not r.get("serieus")]

    # Build day assignments
    assigned = {}
    day_index = 0
    for recipe in other:
        # Skip weekend days for non-serious meals if possible
        while days[day_index] in ["Zaterdag", "Zondag"] and serious and day_index < 5:
            day_index += 1
        if day_index < len(days):
            assigned[days[day_index]] = recipe
            day_index += 1

    for recipe in serious:
        for day in ["Zaterdag", "Zondag", "Vrijdag"]:
            if day not in assigned:
                assigned[day] = recipe
                break

    plan = {
        "week_start": week_start,
        "days": assigned
    }

    # Generate shopping list via Claude
    recipes_text = json.dumps(picked_recipes, ensure_ascii=False)

    prompt = f"""Gegeven deze recepten voor 4 personen:
{recipes_text}

Maak een gecombineerde boodschappenlijst. Combineer dubbele ingrediënten op.
Markeer items die een glutenvrije variant nodig hebben met [GF].
Gebruik praktische supermarktmaten (bijv. "1 blik tomaten (400g)" niet "380g tomaten").

Geef je antwoord ALLEEN als JSON, geen andere tekst:
{{
  "vlees_vis": [{{"naam": "...", "hoeveelheid": "480g", "gf": false}}],
  "groente_fruit": [...],
  "zuivel_eieren": [...],
  "droog_blik": [...],
  "kruiden_sauzen": [...],
  "overig": [...]
}}
"""

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    shopping_data = json.loads(raw)
    shopping_text = format_shopping_list(shopping_data)

    return plan, shopping_text


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
