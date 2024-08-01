import asyncio

from loguru import logger

from alist_mikananirss.common.database import SubscribeDatabase
from alist_mikananirss.websites import FeedEntry, ResourceInfo, WebsiteFactory

from .download_manager import DownloadManager
from .filters import RegexFilter


class RssMonitor:
    def __init__(
        self,
        subscribe_urls: list[str] | str,
        filter: RegexFilter,
        use_extractor: bool = False,
    ) -> None:
        """The rss feed manager"""
        if not isinstance(subscribe_urls, list):
            subscribe_urls = [subscribe_urls]
        self.subscribe_urls = subscribe_urls
        self.websites = [
            WebsiteFactory.get_website_parser(url) for url in subscribe_urls
        ]
        self.filter = filter
        self.use_extractor = use_extractor
        self.db = SubscribeDatabase()
        self.interval_time = 300

    def set_interval_time(self, interval_time: int):
        self.interval_time = interval_time

    async def get_new_resources(self, fileter: RegexFilter) -> list[ResourceInfo]:
        new_resources: list[ResourceInfo] = []
        for website in self.websites:
            try:
                feed_entries = await website.get_feed_entries()
            except Exception as e:
                logger.error(f"Failed to get feed entries from {website.rss_url}: {e}")
                continue
            feed_entries_filted: list[FeedEntry] = []
            for entry in feed_entries:
                flag = fileter.filt_single(entry.resource_title)
                if flag:
                    feed_entries_filted.append(entry)
            for entry in feed_entries_filted:
                if not self.db.is_resource_title_exist(entry.resource_title):
                    try:
                        resource_info = await website.extract_resource_info(
                            entry, self.use_extractor
                        )
                        new_resources.append(resource_info)
                    except Exception as e:
                        logger.error(
                            f"Pass {entry.resource_title} because of error: {e}"
                        )
                        continue
        return new_resources

    async def run(self):
        while 1:
            logger.info("Start update checking")
            new_resources = await self.get_new_resources(fileter=self.filter)
            if not new_resources:
                logger.info("No new resources")
            else:
                await DownloadManager.add_download_tasks(new_resources)
            await asyncio.sleep(self.interval_time)
