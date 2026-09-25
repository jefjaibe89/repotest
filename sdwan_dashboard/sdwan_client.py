"""
Cisco SD-WAN (Catalyst SD-WAN) vManage REST API client.
Handles authentication, session management, and all health-check data retrieval.
"""

import time
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SDWANError(Exception):
    """Base class for every error this module raises."""


class SDWANAuthError(SDWANError):
    """vManage rejected the credentials, or the session expired."""


class SDWANConnectionError(SDWANError):
    """vManage was unreachable, timed out, or returned an unusable response."""


def describe(exc: Exception) -> str:
    """Summarise a requests exception in terms an operator can act on.

    The raw text carries urllib3 internals and object addresses, which say
    nothing useful to whoever is looking at the dashboard at 3am.
    """
    if isinstance(exc, requests.Timeout):
        return "the controller did not respond in time"
    if isinstance(exc, requests.exceptions.SSLError):
        return "TLS verification failed (check the certificate or set VMANAGE_VERIFY_SSL=false)"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "the proxy refused the connection"
    if isinstance(exc, requests.ConnectionError):
        return "the host is unreachable or refused the connection"
    return exc.__class__.__name__


class SDWANClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool = False,
        timeout: int = 20,
    ):
        self.base_url = f"https://{host}:{port}"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self._authenticated = False
        self.logged_in_at = 0.0

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    # ------------------------------------------------------------------ auth
    def login(self) -> bool:
        """Authenticate against vManage. Raises on transport failure."""
        url = f"{self.base_url}/j_security_check"
        payload = {"j_username": self.username, "j_password": self.password}
        try:
            resp = self.session.post(
                url, data=payload, allow_redirects=False, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise SDWANConnectionError(
                f"Cannot reach vManage at {self.base_url} — {describe(exc)}"
            ) from exc

        # vManage answers a bad login with 200 + an HTML login page and no cookie.
        if resp.status_code in (200, 302) and "JSESSIONID" in self.session.cookies:
            self._get_token()
            self._authenticated = True
            self.logged_in_at = time.time()
            return True

        self._authenticated = False
        raise SDWANAuthError("vManage rejected the supplied credentials")

    def _get_token(self):
        try:
            resp = self.session.get(
                f"{self.base_url}/dataservice/client/token", timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise SDWANConnectionError(f"Failed to fetch XSRF token — {describe(exc)}") from exc
        # vManage <19.2 has no token endpoint; a 404 there is expected and harmless.
        if resp.status_code == 200:
            self.session.headers.update({"X-XSRF-TOKEN": resp.text})

    def logout(self):
        try:
            self.session.get(f"{self.base_url}/logout", timeout=self.timeout)
        except requests.RequestException:
            pass  # Best effort: the session is being discarded anyway.
        self._authenticated = False

    # ---------------------------------------------------------------- helpers
    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}/dataservice{path}"
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise SDWANConnectionError(f"Request to {path} failed — {describe(exc)}") from exc

        if resp.status_code in (401, 403):
            self._authenticated = False
            raise SDWANAuthError(f"vManage session expired or unauthorized for {path}")
        if resp.status_code >= 400:
            raise SDWANConnectionError(f"vManage returned HTTP {resp.status_code} for {path}")

        # An expired session can also surface as the HTML login page with a 200.
        try:
            return resp.json()
        except ValueError as exc:
            if "text/html" in resp.headers.get("Content-Type", ""):
                self._authenticated = False
                raise SDWANAuthError(f"vManage session expired (HTML response for {path})") from exc
            raise SDWANConnectionError(f"Malformed JSON from {path}: {exc}") from exc

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    # -------------------------------------------------------------- endpoints
    def get_device_list(self) -> list[dict]:
        data = self._get("/device")
        return data.get("data", [])

    def get_device_counters(self) -> dict:
        data = self._get("/device/counters")
        return data.get("data", {})

    def get_control_status(self) -> list[dict]:
        data = self._get("/device/control/count")
        return data.get("data", [])

    def get_interface_stats(self) -> list[dict]:
        data = self._get("/statistics/interface/aggregation")
        return data.get("data", [])

    def get_bfd_sessions(self) -> list[dict]:
        data = self._get("/device/bfd/summary")
        return data.get("data", [])

    def get_omp_peers(self) -> list[dict]:
        data = self._get("/device/omp/summary")
        return data.get("data", [])

    def get_alarms(self, hours: int = 24) -> list[dict]:
        payload = {
            "query": {
                "condition": "AND",
                "rules": [
                    {
                        "value": [str(hours)],
                        "field": "entry_time",
                        "type": "date",
                        "operator": "last_n_hours",
                    }
                ],
            },
            "size": 100,
        }
        data = self._request("POST", "/alarms", json=payload)
        return data.get("data", [])

    def get_wan_edges(self) -> list[dict]:
        data = self._get("/device?deviceRole=cedge,vedge")
        return data.get("data", [])

    def get_system_status(self, device_id: str) -> dict:
        data = self._get(f"/device/system/status?deviceId={device_id}")
        return data.get("data", [{}])[0]

    def get_reachability_summary(self) -> dict:
        data = self._get("/device/reachability/summary")
        return data.get("data", {})


# ------------------------------------------------- demo / mock data provider
class MockSDWANClient:
    """Returns realistic-looking static data for demo / development use."""

    def get_device_list(self) -> list[dict]:
        return [
            {"system-ip": "1.1.1.1", "host-name": "vManage-1", "device-type": "vmanage",
             "reachability": "reachable", "status": "normal", "board-serial": "SN-001",
             "version": "20.12.1", "site-id": "100", "uptime-date": 1713000000000},
            {"system-ip": "2.2.2.1", "host-name": "vSmart-1", "device-type": "vsmart",
             "reachability": "reachable", "status": "normal", "board-serial": "SN-002",
             "version": "20.12.1", "site-id": "100", "uptime-date": 1713000000000},
            {"system-ip": "2.2.2.2", "host-name": "vSmart-2", "device-type": "vsmart",
             "reachability": "reachable", "status": "normal", "board-serial": "SN-003",
             "version": "20.12.1", "site-id": "100", "uptime-date": 1713000000000},
            {"system-ip": "3.3.3.1", "host-name": "vBond-1", "device-type": "vbond",
             "reachability": "reachable", "status": "normal", "board-serial": "SN-004",
             "version": "20.12.1", "site-id": "100", "uptime-date": 1713000000000},
            {"system-ip": "10.0.1.1", "host-name": "cedge-BR1-MPLS", "device-type": "vedge",
             "reachability": "reachable", "status": "normal", "board-serial": "FTX2214ABCD",
             "version": "17.12.3", "site-id": "101", "uptime-date": 1713100000000,
             "cpu-load": 18, "mem-util": 52, "device-model": "C8300-1N1S-4T2X"},
            {"system-ip": "10.0.1.2", "host-name": "cedge-BR1-INET", "device-type": "vedge",
             "reachability": "reachable", "status": "normal", "board-serial": "FTX2214ABCE",
             "version": "17.12.3", "site-id": "101", "uptime-date": 1713100000000,
             "cpu-load": 22, "mem-util": 60, "device-model": "C8300-1N1S-4T2X"},
            {"system-ip": "10.0.2.1", "host-name": "cedge-BR2-MPLS", "device-type": "vedge",
             "reachability": "reachable", "status": "normal", "board-serial": "FTX2215WXYZ",
             "version": "17.12.3", "site-id": "102", "uptime-date": 1713200000000,
             "cpu-load": 31, "mem-util": 68, "device-model": "C8200-1N-4T"},
            {"system-ip": "10.0.3.1", "host-name": "cedge-HQ-1", "device-type": "vedge",
             "reachability": "reachable", "status": "normal", "board-serial": "FTX2210HQHQ",
             "version": "17.12.3", "site-id": "200", "uptime-date": 1712000000000,
             "cpu-load": 45, "mem-util": 72, "device-model": "ISR4451-X/K9"},
            {"system-ip": "10.0.4.1", "host-name": "cedge-DC1-1", "device-type": "vedge",
             "reachability": "reachable", "status": "normal", "board-serial": "FTX2210DC01",
             "version": "17.12.3", "site-id": "300", "uptime-date": 1711000000000,
             "cpu-load": 55, "mem-util": 80, "device-model": "C8500-12X4QC"},
            {"system-ip": "10.0.5.1", "host-name": "cedge-BR5-INET", "device-type": "vedge",
             "reachability": "unreachable", "status": "error", "board-serial": "FTX2213BR05",
             "version": "17.12.1", "site-id": "105", "uptime-date": None,
             "cpu-load": None, "mem-util": None, "device-model": "C1117-4PLTEEA"},
            {"system-ip": "10.0.6.1", "host-name": "cedge-BR6-LTE", "device-type": "vedge",
             "reachability": "unreachable", "status": "warning", "board-serial": "FTX2213BR06",
             "version": "17.12.2", "site-id": "106", "uptime-date": None,
             "cpu-load": None, "mem-util": None, "device-model": "C1117-4PLTEEA"},
        ]

    def get_device_counters(self) -> dict:
        return {
            "totalCount": 11,
            "reachableCount": 9,
            "unreachableCount": 2,
            "partialCount": 0,
        }

    def get_bfd_sessions(self) -> list[dict]:
        return [
            {"system-ip": "10.0.1.1", "host-name": "cedge-BR1-MPLS", "bfd-sessions-up": 4, "bfd-sessions-down": 0, "total": 4},
            {"system-ip": "10.0.1.2", "host-name": "cedge-BR1-INET", "bfd-sessions-up": 3, "bfd-sessions-down": 1, "total": 4},
            {"system-ip": "10.0.2.1", "host-name": "cedge-BR2-MPLS", "bfd-sessions-up": 4, "bfd-sessions-down": 0, "total": 4},
            {"system-ip": "10.0.3.1", "host-name": "cedge-HQ-1",    "bfd-sessions-up": 8, "bfd-sessions-down": 0, "total": 8},
            {"system-ip": "10.0.4.1", "host-name": "cedge-DC1-1",   "bfd-sessions-up": 6, "bfd-sessions-down": 0, "total": 6},
        ]

    def get_omp_peers(self) -> list[dict]:
        return [
            {"system-ip": "10.0.1.1", "host-name": "cedge-BR1-MPLS", "oper-state": "up", "peers": 2},
            {"system-ip": "10.0.1.2", "host-name": "cedge-BR1-INET", "oper-state": "up", "peers": 2},
            {"system-ip": "10.0.2.1", "host-name": "cedge-BR2-MPLS", "oper-state": "up", "peers": 2},
            {"system-ip": "10.0.3.1", "host-name": "cedge-HQ-1",    "oper-state": "up", "peers": 2},
            {"system-ip": "10.0.4.1", "host-name": "cedge-DC1-1",   "oper-state": "up", "peers": 2},
        ]

    def get_alarms(self, hours: int = 24) -> list[dict]:
        return [
            {"severity": "Critical", "type": "Device unreachable",   "message": "cedge-BR5-INET is unreachable",       "entry_time": 1713340000000, "acknowledged": False},
            {"severity": "Critical", "type": "Device unreachable",   "message": "cedge-BR6-LTE is unreachable",        "entry_time": 1713330000000, "acknowledged": False},
            {"severity": "Major",    "type": "BFD down",             "message": "BFD session down on cedge-BR1-INET",  "entry_time": 1713320000000, "acknowledged": False},
            {"severity": "Major",    "type": "High CPU",             "message": "CPU > 80% on cedge-DC1-1",            "entry_time": 1713310000000, "acknowledged": True},
            {"severity": "Minor",    "type": "Interface flap",       "message": "GigE0/0/2 flap on cedge-BR2-MPLS",   "entry_time": 1713300000000, "acknowledged": True},
            {"severity": "Minor",    "type": "High memory",          "message": "Memory > 75% on cedge-HQ-1",          "entry_time": 1713290000000, "acknowledged": True},
            {"severity": "Info",     "type": "Config change",        "message": "Template pushed to cedge-BR1-MPLS",   "entry_time": 1713280000000, "acknowledged": True},
        ]

    def get_control_status(self) -> list[dict]:
        return [
            {"device-type": "vmanage", "count": 1, "up": 1, "down": 0},
            {"device-type": "vsmart",  "count": 2, "up": 2, "down": 0},
            {"device-type": "vbond",   "count": 1, "up": 1, "down": 0},
        ]

    def get_interface_stats(self) -> list[dict]:
        return [
            {"host-name": "cedge-BR1-MPLS", "interface": "GigabitEthernet0/0/0", "tx-kbps": 45000,  "rx-kbps": 38000,  "if-oper-status": "Up"},
            {"host-name": "cedge-BR1-INET", "interface": "GigabitEthernet0/0/1", "tx-kbps": 120000, "rx-kbps": 95000,  "if-oper-status": "Up"},
            {"host-name": "cedge-BR2-MPLS", "interface": "GigabitEthernet0/0/0", "tx-kbps": 30000,  "rx-kbps": 22000,  "if-oper-status": "Up"},
            {"host-name": "cedge-HQ-1",     "interface": "GigabitEthernet0/0/0", "tx-kbps": 280000, "rx-kbps": 310000, "if-oper-status": "Up"},
            {"host-name": "cedge-DC1-1",    "interface": "GigabitEthernet0/0/0", "tx-kbps": 540000, "rx-kbps": 490000, "if-oper-status": "Up"},
            {"host-name": "cedge-BR1-INET", "interface": "GigabitEthernet0/0/2", "tx-kbps": 0,      "rx-kbps": 0,      "if-oper-status": "Down"},
        ]

    def get_reachability_summary(self) -> dict:
        return {"reachable": 9, "unreachable": 2}
