#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

VERSION = '1.0.0'
STARTED_AT = time.time()
TOKEN = os.environ.get('VPN_BOT_AGENT_TOKEN', '').strip()
HOST = os.environ.get('VPN_BOT_AGENT_HOST', '0.0.0.0').strip() or '0.0.0.0'
PORT = int(os.environ.get('VPN_BOT_AGENT_PORT', '8799'))
COMMAND_TIMEOUT = int(os.environ.get('VPN_BOT_AGENT_TIMEOUT', '25'))


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_meminfo() -> tuple[int, int]:
    total = 0
    available = 0
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as handle:
            for line in handle:
                if line.startswith('MemTotal:'):
                    total = int(line.split()[1])
                elif line.startswith('MemAvailable:'):
                    available = int(line.split()[1])
    except OSError:
        return 0, 0
    return total, available


def memory_percent() -> int:
    total, available = read_meminfo()
    if total <= 0:
        return 0
    used = max(total - available, 0)
    return int(round(used * 100 / total))


def disk_percent() -> int:
    usage = shutil.disk_usage('/')
    if usage.total <= 0:
        return 0
    return int(round(usage.used * 100 / usage.total))


def uptime_text() -> str:
    try:
        with open('/proc/uptime', 'r', encoding='utf-8') as handle:
            total_seconds = int(float(handle.read().split()[0]))
    except OSError:
        total_seconds = int(time.time() - STARTED_AT)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f'{days}d {hours}h {minutes}m'
    if hours:
        return f'{hours}h {minutes}m'
    return f'{minutes}m'


def load_text() -> str:
    try:
        load1, load5, load15 = os.getloadavg()
        return f'{load1:.2f} {load5:.2f} {load15:.2f}'
    except OSError:
        return '-'


def service_state(name: str) -> str:
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', name],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return 'unknown'
    return (result.stdout or result.stderr or 'unknown').strip()


def services_snapshot() -> dict[str, str]:
    return {
        'x-ui': service_state('x-ui'),
        'xray': service_state('xray'),
        'ssh': service_state('ssh'),
        'docker': service_state('docker'),
    }


def run_shell(command: str) -> dict:
    try:
        result = subprocess.run(
            command,
            shell=True,
            executable='/bin/bash',
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            'message': f'Команда превысила лимит {COMMAND_TIMEOUT} сек.',
            'stdout': (exc.stdout or '').strip(),
            'stderr': (exc.stderr or '').strip(),
            'exit_code': 124,
        }
    except Exception as exc:
        return {
            'message': f'Ошибка запуска команды: {exc}',
            'stdout': '',
            'stderr': str(exc),
            'exit_code': 1,
        }
    return {
        'message': 'Команда выполнена',
        'stdout': (result.stdout or '').strip(),
        'stderr': (result.stderr or '').strip(),
        'exit_code': int(result.returncode),
    }


class AgentHandler(BaseHTTPRequestHandler):
    server_version = 'vpn-bot-agent/1.0'

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        if self.path != '/health':
            json_response(self, {'error': 'Not found'}, status=HTTPStatus.NOT_FOUND)
            return
        payload = {
            'host': socket.gethostname(),
            'platform': platform.platform(),
            'uptime': uptime_text(),
            'load': load_text(),
            'memory_percent': memory_percent(),
            'disk_percent': disk_percent(),
            'services': services_snapshot(),
            'version': VERSION,
        }
        json_response(self, payload)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        if self.path == '/run':
            body = self._read_json_body()
            command = str(body.get('command') or '').strip()
            if not command:
                json_response(self, {'error': 'Команда не передана.'}, status=HTTPStatus.BAD_REQUEST)
                return
            json_response(self, run_shell(command))
            return
        if self.path.startswith('/run/'):
            command = unquote(self.path.removeprefix('/run/')).strip()
            if not command:
                json_response(self, {'error': 'Команда не передана.'}, status=HTTPStatus.BAD_REQUEST)
                return
            json_response(self, run_shell(command))
            return
        json_response(self, {'error': 'Not found'}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _authorized(self) -> bool:
        if not TOKEN:
            json_response(self, {'error': 'VPN_BOT_AGENT_TOKEN is not configured.'}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return False
        auth_header = self.headers.get('Authorization', '').strip()
        token_header = self.headers.get('X-Agent-Token', '').strip()
        token = token_header
        if auth_header.lower().startswith('bearer '):
            token = auth_header[7:].strip()
        if token != TOKEN:
            json_response(self, {'error': 'Unauthorized'}, status=HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b'{}'
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}


def main() -> None:
    Path('/opt/vpn-bot-agent').mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), AgentHandler)
    print(f'vpn-bot-agent listening on {HOST}:{PORT}')
    httpd.serve_forever()


if __name__ == '__main__':
    main()
