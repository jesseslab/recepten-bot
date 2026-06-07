"""
db.py — SQLite database layer for recepten bot
"""
import aiosqlite
import json
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path("/app/data/recepten.db")


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS weekly_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,          -- ISO date of Monday
                proposals_json TEXT NOT NULL,      -- all 10 proposed recipes
                picked_indices TEXT,               -- e.g. "1,3,5,7,9"
                plan_json TEXT,                    -- final weekly plan with days
                shopping_list TEXT,                -- formatted shopping list
                created_at TEXT DEFAULT (datetime('now')),
                finalized_at TEXT
            );

            CREATE TABLE IF NOT EXISTS pick_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                recipe_name TEXT NOT NULL,
                recipe_type TEXT,                  -- vega, vlees, vis, wildcard etc
                cuisine TEXT,
                picked_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS favourites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_name TEXT NOT NULL UNIQUE,
                recipe_type TEXT,
                cuisine TEXT,
                times_picked INTEGER DEFAULT 1,
                last_picked TEXT
            );

            CREATE TABLE IF NOT EXISTS day_swaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                day_from TEXT NOT NULL,
                day_to TEXT NOT NULL,
                swapped_at TEXT DEFAULT (datetime('now'))
            );
        """)
        await db.commit()


async def save_proposals(week_start: str, proposals: list) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO weekly_plans (week_start, proposals_json) VALUES (?, ?)",
            (week_start, json.dumps(proposals, ensure_ascii=False))
        )
        await db.commit()
        return cursor.lastrowid


async def save_picks(week_start: str, picked_indices: str, plan: dict, shopping_list: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE weekly_plans
            SET picked_indices = ?, plan_json = ?, shopping_list = ?, finalized_at = datetime('now')
            WHERE week_start = ?
        """, (picked_indices, json.dumps(plan, ensure_ascii=False), shopping_list, week_start))
        await db.commit()


async def get_current_plan(week_start: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM weekly_plans WHERE week_start = ? ORDER BY id DESC LIMIT 1",
            (week_start,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None


async def record_picks(week_start: str, recipes: list):
    """Log picked recipes for learning."""
    async with aiosqlite.connect(DB_PATH) as db:
        for recipe in recipes:
            name = recipe.get("naam", "")
            rtype = recipe.get("type", "")
            cuisine = recipe.get("keuken", "")

            await db.execute(
                "INSERT INTO pick_history (week_start, recipe_name, recipe_type, cuisine) VALUES (?,?,?,?)",
                (week_start, name, rtype, cuisine)
            )
            # Upsert into favourites
            await db.execute("""
                INSERT INTO favourites (recipe_name, recipe_type, cuisine, times_picked, last_picked)
                VALUES (?, ?, ?, 1, date('now'))
                ON CONFLICT(recipe_name) DO UPDATE SET
                    times_picked = times_picked + 1,
                    last_picked = date('now')
            """, (name, rtype, cuisine))

        await db.commit()


async def get_top_picks(limit: int = 10) -> list:
    """Return most picked recipes for learning context."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT recipe_name, recipe_type, cuisine, times_picked FROM favourites ORDER BY times_picked DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_recent_picks(weeks: int = 4) -> list:
    """Return recipes picked in the last N weeks to avoid repetition."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT DISTINCT recipe_name FROM pick_history
            WHERE picked_at >= datetime('now', ? || ' days')
            ORDER BY picked_at DESC
        """, (f"-{weeks * 7}",)) as cursor:
            rows = await cursor.fetchall()
            return [r["recipe_name"] for r in rows]


async def swap_days(week_start: str, day_from: str, day_to: str) -> bool:
    """Swap two days in the current plan."""
    plan_row = await get_current_plan(week_start)
    if not plan_row or not plan_row.get("plan_json"):
        return False

    plan = json.loads(plan_row["plan_json"])
    days = plan.get("days", {})

    if day_from not in days or day_to not in days:
        return False

    days[day_from], days[day_to] = days[day_to], days[day_from]
    plan["days"] = days

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE weekly_plans SET plan_json = ? WHERE week_start = ?",
            (json.dumps(plan, ensure_ascii=False), week_start)
        )
        await db.execute(
            "INSERT INTO day_swaps (week_start, day_from, day_to) VALUES (?,?,?)",
            (week_start, day_from, day_to)
        )
        await db.commit()

    return True
