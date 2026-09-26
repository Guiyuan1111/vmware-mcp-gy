"""VMware Workstation Pro REST API Client."""

import httpx
from typing import Any

# 进程级共享连接池：避免每次请求重建 TCP 连接与 httpx 实例。
# 客户端按进程生命周期存活、不主动关闭；首次调用时以传入 auth/verify 创建，此后参数仅首建生效。
_shared_client: httpx.AsyncClient | None = None


def get_shared_client(auth: tuple[str, str] | None = None, verify: bool = False) -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(auth=auth, verify=verify)
    return _shared_client


class VMwareClient:
    """HTTP client for VMware Workstation Pro REST API."""

    def __init__(self, host: str = "localhost", port: int = 8697, username: str = "", password: str = "", verify: bool = False):
        self.base_url = f"http://{host}:{port}/api"
        self.auth = (username, password) if username else None
        self.verify = verify

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        client = get_shared_client(auth=self.auth, verify=self.verify)
        resp = await client.request(method, f"{self.base_url}{path}", **kwargs)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return None

    # VM Management
    async def list_vms(self) -> list[dict]:
        return await self._request("GET", "/vms")

    async def get_vm(self, vm_id: str) -> dict:
        return await self._request("GET", f"/vms/{vm_id}")

    async def create_vm(self, vm_id: str, name: str) -> dict:
        return await self._request("POST", f"/vms/{vm_id}", json={"name": name})

    async def delete_vm(self, vm_id: str) -> None:
        await self._request("DELETE", f"/vms/{vm_id}")

    async def update_vm(self, vm_id: str, settings: dict) -> dict:
        return await self._request("PUT", f"/vms/{vm_id}", json=settings)

    # VM Power
    async def get_power_state(self, vm_id: str) -> dict:
        return await self._request("GET", f"/vms/{vm_id}/power")

    async def change_power_state(self, vm_id: str, state: str) -> dict:
        return await self._request("PUT", f"/vms/{vm_id}/power", params={"state": state})

    # VM Network Adapters
    async def list_nics(self, vm_id: str) -> list[dict]:
        return await self._request("GET", f"/vms/{vm_id}/nic")

    async def create_nic(self, vm_id: str, nic_config: dict) -> dict:
        return await self._request("POST", f"/vms/{vm_id}/nic", json=nic_config)


    async def delete_nic(self, vm_id: str, index: int) -> None:
        await self._request("DELETE", f"/vms/{vm_id}/nic/{index}")

    async def get_vm_ip(self, vm_id: str) -> dict:
        return await self._request("GET", f"/vms/{vm_id}/ip")

    # VM Shared Folders
    async def list_shared_folders(self, vm_id: str) -> list[dict]:
        return await self._request("GET", f"/vms/{vm_id}/sharedfolders")

    async def create_shared_folder(self, vm_id: str, folder_config: dict) -> dict:
        return await self._request("POST", f"/vms/{vm_id}/sharedfolders", json=folder_config)


    async def delete_shared_folder(self, vm_id: str, folder_id: str) -> None:
        await self._request("DELETE", f"/vms/{vm_id}/sharedfolders/{folder_id}")

    # Host Networks
    async def list_networks(self) -> list[dict]:
        return await self._request("GET", "/vmnet")

    async def create_network(self, network_config: dict) -> dict:
        return await self._request("POST", "/vmnets", json=network_config)



    async def get_portforwards(self, vmnet: str) -> list[dict]:
        return await self._request("GET", f"/vmnet/{vmnet}/portforward")

    async def update_portforward(self, vmnet: str, protocol: str, port: int, config: dict) -> dict:
        return await self._request("PUT", f"/vmnet/{vmnet}/portforward/{protocol}/{port}", json=config)

    async def delete_portforward(self, vmnet: str, protocol: str, port: int) -> None:
        await self._request("DELETE", f"/vmnet/{vmnet}/portforward/{protocol}/{port}")
