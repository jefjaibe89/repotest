"""
Cisco SD-WAN (Catalyst SD-WAN) vManage REST API client.
Handles authentication, session management, and all health-check data retrieval.
"""

import math
import time
from typing import Any

import requests
import urllib3

import i18n

def _silence_insecure_warning():
    """Suppress urllib3's warning only for a client that opted out of TLS checks.

    Calling this at import time silenced it for every client, including ones
    that do verify — removing the only runtime signal that verification was off.
    """
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SDWANError(Exception):
    """Base class for every error this module raises.

    Carries a catalog key alongside the English message so the dashboard can
    show the reason in the viewer's language instead of whatever language the
    poller happened to be running in.
    """

    def __init__(self, message: str, key: str | None = None):
        super().__init__(message)
        self.key = key


class SDWANAuthError(SDWANError):
    """vManage rejected the credentials, or the session expired."""


class SDWANConnectionError(SDWANError):
    """vManage was unreachable, timed out, or returned an unusable response."""


def describe_key(exc: Exception) -> str | None:
    """Catalog key for a transport failure, so the UI can render it translated.

    Returns None when the failure has no known cause, and the caller falls back
    to the exception class name.
    """
    if isinstance(exc, requests.Timeout):
        return "vmanage.timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        # Never suggest turning verification off here: this is exactly the
        # error an interception attack produces.
        return "vmanage.tls"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "vmanage.proxy"
    if isinstance(exc, requests.ConnectionError):
        return "vmanage.unreachable"
    return None


def describe(exc: Exception) -> str:
    """Summarise a requests exception in terms an operator can act on.

    Always English: this text goes to the logs and to the stored record. The
    browser re-renders it from the key in the viewer's own language.
    """
    key = describe_key(exc)
    if key is None:
        return exc.__class__.__name__
    return i18n.translate(key, i18n.DEFAULT_LOCALE)


class SDWANClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool | str = True,
        timeout: int = 20,
    ):
        self.base_url = f"https://{host}:{port}"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        # requests takes True, False, or a CA bundle path here.
        self.session.verify = verify_ssl
        if verify_ssl is False:
            _silence_insecure_warning()
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
                f"Cannot reach vManage at {self.base_url} — {describe(exc)}",
                key=describe_key(exc),
            ) from exc

        # vManage answers a bad login with 200 + an HTML login page and no cookie.
        if resp.status_code in (200, 302) and "JSESSIONID" in self.session.cookies:
            self._get_token()
            self._authenticated = True
            self.logged_in_at = time.time()
            return True

        self._authenticated = False
        raise SDWANAuthError(
            "vManage rejected the supplied credentials", key="vmanage.unauthorized"
        )

    def _get_token(self):
        try:
            resp = self.session.get(
                f"{self.base_url}/dataservice/client/token", timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise SDWANConnectionError(
                f"Failed to fetch XSRF token — {describe(exc)}", key=describe_key(exc)
            ) from exc
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
            raise SDWANConnectionError(
                f"Request to {path} failed — {describe(exc)}", key=describe_key(exc)
            ) from exc

        if resp.status_code in (401, 403):
            self._authenticated = False
            raise SDWANAuthError(
                f"vManage session expired or unauthorized for {path}",
                key="vmanage.unauthorized",
            )
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

    # ------------------------------------------------- tunnels and drill-down
    def get_tunnel_stats(self) -> list[dict]:
        """Fabric-wide IPsec/TLOC tunnel state."""
        data = self._get("/device/tunnel/statistics")
        return data.get("data", [])

    def get_device_tunnels(self, device_id: str) -> list[dict]:
        data = self._get(f"/device/tunnel/statistics?deviceId={device_id}")
        return data.get("data", [])

    def get_device_interfaces(self, device_id: str) -> list[dict]:
        data = self._get(f"/device/interface?deviceId={device_id}")
        return data.get("data", [])

    def get_device_control_connections(self, device_id: str) -> list[dict]:
        data = self._get(f"/device/control/connections?deviceId={device_id}")
        return data.get("data", [])

    def get_device_omp_routes(self, device_id: str) -> list[dict]:
        data = self._get(f"/device/omp/routes/received?deviceId={device_id}")
        return data.get("data", [])

    # ------------------------------------------------------ QoS and capacity
    def get_qos_stats(self) -> list[dict]:
        """Per-interface, per-queue counters from the applied QoS policy."""
        data = self._get("/device/qos/scheduler")
        return data.get("data", [])

    def get_link_stats(self) -> list[dict]:
        """WAN interfaces with their configured bandwidth, for utilisation."""
        data = self._get("/device/interface?vpn-id=0")
        return data.get("data", [])

    # -------------------------------------------- application-aware routing
    def get_sla_classes(self) -> list[dict]:
        """SLA class definitions: the latency/loss/jitter budget of each class."""
        data = self._get("/device/app-route/sla-class")
        return data.get("data", [])

    def get_app_route_stats(self) -> list[dict]:
        """Per-tunnel app-route measurements, used to judge SLA compliance."""
        data = self._get("/device/app-route/statistics")
        return data.get("data", [])

    def get_app_route_events(self, hours: int = 24) -> list[dict]:
        """Path switchovers: when app-aware routing moved traffic, and why."""
        payload = {
            "query": {
                "condition": "AND",
                "rules": [
                    {
                        "value": [str(hours)],
                        "field": "entry_time",
                        "type": "date",
                        "operator": "last_n_hours",
                    },
                    {
                        "value": ["app-route-sla-change"],
                        "field": "eventname",
                        "type": "string",
                        "operator": "in",
                    },
                ],
            },
            "size": 100,
        }
        data = self._request("POST", "/event", json=payload)
        return data.get("data", [])

    def get_system_status(self, device_id: str) -> dict:
        data = self._get(f"/device/system/status?deviceId={device_id}")
        return data.get("data", [{}])[0]

    def get_reachability_summary(self) -> dict:
        data = self._get("/device/reachability/summary")
        return data.get("data", {})


# ------------------------------------------------- demo / mock data provider
class MockSDWANClient:
    """Returns realistic-looking demo data, so the dashboard runs with no controller.

    CPU and memory drift slowly around their baseline: a demo whose history
    charts are perfectly flat lines looks broken rather than healthy.
    """

    def _drift(self, base: int, spread: int = 6, phase: float = 0.0) -> int:
        """Nudge a baseline value with a smooth, deterministic wobble."""
        wave = math.sin(time.time() / 90.0 + phase)
        return max(1, min(99, int(round(base + wave * spread))))

    def get_device_list(self) -> list[dict]:
        devices = self._base_devices()
        for i, d in enumerate(devices):
            if d.get("cpu-load") is not None:
                d["cpu-load"] = self._drift(d["cpu-load"], phase=i)
            if d.get("mem-util") is not None:
                d["mem-util"] = self._drift(d["mem-util"], spread=4, phase=i + 0.5)
        return devices

    def _base_devices(self) -> list[dict]:
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

    # ------------------------------------------------- tunnels and drill-down
    def get_tunnel_stats(self) -> list[dict]:
        return [
            {"system-ip": "10.0.1.1", "host-name": "cedge-BR1-MPLS", "local-color": "mpls",
             "remote-color": "mpls", "remote-system-ip": "10.0.3.1", "state": "up",
             "tunnel-protocol": "IPSEC", "latency": 12, "loss-percentage": 0.0, "jitter": 2},
            {"system-ip": "10.0.1.1", "host-name": "cedge-BR1-MPLS", "local-color": "mpls",
             "remote-color": "mpls", "remote-system-ip": "10.0.4.1", "state": "up",
             "tunnel-protocol": "IPSEC", "latency": 18, "loss-percentage": 0.1, "jitter": 3},
            {"system-ip": "10.0.1.2", "host-name": "cedge-BR1-INET", "local-color": "biz-internet",
             "remote-color": "biz-internet", "remote-system-ip": "10.0.3.1", "state": "up",
             "tunnel-protocol": "IPSEC", "latency": 34, "loss-percentage": 0.4, "jitter": 8},
            {"system-ip": "10.0.1.2", "host-name": "cedge-BR1-INET", "local-color": "biz-internet",
             "remote-color": "biz-internet", "remote-system-ip": "10.0.4.1", "state": "down",
             "tunnel-protocol": "IPSEC", "latency": None, "loss-percentage": 100.0, "jitter": None},
            {"system-ip": "10.0.2.1", "host-name": "cedge-BR2-MPLS", "local-color": "mpls",
             "remote-color": "mpls", "remote-system-ip": "10.0.3.1", "state": "up",
             "tunnel-protocol": "IPSEC", "latency": 15, "loss-percentage": 0.0, "jitter": 2},
            {"system-ip": "10.0.3.1", "host-name": "cedge-HQ-1", "local-color": "mpls",
             "remote-color": "mpls", "remote-system-ip": "10.0.4.1", "state": "up",
             "tunnel-protocol": "IPSEC", "latency": 8, "loss-percentage": 0.0, "jitter": 1},
            {"system-ip": "10.0.4.1", "host-name": "cedge-DC1-1", "local-color": "biz-internet",
             "remote-color": "biz-internet", "remote-system-ip": "10.0.2.1", "state": "up",
             "tunnel-protocol": "IPSEC", "latency": 41, "loss-percentage": 1.2, "jitter": 11},
        ]

    def get_device_tunnels(self, device_id: str) -> list[dict]:
        return [t for t in self.get_tunnel_stats() if t["system-ip"] == device_id]

    def get_device_interfaces(self, device_id: str) -> list[dict]:
        catalogue = {
            "10.0.1.1": [
                {"ifname": "GigabitEthernet0/0/0", "if-oper-status": "Up", "if-admin-status": "Up",
                 "ip-address": "172.16.1.1/30", "vpn-id": "0", "speed-mbps": "1000"},
                {"ifname": "GigabitEthernet0/0/1", "if-oper-status": "Up", "if-admin-status": "Up",
                 "ip-address": "10.1.1.1/24", "vpn-id": "10", "speed-mbps": "1000"},
            ],
            "10.0.1.2": [
                {"ifname": "GigabitEthernet0/0/1", "if-oper-status": "Up", "if-admin-status": "Up",
                 "ip-address": "203.0.113.5/30", "vpn-id": "0", "speed-mbps": "1000"},
                {"ifname": "GigabitEthernet0/0/2", "if-oper-status": "Down", "if-admin-status": "Up",
                 "ip-address": "10.1.2.1/24", "vpn-id": "10", "speed-mbps": "1000"},
            ],
        }
        return catalogue.get(device_id, [
            {"ifname": "GigabitEthernet0/0/0", "if-oper-status": "Up", "if-admin-status": "Up",
             "ip-address": "172.16.9.1/30", "vpn-id": "0", "speed-mbps": "1000"},
        ])

    def get_device_control_connections(self, device_id: str) -> list[dict]:
        return [
            {"peer-type": "vsmart", "system-ip": "2.2.2.1", "local-color": "mpls",
             "state": "up", "protocol": "dtls", "uptime": "12:04:33"},
            {"peer-type": "vsmart", "system-ip": "2.2.2.2", "local-color": "mpls",
             "state": "up", "protocol": "dtls", "uptime": "12:04:31"},
            {"peer-type": "vmanage", "system-ip": "1.1.1.1", "local-color": "mpls",
             "state": "up", "protocol": "dtls", "uptime": "12:04:35"},
        ]

    def get_device_omp_routes(self, device_id: str) -> list[dict]:
        return [
            {"vpn-id": "10", "prefix": "10.1.0.0/16", "from-peer": "2.2.2.1", "status": "C,I,R"},
            {"vpn-id": "10", "prefix": "10.2.0.0/16", "from-peer": "2.2.2.1", "status": "C,I,R"},
            {"vpn-id": "20", "prefix": "192.168.0.0/16", "from-peer": "2.2.2.2", "status": "C,I,R"},
        ]

    # ------------------------------------------------------ QoS and capacity
    def get_qos_stats(self) -> list[dict]:
        """Four queues per WAN interface, as a branch QoS policy normally has.

        Voice is policed but never dropped; the pain shows up in best-effort on
        the congested internet circuits, which is where it shows up in practice.
        """
        rows = [
            # host, interface, policy, queue, class, tx-pkts, tx-bytes, drops
            ("cedge-BR1-MPLS", "GigabitEthernet0/0/0", "BRANCH-QOS", 0, "voice",         1_284_000,   205_440_000,      0),
            ("cedge-BR1-MPLS", "GigabitEthernet0/0/0", "BRANCH-QOS", 1, "critical-data",   842_500,   674_000_000,     31),
            ("cedge-BR1-MPLS", "GigabitEthernet0/0/0", "BRANCH-QOS", 2, "business",      1_105_300,   994_770_000,    218),
            ("cedge-BR1-MPLS", "GigabitEthernet0/0/0", "BRANCH-QOS", 3, "best-effort",   2_640_800, 1_848_560_000,  4_912),

            ("cedge-BR1-INET", "GigabitEthernet0/0/1", "BRANCH-QOS", 0, "voice",           962_100,   153_936_000,      0),
            ("cedge-BR1-INET", "GigabitEthernet0/0/1", "BRANCH-QOS", 1, "critical-data",   611_400,   489_120_000,    402),
            ("cedge-BR1-INET", "GigabitEthernet0/0/1", "BRANCH-QOS", 2, "business",        884_200,   795_780_000,  3_140),
            ("cedge-BR1-INET", "GigabitEthernet0/0/1", "BRANCH-QOS", 3, "best-effort",   3_218_900, 2_253_230_000, 41_870),

            ("cedge-BR2-MPLS", "GigabitEthernet0/0/0", "BRANCH-QOS", 0, "voice",           704_600,   112_736_000,      0),
            ("cedge-BR2-MPLS", "GigabitEthernet0/0/0", "BRANCH-QOS", 1, "critical-data",   398_200,   318_560_000,      8),
            ("cedge-BR2-MPLS", "GigabitEthernet0/0/0", "BRANCH-QOS", 2, "business",        612_900,   551_610_000,     94),
            ("cedge-BR2-MPLS", "GigabitEthernet0/0/0", "BRANCH-QOS", 3, "best-effort",   1_488_300, 1_041_810_000,  1_204),

            ("cedge-HQ-1",     "GigabitEthernet0/0/0", "HQ-QOS",     0, "voice",         4_112_700,   658_032_000,      0),
            ("cedge-HQ-1",     "GigabitEthernet0/0/0", "HQ-QOS",     1, "critical-data", 3_004_100, 2_403_280_000,     52),
            ("cedge-HQ-1",     "GigabitEthernet0/0/0", "HQ-QOS",     2, "business",      5_218_600, 4_696_740_000,    611),
            ("cedge-HQ-1",     "GigabitEthernet0/0/0", "HQ-QOS",     3, "best-effort",   9_884_200, 6_918_940_000,  7_330),

            ("cedge-DC1-1",    "GigabitEthernet0/0/0", "DC-QOS",     0, "voice",         6_440_900, 1_030_544_000,      0),
            ("cedge-DC1-1",    "GigabitEthernet0/0/0", "DC-QOS",     1, "critical-data", 5_118_300, 4_094_640_000,      0),
            ("cedge-DC1-1",    "GigabitEthernet0/0/0", "DC-QOS",     2, "business",      8_002_400, 7_202_160_000,    145),
            ("cedge-DC1-1",    "GigabitEthernet0/0/0", "DC-QOS",     3, "best-effort",  14_330_700, 10_031_490_000, 2_088),
        ]
        return [
            {
                "host-name": host, "interface": iface, "policy-name": policy,
                "queue-id": queue, "class-name": cls, "vpn-id": "0",
                "tx-packets": pkts, "tx-bytes": byts, "drop-packets": drops,
            }
            for host, iface, policy, queue, cls, pkts, byts, drops in rows
        ]

    def get_link_stats(self) -> list[dict]:
        """WAN circuits with configured bandwidth, so utilisation is a ratio.

        Bandwidth is in kbps, matching how vManage reports it.
        """
        rows = [
            # host, interface, color, bw-up, bw-down, tx, rx, status
            ("cedge-BR1-MPLS", "GigabitEthernet0/0/0", "mpls",          100_000, 100_000,  45_000,  38_000, "Up"),
            ("cedge-BR1-INET", "GigabitEthernet0/0/1", "biz-internet",  200_000, 200_000, 184_000, 171_000, "Up"),
            ("cedge-BR2-MPLS", "GigabitEthernet0/0/0", "mpls",           50_000,  50_000,  30_000,  22_000, "Up"),
            ("cedge-HQ-1",     "GigabitEthernet0/0/0", "mpls",          500_000, 500_000, 280_000, 310_000, "Up"),
            ("cedge-DC1-1",    "GigabitEthernet0/0/0", "biz-internet", 1_000_000, 1_000_000, 540_000, 490_000, "Up"),
            ("cedge-BR1-INET", "GigabitEthernet0/0/2", "lte",            20_000,  20_000,       0,       0, "Down"),
        ]
        return [
            {
                "host-name": host, "interface": iface, "color": color,
                "bandwidth-upstream": up, "bandwidth-downstream": down,
                "tx-kbps": tx, "rx-kbps": rx, "if-oper-status": status, "vpn-id": "0",
            }
            for host, iface, color, up, down, tx, rx, status in rows
        ]

    # -------------------------------------------- application-aware routing
    def get_sla_classes(self) -> list[dict]:
        return [
            {"name": "VOICE-SLA",    "latency": 50,  "loss": 1.0, "jitter": 20},
            {"name": "CRITICAL-SLA", "latency": 150, "loss": 2.0, "jitter": 50},
            {"name": "BULK-SLA",     "latency": 300, "loss": 5.0, "jitter": 100},
        ]

    def get_app_route_stats(self) -> list[dict]:
        """Per-tunnel measurements against the SLA class bound to each one."""
        rows = [
            # host, local-color, remote-ip, remote-color, sla, latency, loss, jitter, policy
            ("cedge-BR1-MPLS", "mpls",         "10.0.3.1", "mpls",          "VOICE-SLA",    12,  0.0,  2, "AAR-VOICE"),
            ("cedge-BR1-MPLS", "mpls",         "10.0.4.1", "mpls",          "CRITICAL-SLA", 18,  0.1,  3, "AAR-CRITICAL"),
            ("cedge-BR1-INET", "biz-internet", "10.0.3.1", "biz-internet",  "VOICE-SLA",    34,  0.4,  8, "AAR-VOICE"),
            # Out of SLA on both loss and jitter: this is what moved the traffic.
            ("cedge-BR1-INET", "biz-internet", "10.0.4.1", "biz-internet",  "VOICE-SLA",    78,  3.2, 27, "AAR-VOICE"),
            ("cedge-BR2-MPLS", "mpls",         "10.0.3.1", "mpls",          "VOICE-SLA",    15,  0.0,  2, "AAR-VOICE"),
            ("cedge-BR2-MPLS", "mpls",         "10.0.4.1", "mpls",          "BULK-SLA",     22,  0.3,  6, "AAR-BULK"),
            ("cedge-HQ-1",     "mpls",         "10.0.4.1", "mpls",          "CRITICAL-SLA",  8,  0.0,  1, "AAR-CRITICAL"),
            ("cedge-DC1-1",    "biz-internet", "10.0.2.1", "biz-internet",  "BULK-SLA",     41,  1.2, 11, "AAR-BULK"),
            # Latency over the CRITICAL budget, loss and jitter still fine.
            ("cedge-DC1-1",    "biz-internet", "10.0.3.1", "biz-internet",  "CRITICAL-SLA", 187, 1.8, 34, "AAR-CRITICAL"),
        ]
        return [
            {
                "host-name": host, "local-color": local, "remote-system-ip": remote_ip,
                "remote-color": remote_color, "sla-class": sla, "app-route-policy": policy,
                "latency": lat, "loss": loss, "jitter": jit,
            }
            for host, local, remote_ip, remote_color, sla, lat, loss, jit, policy in rows
        ]

    def get_app_route_events(self, hours: int = 24) -> list[dict]:
        """Recent path switchovers, newest first."""
        now = int(time.time() * 1000)
        minute = 60_000
        rows = [
            (now - 14 * minute,  "cedge-BR1-INET", "AAR-VOICE",    "VOICE-SLA",    "biz-internet", "mpls",         "loss"),
            (now - 52 * minute,  "cedge-DC1-1",    "AAR-CRITICAL", "CRITICAL-SLA", "biz-internet", "mpls",         "latency"),
            (now - 96 * minute,  "cedge-BR1-INET", "AAR-VOICE",    "VOICE-SLA",    "mpls",         "biz-internet", "recovered"),
            (now - 183 * minute, "cedge-BR2-MPLS", "AAR-BULK",     "BULK-SLA",     "mpls",         "biz-internet", "jitter"),
            (now - 301 * minute, "cedge-BR1-INET", "AAR-VOICE",    "VOICE-SLA",    "biz-internet", "mpls",         "loss"),
        ]
        return [
            {
                "entry_time": ts, "host-name": host, "app-route-policy": policy,
                "sla-class": sla, "from-color": src, "to-color": dst, "reason": reason,
            }
            for ts, host, policy, sla, src, dst, reason in rows
        ]
