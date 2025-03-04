from datetime import datetime, time

import discord
from beanie.odm.operators.update.array import Pull
from discord.ext import tasks

from bot_globals import client, logger
from embeds.questions_embeds import daily_question_embed
from models.analytics_model import Analytics, AnalyticsHistory
from models.server_model import Server
from models.user_model import User
from utils.leaderboards import send_leaderboard_winners
from utils.roles import update_roles
from utils.stats import update_rankings, update_stats


async def send_daily_question(server: Server, embed: discord.Embed) -> None:
    logger.info("file: utils/message_scheduler.py ~ send_daily ~ run")

    for channel_id in server.channels.daily_question:
        channel = client.get_channel(channel_id)

        if not channel or not isinstance(channel, discord.TextChannel):
            continue

        try:
            await channel.send(embed=embed)
        except discord.errors.Forbidden as e:
            logger.exception(
                "file: utils/message_scheduler.py ~ send_daily ~ missing permissions on channel id %s. Error: %s", channel.id, e)

        # async for message in channel.history(limit=1):
        #     try:
        #         await message.pin()
        #     except discord.errors.Forbidden as e:
        #         logger.exception(
        #             "file: utils/message_scheduler.py ~ send_daily ~ message not pinned due to missing permissions in channel %s", channel_id)

        logger.info(
            "file: utils/message_scheduler.py ~ send_daily ~ daily question sent to channel %s", channel.id)

    logger.info(
        "file: utils/message_scheduler.py ~ send_daily ~ all daily questions sent to server ID: %s", server.id)


@tasks.loop(time=[time(hour=hour, minute=minute) for hour in range(24) for minute in [0, 30]])
async def send_daily_question_and_update_stats_schedule() -> None:
    await send_daily_question_and_update_stats()


async def send_daily_question_and_update_stats(force_update_stats: bool = True, force_daily_reset: bool = False, force_weekly_reset: bool = False) -> None:
    logger.info(
        "file: utils/message_scheduler.py ~ send_daily_question_and_update_stats ~ started")

    now = datetime.utcnow()

    daily_reset = (now.hour == 0 and now.minute == 0) or force_daily_reset
    weekly_reset = (now.weekday() == 0 and now.hour ==
                    0 and now.minute == 0) or force_weekly_reset
    midday = (now.hour == 12 and now.minute == 0)
    remove_unneeded_data = (now.day == 1 and now.hour ==
                            12 and now.minute == 0)

    if force_update_stats:
        async for user in User.all():
            await update_stats(user, now, daily_reset, weekly_reset)

    async for server in Server.all(fetch_links=True):
        server.last_updated = now
        await server.save_changes()

        if daily_reset:
            await update_rankings(server, now, "daily")
            await send_leaderboard_winners(server, "yesterday")

        if weekly_reset:
            await update_rankings(server, now, "weekly")
            await send_leaderboard_winners(server, "last_week")

        if midday:
            await update_roles(server)

    if daily_reset:
        embed = await daily_question_embed()

        async for server in Server.all(fetch_links=True):
            await send_daily_question(server, embed)

        analytics = await Analytics.find_all().to_list()

        if not analytics:
            analytics = Analytics()
            await analytics.create()
        else:
            analytics = analytics[0]

        analytics.history.append(AnalyticsHistory(
            distinct_users=analytics.distinct_users_today, command_count=analytics.command_count_today))

        analytics.distinct_users_today = []
        analytics.command_count_today = 0

        await analytics.save()

    # Data removal process
    if remove_unneeded_data:
        async for server in Server.all():
            # So that we can access user.id
            await server.fetch_all_links()

            guild = client.get_guild(server.id)

            # Delete server document if the bot isn't in the server anymore
            if not guild or guild not in client.guilds:
                await server.delete()

                logger.info(
                    "file: utils/message_scheduler.py ~ send_daily_question_and_update_stats ~ server document deleted ~ id: %s", server.id)

            for user in server.users:
                # unlink user if server was deleted or if bot not in the server anymore
                unlink_user = not guild or not guild.get_member(
                    user.id)

                # Unlink user from server if they're not in the server anymore
                if unlink_user:
                    await User.find_one(User.id == user.id).update(Pull({User.display_information: {"server_id": server.id}}))
                    await Server.find_one(Server.id == server.id).update(Pull({Server.users: {"$id": user.id}}))

                    logger.info(
                        "file: utils/message_scheduler.py ~ send_daily_question_and_update_stats ~ user unlinked from server ~ user_id: %s, server_id: %s", user.id, server.id)

        async for user in User.all():
            # Delete user document if they're not in any server with the bot in it
            if len(user.display_information) == 0:
                await User.find_one(User.id == user.id).delete()

                logger.info(
                    "file: utils/message_scheduler.py ~ send_daily_question_and_update_stats ~ user document deleted ~ id: %s", user.id)

                continue

    logger.info(
        "file: utils/message_scheduler.py ~ send_daily_question_and_update_stats ~ ended")
