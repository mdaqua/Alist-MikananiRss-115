from async_lru import alru_cache

from ..utils import Singleton
from .base import ExtractorBase
from .models import AnimeNameExtractResult, ResourceTitleExtractResult
from .regex import RegexExtractor

from loguru import logger
import aiohttp


class Extractor(metaclass=Singleton):
    def __init__(self, extractor: ExtractorBase = None):
        self._extractor = extractor
        self._tmp_regex_extractor = RegexExtractor()

    @classmethod
    def initialize(cls, extractor: ExtractorBase):
        """Initialize the Extractor with a specific extractor."""
        instance_ = cls()
        instance_.set_extractor(extractor)

    def set_extractor(self, extractor: ExtractorBase):
        """Set the extractor to be used."""
        self._extractor = extractor

    @alru_cache(maxsize=128)
    async def _analyse_anime_name(self, anime_name: str) -> AnimeNameExtractResult:
        """Analyse the anime name."""
        try:
            return await self._tmp_regex_extractor.analyse_anime_name(anime_name)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                logger.error(f"404 Not Found while analysing anime name: {anime_name}")
                return AnimeNameExtractResult(
                    anime_name="Unknown", season="Unknown"
                )
            logger.error(f"Error analysing anime name: {anime_name}, error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error analysing anime name: {anime_name}, error: {e}")
            raise

    @alru_cache(maxsize=128)
    async def _analyse_resource_title(
        self, resource_name: str, use_tmdb: bool = True
    ) -> ResourceTitleExtractResult:
        """Analyse the resource title."""
        try:
            return await self._extractor.analyse_resource_title(resource_name, use_tmdb)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                logger.error(f"404 Not Found while analysing resource title: {resource_name}")
                return ResourceTitleExtractResult(
                    episode="Unknown", quality="Unknown", languages=[], version="Unknown"
                )
            logger.error(f"Error analysing resource title: {resource_name}, error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error analysing resource title: {resource_name}, error: {e}")
            raise

    @classmethod
    async def analyse_anime_name(cls, anime_name: str) -> AnimeNameExtractResult:
        # chatgpt对番剧名分析不稳定，所以固定用正则分析番剧名
        instance = cls()
        if instance._extractor is None:
            raise RuntimeError("Extractor is not initialized")
        return await instance._analyse_anime_name(anime_name)

    @classmethod
    async def analyse_resource_title(
        cls, resource_name: str, use_tmdb: bool = True
    ) -> ResourceTitleExtractResult:
        instance = cls()
        if instance._extractor is None:
            raise RuntimeError("Extractor is not initialized")
        return await instance._analyse_resource_title(resource_name, use_tmdb)
