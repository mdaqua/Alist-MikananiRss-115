import asyncio
import mimetypes
import os
import requests
import tempfile
import libtorrent as lt
import urllib.parse
from typing import Optional

import aiohttp

from alist_mikananirss.alist.tasks import (
    AlistDeletePolicy,
    AlistDownloaderType,
    AlistDownloadTask,
    AlistTask,
    AlistTaskList,
    AlistTaskState,
    AlistTaskStatus,
    AlistTaskType,
    AlistTransferTask,
)


def download_torrent_file(torrent_url: str, temp_dir: str) -> str:
    """ download torrent file to temp dir."""
    response = requests.get(torrent_url)
    torrent_file_path = os.path.join(temp_dir, "downloaded_file.torrent")
    with open(torrent_file_path, 'wb') as file:
        file.write(response.content)
    return torrent_file_path

def torrent2magnet(torrent_file_path: str) -> str:
    """convert torrent file to magnet link."""
    info = lt.torrent_info(torrent_file_path)
    return lt.make_magnet_uri(info)

class AlistClientError(Exception):
    pass


class Alist:
    def __init__(self, base_url: str, token: str, downloader: AlistDownloaderType):
        self.base_url = base_url
        self.token = token
        self.downloader = downloader
        self.session = None
        self._session_lock = asyncio.Lock()

    async def _ensure_session(self):
        async with self._session_lock:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(trust_env=True)

    async def _api_call(
        self,
        method: str,
        endpoint: str,
        custom_headers: dict[str, str] = None,
        **kwargs,
    ):
        await self._ensure_session()
        url = urllib.parse.urljoin(self.base_url, endpoint)
        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json",
            "User-Agent": "Alist-Mikanirss",
        }
        if custom_headers:
            headers.update(custom_headers)
        async with self.session.request(method, url, headers=headers, **kwargs) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if data["code"] != 200:
                raise AlistClientError(data.get("message", "Unknown error"))
            return data["data"]

    async def _init_alist_version(self):
        response_data = await self._api_call("GET", "/api/public/settings")
        self.version = response_data["version"][1:]  # 去掉字母v

    async def get_alist_ver(self):
        if not hasattr(self, "version"):
            await self._init_alist_version()
        return self.version

    async def add_offline_download_task(
        self,
        save_path: str,
        urls: list[str],
        policy: AlistDeletePolicy = AlistDeletePolicy.DeleteAlways,
    ) -> list[AlistDownloadTask]:
        # 115 Cloud downloader only support magnet link
        if self.downloader == AlistDownloaderType.CLOUD_115:
            temp_urls = []
            # if url is a torrent file, convert it to magnet link
            for url in urls:
                if url.endswith(".torrent"):
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_urls.append(torrent2magnet(download_torrent_file(url, temp_dir)))
                else:
                    temp_urls.append(url)
            urls = temp_urls

        response_data = await self._api_call(
            "POST",
            "api/fs/add_offline_download",
            json={
                "delete_policy": policy.value,
                "path": save_path,
                "urls": urls,
                "tool": self.downloader.value,
            },
        )
        return [AlistDownloadTask.from_json(task) for task in response_data["tasks"]]

    async def upload(self, save_path: str, file_path: str) -> bool:
        """upload local file to Alist.

        Args:
            save_path (str): Alist path
            file_path (str): local file path
        """
        file_path = os.path.abspath(file_path)
        file_name = os.path.basename(file_path)

        # Use utf-8 encoding to avoid UnicodeEncodeError
        file_path_encoded = file_path.encode("utf-8")

        mime_type = mimetypes.guess_type(file_name)[0]
        file_stat = os.stat(file_path)
        upload_path = urllib.parse.quote(f"{save_path}/{file_name}")

        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(file_stat.st_size),
            "file-path": upload_path,
        }

        with open(file_path_encoded, "rb") as f:
            await self._api_call("PUT", "api/fs/put", custom_headers=headers, data=f)
        return True

    async def list_dir(
        self, path, password=None, page=1, per_page=30, refresh=False
    ) -> list[str]:
        """List dir.

        Args:
            path (str): dir path
            password (str, optional): dir's password. Defaults to None.
            page (int, optional): page number. Defaults to 1.
            per_page (int, optional): how many item in one page. Defaults to 30.
            refresh (bool, optional): force to refresh. Defaults to False.

        Returns:
            Tuple[bool, List[str]]: Success flag and a list of files in the dir.
        """
        response_data = await self._api_call(
            "POST",
            "api/fs/list",
            json={
                "path": path,
                "password": password,
                "page": page,
                "per_page": per_page,
                "refresh": refresh,
            },
        )
        if response_data["content"]:
            files_list = [file_info["name"] for file_info in response_data["content"]]
        else:
            files_list = []
        return files_list

    async def _fetch_tasks(
        self, task_type: AlistTaskType, status: AlistTaskStatus
    ) -> AlistTaskList:
        json_data = await self._api_call(
            "GET", f"/api/task/{task_type.value}/{status.value}"
        )

        if task_type == AlistTaskType.TRANSFER:
            task_class = AlistTransferTask
        else:
            task_class = AlistDownloadTask

        tasks = [task_class.from_json(task) for task in json_data] if json_data else []
        return AlistTaskList(tasks)

    async def get_task_list(
        self, task_type: AlistTaskType, status: Optional[AlistTaskState] = None
    ) -> AlistTaskList:
        """
        Get Alist task list.

        Args:
            task_type (TaskType):
            status (TaskStatus, optional): Undone or Done; If None, return all tasks. Defaults to None.

        Returns:
            TaskList: The list contains all query tasks.
        """
        if status is None:
            done_tasks = await self._fetch_tasks(task_type, AlistTaskState.DONE)
            undone_tasks = await self._fetch_tasks(task_type, AlistTaskState.UNDONE)
            return done_tasks + undone_tasks
        else:
            return await self._fetch_tasks(task_type, status)

    async def cancel_task(
        self,
        task: AlistTask,
    ) -> bool:
        await self._api_call(
            "POST", f"/api/task/{task.task_type.value}/cancel?tid={task.tid}"
        )
        return True

    async def rename(self, path: str, new_name: str):
        """Rename a file or dir.

        Args:
            path (str): The absolute path of the file or dir of Alist
            new_name (str): Only name, not include path.
        """
        await self._api_call(
            "POST", "api/fs/rename", json={"path": path, "name": new_name}
        )

    async def is_folder_exist(self, path: str) -> bool:
        """Check if a folder exists.

        Args:
            path (str): folder path

        Returns:
            dict: folder state
        """
        try:
            response_data = await self._api_call(
                "POST",
                "api/fs/dirs",
                json={
                    "path": path
                },
            )
            return True
        except AlistClientError as e:
            if "not found" in str(e).lower():
                return False
            raise
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return False
            raise
    
    async def create_folder(self, path: str):
        """Create folder.

        Args:
            path (str): folder path
        """
        await self._api_call(
            "POST",
            "api/fs/mkdir",
            json={
                "path": path
            },
        )
