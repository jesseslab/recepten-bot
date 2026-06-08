"""
bot.py — Telegram bot handlers for recepten workflow
"""
import json
import logging
import os
from datetime import date, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

import db
import claude_api
import vps_push

logger = logging.getLogger(__name__)

GROUP_ID = int(os.environ["TELEGRAM_GROUP_ID"])

DAYS_NL = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]

MIN_PICKS = 2
MAX_PICKS = 7

# In-memory state for current proposal session
_pending_proposals: dict = {}  # week_start -> list of lightweight proposals
_selected: dict = {}           # week_start -> set of selected recipe numbers


def get_week_start() -> str:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


def format_proposals(recipes: list) -> str:
    lines = ["🍽️ *Deze week: 10 recepten*\n"]
    lines.append(f"Kies {MIN_PICKS} tot {MAX_PICKS} nummers via de knoppen hieronder\\.\n")

    type_icons = {"vlees": "🥩", "vis": "🐟", "vega": "🌱", "gevogelte": "🍳"}

    for r in recipes:
        num = r["nummer"]
        icon = type_icons.get(r["type"], "🍴")
        wildcard = " 🌍" if r.get("wildcard") else ""
        serieus = " 👨‍🍳" if r.get("serieus") else ""
        gf = "✅" if r["gluten"] == "geen" else ("⚠️" if r["gluten"] == "aanpasbaar" else "❌")
        stars = {"makkelijk": "⭐", "gemiddeld": "⭐⭐", "moeilijk": "⭐⭐⭐"}.get(r["moeilijkheid"], "⭐")

        lines.append(
            f"*{num}\\. {escape(r['naam'])}* {icon}{wildcard}{serieus}\n"
            f"   {escape(r['beschrijving'])}\n"
            f"   {stars} · {escape(str(r['tijd_minuten']))} min · GF: {gf}\n"
        )

    lines.append("\n_🌍 \\= wildcard \\| 👨‍🍳 \\= serieus koken \\| ⚠️ GF \\= aanpasbaar_")
    return "\n".join(lines)


def format_weekly_overview(plan: dict) -> str:
    lines = [f"📅 *Weekmenu {escape(plan['week_start'])}*\n"]
    for day, recipe in plan["days"].items():
        icon = {"vlees": "🥩", "vis": "🐟", "vega": "🌱", "gevogelte": "🍳"}.get(recipe.get("type", ""), "🍴")
        tijd = recipe.get("tijd_minuten", "?")
        lines.append(f"*{day}:* {icon} {escape(recipe['naam'])} \\({tijd} min\\)")
    lines.append("\n_/recept \\<dag\\> voor het recept · /vandaag voor vandaag · /swap \\<dag1\\> \\<dag2\\> · /vervang \\<dag\\>_")
    return "\n".join(lines)


def format_recipe(recipe: dict) -> str:
    lines = [f"👨‍🍳 *{escape(recipe['naam'])}*\n"]

    if recipe.get("gluten") == "aanpasbaar":
        lines.append(f"⚠️ _GF tip: {escape(recipe.get('gluten_tip', 'gebruik GF variant'))}_\n")

    lines.append("*Ingrediënten \\(4 personen\\):*")
    for ing in recipe.get("ingredienten", []):
        lines.append(f"• {ing.get('hoeveelheid', '')}{ing.get('eenheid', '')} {escape(ing['naam'])}")

    lines.append("\n*Bereiding:*")
    for i, stap in enumerate(recipe.get("bereidingswijze", []), 1):
        lines.append(f"{i}\\. {escape(stap)}")

    if recipe.get("tip"):
        lines.append(f"\n💡 _{escape(recipe['tip'])}_")

    return "\n".join(lines)


def escape(text: str) -> str:
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def escape_shopping_list(text: str) -> str:
    import re
    lines = []
    for line in text.split('\n'):
        m = re.match(r'^(\*)(.*?)(\*)$', line.strip())
        if m:
            lines.append(f"*{escape(m.group(2))}*")
        else:
            lines.append(escape(line))
    return '\n'.join(lines)


def build_rating_keyboard(week_start: str, day: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 Lekker", callback_data=f"ru_{week_start}_{day}"),
        InlineKeyboardButton("👎 Viel tegen", callback_data=f"rd_{week_start}_{day}"),
        InlineKeyboardButton("🚫 Nooit meer", callback_data=f"rn_{week_start}_{day}"),
    ]])


def build_proposal_keyboard(recipes: list, selected: set) -> InlineKeyboardMarkup:
    buttons = []
    for r in recipes:
        num = r["nummer"]
        icon = "✅" if num in selected else "⬜"
        name = r["naam"][:28]
        buttons.append([InlineKeyboardButton(f"{icon} {num}. {name}", callback_data=f"tog_{num}")])

    n = len(selected)
    if n >= MIN_PICKS:
        confirm_label = f"✓ Bevestig ({n} gekozen)"
        confirm_data = "conf"
    else:
        confirm_label = f"Kies nog {MIN_PICKS - n} recept{'en' if MIN_PICKS - n != 1 else ''}..."
        confirm_data = "noop"

    buttons.append([InlineKeyboardButton(confirm_label, callback_data=confirm_data)])
    return InlineKeyboardMarkup(buttons)


async def send_proposals(app: Application):
    """Called by scheduler on Tuesday morning or via /genereer."""
    week_start = get_week_start()
    logger.info(f"send_proposals: starting for week {week_start}")

    await app.bot.send_message(
        chat_id=GROUP_ID,
        text="🍳 *Tijd voor het weekmenu\\!*\nEven nadenken\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    top_picks = await db.get_top_picks()
    recent_picks = await db.get_recent_picks()
    blacklist = await db.get_blacklist()
    liked = await db.get_liked_recipes()
    disliked = await db.get_disliked_recipes()
    logger.info("send_proposals: calling Claude API for proposals")
    recipes = await claude_api.generate_proposals(top_picks, recent_picks, blacklist, liked, disliked)
    logger.info(f"send_proposals: received {len(recipes)} proposals")

    _pending_proposals[week_start] = recipes
    _selected[week_start] = set()
    await db.save_proposals(week_start, recipes)

    text = format_proposals(recipes)
    keyboard = build_proposal_keyboard(recipes, set())
    await app.bot.send_message(
        chat_id=GROUP_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    data = query.data
    week_start = get_week_start()

    # --- Recipe selection toggles ---
    if data.startswith("tog_"):
        num = int(data.split("_")[1])
        proposals = _pending_proposals.get(week_start)
        if not proposals:
            await query.answer("Geen actieve receptenlijst. Stuur /genereer.", show_alert=True)
            return

        if week_start not in _selected:
            _selected[week_start] = set()

        if num in _selected[week_start]:
            _selected[week_start].discard(num)
        else:
            if len(_selected[week_start]) >= MAX_PICKS:
                await query.answer(f"Maximum {MAX_PICKS} recepten geselecteerd.", show_alert=True)
                return
            _selected[week_start].add(num)

        keyboard = build_proposal_keyboard(proposals, _selected[week_start])
        await query.edit_message_reply_markup(reply_markup=keyboard)

    # --- Not enough selected yet ---
    elif data == "noop":
        n = len(_selected.get(week_start, set()))
        await query.answer(f"Kies minimaal {MIN_PICKS} recepten ({n}/{MIN_PICKS}).", show_alert=False)

    # --- Confirm selection: generate full recipes + shopping list ---
    elif data == "conf":
        proposals = _pending_proposals.get(week_start)
        selected_nums = _selected.get(week_start, set())

        if not proposals or len(selected_nums) < MIN_PICKS:
            await query.answer(f"Kies minimaal {MIN_PICKS} recepten.", show_alert=True)
            return

        picked = [r for r in proposals if r["nummer"] in selected_nums]

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "✅ Lekker\\! Even de volledige recepten en boodschappenlijst maken\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

        plan, shopping_list, full_recipes = await claude_api.generate_full_recipes_and_shopping(picked, week_start)
        await db.save_picks(week_start, ",".join(map(str, sorted(selected_nums))), plan, shopping_list)
        await db.record_picks(week_start, picked)

        await query.message.reply_text(format_weekly_overview(plan), parse_mode=ParseMode.MARKDOWN_V2)
        await query.message.reply_text(escape_shopping_list(shopping_list), parse_mode=ParseMode.MARKDOWN_V2)

        try:
            await vps_push.push_plan_to_vps(plan, full_recipes, shopping_list)
        except Exception as e:
            logger.warning(f"VPS push failed: {e}")

        _selected.pop(week_start, None)
        await schedule_defrost_check(context.application, plan)

    # --- Vervang: substitute a day with chosen proposal ---
    elif data.startswith("sub_"):
        parts = data.split("_", 2)  # sub_Maandag_3
        if len(parts) != 3:
            return
        _, day, num_str = parts
        num = int(num_str)

        plan_row = await db.get_current_plan(week_start)
        if not plan_row:
            await query.answer("Weekmenu niet gevonden.", show_alert=True)
            return

        proposals = json.loads(plan_row.get("proposals_json", "[]"))
        proposal = next((p for p in proposals if p["nummer"] == num), None)
        if not proposal:
            await query.answer("Recept niet gevonden.", show_alert=True)
            return

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"🔄 Even {escape(proposal['naam'])} uitwerken\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

        full_recipe = await claude_api.generate_single_recipe(proposal)

        plan = json.loads(plan_row["plan_json"])
        plan["days"][day] = full_recipe
        shopping_list = plan_row.get("shopping_list", "")
        await db.update_plan_json(week_start, plan)

        await query.message.reply_text(
            f"✅ *{escape(day)}* vervangen door *{escape(full_recipe['naam'])}*\\!",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.message.reply_text(format_weekly_overview(plan), parse_mode=ParseMode.MARKDOWN_V2)

        try:
            await vps_push.push_plan_to_vps(plan, list(plan["days"].values()), shopping_list)
        except Exception as e:
            logger.warning(f"VPS push after vervang failed: {e}")

    elif data == "subcancel":
        await query.edit_message_reply_markup(reply_markup=None)

    # --- Ratings: 👍 👎 🚫 ---
    elif data.startswith("ru_") or data.startswith("rd_") or data.startswith("rn_"):
        parts = data.split("_", 2)  # ["ru", "2026-06-08", "Maandag"]
        if len(parts) != 3:
            return
        prefix, ws, day = parts

        plan_row = await db.get_current_plan(ws)
        if not plan_row or not plan_row.get("plan_json"):
            await query.answer("Plan niet meer gevonden.", show_alert=True)
            return

        plan = json.loads(plan_row["plan_json"])
        recipe = plan["days"].get(day)
        if not recipe:
            await query.answer("Recept niet gevonden.", show_alert=True)
            return

        name = recipe["naam"]
        if prefix == "ru":
            await db.add_rating(name, "up")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer(f"👍 Genoteerd!", show_alert=False)
        elif prefix == "rd":
            await db.add_rating(name, "down")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("👎 Onthouden — wordt minder voorgesteld.", show_alert=False)
        elif prefix == "rn":
            await db.add_rating(name, "never")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer(f"🚫 {name} staat op de nooit-meer-lijst.", show_alert=True)


async def cmd_genereer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/genereer — manually trigger proposal generation"""
    actual_id = update.effective_chat.id
    logger.info(f"cmd_genereer: chat_id={actual_id}, expected GROUP_ID={GROUP_ID}, match={actual_id == GROUP_ID}")
    if actual_id != GROUP_ID:
        return
    try:
        await send_proposals(context.application)
    except Exception as e:
        logger.exception(f"cmd_genereer failed: {e}")
        await update.message.reply_text("Er ging iets mis bij het genereren. Check de logs.")


async def cmd_recept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/recept <dag> — show full recipe for a day"""
    if not context.args:
        await update.message.reply_text("Gebruik: /recept Maandag")
        return

    day = context.args[0].capitalize()
    week_start = get_week_start()
    plan_row = await db.get_current_plan(week_start)

    if not plan_row or not plan_row.get("plan_json"):
        await update.message.reply_text("Geen weekmenu gevonden voor deze week.")
        return

    plan = json.loads(plan_row["plan_json"])
    recipe = plan["days"].get(day)

    if not recipe:
        await update.message.reply_text(f"Geen recept gevonden voor {day}.")
        return

    await update.message.reply_text(format_recipe(recipe), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_vandaag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/vandaag — show today's recipe"""
    week_start = get_week_start()
    plan_row = await db.get_current_plan(week_start)

    if not plan_row or not plan_row.get("plan_json"):
        await update.message.reply_text("Geen weekmenu gevonden. Stuur /genereer om te starten.")
        return

    plan = json.loads(plan_row["plan_json"])
    today = DAYS_NL[date.today().weekday()]
    recipe = plan["days"].get(today)

    if not recipe:
        await update.message.reply_text(f"Geen recept voor vandaag ({today}).")
        return

    await update.message.reply_text(
        format_recipe(recipe),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=build_rating_keyboard(week_start, today)
    )


async def cmd_swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/swap <dag1> <dag2> — swap two days"""
    if len(context.args) != 2:
        await update.message.reply_text("Gebruik: /swap Maandag Dinsdag")
        return

    day1 = context.args[0].capitalize()
    day2 = context.args[1].capitalize()
    week_start = get_week_start()

    success = await db.swap_days(week_start, day1, day2)
    if success:
        plan_row = await db.get_current_plan(week_start)
        plan = json.loads(plan_row["plan_json"])
        try:
            await vps_push.push_plan_to_vps(plan, list(plan["days"].values()), plan_row.get("shopping_list", ""))
        except Exception as e:
            logger.warning(f"VPS push after swap failed: {e}")

        await update.message.reply_text(
            f"✅ {escape(day1)} en {escape(day2)} omgewisseld\\!",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await update.message.reply_text("Kon niet wisselen. Controleer de dagnamen.")


async def cmd_vervang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/vervang <dag> — replace a day with an alternative from proposals"""
    if not context.args:
        await update.message.reply_text("Gebruik: /vervang Maandag")
        return

    day = context.args[0].capitalize()
    week_start = get_week_start()
    plan_row = await db.get_current_plan(week_start)

    if not plan_row or not plan_row.get("plan_json"):
        await update.message.reply_text("Geen weekmenu gevonden.")
        return

    plan = json.loads(plan_row["plan_json"])
    if day not in plan["days"]:
        await update.message.reply_text(f"{day} staat niet in het weekmenu.")
        return

    proposals = json.loads(plan_row.get("proposals_json", "[]"))
    used_names = {r["naam"] for r in plan["days"].values()}
    alternatives = [p for p in proposals if p["naam"] not in used_names]

    if not alternatives:
        await update.message.reply_text("Geen alternatieven beschikbaar. Doe /genereer voor een nieuw voorstel.")
        return

    type_icons = {"vlees": "🥩", "vis": "🐟", "vega": "🌱", "gevogelte": "🍳"}
    buttons = []
    for r in alternatives[:6]:
        icon = type_icons.get(r.get("type", ""), "🍴")
        buttons.append([InlineKeyboardButton(
            f"{icon} {r['naam'][:35]}",
            callback_data=f"sub_{day}_{r['nummer']}"
        )])
    buttons.append([InlineKeyboardButton("✗ Annuleren", callback_data="subcancel")])

    await update.message.reply_text(
        f"Wat in plaats van *{escape(day)}* \\({escape(plan['days'][day]['naam'])}\\)?",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cmd_lijst(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/lijst — resend shopping list"""
    week_start = get_week_start()
    plan_row = await db.get_current_plan(week_start)

    if not plan_row or not plan_row.get("shopping_list"):
        await update.message.reply_text("Geen boodschappenlijst gevonden voor deze week.")
        return

    await update.message.reply_text(escape_shopping_list(plan_row["shopping_list"]), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/menu — show this week's overview"""
    week_start = get_week_start()
    plan_row = await db.get_current_plan(week_start)

    if not plan_row or not plan_row.get("plan_json"):
        await update.message.reply_text("Geen weekmenu gevonden. Stuur /genereer om te starten.")
        return

    plan = json.loads(plan_row["plan_json"])
    await update.message.reply_text(format_weekly_overview(plan), parse_mode=ParseMode.MARKDOWN_V2)


async def send_daily_reminder(app: Application):
    """Called by scheduler every evening at 18:00."""
    week_start = get_week_start()
    plan_row = await db.get_current_plan(week_start)

    if not plan_row or not plan_row.get("plan_json"):
        return

    plan = json.loads(plan_row["plan_json"])
    today = DAYS_NL[date.today().weekday()]
    tomorrow = DAYS_NL[(date.today().weekday() + 1) % 7]

    today_recipe = plan["days"].get(today)
    tomorrow_recipe = plan["days"].get(tomorrow)

    if not today_recipe:
        return

    icon = {"vlees": "🥩", "vis": "🐟", "vega": "🌱", "gevogelte": "🍳"}.get(today_recipe.get("type", ""), "🍴")
    msg = f"{icon} *Vanavond:* {escape(today_recipe['naam'])}\n_/vandaag voor het volledige recept_"

    if tomorrow_recipe and tomorrow_recipe.get("type") in ("vlees", "vis", "gevogelte"):
        msg += f"\n\n🧊 *Vergeet niet:* haal het vlees voor morgen \\({escape(tomorrow_recipe['naam'])}\\) uit de vriezer\\!"

    await app.bot.send_message(
        chat_id=GROUP_ID,
        text=msg,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=build_rating_keyboard(week_start, today)
    )


async def send_shopping_reminder(app: Application):
    """Thursday 14:00 — resend shopping list."""
    week_start = get_week_start()
    plan_row = await db.get_current_plan(week_start)

    if not plan_row or not plan_row.get("shopping_list"):
        return

    await app.bot.send_message(
        chat_id=GROUP_ID,
        text="🛒 *Boodschappen reminder\\!*\nJe doet vandaag boodschappen\\. Hier de lijst:",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await app.bot.send_message(
        chat_id=GROUP_ID,
        text=escape_shopping_list(plan_row["shopping_list"]),
        parse_mode=ParseMode.MARKDOWN_V2
    )


async def schedule_defrost_check(app: Application, plan: dict):
    pass


def register_handlers(app: Application):
    app.add_handler(CommandHandler("genereer", cmd_genereer))
    app.add_handler(CommandHandler("recept", cmd_recept))
    app.add_handler(CommandHandler("vandaag", cmd_vandaag))
    app.add_handler(CommandHandler("swap", cmd_swap))
    app.add_handler(CommandHandler("vervang", cmd_vervang))
    app.add_handler(CommandHandler("lijst", cmd_lijst))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CallbackQueryHandler(handle_callback))
