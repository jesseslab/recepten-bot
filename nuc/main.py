"""
main.py — Entry point, wires bot + scheduler together
"""
import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram.ext import Application

import db
import bot as bot_module

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    await db.init_db()
    logger.info("Database initialised")

    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()
    bot_module.register_handlers(app)

    scheduler = AsyncIOScheduler(timezone="Europe/Amsterdam")

    # Tuesday 09:00 — propose recipes
    scheduler.add_job(
        bot_module.send_proposals,
        trigger="cron",
        day_of_week="tue",
        hour=int(os.getenv("PROPOSAL_HOUR", 9)),
        minute=0,
        args=[app]
    )

    # Daily 18:00 — evening reminder + defrost alert
    scheduler.add_job(
        bot_module.send_daily_reminder,
        trigger="cron",
        hour=int(os.getenv("REMINDER_HOUR", 18)),
        minute=0,
        args=[app]
    )

    # Thursday 14:00 — shopping list reminder
    scheduler.add_job(
        bot_module.send_shopping_reminder,
        trigger="cron",
        day_of_week="thu",
        hour=int(os.getenv("SHOPPING_REMINDER_HOUR", 14)),
        minute=0,
        args=[app]
    )

    scheduler.start()
    logger.info("Scheduler started")

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("Bot polling started")

    # Keep running
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
